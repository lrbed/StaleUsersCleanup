import threading
import time
import csv
import json
import os
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

from app.database import (
    get_db_connection,
    upsert_user,
    update_user_ad_status,
    add_log,
    create_execution_run,
    update_execution_checkpoint,
    DEFAULT_DB_PATH
)
from app.config import load_config
from app.cloudsoc_client import CloudsocClient
from app.ldap_client import LDAPSyncEngine


def matches_criteria(user_obj: Dict[str, Any], criteria: Optional[Dict[str, Any]]) -> bool:
    """Evaluates whether a user object matches the targeted ingestion criteria."""
    if not criteria:
        return True

    match_mode = criteria.get("match_mode", "all").lower()
    checks = []

    # 1. Email domains
    email_domains = criteria.get("email_domains", [])
    if email_domains:
        email = user_obj.get("email", "").lower()
        matched = any(email.endswith(d.lower()) or f"@{d.lower().lstrip('@')}" in email for d in email_domains if d)
        checks.append(matched)

    # 2. NetBIOS domains (matches secondary_user_id like DOMAIN\user)
    netbios_domains = criteria.get("netbios_domains", [])
    if netbios_domains:
        sec_id = user_obj.get("secondary_user_id") or ""
        matched = False
        if "\\" in sec_id:
            domain_part = sec_id.split("\\")[0].strip().lower()
            matched = any(domain_part == nd.strip().lower() for nd in netbios_domains if nd)
        checks.append(matched)

    # 3. CloudSOC groups
    cloudsoc_groups = criteria.get("cloudsoc_groups", [])
    if cloudsoc_groups:
        raw_groups = user_obj.get("groups", [])
        if isinstance(raw_groups, str):
            try:
                raw_groups = json.loads(raw_groups)
            except Exception:
                raw_groups = []
        user_group_names = []
        if isinstance(raw_groups, list):
            for g in raw_groups:
                if isinstance(g, dict):
                    user_group_names.append(g.get("name", "").lower())
                elif isinstance(g, str):
                    user_group_names.append(g.lower())
        matched = any(any(cg.lower() in ug for ug in user_group_names) for cg in cloudsoc_groups if cg)
        checks.append(matched)

    # 4. Created by filter
    created_by_filter = criteria.get("created_by_filter", [])
    if created_by_filter:
        created_by = (user_obj.get("created_by") or "").lower()
        matched = any(cf.lower() in created_by for cf in created_by_filter if cf)
        checks.append(matched)

    # 5. Stale-Only Filter based on inactivity periods ONLY
    if criteria.get("stale_only"):
        threshold_days = int(criteria.get("inactivity_threshold_days", 90))
        include_blank = bool(criteria.get("include_blank_activity", True))
        last_activity = user_obj.get("last_activity_date") or user_obj.get("Last Activity Date") or ""
        is_stale = False

        if not str(last_activity).strip():
            is_stale = include_blank
        else:
            activity_str = str(last_activity).strip()
            parsed_dt = None
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    clean_str = activity_str.replace("T", " ").replace("Z", "").split(".")[0]
                    parsed_dt = datetime.strptime(clean_str, fmt.replace("T", " "))
                    break
                except Exception:
                    pass

            if parsed_dt:
                delta_days = (datetime.now(timezone.utc).replace(tzinfo=None) - parsed_dt).days
                if delta_days >= threshold_days:
                    is_stale = True
            else:
                is_stale = include_blank

        checks.append(is_stale)

    if not checks:
        return True

    if match_mode == "any":
        return any(checks)
    return all(checks)


