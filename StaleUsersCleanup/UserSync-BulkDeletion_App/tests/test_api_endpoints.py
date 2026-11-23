import os
import sys
import json
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
from app.database import init_db


class TestApiEndpoints(unittest.TestCase):
    def setUp(self):
        self.temp_db_fd, self.temp_db = tempfile.mkstemp(suffix=".db")
        os.close(self.temp_db_fd)
        init_db(self.temp_db)
        app.config["TESTING"] = True
        self.client = app.test_client()

    def tearDown(self):
        if os.path.exists(self.temp_db):
            try:
                os.remove(self.temp_db)
            except Exception:
                pass

    def test_config_endpoint(self):
        res = self.client.get("/api/config")
        self.assertEqual(res.status_code, 200)
        cfg_data = res.get_json()
        self.assertTrue(cfg_data["success"])
        self.assertIn("config", cfg_data)
        # Verify secret is never returned in plaintext
        self.assertNotIn("key_secret", cfg_data["config"])

    def test_preflight_endpoint(self):
        res = self.client.post("/api/preflight")
        self.assertEqual(res.status_code, 200)
        preflight_data = res.get_json()
        self.assertTrue(preflight_data["success"])
        self.assertIn("checks", preflight_data)
        self.assertIn("cloudsoc", preflight_data["checks"])
        self.assertIn("active_directory", preflight_data["checks"])
        self.assertIn("database", preflight_data["checks"])

    def test_users_pagination_endpoint(self):
        res = self.client.get("/api/users?page=1&limit=10")
        self.assertEqual(res.status_code, 200)
        users_data = res.get_json()
        self.assertIn("users", users_data)
        self.assertIn("total", users_data)
        self.assertIn("page", users_data)
        self.assertEqual(users_data["page"], 1)
        self.assertEqual(users_data["limit"], 10)

    def test_stats_endpoint(self):
        res = self.client.get("/api/stats")
        self.assertEqual(res.status_code, 200)
        stats_data = res.get_json()
        self.assertIn("total_users", stats_data)
        self.assertIn("ad_status", stats_data)

    def test_sync_status_endpoint(self):
        res = self.client.get("/api/sync/status")
        self.assertEqual(res.status_code, 200)
        status_data = res.get_json()
        self.assertIn("is_running", status_data)
        self.assertIn("percent_complete", status_data)


if __name__ == "__main__":
    unittest.main()
