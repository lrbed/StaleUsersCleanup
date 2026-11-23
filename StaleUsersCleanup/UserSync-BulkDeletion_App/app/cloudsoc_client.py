import time
import ssl
import json
import base64
import threading
import urllib.request
import urllib.parse
import urllib.error
import socket
from typing import Dict, Any, Optional, List
from app.config import generate_auth_header


class AuthExpiredException(Exception):
    """Raised when CloudSOC returns 401/403 — signals the queue to pause for re-auth."""
    pass


class RateLimiter:
    """
    Token bucket and sliding-window rate limiter enforcing:
    1. Maximum calls per second (default: ~2.5 calls/s = 0.4s interval)
    2. Strict hourly budget (default: 10,000 calls per 3600 seconds)

    Supports a Try-Now interrupt so the UI can override an active backoff wait.
    Also exposes live telemetry: total_calls_session, hourly_quota_remaining, total_429_count.
    """
    def __init__(self, max_per_second: float = 2.5, max_per_hour: int = 10000):
        self.min_interval = 1.0 / max_per_second if max_per_second > 0 else 0.4
        self.max_per_hour = max_per_hour
        self.last_call_time = 0.0
        self.call_timestamps: List[float] = []
        self._lock = threading.Lock()

        # Backoff interrupt support
        self._backoff_event = threading.Event()
        self.is_in_backoff: bool = False
        self.backoff_seconds_remaining: float = 0.0
        self.backoff_until: float = 0.0

        # Live telemetry counters
        self.total_calls_session: int = 0
        self.total_429_count: int = 0
        self.total_retries: int = 0
        self.calls_last_hour: int = 0

    def acquire(self) -> float:
        """Block until a call slot is available; respects rate limits and hourly budget."""
        with self._lock:
            now = time.time()
            # Prune call_timestamps older than 1 hour
            one_hour_ago = now - 3600.0
            self.call_timestamps = [t for t in self.call_timestamps if t > one_hour_ago]
            self.calls_last_hour = len(self.call_timestamps)

            # Enforce hourly limit — sleep until oldest slot expires
            if len(self.call_timestamps) >= self.max_per_hour:
                oldest = self.call_timestamps[0]
                sleep_needed = (oldest + 3600.0) - now + 1.0
                if sleep_needed > 0:
                    time.sleep(sleep_needed)
                    now = time.time()

            # Enforce per-second interval
            elapsed = now - self.last_call_time
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)

            actual_time = time.time()
            self.last_call_time = actual_time
            self.call_timestamps.append(actual_time)
            self.total_calls_session += 1
            self.calls_last_hour = len([t for t in self.call_timestamps if t > time.time() - 3600.0])
            return actual_time

    def wait_backoff(self, wait_time: float) -> None:
        """
        Interruptible backoff sleep. The event is cleared before sleeping so that
        interrupt_backoff() can fire it at any point to cut the wait short.
        """
        self.is_in_backoff = True
        self.backoff_until = time.time() + wait_time
        self._backoff_event.clear()

        # Sleep in short slices to keep backoff_seconds_remaining accurate
        deadline = time.time() + wait_time
        while time.time() < deadline:
            self.backoff_seconds_remaining = max(0.0, self.backoff_until - time.time())
            if self._backoff_event.wait(timeout=0.5):
                # Interrupted by try-now
                break

        self.backoff_seconds_remaining = 0.0
        self.is_in_backoff = False

    def interrupt_backoff(self) -> None:
        """Signal any in-progress backoff sleep to wake up immediately (Try-Now)."""
        self._backoff_event.set()

    def record_429(self) -> None:
        self.total_429_count += 1
        self.total_retries += 1

    def record_retry(self) -> None:
        self.total_retries += 1

    @property
    def hourly_quota_remaining(self) -> int:
        one_hour_ago = time.time() - 3600.0
        used = len([t for t in self.call_timestamps if t > one_hour_ago])
        return max(0, self.max_per_hour - used)

    def get_telemetry(self) -> Dict[str, Any]:
        return {
            "total_calls_session": self.total_calls_session,
            "calls_last_hour": self.calls_last_hour,
            "hourly_quota_remaining": self.hourly_quota_remaining,
            "hourly_limit": self.max_per_hour,
            "total_429_count": self.total_429_count,
            "total_retries": self.total_retries,
            "is_in_backoff": self.is_in_backoff,
            "backoff_seconds_remaining": round(self.backoff_seconds_remaining, 1),
        }


