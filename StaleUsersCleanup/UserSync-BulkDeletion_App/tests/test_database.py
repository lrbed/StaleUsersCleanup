import os
import sys
import tempfile
import json
import sqlite3
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import (
    init_db,
    get_db_connection,
    upsert_user,
    update_user_ad_status,
    add_log,
    create_execution_run,
    update_execution_checkpoint,
    get_latest_interrupted_run
)


class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.temp_db_fd, self.temp_db = tempfile.mkstemp(suffix=".db")
        os.close(self.temp_db_fd)
        init_db(self.temp_db)

    def tearDown(self):
        if os.path.exists(self.temp_db):
            try:
                os.remove(self.temp_db)
            except Exception:
                pass

    def test_database_initialization(self):
        conn = get_db_connection(self.temp_db)
        try:
            cursor = conn.cursor()
            tables = [row["name"] for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            self.assertIn("users", tables)
            self.assertIn("logs", tables)
            self.assertIn("execution_runs", tables)
            self.assertIn("app_config", tables)

            indexes = [row["name"] for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()]
            self.assertIn("idx_users_email", indexes)
            self.assertIn("idx_users_cloudsoc_id", indexes)
            self.assertIn("idx_users_ad_status", indexes)
        finally:
            conn.close()

    def test_upsert_and_raw_json_preservation(self):
        sample_user = {
            "id": "uuid-12345",
            "email": "testuser@domain.com",
            "first_name": "Test",
            "last_name": "User",
            "secondary_user_id": "CORP\\testuser",
            "created_by": "Directory Sync",
            "is_active": True,
            "groups": ["GroupA", "GroupB"],
            "last_activity_date": "2026-01-15 10:00:00"
        }

        # 1. First insert
        row_id = upsert_user(sample_user, db_path=self.temp_db)
        self.assertGreater(row_id, 0)

        conn = get_db_connection(self.temp_db)
        try:
            user = conn.execute("SELECT * FROM users WHERE email = 'testuser@domain.com'").fetchone()
            self.assertIsNotNone(user)
            self.assertEqual(user["cloudsoc_id"], "uuid-12345")
            self.assertEqual(user["first_name"], "Test")
            self.assertEqual(user["secondary_user_id"], "CORP\\testuser")
            self.assertEqual(user["is_active"], 1)
            self.assertEqual(user["ad_status"], "Unchecked")
            # Verify raw JSON was saved for Undo safety net
            raw = json.loads(user["raw_json"])
            self.assertEqual(raw["email"], "testuser@domain.com")
            self.assertEqual(raw["id"], "uuid-12345")
        finally:
            conn.close()

        # 2. Upsert update (same email, new name and inactive)
        update_user_data = {
            "id": "uuid-12345",
            "email": "testuser@domain.com",
            "first_name": "TestUpdated",
            "last_name": "UserUpdated",
            "is_active": False
        }
        upsert_user(update_user_data, db_path=self.temp_db)

        conn = get_db_connection(self.temp_db)
        try:
            user = conn.execute("SELECT * FROM users WHERE email = 'testuser@domain.com'").fetchone()
            self.assertEqual(user["first_name"], "TestUpdated")
            self.assertEqual(user["is_active"], 0)
            # Preserves previously populated secondary_user_id
            self.assertEqual(user["secondary_user_id"], "CORP\\testuser")
        finally:
            conn.close()

    def test_ad_status_update(self):
        upsert_user({"email": "adtest@domain.com"}, db_path=self.temp_db)

        update_user_ad_status(
            email="adtest@domain.com",
            ad_status="Disabled",
            ad_forest="forest1.pcslabs.org",
            ad_details="Disabled account found (UAC: 514)",
            db_path=self.temp_db
        )

        conn = get_db_connection(self.temp_db)
        try:
            user = conn.execute("SELECT * FROM users WHERE email = 'adtest@domain.com'").fetchone()
            self.assertEqual(user["ad_status"], "Disabled")
            self.assertEqual(user["ad_forest"], "forest1.pcslabs.org")
            self.assertIn("UAC: 514", user["ad_details"])
            self.assertIsNotNone(user["ad_checked_at"])
        finally:
            conn.close()

    def test_execution_runs_checkpoints(self):
        # Create run
        run_id = create_execution_run("cloudsoc_import", total_candidates=1000, db_path=self.temp_db)
        self.assertGreater(run_id, 0)

        # Update checkpoint
        update_execution_checkpoint(run_id, processed_count=250, success_count=248, failed_count=2, status="running", db_path=self.temp_db)

        interrupted = get_latest_interrupted_run(db_path=self.temp_db)
        self.assertIsNotNone(interrupted)
        self.assertEqual(interrupted["id"], run_id)
        self.assertEqual(interrupted["processed_count"], 250)
        self.assertEqual(interrupted["status"], "running")

        # Complete run
        update_execution_checkpoint(run_id, processed_count=1000, success_count=998, failed_count=2, status="completed", db_path=self.temp_db)
        interrupted2 = get_latest_interrupted_run(db_path=self.temp_db)
        self.assertIsNone(interrupted2)


if __name__ == "__main__":
    unittest.main()
