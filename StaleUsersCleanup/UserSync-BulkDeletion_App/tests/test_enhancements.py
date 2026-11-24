import os
import sys
import json
import time
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import (
    init_db,
    get_db_connection,
    upsert_user,
    set_users_action,
    create_execution_run,
    get_all_interrupted_runs,
    dismiss_interrupted_run
)
from app.config import save_config, load_config
from app.ldap_client import LDAPSyncEngine
from app.sync_service import matches_criteria
from app.execution_service import ExecutionService
from app.main import app
import app.main as main_module


class TestEnterpriseEnhancements(unittest.TestCase):
    def setUp(self):
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.temp_db_fd)
        init_db(self.temp_db_path)
        app.config["TESTING"] = True
        self.client = app.test_client()

    def tearDown(self):
        if os.path.exists(self.temp_db_path):
            try:
                os.remove(self.temp_db_path)
            except Exception:
                pass

    # -----------------------------------------------------------------
    # 1. MANUAL AD CONNECTIVITY & DIRECT DC FALLBACK
    # -----------------------------------------------------------------
    def test_manual_ad_connection_diagnostics(self):
        engine = LDAPSyncEngine()

        # Test with empty host
        res_empty = engine.test_manual_ad_connection(host="")
        self.assertEqual(res_empty["status"], "Failed")
        self.assertIn("No manual AD host", res_empty["message"])

        # Test with unreachable host (socket closed)
        with patch("socket.socket") as mock_sock:
            mock_inst = MagicMock()
            mock_inst.connect_ex.return_value = 111  # Connection refused
            mock_sock.return_value = mock_inst

            res_closed = engine.test_manual_ad_connection(host="10.0.0.99", port=389)
            self.assertEqual(res_closed["status"], "Failed")
            self.assertIn("closed or unreachable", res_closed["message"])

        # Test with successful socket and successful bind
        with patch("socket.socket") as mock_sock, \
             patch("app.ldap_client.Server") as mock_server, \
             patch("app.ldap_client.Connection") as mock_conn:

            mock_inst = MagicMock()
            mock_inst.connect_ex.return_value = 0
            mock_sock.return_value = mock_inst

            mock_conn_inst = MagicMock()
            mock_conn.return_value = mock_conn_inst

            res_ok = engine.test_manual_ad_connection(
                host="dc01.corp.local",
                port=389,
                username="admin@corp.local",
                password="secret_pass",
                base_dn="DC=corp,DC=local"
            )
            self.assertEqual(res_ok["status"], "Passed")
            self.assertTrue(res_ok["socket_connected"])
            self.assertTrue(res_ok["bind_success"])
            self.assertTrue(res_ok["base_dn_valid"])

    def test_manual_ad_mode_in_user_status(self):
        # Configure engine in manual mode
        engine = LDAPSyncEngine(
            manual_mode=True,
            manual_host="dc01.corp.local",
            manual_port=389,
            manual_base_dn="DC=corp,DC=local",
            username="svc@corp.local",
            password="pwd"
        )

        with patch("socket.socket") as mock_sock, \
             patch("app.ldap_client.Server") as mock_server, \
             patch("app.ldap_client.Connection") as mock_conn:

            mock_inst = MagicMock()
            mock_inst.connect_ex.return_value = 0
            mock_sock.return_value = mock_inst

            mock_conn_inst = MagicMock()
            # Simulate Active user returned
            entry = MagicMock()
            entry.userAccountControl.value = 512  # Normal enabled account
            mock_conn_inst.entries = [entry]
            mock_conn.return_value = mock_conn_inst

            status = engine.get_ad_user_status("active_user@corp.local")
            self.assertEqual(status["status"], "Active")
            self.assertIn("ManualDC", status["forest"])

    # -----------------------------------------------------------------
    # 2. STALE-ONLY INGESTION FILTERING (INACTIVITY THRESHOLDS)
    # -----------------------------------------------------------------
    def test_stale_only_ingestion_criteria(self):
        now = datetime.utcnow()
        recent_date = (now - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
        stale_100d = (now - timedelta(days=100)).strftime("%Y-%m-%d %H:%M:%S")
        stale_200d = (now - timedelta(days=200)).strftime("%Y-%m-%d %H:%M:%S")

        user_active_recent = {"email": "active@domain.com", "last_activity_date": recent_date}
        user_stale_100d = {"email": "stale100@domain.com", "last_activity_date": stale_100d}
        user_stale_200d = {"email": "stale200@domain.com", "last_activity_date": stale_200d}
        user_blank_activity = {"email": "blank@domain.com", "last_activity_date": ""}

        # Criteria: Stale only > 90 days, including blank
        crit_90d = {
            "stale_only": True,
            "inactivity_threshold_days": 90,
            "include_blank_activity": True
        }
        self.assertFalse(matches_criteria(user_active_recent, crit_90d))  # 10d < 90d -> skipped
        self.assertTrue(matches_criteria(user_stale_100d, crit_90d))     # 100d >= 90d -> matched
        self.assertTrue(matches_criteria(user_blank_activity, crit_90d))  # Blank -> matched

        # Criteria: Stale only > 180 days
        crit_180d = {
            "stale_only": True,
            "inactivity_threshold_days": 180,
            "include_blank_activity": False
        }
        self.assertFalse(matches_criteria(user_stale_100d, crit_180d))    # 100d < 180d -> skipped
        self.assertTrue(matches_criteria(user_stale_200d, crit_180d))     # 200d >= 180d -> matched
        self.assertFalse(matches_criteria(user_blank_activity, crit_180d)) # include_blank is False

    # -----------------------------------------------------------------
    # 3. CRASH AUTO-DISCOVERY & CHECKPOINT RECOVERY
    # -----------------------------------------------------------------
    def test_interrupted_run_discovery_and_resumption(self):
        # 1. Create interrupted run record
        run_id = create_execution_run(run_mode="dry_run", total_candidates=2, db_path=self.temp_db_path)

        interrupted = get_all_interrupted_runs(self.temp_db_path)
        self.assertEqual(len(interrupted), 1)
        self.assertEqual(interrupted[0]["id"], run_id)

        # 2. Seed pending candidate users
        upsert_user({
            "email": "resume_user1@example.com",
            "cloudsoc_id": "uuid-res-1",
            "role": "End User",
            "ad_status": "NotFound",
            "is_active": 1
        }, self.temp_db_path)
        upsert_user({
            "email": "resume_user2@example.com",
            "cloudsoc_id": "uuid-res-2",
            "role": "End User",
            "ad_status": "NotFound",
            "is_active": 1
        }, self.temp_db_path)

        with get_db_connection(self.temp_db_path) as conn:
            rows = conn.execute("SELECT id FROM users WHERE email LIKE 'resume_user%'").fetchall()
            ids = [r["id"] for r in rows]

        set_users_action("deactivate", ids, db_path=self.temp_db_path)

        # 3. Resume the interrupted run
        exec_service = ExecutionService(self.temp_db_path)
        res = exec_service.resume_interrupted_run(run_id)
        self.assertTrue(res["success"])
        self.assertEqual(res["run_id"], run_id)

        exec_service._thread.join(timeout=5.0)

        # 4. Confirm run completed
        with get_db_connection(self.temp_db_path) as conn:
            run = conn.execute("SELECT * FROM execution_runs WHERE id = ?", (run_id,)).fetchone()
            self.assertEqual(run["status"], "completed")
            self.assertEqual(run["success_count"], 2)

    def test_dismiss_interrupted_run(self):
        run_id = create_execution_run(run_mode="dry_run", total_candidates=5, db_path=self.temp_db_path)
        ok = dismiss_interrupted_run(run_id, self.temp_db_path)
        self.assertTrue(ok)

        # Verify no longer returned as interrupted
        interrupted = get_all_interrupted_runs(self.temp_db_path)
        self.assertEqual(len(interrupted), 0)

    # -----------------------------------------------------------------
    # 4. ACTIVE OPERATIONS & IN-APP DOCS ENDPOINTS
    # -----------------------------------------------------------------
    def test_active_operations_and_docs_endpoints(self):
        # Active operations
        res = self.client.get("/api/app/active-operations")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("has_active_operations", data)
        self.assertIn("sync_running", data)
        self.assertIn("execution_running", data)

        # In-app docs
        doc_res = self.client.get("/api/docs/content")
        self.assertEqual(doc_res.status_code, 200)
        doc_data = doc_res.get_json()
        self.assertTrue(doc_data["success"])
        self.assertGreater(len(doc_data["docs"]), 3)
        topics = [d["id"] for d in doc_data["docs"]]
        self.assertIn("quick-start", topics)
        self.assertIn("ad-safety-lock", topics)
        self.assertIn("manual-ad-connection", topics)

    # -----------------------------------------------------------------
    # 5. UAT MULTI-TENANT SWITCHING, SILENT CONFIG & DISTINCT GROUPS
    # -----------------------------------------------------------------
    def test_detected_tenants_and_tenant_switching(self):
        # 1. Test GET /api/config/detected-tenants
        res = self.client.get("/api/config/detected-tenants")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertIsInstance(data.get("tenants"), list)

        # 2. Test switching tenant with invalid file
        bad_res = self.client.post("/api/config/switch-tenant", json={"filename": "non_existent.json"})
        self.assertEqual(bad_res.status_code, 404)

        # 3. Test silent upload
        upload_res = self.client.post("/api/config/upload-silent", json={
            "tenant": "test-silent-tenant",
            "api_server": "https://api.test.elastica.net/",
            "user_id": "test_user_id",
            "key_id": "test_key_id",
            "key_secret": "super_secret_not_revealed"
        })
        self.assertEqual(upload_res.status_code, 200)
        up_data = upload_res.get_json()
        self.assertTrue(up_data["success"])
        self.assertEqual(up_data["tenant"], "test-silent-tenant")
        # Ensure key_secret is NOT exposed in response
        self.assertNotIn("key_secret", up_data)

    def test_users_groups_endpoint(self):
        # Seed users with group JSON arrays
        u1 = {
            "id": "g1", "email": "g1@test.com", "first_name": "G", "last_name": "One",
            "groups": "[\"Engineering\", \"Cloudsoc_PowerAdmins\"]"
        }
        u2 = {
            "id": "g2", "email": "g2@test.com", "first_name": "G", "last_name": "Two",
            "groups": "['Engineering', 'Security Ops']"
        }
        upsert_user(u1, self.temp_db_path)
        upsert_user(u2, self.temp_db_path)

        with patch("app.main.DEFAULT_DB_PATH", self.temp_db_path):
            res = self.client.get("/api/users/groups")
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data["success"])
            groups = data["groups"]
            self.assertIn("Engineering", groups)
            self.assertIn("Cloudsoc_PowerAdmins", groups)
            self.assertIn("Security Ops", groups)



    # -----------------------------------------------------------------
    # 6. ACTIVE DIRECTORY INGESTION & DEFAULTS
    # -----------------------------------------------------------------
    @patch("app.ldap_client.HAS_LDAP3", True)
    @patch("app.ldap_client.Server")
    @patch("app.ldap_client.Connection")
    def test_ad_user_ingestion_defaults_and_endpoint(self, mock_conn_cls, mock_server_cls):
        # Setup mock LDAP connection and generator
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn

        mock_entries = [
            {
                "type": "searchResEntry",
                "attributes": {
                    "mail": "john.doe@company.com",
                    "displayName": "John Doe",
                    "sAMAccountName": "jdoe",
                    "userAccountControl": 512,  # Active
                    "objectGUID": "1234-guid"
                }
            },
            {
                "type": "searchResEntry",
                "attributes": {
                    "userPrincipalName": "disabled.user@company.com",
                    "displayName": "Disabled User",
                    "sAMAccountName": "duser",
                    "userAccountControl": 514,  # 512 | 2 = ACCOUNTDISABLE
                    "objectGUID": "5678-guid"
                }
            }
        ]
        mock_conn.extend.standard.paged_search.return_value = iter(mock_entries)

        # 1. Test query_ad_users directly with empty parameters (defaults)
        engine = LDAPSyncEngine(
            manual_host="127.0.0.1",
            manual_base_dn="DC=corp,DC=local",
            forests=["corp.local"]
        )
        users = engine.query_ad_users(base_dn=None, ldap_filter=None)
        self.assertEqual(len(users), 2)
        self.assertEqual(users[0]["email"], "john.doe@company.com")
        self.assertEqual(users[0]["ad_status"], "ACTIVE")
        self.assertEqual(users[1]["email"], "disabled.user@company.com")
        self.assertEqual(users[1]["ad_status"], "DISABLED")

        # Verify default filter used
        paged_call_args = mock_conn.extend.standard.paged_search.call_args[1]
        self.assertEqual(paged_call_args["search_base"], "DC=corp,DC=local")
        self.assertEqual(paged_call_args["search_filter"], "(&(objectCategory=person)(objectClass=user))")

        # 2. Test REST endpoint POST /api/sync/ad-ingest/start with blank params
        with patch.object(main_module.sync_service, "start_ad_ingest", return_value={"success": True, "run_id": 99}):
            res = self.client.post("/api/sync/ad-ingest/start", json={
                "ldap_filter": "",
                "base_dn": "",
                "count_limit": 0
            })
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data["success"])
            self.assertEqual(data["run_id"], 99)

    # -----------------------------------------------------------------
    # 7. SORTABLE USERS TABLE & STANDALONE SAM USERNAME MATCHING
    # -----------------------------------------------------------------
    def test_users_table_sorting_endpoint(self):
        # Insert 3 users with different emails and activity dates
        upsert_user({"email": "charlie@test.com", "first_name": "Charlie", "last_name": "C", "last_activity_date": "2026-03-01"}, self.temp_db_path)
        upsert_user({"email": "alice@test.com", "first_name": "Alice", "last_name": "A", "last_activity_date": "2026-01-01"}, self.temp_db_path)
        upsert_user({"email": "bob@test.com", "first_name": "Bob", "last_name": "B", "last_activity_date": "2026-02-01"}, self.temp_db_path)

        with patch("app.main.DEFAULT_DB_PATH", self.temp_db_path):
            # Sort by email ASC
            res_asc = self.client.get("/api/users?sort_by=email&sort_dir=asc")
            self.assertEqual(res_asc.status_code, 200)
            users_asc = res_asc.get_json()["users"]
            self.assertEqual(users_asc[0]["email"], "alice@test.com")
            self.assertEqual(users_asc[1]["email"], "bob@test.com")
            self.assertEqual(users_asc[2]["email"], "charlie@test.com")

            # Sort by email DESC
            res_desc = self.client.get("/api/users?sort_by=email&sort_dir=desc")
            self.assertEqual(res_desc.status_code, 200)
            users_desc = res_desc.get_json()["users"]
            self.assertEqual(users_desc[0]["email"], "charlie@test.com")
            self.assertEqual(users_desc[1]["email"], "bob@test.com")
            self.assertEqual(users_desc[2]["email"], "alice@test.com")

    @patch("socket.socket")
    @patch("app.ldap_client.HAS_LDAP3", True)
    @patch("app.ldap_client.Server")
    @patch("app.ldap_client.Connection")
    def test_standalone_sam_attribute_matching(self, mock_conn_cls, mock_server_cls, mock_sock_cls):
        # Mock socket connect_ex to succeed (0)
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 0
        mock_sock_cls.return_value = mock_sock

        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn

        # Mock an active user entry
        mock_entry = MagicMock()
        mock_entry.userAccountControl.value = 512
        mock_conn.entries = [mock_entry]

        engine = LDAPSyncEngine(
            manual_mode=True,
            manual_host="127.0.0.1",
            manual_port=389,
            manual_base_dn="DC=corp,DC=local",
            forests=["corp.local"]
        )

        # Test netbios stripping: CORP\jdoe -> search for jdoe
        res = engine.get_ad_user_status(
            email="john.doe@company.com",
            secondary_user_id="CORP\\jdoe",
            matching_attribute="sam"
        )
        self.assertEqual(res["status"], "Active")

        # Verify search filter had sAMAccountName=jdoe
        search_call_args = mock_conn.search.call_args[1]
        self.assertIn("(sAMAccountName=jdoe)", search_call_args["search_filter"])


if __name__ == "__main__":
    unittest.main()