class SyncService:
    """
    Background Synchronization Service for CloudSOC pagination loop and LDAP delta engine.
    """
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._cancel_flag = False
        self._pause_flag = False

        self.current_run_id: Optional[int] = None
        self.current_mode: str = "idle"  # idle, cloudsoc_import, ldap_sync, csv_import
        self.total_count: int = 0
        self.processed_count: int = 0
        self.success_count: int = 0
        self.failed_count: int = 0
        self.current_offset: int = 0
        self.current_message: str = "Ready"
        self.start_time: Optional[float] = None
        self.criteria: Optional[Dict[str, Any]] = None

    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            is_active = self._thread is not None and self._thread.is_alive()
            elapsed_sec = round(time.time() - self.start_time, 1) if (self.start_time and is_active) else 0.0
            percent = round((self.processed_count / self.total_count * 100), 1) if self.total_count > 0 else 0.0

            recent_logs = []
            try:
                with get_db_connection(self.db_path) as conn:
                    rows = conn.execute("""
                        SELECT id, timestamp, level, user_email, action, status, message
                        FROM logs
                        ORDER BY id DESC LIMIT 15
                    """).fetchall()
                    recent_logs = [dict(r) for r in rows]
            except Exception:
                pass

            return {
                "is_running": is_active,
                "mode": self.current_mode,
                "run_id": self.current_run_id,
                "is_paused": self._pause_flag,
                "is_cancelled": self._cancel_flag,
                "total_count": self.total_count,
                "processed_count": self.processed_count,
                "success_count": self.success_count,
                "failed_count": self.failed_count,
                "current_offset": self.current_offset,
                "percent_complete": percent,
                "elapsed_seconds": elapsed_sec,
                "message": self.current_message,
                "criteria": self.criteria,
                "recent_logs": recent_logs
            }

    def cancel(self) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                self._cancel_flag = True
                self.current_message = "Cancelling..."
                return True
            return False

    def pause(self) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                self._pause_flag = True
                self.current_message = "Paused"
                return True
            return False

    def resume(self) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive() and self._pause_flag:
                self._pause_flag = False
                self.current_message = "Resuming..."
                return True
            return False

    # -------------------------------------------------------------
    # CLOUDSOC IMPORT WORKER (With Targeted Ingestion)
    # -------------------------------------------------------------
    def start_cloudsoc_sync(self, batch_size: int = 100, criteria: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return {"success": False, "error": "A synchronization task is already in progress"}

            self._cancel_flag = False
            self._pause_flag = False
            self.current_mode = "cloudsoc_import"
            self.processed_count = 0
            self.success_count = 0
            self.failed_count = 0
            self.current_offset = 0
            self.start_time = time.time()
            self.criteria = criteria
            self.current_message = "Initializing CloudSOC Sync..."

            cfg = load_config(self.db_path)
            config_snapshot = dict(cfg)
            if criteria:
                config_snapshot["criteria"] = criteria

            self.current_run_id = create_execution_run(
                run_mode="cloudsoc_import",
                config_snapshot=config_snapshot,
                db_path=self.db_path
            )

            self._thread = threading.Thread(
                target=self._run_cloudsoc_sync,
                args=(cfg, batch_size, criteria),
                daemon=True
            )
            self._thread.start()
            return {"success": True, "run_id": self.current_run_id}

    def _run_cloudsoc_sync(self, cfg: Dict[str, Any], batch_size: int, criteria: Optional[Dict[str, Any]]):
        run_id = self.current_run_id
        criteria_msg = f" with criteria: {json.dumps(criteria)}" if criteria else " (full download)"
        add_log("INFO", f"Starting CloudSOC user sync{criteria_msg}", run_id=run_id, db_path=self.db_path)

        def log_cb(lvl, msg):
            add_log(lvl, msg, run_id=run_id, db_path=self.db_path)

        count_limit = None
        if criteria and criteria.get("count_limit"):
            try:
                count_limit = int(criteria["count_limit"])
            except Exception:
                count_limit = None

        try:
            from app.cloudsoc_client import get_global_rate_limiter
            shared_limiter = get_global_rate_limiter(
                max_per_second=float(cfg.get("rate_limit_per_second", 2.5)),
                max_per_hour=int(cfg.get("hourly_call_limit", 10000)),
                db_path=self.db_path
            )
            client = CloudsocClient(
                api_server=cfg["api_server"],
                tenant=cfg["tenant"],
                key_id=cfg["key_id"],
                key_secret=cfg["key_secret"],
                rate_limit_per_second=float(cfg.get("rate_limit_per_second", 2.5)),
                hourly_call_limit=int(cfg.get("hourly_call_limit", 10000)),
                max_retries=int(cfg.get("max_retries", 5)),
                base_backoff_seconds=int(cfg.get("base_backoff_seconds", 15)),
                logger_func=log_cb,
                rate_limiter=shared_limiter,
                db_path=self.db_path
            )

            # Initial call to determine total_count
            initial_page = client.get_users_page(limit=batch_size, offset=0)
            meta = initial_page.get("meta", {})
            self.total_count = meta.get("total_count", 0)

            add_log("INFO", f"CloudSOC reports {self.total_count} total users in tenant", run_id=run_id, db_path=self.db_path)

            offset = 0
            while offset < self.total_count or (offset == 0 and self.total_count == 0):
                # Check cancellation
                if self._cancel_flag:
                    add_log("WARNING", "Sync cancelled by administrator", run_id=run_id, db_path=self.db_path)
                    update_execution_checkpoint(run_id, self.processed_count, self.success_count, self.failed_count, status="interrupted", db_path=self.db_path)
                    self.current_message = "Cancelled"
                    return

                # Check pause
                while self._pause_flag:
                    time.sleep(1.0)
                    if self._cancel_flag:
                        break

                page_data = client.get_users_page(limit=batch_size, offset=offset) if offset > 0 else initial_page
                objects = page_data.get("objects", [])

                if not objects:
                    break

                for user_obj in objects:
                    if self._cancel_flag:
                        break

                    # Check targeted ingestion criteria
                    if criteria and not matches_criteria(user_obj, criteria):
                        self.processed_count += 1
                        continue

                    try:
                        if not user_obj.get("id"):
                            res_uri = user_obj.get("resource_uri", "")
                            if "/user_management/" in res_uri:
                                user_obj["id"] = res_uri.strip("/").split("/")[-1]

                        upsert_user(user_obj, db_path=self.db_path)
                        self.success_count += 1
                    except Exception as ex:
                        self.failed_count += 1
                        add_log("ERROR", f"Failed to save user {user_obj.get('email')}: {str(ex)}", run_id=run_id, user_email=user_obj.get("email"), db_path=self.db_path)

                    self.processed_count += 1

                    # Count limit reached check
                    if count_limit and self.success_count >= count_limit:
                        add_log("INFO", f"Target count limit reached ({count_limit} users imported)", run_id=run_id, db_path=self.db_path)
                        break

                offset += len(objects)
                self.current_offset = offset
                self.current_message = f"Evaluated {self.processed_count} users ({self.success_count} matched criteria, offset {offset})"

                # Checkpoint in DB
                update_execution_checkpoint(
                    run_id,
                    processed_count=self.processed_count,
                    success_count=self.success_count,
                    failed_count=self.failed_count,
                    status="running",
                    db_path=self.db_path
                )

                if count_limit and self.success_count >= count_limit:
                    break

                # Tastypie pagination check
                next_page = page_data.get("meta", {}).get("next")
                if not next_page:
                    break

            status_str = "completed" if not self._cancel_flag else "interrupted"
            update_execution_checkpoint(
                run_id,
                processed_count=self.processed_count,
                success_count=self.success_count,
                failed_count=self.failed_count,
                status=status_str,
                db_path=self.db_path
            )
            self.current_message = f"CloudSOC Sync {status_str}: {self.success_count} users imported"
            add_log("INFO", self.current_message, run_id=run_id, db_path=self.db_path)

        except Exception as e:
            self.current_message = f"Sync failed: {str(e)}"
            add_log("ERROR", f"CloudSOC pagination crashed: {str(e)}", run_id=run_id, db_path=self.db_path)
            update_execution_checkpoint(run_id, self.processed_count, self.success_count, self.failed_count, status="failed", db_path=self.db_path)

        finally:
            self.current_mode = "idle"

    # -------------------------------------------------------------
    # LDAP SYNC WORKER
    # -------------------------------------------------------------
    def start_ldap_sync(
        self,
        recheck_all: bool = False,
        matching_attribute: str = "mail_or_upn",
        custom_attribute: Optional[str] = None
    ) -> Dict[str, Any]:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return {"success": False, "error": "A synchronization task is already in progress"}

            self._cancel_flag = False
            self._pause_flag = False
            self.current_mode = "ldap_sync"
            self.processed_count = 0
            self.success_count = 0
            self.failed_count = 0
            self.start_time = time.time()
            self.current_message = "Initializing LDAP Correlation Engine..."

            cfg = load_config(self.db_path)
            config_snapshot = dict(cfg)
            config_snapshot["correlation_params"] = {
                "recheck_all": recheck_all,
                "matching_attribute": matching_attribute,
                "custom_attribute": custom_attribute
            }

            self.current_run_id = create_execution_run(
                run_mode="ldap_sync",
                config_snapshot=config_snapshot,
                db_path=self.db_path
            )

            self._thread = threading.Thread(
                target=self._run_ldap_sync,
                args=(cfg, recheck_all, matching_attribute, custom_attribute),
                daemon=True
            )
            self._thread.start()
            return {"success": True, "run_id": self.current_run_id}

    def _run_ldap_sync(
        self,
        cfg: Dict[str, Any],
        recheck_all: bool,
        matching_attribute: str = "mail_or_upn",
        custom_attribute: Optional[str] = None
    ):
        run_id = self.current_run_id
        attr_label = matching_attribute if matching_attribute != "custom" else f"custom:{custom_attribute}"
        add_log("INFO", f"Starting Active Directory (LDAP) Safety Check loop (Matching on: {attr_label})", run_id=run_id, db_path=self.db_path)

        def log_cb(lvl, msg):
            add_log(lvl, msg, run_id=run_id, db_path=self.db_path)

        try:
            ldap_engine = LDAPSyncEngine(
                host=cfg.get("ad_host", ""),
                manual_host=cfg.get("ad_host", ""),
                manual_port=int(cfg.get("ad_port", 389)),
                manual_base_dn=cfg.get("ad_base_dn", ""),
                manual_use_ssl=bool(cfg.get("ad_use_ssl", False)),
                manual_mode=bool(cfg.get("ad_manual_mode", True)),
                forests=cfg.get("ad_forests", []),
                username=cfg.get("ad_username", ""),
                password=cfg.get("ad_password", ""),
                use_ssl=bool(cfg.get("ad_use_ssl", False)),
                default_port=int(cfg.get("ad_port", 389)),
                extra_attributes=cfg.get("extra_ad_attributes"),
                logger_func=log_cb
            )

            # Query candidate users from SQLite including secondary_user_id
            with get_db_connection(self.db_path) as conn:
                if recheck_all:
                    users = conn.execute("SELECT email, secondary_user_id, cloudsoc_id FROM users").fetchall()
                else:
                    users = conn.execute("SELECT email, secondary_user_id, cloudsoc_id FROM users WHERE ad_status IN ('Unchecked', 'Error')").fetchall()

            self.total_count = len(users)
            add_log("INFO", f"Checking {self.total_count} candidate users against AD (Attribute: {attr_label})", run_id=run_id, db_path=self.db_path)

            for u in users:
                if self._cancel_flag:
                    add_log("WARNING", "LDAP sync cancelled by administrator", run_id=run_id, db_path=self.db_path)
                    update_execution_checkpoint(run_id, self.processed_count, self.success_count, self.failed_count, status="interrupted", db_path=self.db_path)
                    self.current_message = "Cancelled"
                    return

                while self._pause_flag:
                    time.sleep(1.0)
                    if self._cancel_flag:
                        break

                email = u["email"]
                sec_id = u["secondary_user_id"] if "secondary_user_id" in u.keys() else None
                ad_res = ldap_engine.get_ad_user_status(
                    email=email,
                    secondary_user_id=sec_id,
                    matching_attribute=matching_attribute,
                    custom_attribute_name=custom_attribute
                )

                status = ad_res.get("status", "Error")
                forest = ad_res.get("forest", "-")
                details = ad_res.get("details", "")

                update_user_ad_status(
                    email=email,
                    ad_status=status,
                    ad_forest=forest,
                    ad_details=details,
                    db_path=self.db_path
                )

                if status == "Error":
                    self.failed_count += 1
                else:
                    self.success_count += 1

                self.processed_count += 1

                if self.processed_count % 25 == 0:
                    update_execution_checkpoint(
                        run_id,
                        processed_count=self.processed_count,
                        success_count=self.success_count,
                        failed_count=self.failed_count,
                        status="running",
                        db_path=self.db_path
                    )
                    self.current_message = f"Checked {self.processed_count}/{self.total_count} users (AD Status: {status})"

            status_str = "completed" if not self._cancel_flag else "interrupted"
            update_execution_checkpoint(
                run_id,
                processed_count=self.processed_count,
                success_count=self.success_count,
                failed_count=self.failed_count,
                status=status_str,
                db_path=self.db_path
            )
            self.current_message = f"LDAP Sync {status_str}: {self.processed_count} users evaluated"
            add_log("INFO", self.current_message, run_id=run_id, db_path=self.db_path)

        except Exception as e:
            self.current_message = f"LDAP Sync failed: {str(e)}"
            add_log("ERROR", f"LDAP loop crashed: {str(e)}", run_id=run_id, db_path=self.db_path)
            update_execution_checkpoint(run_id, self.processed_count, self.success_count, self.failed_count, status="failed", db_path=self.db_path)

        finally:
            self.current_mode = "idle"

    # -------------------------------------------------------------
    # CSV IMPORT WORKER (Supports Raw Users & Staged Cleanup Plans)
    # -------------------------------------------------------------
    def import_csv_file(self, file_path: str, import_mode: str = "raw_users") -> Dict[str, Any]:
        """
        Import user data directly from CSV.
        import_mode:
          - 'raw_users': Standard CloudSOC export or HR spreadsheet.
          - 'cleanup_plan': Restores previously saved cleanup plan with actions and AD status.
        """
        if not os.path.isfile(file_path):
            return {"success": False, "error": f"File not found: {file_path}"}

        run_id = create_execution_run(run_mode="csv_import", config_snapshot={"source_file": file_path, "import_mode": import_mode}, db_path=self.db_path)
        add_log("INFO", f"Importing users from CSV ({import_mode}): {file_path}", run_id=run_id, db_path=self.db_path)

        imported = 0
        errors = 0

        with open(file_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []

            # Resolve email column
            email_col = None
            for h in headers:
                clean_h = " ".join(h.split()).strip().lower()
                if clean_h in ("email", "userid", "user id", "email address"):
                    email_col = h
                    break

            if not email_col:
                msg = f"No valid Email column found in CSV. Headers: {headers}"
                add_log("ERROR", msg, run_id=run_id, db_path=self.db_path)
                update_execution_checkpoint(run_id, 0, 0, 0, status="failed", db_path=self.db_path)
                return {"success": False, "error": msg}

            for row in reader:
                email = row.get(email_col, "").strip()
                if not email:
                    continue

                user_dict = {
                    "email": email,
                    "first_name": row.get("First Name") or row.get("first_name", ""),
                    "last_name": row.get("Last Name") or row.get("last_name", ""),
                    "secondary_user_id": row.get("Secondary ID") or row.get("secondary_user_id", ""),
                    "created_by": row.get("Created By") or row.get("created_by", ""),
                    "created_on": row.get("Creation Date") or row.get("created_on", ""),
                    "modified_by": row.get("Modified By") or row.get("modified_by", ""),
                    "modified_on": row.get("Modified Date") or row.get("modified_on", ""),
                    "title": row.get("Title") or row.get("title", ""),
                    "groups": row.get("Groups") or row.get("groups", "[]"),
                    "last_activity_date": row.get("Last Activity Date") or row.get("last_activity_date", ""),
                    "work_phone": row.get("Work Phone") or row.get("work_phone", ""),
                    "cell_phone": row.get("Cell Phone") or row.get("cell_phone", ""),
                    "Active": row.get("Active") or row.get("is_active", "Yes"),
                    "cloudsoc_id": row.get("cloudsoc_id") or row.get("CloudSOC ID", ""),
                    "ad_status": row.get("ad_status") or row.get("AD Status", "Unchecked"),
                    "selected_action": (row.get("selected_action") or row.get("Planned Action", "none")).lower() if import_mode == "cleanup_plan" else "none",
                    "action_status": "pending" if (import_mode == "cleanup_plan" and (row.get("selected_action") or row.get("Planned Action", "none")).lower() != "none") else "none",
                    "raw_json": json.dumps(row)
                }

                try:
                    upsert_user(user_dict, db_path=self.db_path)
                    imported += 1
                except Exception as ex:
                    errors += 1
                    add_log("ERROR", f"Error importing row for {email}: {str(ex)}", run_id=run_id, user_email=email, db_path=self.db_path)

        update_execution_checkpoint(run_id, processed_count=imported, success_count=imported, failed_count=errors, status="completed", db_path=self.db_path)
        add_log("INFO", f"CSV Import complete. {imported} users imported, {errors} errors.", run_id=run_id, db_path=self.db_path)
        return {"success": True, "imported": imported, "errors": errors}

    # -------------------------------------------------------------
    # ACTIVE DIRECTORY INGESTION WORKER
    # -------------------------------------------------------------
    def start_ad_ingest(
        self,
        ldap_filter: Optional[str] = None,
        base_dn: Optional[str] = None,
        count_limit: int = 0
    ) -> Dict[str, Any]:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return {"success": False, "error": "A synchronization task is already in progress"}

            self._cancel_flag = False
            self._pause_flag = False
            self.current_mode = "ad_ingest"
            self.processed_count = 0
            self.success_count = 0
            self.failed_count = 0
            self.total_count = 0
            self.start_time = time.time()
            self.current_message = "Initializing Active Directory Ingestion..."

            cfg = load_config(self.db_path)
            config_snapshot = dict(cfg)
            config_snapshot["ad_ingest_params"] = {
                "ldap_filter": ldap_filter,
                "base_dn": base_dn,
                "count_limit": count_limit
            }

            self.current_run_id = create_execution_run(
                run_mode="ad_ingest",
                config_snapshot=config_snapshot,
                db_path=self.db_path
            )

            self._thread = threading.Thread(
                target=self._run_ad_ingest,
                args=(cfg, ldap_filter, base_dn, count_limit),
                daemon=True
            )
            self._thread.start()
            return {"success": True, "run_id": self.current_run_id}

    def _run_ad_ingest(
        self,
        cfg: Dict[str, Any],
        ldap_filter: Optional[str],
        base_dn: Optional[str],
        count_limit: int
    ):
        run_id = self.current_run_id
        effective_filter = (ldap_filter or "").strip() or "(&(objectCategory=person)(objectClass=user))"
        effective_base = (base_dn or "").strip() or cfg.get("ad_base_dn", "")

        add_log("INFO", f"Starting Active Directory Ingestion (Filter: {effective_filter}, Base DN: {effective_base or 'Default'}, Limit: {count_limit or 'None'})", run_id=run_id, db_path=self.db_path)

        def log_cb(lvl, msg):
            add_log(lvl, msg, run_id=run_id, db_path=self.db_path)

        try:
            ldap_engine = LDAPSyncEngine(
                manual_host=cfg.get("ad_host", ""),
                manual_port=int(cfg.get("ad_port", 389)),
                username=cfg.get("ad_username", ""),
                password=cfg.get("ad_password", ""),
                manual_base_dn=cfg.get("ad_base_dn", ""),
                manual_use_ssl=bool(cfg.get("ad_use_ssl", False)),
                forests=cfg.get("ad_forests", []),
                logger_func=log_cb
            )

            self.current_message = "Querying Active Directory via LDAP..."
            ad_users = ldap_engine.query_ad_users(
                base_dn=effective_base,
                ldap_filter=effective_filter,
                size_limit=count_limit
            )

            self.total_count = len(ad_users)
            add_log("INFO", f"Discovered {self.total_count} matching accounts from Active Directory", run_id=run_id, db_path=self.db_path)

            for u in ad_users:
                if self._cancel_flag:
                    add_log("WARNING", "Active Directory Ingestion cancelled by administrator", run_id=run_id, db_path=self.db_path)
                    update_execution_checkpoint(run_id, self.processed_count, self.success_count, self.failed_count, status="interrupted", db_path=self.db_path)
                    self.current_message = "Cancelled"
                    return

                while self._pause_flag:
                    time.sleep(1.0)
                    if self._cancel_flag:
                        break

                email = u.get("email")
                if not email:
                    continue

                user_dict = {
                    "email": email,
                    "name": u.get("name", email),
                    "secondary_user_id": u.get("sam_account_name", ""),
                    "ad_status": u.get("ad_status", "Unchecked"),
                    "ad_checked_at": datetime.now(timezone.utc).isoformat(),
                    "source": "ad_ingest",
                    "raw_json": json.dumps(u)
                }

                try:
                    upsert_user(user_dict, db_path=self.db_path)
                    self.success_count += 1
                except Exception as ex:
                    self.failed_count += 1
                    add_log("ERROR", f"Failed to ingest AD user {email}: {str(ex)}", run_id=run_id, user_email=email, db_path=self.db_path)

                self.processed_count += 1
                self.current_message = f"Ingested {self.processed_count}/{self.total_count}: {email}"

                if self.processed_count % 50 == 0:
                    update_execution_checkpoint(run_id, self.processed_count, self.success_count, self.failed_count, status="running", db_path=self.db_path)

            update_execution_checkpoint(run_id, self.processed_count, self.success_count, self.failed_count, status="completed", db_path=self.db_path)
            self.current_message = f"Ingestion Complete. Discovered: {self.total_count}, Ingested: {self.success_count}, Errors: {self.failed_count}"
            add_log("INFO", self.current_message, run_id=run_id, db_path=self.db_path)

        except Exception as e:
            err = str(e)
            self.failed_count += 1
            self.current_message = f"Fatal Error: {err}"
            add_log("ERROR", f"Active Directory Ingestion failed: {err}", run_id=run_id, db_path=self.db_path)
            update_execution_checkpoint(run_id, self.processed_count, self.success_count, self.failed_count, status="failed", db_path=self.db_path)
