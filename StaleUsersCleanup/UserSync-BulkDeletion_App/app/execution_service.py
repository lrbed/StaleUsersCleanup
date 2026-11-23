import threading
import time
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from app.database import (
    get_db_connection,
    add_log,
    create_execution_run,
    update_execution_checkpoint,
    DEFAULT_DB_PATH
)
from app.config import load_config
from app.cloudsoc_client import CloudsocClient, AuthExpiredException


class ExecutionService:
    """
    Phase 4 Core Execution Engine.
    State Machine: idle -> running <-> paused -> completed / stopped / failed / awaiting_reauth.
    Enforces rate limits, per-user checkpointing, role safety guards, AD safety locks,
    dry-run execution, idempotency, and undo operations.
    """
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._pause_event = threading.Event()
        self._pause_event.set()  # set means NOT paused
        self._stop_flag = False

        self.current_state: str = "idle"  # idle, running, paused, stopped, awaiting_reauth
        self.current_run_id: Optional[int] = None
        self.run_mode: str = "dry_run"  # dry_run or live
        self.total_candidates: int = 0
        self.processed_count: int = 0
        self.success_count: int = 0
        self.failed_count: int = 0
        self.skipped_count: int = 0
        self.current_user_email: str = ""
        self.current_message: str = "Idle"
        self.start_time: Optional[float] = None
        self.client: Optional[CloudsocClient] = None

        # Cached group URI for move_group optimization (economy of API calls)
        self._cached_group_uri: Optional[str] = None
        self._cached_group_name: Optional[str] = None

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            elapsed = round(time.time() - self.start_time, 1) if (self.start_time and self.current_state in ("running", "paused", "awaiting_reauth")) else 0.0
            percent = round((self.processed_count / self.total_candidates * 100), 1) if self.total_candidates > 0 else 0.0

            telemetry = {}
            if self.client:
                telemetry = self.client.get_telemetry()

            # Retrieve recent logs for this execution run
            recent_logs = []
            try:
                with get_db_connection(self.db_path) as conn:
                    if self.current_run_id:
                        rows = conn.execute("""
                            SELECT id, timestamp, level, user_email, action, status, message
                            FROM logs WHERE run_id = ?
                            ORDER BY id DESC LIMIT 15
                        """, (self.current_run_id,)).fetchall()
                    else:
                        rows = conn.execute("""
                            SELECT id, timestamp, level, user_email, action, status, message
                            FROM logs ORDER BY id DESC LIMIT 15
                        """).fetchall()
                    recent_logs = [dict(r) for r in rows]
            except Exception:
                pass

            return {
                "state": self.current_state,
                "run_id": self.current_run_id,
                "run_mode": self.run_mode,
                "total_candidates": self.total_candidates,
                "processed_count": self.processed_count,
                "success_count": self.success_count,
                "failed_count": self.failed_count,
                "skipped_count": self.skipped_count,
                "percent_complete": percent,
                "elapsed_seconds": elapsed,
                "current_user": self.current_user_email,
                "message": self.current_message,
                "telemetry": telemetry,
                "recent_logs": recent_logs
            }

    def start_execution(self, mode: str = "dry_run") -> Dict[str, Any]:
        with self._lock:
            if self.current_state in ("running", "paused", "awaiting_reauth"):
                return {"success": False, "error": f"Execution already active in state '{self.current_state}'"}

            self.run_mode = mode if mode in ("dry_run", "live") else "dry_run"
            self._stop_flag = False
            self._pause_event.set()
            self.processed_count = 0
            self.success_count = 0
            self.failed_count = 0
            self.skipped_count = 0
            self.start_time = time.time()
            self.current_state = "running"
            self.current_message = f"Starting {self.run_mode.upper()} execution..."

            with get_db_connection(self.db_path) as conn:
                count_row = conn.execute("""
                    SELECT COUNT(*) as c FROM users
                    WHERE selected_action != 'none' AND action_status = 'pending'
                """).fetchone()
                self.total_candidates = count_row["c"] if count_row else 0

            cfg = load_config(self.db_path)
            self.current_run_id = create_execution_run(
                run_mode=self.run_mode,
                total_candidates=self.total_candidates,
                config_snapshot=cfg,
                db_path=self.db_path
            )

            # Initialize client
            def log_cb(lvl, msg):
                add_log(lvl, msg, run_id=self.current_run_id, db_path=self.db_path)

            from app.cloudsoc_client import get_global_rate_limiter
            shared_limiter = get_global_rate_limiter(
                max_per_second=float(cfg.get("rate_limit_per_second", 2.5)),
                max_per_hour=int(cfg.get("hourly_call_limit", 10000)),
                db_path=self.db_path
            )

            self.client = CloudsocClient(
                api_server=cfg["api_server"],
                tenant=cfg["tenant"],
                key_id=cfg["key_id"],
                key_secret=cfg.get("key_secret", ""),
                rate_limit_per_second=float(cfg.get("rate_limit_per_second", 2.5)),
                hourly_call_limit=int(cfg.get("hourly_call_limit", 10000)),
                max_retries=int(cfg.get("max_retries", 5)),
                base_backoff_seconds=int(cfg.get("base_backoff_seconds", 15)),
                logger_func=log_cb,
                rate_limiter=shared_limiter,
                db_path=self.db_path
            )

            self._thread = threading.Thread(
                target=self._worker_loop,
                args=(cfg,),
                daemon=True
            )
            self._thread.start()
            return {"success": True, "run_id": self.current_run_id, "mode": self.run_mode}

    def resume_interrupted_run(self, run_id: int) -> Dict[str, Any]:
        """Resumes a historical run that was interrupted by crash/restart."""
        with self._lock:
            if self.current_state in ("running", "paused", "awaiting_reauth"):
                return {"success": False, "error": f"Execution already active in state '{self.current_state}'"}

            with get_db_connection(self.db_path) as conn:
                run = conn.execute("SELECT * FROM execution_runs WHERE id = ?", (run_id,)).fetchone()
                if not run:
                    return {"success": False, "error": f"Run #{run_id} not found"}

                count_row = conn.execute("""
                    SELECT COUNT(*) as c FROM users
                    WHERE selected_action != 'none' AND action_status = 'pending'
                """).fetchone()
                self.total_candidates = count_row["c"] if count_row else 0

                conn.execute("UPDATE execution_runs SET status = 'running', last_checkpoint_at = datetime('now') WHERE id = ?", (run_id,))
                conn.commit()

            self.current_run_id = run_id
            self.run_mode = run["run_mode"] if run["run_mode"] in ("dry_run", "live") else "dry_run"
            self._stop_flag = False
            self._pause_event.set()
            self.processed_count = run["processed_count"] or 0
            self.success_count = run["success_count"] or 0
            self.failed_count = run["failed_count"] or 0
            self.skipped_count = run["skipped_count"] or 0
            self.start_time = time.time()
            self.current_state = "running"
            self.current_message = f"Resumed {self.run_mode.upper()} Run #{run_id} from checkpoint..."

            cfg = load_config(self.db_path)
            def log_cb(lvl, msg):
                add_log(lvl, msg, run_id=self.current_run_id, db_path=self.db_path)

            from app.cloudsoc_client import get_global_rate_limiter
            shared_limiter = get_global_rate_limiter(
                max_per_second=float(cfg.get("rate_limit_per_second", 2.5)),
                max_per_hour=int(cfg.get("hourly_call_limit", 10000)),
                db_path=self.db_path
            )

            self.client = CloudsocClient(
                api_server=cfg["api_server"],
                tenant=cfg["tenant"],
                key_id=cfg["key_id"],
                key_secret=cfg.get("key_secret", ""),
                rate_limit_per_second=float(cfg.get("rate_limit_per_second", 2.5)),
                hourly_call_limit=int(cfg.get("hourly_call_limit", 10000)),
                max_retries=int(cfg.get("max_retries", 5)),
                base_backoff_seconds=int(cfg.get("base_backoff_seconds", 15)),
                logger_func=log_cb,
                rate_limiter=shared_limiter,
                db_path=self.db_path
            )

            self._thread = threading.Thread(
                target=self._worker_loop,
                args=(cfg,),
                daemon=True
            )
            self._thread.start()
            return {"success": True, "run_id": self.current_run_id, "mode": self.run_mode}

    def pause(self) -> bool:
        with self._lock:
            if self.current_state == "running":
                self._pause_event.clear()
                self.current_state = "paused"
                self.current_message = "Execution Paused by Administrator"
                add_log("WARNING", "Execution paused by admin", run_id=self.current_run_id, db_path=self.db_path)
                return True
            return False

    def resume(self) -> bool:
        with self._lock:
            if self.current_state in ("paused", "awaiting_reauth"):
                self._pause_event.set()
                self.current_state = "running"
                self.current_message = "Resuming execution..."
                add_log("INFO", "Execution resumed by admin", run_id=self.current_run_id, db_path=self.db_path)
                return True
            return False

    def stop(self) -> bool:
        with self._lock:
            if self.current_state in ("running", "paused", "awaiting_reauth"):
                self._stop_flag = True
                self._pause_event.set()  # Unblock if paused so thread can exit
                self.current_state = "stopped"
                self.current_message = "Stopping execution..."
                add_log("WARNING", "Execution stopped by admin", run_id=self.current_run_id, db_path=self.db_path)
                return True
            return False

    def retry_failed(self) -> Dict[str, Any]:
        """Reset failed users back to pending and start execution."""
        with self._lock:
            if self.current_state in ("running", "paused"):
                return {"success": False, "error": "Execution is currently in progress"}

        with get_db_connection(self.db_path) as conn:
            cursor = conn.execute("""
                UPDATE users
                SET action_status = 'pending', action_result = NULL
                WHERE action_status = 'failed' AND selected_action != 'none'
            """)
            conn.commit()
            count = cursor.rowcount

        add_log("INFO", f"Reset {count} failed user actions to 'pending' for retry", db_path=self.db_path)
        return {"success": True, "reset_count": count}

    def override_backoff(self) -> bool:
        """User clicks 'Try Now' to bypass backoff sleep."""
        if self.client:
            self.client.interrupt_backoff()
            add_log("INFO", "Administrator triggered 'Try Now' (backoff overridden)", run_id=self.current_run_id, db_path=self.db_path)
            return True
        return False

    def handle_reauth_complete(self) -> bool:
        """Called when admin updates credentials or clicks re-auth confirmation."""
        with self._lock:
            if self.current_state == "awaiting_reauth":
                cfg = load_config(self.db_path)
                if self.client:
                    self.client.key_id = cfg.get("key_id", "")
                    self.client.key_secret = cfg.get("key_secret", "")
                self._pause_event.set()
                self.current_state = "running"
                self.current_message = "Re-authentication accepted. Resuming execution..."
                add_log("INFO", "Re-authentication validated, queue resumed", run_id=self.current_run_id, db_path=self.db_path)
                return True
        return False

    # -----------------------------------------------------------------
    # CORE WORKER LOOP
    # -----------------------------------------------------------------
    def _worker_loop(self, cfg: Dict[str, Any]):
        run_id = self.current_run_id
        is_dry_run = (self.run_mode == "dry_run")
        mode_label = "DRY-RUN" if is_dry_run else "LIVE"

        add_log("INFO", f"Execution run #{run_id} started in {mode_label} mode", run_id=run_id, db_path=self.db_path)

        # 1. Fetch pending candidate users
        with get_db_connection(self.db_path) as conn:
            candidate_rows = conn.execute("""
                SELECT * FROM users
                WHERE selected_action != 'none' AND action_status = 'pending'
                ORDER BY id ASC
            """).fetchall()
            candidates = [dict(r) for r in candidate_rows]

        self.total_candidates = len(candidates)
        add_log("INFO", f"Found {self.total_candidates} pending candidate users to process", run_id=run_id, db_path=self.db_path)

        if self.total_candidates == 0:
            self.current_state = "idle"
            self.current_message = "No pending actions found in queue."
            update_execution_checkpoint(run_id, 0, 0, 0, 0, status="completed", db_path=self.db_path)
            return

        # Prepare role guard configuration
        role_guard_enabled = bool(cfg.get("role_guard_enabled", True))
        raw_roles = cfg.get("protected_roles", [])
        if isinstance(raw_roles, str):
            protected_roles = [r.strip().lower() for r in raw_roles.split(",") if r.strip()]
        else:
            protected_roles = [str(r).strip().lower() for r in raw_roles]

        raw_groups = cfg.get("protected_groups", [])
        if isinstance(raw_groups, str):
            protected_groups = [g.strip().lower() for g in raw_groups.split(",") if g.strip()]
        else:
            protected_groups = [str(g).strip().lower() for g in raw_groups]

        ad_safety_lock = bool(cfg.get("ad_safety_lock", True))
        target_move_group = cfg.get("target_move_group", "Pending_Deletion")

        for user in candidates:
            # Check stop condition
            if self._stop_flag:
                self.current_state = "stopped"
                self.current_message = f"Execution stopped after {self.processed_count} users."
                update_execution_checkpoint(run_id, self.processed_count, self.success_count, self.failed_count, self.skipped_count, status="interrupted", db_path=self.db_path)
                return

            # Check pause condition
            while not self._pause_event.is_set():
                time.sleep(0.5)
                if self._stop_flag:
                    self.current_state = "stopped"
                    update_execution_checkpoint(run_id, self.processed_count, self.success_count, self.failed_count, self.skipped_count, status="interrupted", db_path=self.db_path)
                    return

            user_id = user["id"]
            email = user.get("email", "")
            action = user.get("selected_action", "none").lower()
            uuid = user.get("cloudsoc_id")
            self.current_user_email = email
            self.current_message = f"Processing [{action}]: {email}"

            # ---------------------------------------------------------
            # SAFETY GUARD 1: Privileged / Protected Role & Group Guard
            # ---------------------------------------------------------
            if role_guard_enabled:
                user_role = str(user.get("role", "")).strip().lower()
                is_priv = bool(user.get("is_privileged", 0))

                # Check role
                role_matched = is_priv or any(pr in user_role for pr in protected_roles if pr)

                # Check groups
                group_matched = False
                user_groups_raw = user.get("groups", "[]")
                try:
                    user_groups = json.loads(user_groups_raw) if isinstance(user_groups_raw, str) else user_groups_raw
                    if isinstance(user_groups, list):
                        for g in user_groups:
                            g_name = str(g.get("name") if isinstance(g, dict) else g).strip().lower()
                            if any(pg in g_name for pg in protected_groups if pg):
                                group_matched = True
                                break
                except Exception:
                    pass

                if role_matched or group_matched:
                    reason = f"Role Guard Skipped: {'Protected Role (' + user_role + ')' if role_matched else ''} {'Protected Group' if group_matched else ''}".strip()
                    self._record_user_result(user_id, email, action, "skipped", reason, run_id)
                    self.skipped_count += 1
                    self.processed_count += 1
                    continue

            # ---------------------------------------------------------
            # SAFETY GUARD 2: Active Directory Safety Lock (Fail-Closed)
            # ---------------------------------------------------------
            if ad_safety_lock:
                ad_status = str(user.get("ad_status", "Unchecked")).strip()
                if ad_status == "Active":
                    reason = "AD Safety Lock: User is Active in Active Directory. Skipped."
                    self._record_user_result(user_id, email, action, "skipped", reason, run_id)
                    self.skipped_count += 1
                    self.processed_count += 1
                    continue
                elif ad_status == "Error":
                    reason = "AD Safety Lock: AD status is Error (Fail-Closed protection). Skipped."
                    self._record_user_result(user_id, email, action, "skipped", reason, run_id)
                    self.skipped_count += 1
                    self.processed_count += 1
                    continue
                elif ad_status == "Unchecked":
                    reason = "AD Safety Lock: AD status is Unchecked (Fail-Closed protection). Skipped."
                    self._record_user_result(user_id, email, action, "skipped", reason, run_id)
                    self.skipped_count += 1
                    self.processed_count += 1
                    continue

            # ---------------------------------------------------------
            # EXECUTE ACTION (Dry-run or Live)
            # ---------------------------------------------------------
            try:
                success, result_msg = self._dispatch_action(user, action, is_dry_run, target_move_group, run_id)
                status_str = "completed" if success else "failed"

                if success:
                    self.success_count += 1
                else:
                    self.failed_count += 1

                self._record_user_result(user_id, email, action, status_str, result_msg, run_id)

            except AuthExpiredException as auth_ex:
                # Handle 401/403: Transition to awaiting_reauth
                add_log("ERROR", f"Authentication Expired during execution: {str(auth_ex)}", run_id=run_id, user_email=email, db_path=self.db_path)
                self.current_state = "awaiting_reauth"
                self.current_message = "Authentication Expired. Execution paused. Please update credentials."
                self._pause_event.clear()

                # Wait for reauth resolution
                while self.current_state == "awaiting_reauth":
                    time.sleep(1.0)
                    if self._stop_flag:
                        self.current_state = "stopped"
                        update_execution_checkpoint(run_id, self.processed_count, self.success_count, self.failed_count, self.skipped_count, status="interrupted", db_path=self.db_path)
                        return

                # Retry current user once reauth succeeds
                try:
                    success, result_msg = self._dispatch_action(user, action, is_dry_run, target_move_group, run_id)
                    status_str = "completed" if success else "failed"
                    if success:
                        self.success_count += 1
                    else:
                        self.failed_count += 1
                    self._record_user_result(user_id, email, action, status_str, result_msg, run_id)
                except Exception as retry_ex:
                    self.failed_count += 1
                    self._record_user_result(user_id, email, action, "failed", str(retry_ex), run_id)

            except Exception as ex:
                self.failed_count += 1
                self._record_user_result(user_id, email, action, "failed", str(ex), run_id)

            self.processed_count += 1

            # Checkpoint in SQLite after EVERY single user (Fault-tolerance requirement)
            update_execution_checkpoint(
                run_id=run_id,
                processed_count=self.processed_count,
                success_count=self.success_count,
                failed_count=self.failed_count,
                skipped_count=self.skipped_count,
                status="running",
                db_path=self.db_path
            )

        final_status = "completed" if not self._stop_flag else "interrupted"
        self.current_state = "idle"
        self.current_message = f"Execution {final_status}: {self.success_count} succeeded, {self.skipped_count} skipped, {self.failed_count} failed."
        update_execution_checkpoint(
            run_id=run_id,
            processed_count=self.processed_count,
            success_count=self.success_count,
            failed_count=self.failed_count,
            skipped_count=self.skipped_count,
            status=final_status,
            db_path=self.db_path
        )
        add_log("INFO", self.current_message, run_id=run_id, db_path=self.db_path)

    # -----------------------------------------------------------------
    # ACTION DISPATCHER & IDEMPOTENCY
    # -----------------------------------------------------------------
    def _dispatch_action(
        self,
        user: Dict[str, Any],
        action: str,
        is_dry_run: bool,
        target_group: str,
        run_id: int
    ) -> (bool, str):
        uuid = user.get("cloudsoc_id")
        email = user.get("email", "")

        # -------------------------------------------------------------
        # Action: DEACTIVATE
        # -------------------------------------------------------------
        if action == "deactivate":
            # Idempotency Check: if already inactive in DB, skip API call
            if user.get("is_active") == 0:
                return True, "Already inactive in CloudSOC (idempotent skip)"

            if is_dry_run:
                return True, f"[DRY-RUN] Would deactivate user (is_active: false)"

            if not uuid:
                # Attempt to resolve UUID by email
                lookup = self.client.get_user_by_email(email)
                if lookup and lookup.get("uuid"):
                    uuid = lookup["uuid"]
                else:
                    return False, "Failed to resolve CloudSOC UUID for deactivation"

            # Execute PATCH
            self.client.update_user(uuid, {"is_active": False})
            # Update local DB is_active
            with get_db_connection(self.db_path) as conn:
                conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user["id"],))
                conn.commit()
            return True, "Deactivated successfully in CloudSOC"

        # -------------------------------------------------------------
        # Action: MOVE TO GROUP
        # -------------------------------------------------------------
        elif action == "move_group":
            # Parse user's current groups
            user_groups_raw = user.get("groups", "[]")
            try:
                current_groups = json.loads(user_groups_raw) if isinstance(user_groups_raw, str) else user_groups_raw
                if not isinstance(current_groups, list):
                    current_groups = []
            except Exception:
                current_groups = []

            # Idempotency Check: already in group?
            already_in_group = False
            for g in current_groups:
                g_name = (g.get("name") if isinstance(g, dict) else str(g)).strip().lower()
                if g_name == target_group.strip().lower():
                    already_in_group = True
                    break

            if already_in_group:
                return True, f"Already member of '{target_group}' (idempotent skip)"

            if is_dry_run:
                return True, f"[DRY-RUN] Would add user to group '{target_group}'"

            if not uuid:
                lookup = self.client.get_user_by_email(email)
                if lookup and lookup.get("uuid"):
                    uuid = lookup["uuid"]
                else:
                    return False, f"Failed to resolve CloudSOC UUID for group assignment"

            # Resolve or create group (cached on instance to minimize API calls)
            if not self._cached_group_uri or self._cached_group_name != target_group:
                group_uri = self.client.find_or_create_group(target_group)
                self._cached_group_uri = group_uri
                self._cached_group_name = target_group

            # Append new group
            updated_groups = list(current_groups)
            if self._cached_group_uri:
                updated_groups.append(self._cached_group_uri)
            else:
                updated_groups.append({"name": target_group})

            self.client.update_user(uuid, {"groups": updated_groups})

            # Update local DB groups
            with get_db_connection(self.db_path) as conn:
                conn.execute("UPDATE users SET groups = ? WHERE id = ?", (json.dumps(updated_groups), user["id"]))
                conn.commit()
            return True, f"Moved to group '{target_group}' successfully"

        # -------------------------------------------------------------
        # Action: DELETE (Hard Delete with safety net)
        # -------------------------------------------------------------
        elif action == "delete":
            # Safety check: Verify raw_json exists for Hard Undo capability
            raw_json = user.get("raw_json")
            if not raw_json:
                add_log("WARNING", f"User {email} has no cached raw_json profile snapshot", run_id=run_id, user_email=email, db_path=self.db_path)

            if is_dry_run:
                return True, f"[DRY-RUN] Would DELETE user from CloudSOC"

            if not uuid:
                lookup = self.client.get_user_by_email(email)
                if lookup and lookup.get("uuid"):
                    uuid = lookup["uuid"]
                else:
                    return True, "User does not exist in CloudSOC (already deleted / 404)"

            try:
                self.client.delete_user(uuid)
                # Mark as inactive in DB
                with get_db_connection(self.db_path) as conn:
                    conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user["id"],))
                    conn.commit()
                return True, "Deleted from CloudSOC successfully"
            except RuntimeError as re:
                if "404" in str(re):
                    return True, "User already deleted / does not exist in CloudSOC (404 handled)"
                raise

        return False, f"Unknown action: {action}"

    def _record_user_result(
        self,
        user_id: int,
        email: str,
        action: str,
        status: str,
        message: str,
        run_id: int
    ):
        now = datetime.now(timezone.utc).isoformat()
        with get_db_connection(self.db_path) as conn:
            conn.execute("""
                UPDATE users SET
                    action_status = ?,
                    action_result = ?,
                    updated_at = ?
                WHERE id = ?
            """, (status, message, now, user_id))
            conn.commit()

        log_level = "INFO" if status in ("completed", "skipped") else "ERROR"
        add_log(log_level, f"[{action.upper()}] {message}", run_id=run_id, user_email=email, action=action, status=status, db_path=self.db_path)

    # -----------------------------------------------------------------
    # UNDO OPERATIONS (Soft and Hard Undo)
    # -----------------------------------------------------------------
    def undo_action(self, user_id: int, undo_type: str = "soft") -> Dict[str, Any]:
        """
        Soft Undo: Re-activates user if action was deactivate.
        Hard Undo: Re-creates user via POST if action was delete (using stored raw_json).
        """
        with get_db_connection(self.db_path) as conn:
            user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not user:
                return {"success": False, "error": f"User ID {user_id} not found in database"}
            user = dict(user)

        cfg = load_config(self.db_path)
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
            key_secret=cfg.get("key_secret", ""),
            rate_limiter=shared_limiter,
            db_path=self.db_path
        )

        uuid = user.get("cloudsoc_id")
        email = user.get("email")

        if undo_type == "soft":
            # Re-activate user
            if not uuid:
                lookup = client.get_user_by_email(email)
                if lookup:
                    uuid = lookup.get("uuid")
            if not uuid:
                return {"success": False, "error": "Cannot resolve UUID for soft undo"}

            client.update_user(uuid, {"is_active": True})
            with get_db_connection(self.db_path) as conn:
                conn.execute("""
                    UPDATE users SET
                        is_active = 1,
                        selected_action = 'none',
                        action_status = 'reverted',
                        action_result = 'Re-activated via Soft Undo'
                    WHERE id = ?
                """, (user_id,))
                conn.commit()

            add_log("INFO", f"Soft Undo: User {email} re-activated", user_email=email, action="undo", db_path=self.db_path)
            return {"success": True, "message": f"User {email} successfully re-activated"}

        elif undo_type == "hard":
            # Re-create deleted user using raw_json
            raw_json_str = user.get("raw_json")
            if not raw_json_str:
                return {"success": False, "error": "No cached profile snapshot (raw_json) available for Hard Undo"}

            try:
                profile = json.loads(raw_json_str)
            except Exception as e:
                return {"success": False, "error": f"Failed to parse raw_json profile: {str(e)}"}

            create_payload = {
                "email": profile.get("email", email),
                "first_name": profile.get("first_name", user.get("first_name", "")),
                "last_name": profile.get("last_name", user.get("last_name", "")),
                "secondary_user_id": profile.get("secondary_user_id", user.get("secondary_user_id", "")),
                "is_active": True
            }

            res = client.create_user(create_payload)
            new_id = res.get("id") or (res.get("resource_uri", "").strip("/").split("/")[-1] if "/user_management/" in res.get("resource_uri", "") else None)

            with get_db_connection(self.db_path) as conn:
                conn.execute("""
                    UPDATE users SET
                        cloudsoc_id = COALESCE(?, cloudsoc_id),
                        is_active = 1,
                        selected_action = 'none',
                        action_status = 'reverted',
                        action_result = 'Re-created in CloudSOC via Hard Undo'
                    WHERE id = ?
                """, (new_id, user_id))
                conn.commit()

            add_log("INFO", f"Hard Undo: User {email} re-created in CloudSOC", user_email=email, action="undo", db_path=self.db_path)
            return {"success": True, "message": f"User {email} re-created in CloudSOC successfully"}

        return {"success": False, "error": f"Invalid undo_type: {undo_type}"}