class CloudsocClient:
    """
    Client for Symantec CloudSOC (Elastica) Admin APIs.
    Replicates exact logic from Cloudsoc-v2.1.psm1.

    Enhancements (Phase 4):
    - Interruptible backoff via threading.Event
    - Live telemetry (total_calls, 429s, retries, quota_remaining)
    - On-the-fly re-authentication on 401/403 (reauthenticate())
    - AuthExpiredException raised on auth failure after re-auth attempt
    """
    def __init__(
        self,
        api_server: str,
        tenant: str,
        key_id: str,
        key_secret: str,
        rate_limit_per_second: float = 2.5,
        hourly_call_limit: int = 10000,
        max_retries: int = 5,
        base_backoff_seconds: int = 15,
        logger_func=None
    ):
        self.api_server = api_server.rstrip("/")
        self.tenant = tenant.strip("/")
        self.key_id = key_id
        self.key_secret = key_secret
        self.max_retries = max_retries
        self.base_backoff_seconds = base_backoff_seconds
        self.logger = logger_func or (lambda lvl, msg: None)
        self.rate_limiter = RateLimiter(rate_limit_per_second, hourly_call_limit)

        # Auth expiry tracking
        self._auth_failed_count: int = 0
        self._reauth_in_progress: bool = False

        # SSL context (Enterprise SSL inspection tolerant)
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

    def _get_local_ip(self) -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def get_headers(self) -> Dict[str, str]:
        local_ip = self._get_local_ip()
        user_agent = f"PowerShell/5.1 (Windows NT 10.0; Browser/Script; SourceLocation/{local_ip})"
        auth_header = generate_auth_header(self.key_id, self.key_secret)
        return {
            "Authorization": auth_header,
            "X-Elastica-Dbname-Resolved": "True",
            "User-Agent": user_agent,
            "tenant": self.tenant,
            "Content-Type": "application/json"
        }

    def reauthenticate(self, db_path: Optional[str] = None) -> bool:
        """
        Reload credentials from SQLite (or keep existing) and probe with a limit=1 call.
        Returns True if re-auth succeeded; raises AuthExpiredException on failure.
        """
        self._reauth_in_progress = True
        self.logger("WARNING", "Authentication expired — attempting re-authentication")

        if db_path:
            try:
                from app.config import load_config
                cfg = load_config(db_path)
                new_key_id = cfg.get("key_id", "")
                new_key_secret = cfg.get("key_secret", "")
                if new_key_id:
                    self.key_id = new_key_id
                if new_key_secret:
                    self.key_secret = new_key_secret
                self.logger("INFO", f"Re-auth credentials reloaded from DB for key_id={self.key_id[:8]}...")
            except Exception as e:
                self.logger("ERROR", f"Failed to reload credentials from DB: {str(e)}")

        try:
            # Probe with minimal call
            self.request("GET", "api/admin/v1/user_management/", params={"limit": 1}, _skip_reauth=True)
            self._auth_failed_count = 0
            self._reauth_in_progress = False
            self.logger("INFO", "Re-authentication successful")
            return True
        except AuthExpiredException:
            self._reauth_in_progress = False
            raise
        except Exception as e:
            self._reauth_in_progress = False
            self.logger("ERROR", f"Re-authentication probe failed: {str(e)}")
            raise AuthExpiredException(f"Re-authentication failed: {str(e)}")

    def interrupt_backoff(self) -> None:
        """Allow UI to skip current backoff wait (Try-Now button)."""
        self.rate_limiter.interrupt_backoff()

    def get_telemetry(self) -> Dict[str, Any]:
        return self.rate_limiter.get_telemetry()

    def request(
        self,
        method: str,
        endpoint_or_url: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        _skip_reauth: bool = False
    ) -> Dict[str, Any]:
        """
        Executes HTTP request with rate limiting and smart exponential backoff on 429 / 5xx.
        Handles 401/403 by triggering re-authentication (unless _skip_reauth=True to avoid loops).
        """
        if endpoint_or_url.startswith("http://") or endpoint_or_url.startswith("https://"):
            url = endpoint_or_url
        else:
            clean_endpoint = endpoint_or_url.lstrip("/")
            url = f"{self.api_server}/{self.tenant}/{clean_endpoint}"

        if params:
            query = urllib.parse.urlencode(params)
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{query}"

        data_bytes = json.dumps(json_data).encode("utf-8") if json_data is not None else None

        retry_count = 0
        while retry_count <= self.max_retries:
            self.rate_limiter.acquire()
            headers = self.get_headers()  # Regenerate each time (picks up re-authed credentials)
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method.upper())

            try:
                with urllib.request.urlopen(req, context=self.ssl_context, timeout=30) as resp:
                    status_code = resp.status
                    body = resp.read().decode("utf-8")
                    if status_code in (200, 201, 202, 204):
                        self._auth_failed_count = 0
                        if not body:
                            return {"status": status_code, "data": None}
                        try:
                            return json.loads(body)
                        except json.JSONDecodeError:
                            return {"status": status_code, "raw": body}
                    return {"status": status_code, "raw": body}

            except urllib.error.HTTPError as e:
                status_code = e.code
                error_body = ""
                try:
                    error_body = e.read().decode("utf-8")
                except Exception:
                    pass

                # Auth failure — signal re-auth needed
                if status_code in (401, 403) and not _skip_reauth:
                    self._auth_failed_count += 1
                    self.logger("WARNING", f"HTTP {status_code} received — authentication expired or unauthorized")
                    raise AuthExpiredException(f"HTTP {status_code}: Authentication expired. Re-auth required.")

                # Rate limit or server error — exponential backoff
                if status_code in (429, 500, 502, 503, 504) and retry_count < self.max_retries:
                    retry_count += 1
                    if status_code == 429:
                        self.rate_limiter.record_429()
                    else:
                        self.rate_limiter.record_retry()
                    wait_time = (2 ** (retry_count - 1)) * self.base_backoff_seconds
                    self.logger(
                        "WARNING",
                        f"API Threshold Hit (HTTP {status_code}). Backoff: Retrying in {wait_time}s "
                        f"(Attempt {retry_count} of {self.max_retries})"
                    )
                    self.rate_limiter.wait_backoff(wait_time)
                    continue

                self.logger("ERROR", f"CloudSOC HTTP {status_code}: {error_body or e.reason}")
                raise RuntimeError(f"CloudSOC API Error {status_code}: {error_body or e.reason}")

            except (urllib.error.URLError, socket.timeout, ConnectionError) as e:
                if retry_count < self.max_retries:
                    retry_count += 1
                    self.rate_limiter.record_retry()
                    wait_time = (2 ** (retry_count - 1)) * 5
                    self.logger("WARNING", f"Network error: {str(e)}. Retrying in {wait_time}s...")
                    self.rate_limiter.wait_backoff(wait_time)
                    continue
                raise

    def test_connection(self) -> Dict[str, Any]:
        """Test API connectivity and credentials using a minimal limit=1 query."""
        start_time = time.time()
        res = self.request("GET", "api/admin/v1/user_management/", params={"limit": 1})
        latency_ms = round((time.time() - start_time) * 1000, 1)

        meta = res.get("meta", {})
        total_count = meta.get("total_count", 0)
        return {
            "success": True,
            "latency_ms": latency_ms,
            "total_users_in_cloudsoc": total_count,
            "tenant": self.tenant,
            "server": self.api_server
        }

    def get_users_page(self, limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        """Fetches a single page of users."""
        return self.request("GET", "api/admin/v1/user_management/", params={"limit": limit, "offset": offset})

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """
        Replicates Resolve-CloudSOCUuidByEmail from Cloudsoc.psm1
        Searches exact email and returns UUID, CreatedBy, SecondaryUserId.
        """
        clean_email = email.strip().lower()
        res = self.request("GET", "api/admin/v1/user_management/", params={"email": clean_email})
        meta = res.get("meta", {})
        objects = res.get("objects", [])

        if meta.get("total_count", 0) > 0 and len(objects) > 0:
            for obj in objects:
                if obj.get("email", "").strip().lower() == clean_email:
                    res_uri = obj.get("resource_uri", "")
                    uuid = obj.get("id")
                    if not uuid and "/user_management/" in res_uri:
                        parts = res_uri.strip("/").split("/")
                        uuid = parts[-1]
                    return {
                        "uuid": uuid or obj.get("id"),
                        "email": obj.get("email"),
                        "created_by": obj.get("created_by") or "Unknown/None",
                        "secondary_user_id": obj.get("secondary_user_id") or "",
                        "is_active": obj.get("is_active", True),
                        "raw": obj
                    }
        return None

    def get_groups_page(self, limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        """Fetch CloudSOC groups."""
        return self.request("GET", "api/admin/v1/group_management/", params={"limit": limit, "offset": offset})

    def find_or_create_group(self, group_name: str) -> Optional[str]:
        """
        Returns the resource_uri of a named group, creating it if absent.
        Implements idempotency — only one API call if group exists.
        """
        res = self.request("GET", "api/admin/v1/group_management/", params={"name": group_name})
        objects = res.get("objects", [])
        for g in objects:
            if g.get("name", "").strip().lower() == group_name.strip().lower():
                return g.get("resource_uri")

        # Group not found — create it
        create_res = self.request("POST", "api/admin/v1/group_management/", json_data={"name": group_name})
        return create_res.get("resource_uri") or create_res.get("id")

    def delete_user(self, uuid: str) -> Dict[str, Any]:
        """DELETE user by UUID."""
        return self.request("DELETE", f"api/admin/v1/user_management/{uuid}/")

    def update_user(self, uuid: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """PATCH user by UUID."""
        return self.request("PATCH", f"api/admin/v1/user_management/{uuid}/", json_data=payload)

    def create_user(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST user."""
        return self.request("POST", "api/admin/v1/user_management/", json_data=payload)
