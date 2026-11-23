import os
import sys
import json
import time
import tempfile
import sqlite3
import unittest
from unittest.mock import MagicMock, patch

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import (
    init_db,
    get_db_connection,
    upsert_user,
    set_users_action,
    clean_logs,
    get_execution_runs,
    get_run_details,
    add_log,
    create_execution_run,
    update_execution_checkpoint
)
from app.config import save_config, load_config
from app.cloudsoc_client import RateLimiter, CloudsocClient, AuthExpiredException
from app.sync_service import matches_criteria, SyncService
from app.execution_service import ExecutionService


class TestUATPhase4And5(unittest.TestCase):
    def setUp(self):
        # Create temporary database for testing
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.temp_db_fd)
        init_db(self.temp_db_path)

        # Seed configuration
        save_config({
            "api_server": "https://api-vip.elastica.net/",
            "tenant": "test_tenant",
            "key_id": "test_key_id",
            "key_secret": "test_secret",
            "role_guard_enabled": True,
            "protected_roles": ["System Admin", "Admin", "Security Admin"],
            "protected_groups": ["Administrators", "Domain Admins"],
            "ad_safety_lock": True
        }, self.temp_db_path)

    def tearDown(self):
        if os.path.exists(self.temp_db_path):
            try:
                os.remove(self.temp_db_path)
            except Exception:
                pass

    # -----------------------------------------------------------------
    # SCENARIO 1: EXEC-01 - Rate Limiter & Telemetry Tracking
    # -----------------------------------------------------------------
    def test_exec01_rate_limiter_and_telemetry(self):
        rl = RateLimiter(max_per_second=10.0, max_per_hour=100)
        start = time.time()
        for _ in range(3):
            rl.acquire()
        elapsed = time.time() - start
        self.assertGreaterEqual(elapsed, 0.15)  # 2 intervals of 0.1s

        telemetry = rl.get_telemetry()
        self.assertEqual(telemetry["total_calls_session"], 3)
        self.assertEqual(telemetry["calls_last_hour"], 3)
        self.assertEqual(telemetry["hourly_quota_remaining"], 97)
        self.assertFalse(telemetry["is_in_backoff"])

    # -----------------------------------------------------------------
    # SCENARIO 2: EXEC-02 - 429 Exponential Backoff & Try-Now Override
    # -----------------------------------------------------------------
    def test_exec02_backoff_and_try_now_override(self):
        rl = RateLimiter(max_per_second=10.0, max_per_hour=100)

        # Start backoff in background thread
        import threading
        thread = threading.Thread(target=rl.wait_backoff, args=(10.0,), daemon=True)
        thread.start()
        time.sleep(0.1)

        self.assertTrue(rl.is_in_backoff)
        self.assertGreater(rl.backoff_seconds_remaining, 5.0)

        # Trigger interrupt_backoff (Try Now)
        rl.interrupt_backoff()
        thread.join(timeout=1.0)
        self.assertFalse(thread.is_alive())
        self.assertFalse(rl.is_in_backoff)

    # -----------------------------------------------------------------
    # SCENARIO 3: ROLE-GUARD - Configurable Role Safety Guard
    # -----------------------------------------------------------------
    def test_role_guard_skips_privileged_users(self):
        # Insert admin user and regular user
        admin_data = {
            "email": "admin@example.com",
            "cloudsoc_id": "uuid-admin-01",
            "first_name": "Super",
            "last_name": "Admin",
            "role": "System Admin",
            "is_privileged": 1,
            "groups": json.dumps(["Administrators", "All_Users"]),
            "is_active": 1
        }
        user_data = {
            "email": "stale@example.com",
            "cloudsoc_id": "uuid-stale-02",
            "first_name": "Stale",
            "last_name": "User",
            "role": "End User",
            "is_privileged": 0,
            "groups": json.dumps(["Standard"]),
            "is_active": 1,
            "ad_status": "NotFound"
        }
        upsert_user(admin_data, self.temp_db_path)
        upsert_user(user_data, self.temp_db_path)

        with get_db_connection(self.temp_db_path) as conn:
            admin_id = conn.execute("SELECT id FROM users WHERE email = 'admin@example.com'").fetchone()["id"]
            user_id = conn.execute("SELECT id FROM users WHERE email = 'stale@example.com'").fetchone()["id"]

        set_users_action("delete", [admin_id, user_id], db_path=self.temp_db_path)

        exec_service = ExecutionService(self.temp_db_path)
        res = exec_service.start_execution(mode="dry_run")
        self.assertTrue(res["success"])

        # Wait for worker loop to finish
        exec_service._thread.join(timeout=5.0)

        with get_db_connection(self.temp_db_path) as conn:
            admin_row = conn.execute("SELECT * FROM users WHERE id = ?", (admin_id,)).fetchone()
            user_row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

        # Admin must be skipped by Role Guard
        self.assertEqual(admin_row["action_status"], "skipped")
        self.assertIn("Role Guard Skipped", admin_row["action_result"])

        # Regular user must be completed (dry-run)
        self.assertEqual(user_row["action_status"], "completed")
        self.assertIn("[DRY-RUN]", user_row["action_result"])

    # -----------------------------------------------------------------
    # SCENARIO 4: AD SAFETY LOCK - Fail-Closed on Active / Error
    # -----------------------------------------------------------------
    def test_ad_safety_lock_fail_closed(self):
        # 1 user Active in AD, 1 user Error in AD, 1 user NotFound in AD
        upsert_user({
            "email": "active_ad@example.com",
            "cloudsoc_id": "uuid-act-01",
            "role": "End User",
            "ad_status": "Active",
            "is_active": 1
        }, self.temp_db_path)

        upsert_user({
            "email": "error_ad@example.com",
            "cloudsoc_id": "uuid-err-02",
            "role": "End User",
            "ad_status": "Error",
            "is_active": 1
        }, self.temp_db_path)

        upsert_user({
            "email": "notfound_ad@example.com",
            "cloudsoc_id": "uuid-nf-03",
            "role": "End User",
            "ad_status": "NotFound",
            "is_active": 1
        }, self.temp_db_path)

        with get_db_connection(self.temp_db_path) as conn:
            rows = conn.execute("SELECT id, email FROM users").fetchall()
            ids = [r["id"] for r in rows]

        set_users_action("deactivate", ids, db_path=self.temp_db_path)

        exec_service = ExecutionService(self.temp_db_path)
        exec_service.start_execution(mode="dry_run")
        exec_service._thread.join(timeout=5.0)

        with get_db_connection(self.temp_db_path) as conn:
            act_user = conn.execute("SELECT * FROM users WHERE email = 'active_ad@example.com'").fetchone()
            err_user = conn.execute("SELECT * FROM users WHERE email = 'error_ad@example.com'").fetchone()
            nf_user = conn.execute("SELECT * FROM users WHERE email = 'notfound_ad@example.com'").fetchone()

        # Active user skipped
        self.assertEqual(act_user["action_status"], "skipped")
        self.assertIn("Active in Active Directory", act_user["action_result"])

        # Error user skipped (fail-closed!)
        self.assertEqual(err_user["action_status"], "skipped")
        self.assertIn("Fail-Closed", err_user["action_result"])

        # NotFound user processed
        self.assertEqual(nf_user["action_status"], "completed")

    # -----------------------------------------------------------------
    # SCENARIO 5: IDEMPOTENCY - Already inactive or already in group
    # -----------------------------------------------------------------
    def test_idempotency_checks(self):
        # User already inactive in CloudSOC
        upsert_user({
            "email": "already_inactive@example.com",
            "cloudsoc_id": "uuid-inact-01",
            "role": "End User",
            "ad_status": "NotFound",
            "is_active": 0
        }, self.temp_db_path)

        # User already member of Pending_Deletion
        upsert_user({
            "email": "already_in_group@example.com",
            "cloudsoc_id": "uuid-grp-02",
            "role": "End User",
            "ad_status": "NotFound",
            "groups": json.dumps(["Pending_Deletion", "All_Users"]),
            "is_active": 1
        }, self.temp_db_path)

        with get_db_connection(self.temp_db_path) as conn:
            u1_id = conn.execute("SELECT id FROM users WHERE email = 'already_inactive@example.com'").fetchone()["id"]
            u2_id = conn.execute("SELECT id FROM users WHERE email = 'already_in_group@example.com'").fetchone()["id"]

        set_users_action("deactivate", [u1_id], db_path=self.temp_db_path)
        set_users_action("move_group", [u2_id], db_path=self.temp_db_path)

        exec_service = ExecutionService(self.temp_db_path)
        # Mock client so we verify NO api calls were made
        mock_client = MagicMock()
        exec_service.client = mock_client

        # Run worker loop directly or via execution
        exec_service.start_execution(mode="live")
        exec_service._thread.join(timeout=5.0)

        with get_db_connection(self.temp_db_path) as conn:
            u1 = conn.execute("SELECT * FROM users WHERE id = ?", (u1_id,)).fetchone()
            u2 = conn.execute("SELECT * FROM users WHERE id = ?", (u2_id,)).fetchone()

        self.assertEqual(u1["action_status"], "completed")
        self.assertIn("Already inactive", u1["action_result"])
        self.assertEqual(u2["action_status"], "completed")
        self.assertIn("Already member", u2["action_result"])

    # -----------------------------------------------------------------
    # SCENARIO 6: SOFT UNDO & HARD UNDO
    # -----------------------------------------------------------------
    def test_soft_and_hard_undo(self):
        # 1. Soft Undo Test
        upsert_user({
            "email": "soft_undo_user@example.com",
            "cloudsoc_id": "uuid-soft-01",
            "is_active": 0,
            "selected_action": "deactivate",
            "action_status": "completed"
        }, self.temp_db_path)

        with get_db_connection(self.temp_db_path) as conn:
            soft_id = conn.execute("SELECT id FROM users WHERE email = 'soft_undo_user@example.com'").fetchone()["id"]

        exec_service = ExecutionService(self.temp_db_path)
        with patch.object(CloudsocClient, "update_user", return_value={"status": 200}):
            res = exec_service.undo_action(soft_id, "soft")
            self.assertTrue(res["success"])

        with get_db_connection(self.temp_db_path) as conn:
            u_soft = conn.execute("SELECT * FROM users WHERE id = ?", (soft_id,)).fetchone()
            self.assertEqual(u_soft["is_active"], 1)
            self.assertEqual(u_soft["action_status"], "reverted")

        # 2. Hard Undo Test
        profile = {"email": "hard_undo_user@example.com", "first_name": "Test", "last_name": "HardUndo"}
        upsert_user({
            "email": "hard_undo_user@example.com",
            "cloudsoc_id": "uuid-hard-02",
            "is_active": 0,
            "selected_action": "delete",
            "action_status": "completed",
            "raw_json": json.dumps(profile)
        }, self.temp_db_path)

        with get_db_connection(self.temp_db_path) as conn:
            hard_id = conn.execute("SELECT id FROM users WHERE email = 'hard_undo_user@example.com'").fetchone()["id"]

        with patch.object(CloudsocClient, "create_user", return_value={"id": "new-uuid-99", "status": 201}):
            res = exec_service.undo_action(hard_id, "hard")
            self.assertTrue(res["success"])

        with get_db_connection(self.temp_db_path) as conn:
            u_hard = conn.execute("SELECT * FROM users WHERE id = ?", (hard_id,)).fetchone()
            self.assertEqual(u_hard["cloudsoc_id"], "new-uuid-99")
            self.assertEqual(u_hard["is_active"], 1)
            self.assertEqual(u_hard["action_status"], "reverted")

    # -----------------------------------------------------------------
    # SCENARIO 7: TARGETED INGESTION CRITERIA MATCHING
    # -----------------------------------------------------------------
    def test_targeted_ingestion_criteria(self):
        user1 = {
            "email": "john@domain.com",
            "secondary_user_id": "CORP\\johnd",
            "groups": [{"name": "Contractors"}],
            "created_by": "Directory Sync"
        }
        user2 = {
            "email": "jane@other.org",
            "secondary_user_id": "OTHER\\janej",
            "groups": [{"name": "Permanent"}],
            "created_by": "Securlet"
        }

        # 1. Email domain check (Match ALL)
        c1 = {"match_mode": "all", "email_domains": ["domain.com"]}
        self.assertTrue(matches_criteria(user1, c1))
        self.assertFalse(matches_criteria(user2, c1))

        # 2. NetBIOS domain check
        c2 = {"match_mode": "all", "netbios_domains": ["CORP"]}
        self.assertTrue(matches_criteria(user1, c2))
        self.assertFalse(matches_criteria(user2, c2))

        # 3. Match ANY mode
        c3 = {"match_mode": "any", "email_domains": ["other.org"], "netbios_domains": ["CORP"]}
        self.assertTrue(matches_criteria(user1, c3))  # matches netbios
        self.assertTrue(matches_criteria(user2, c3))  # matches email domain

    # -----------------------------------------------------------------
    # SCENARIO 8: LOG CLEANUP & RUN HISTORY (AUD-01 & AUD-02)
    # -----------------------------------------------------------------
    def test_log_cleanup_and_runs(self):
        run_id = create_execution_run("test_run", total_candidates=10, db_path=self.temp_db_path)
        add_log("INFO", "Log message 1", run_id=run_id, db_path=self.temp_db_path)
        add_log("WARNING", "Log message 2", run_id=run_id, db_path=self.temp_db_path)

        runs = get_execution_runs(limit=10, db_path=self.temp_db_path)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["id"], run_id)

        details = get_run_details(run_id, db_path=self.temp_db_path)
        self.assertIsNotNone(details)
        self.assertEqual(len(details["logs"]), 2)

        # Clear logs
        deleted = clean_logs(db_path=self.temp_db_path)
        self.assertEqual(deleted, 2)

        # Verify logs table empty
        with get_db_connection(self.temp_db_path) as conn:
            count = conn.execute("SELECT COUNT(*) as c FROM logs").fetchone()["c"]
            self.assertEqual(count, 0)

    # -----------------------------------------------------------------
    # SCENARIO 9: AUTH-RECOVERY & AWAITING_REAUTH STATE MACHINE
    # -----------------------------------------------------------------
    def test_auth_recovery_state_machine(self):
        upsert_user({
            "email": "user_auth_test@example.com",
            "cloudsoc_id": "uuid-auth-01",
            "role": "End User",
            "ad_status": "NotFound",
            "is_active": 1
        }, self.temp_db_path)

        with get_db_connection(self.temp_db_path) as conn:
            uid = conn.execute("SELECT id FROM users WHERE email = 'user_auth_test@example.com'").fetchone()["id"]

        set_users_action("deactivate", [uid], db_path=self.temp_db_path)

        exec_service = ExecutionService(self.temp_db_path)

        # Mock client to raise AuthExpiredException on first call, succeed on second call
        mock_client = MagicMock()
        mock_client.update_user.side_effect = [
            AuthExpiredException("HTTP 401: Token Expired"),
            {"status": 200}
        ]
        exec_service.client = mock_client

        # Start execution in background thread
        import threading
        t = threading.Thread(target=exec_service._worker_loop, args=(load_config(self.temp_db_path),), daemon=True)
        exec_service.current_state = "running"
        exec_service.run_mode = "live"
        exec_service.current_run_id = 999
        t.start()

        # Wait briefly for worker to hit AuthExpiredException and transition to awaiting_reauth
        time.sleep(0.2)
        self.assertEqual(exec_service.current_state, "awaiting_reauth")

        # Simulate Admin re-authenticating
        reauth_ok = exec_service.handle_reauth_complete()
        self.assertTrue(reauth_ok)

        # Worker should resume and complete
        t.join(timeout=3.0)
        self.assertEqual(exec_service.current_state, "idle")

        with get_db_connection(self.temp_db_path) as conn:
            u = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
            self.assertEqual(u["action_status"], "completed")

    # -----------------------------------------------------------------
    # SCENARIO 10: PER-USER CHECKPOINTING DURING CRASH / INTERRUPTION
    # -----------------------------------------------------------------
    def test_per_user_checkpoint_crash_recovery(self):
        # Insert 3 users
        for i in range(1, 4):
            upsert_user({
                "email": f"checkpoint_user{i}@example.com",
                "cloudsoc_id": f"uuid-chk-{i}",
                "role": "End User",
                "ad_status": "NotFound",
                "is_active": 1
            }, self.temp_db_path)

        with get_db_connection(self.temp_db_path) as conn:
            rows = conn.execute("SELECT id FROM users WHERE email LIKE 'checkpoint_user%'").fetchall()
            ids = [r["id"] for r in rows]

        set_users_action("deactivate", ids, db_path=self.temp_db_path)

        exec_service = ExecutionService(self.temp_db_path)
        exec_service.start_execution(mode="dry_run")
        exec_service._thread.join(timeout=5.0)

        # Verify run checkpoints updated
        with get_db_connection(self.temp_db_path) as conn:
            run = conn.execute("SELECT * FROM execution_runs ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual(run["total_candidates"], 3)
            self.assertEqual(run["processed_count"], 3)
            self.assertEqual(run["success_count"], 3)
            self.assertEqual(run["status"], "completed")



if __name__ == "__main__":
    unittest.main()
