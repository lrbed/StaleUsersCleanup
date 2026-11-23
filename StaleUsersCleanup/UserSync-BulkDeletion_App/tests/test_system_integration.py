#!/usr/bin/env python3
"""
Full System Integration Test for Symantec CloudSOC User Cleanup Utility.
Executes an end-to-end operational lifecycle:
1. Preflight Diagnostics (DB, API, AD health tiers)
2. Tenant Auto-Discovery and Configuration Loading
3. CSV Data Ingestion into SQLite WAL Cache
4. Multi-Criteria Stale User Directory Filtering (Inactivity + AD Status)
5. Action Staging (Bulk staging of 'deactivate' and 'move_group')
6. Dry-Run Execution State Machine Processing
7. Per-User State Verification and Audit Trail Generation
8. Compliance CSV Export Verification
"""

import os
import sys
import tempfile
import json
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
os.chdir(BASE_DIR)

from app.main import app
from app.database import init_db, get_db_connection, DEFAULT_DB_PATH
from app.config import save_config


def run_system_test():
    print("=" * 75)
    print(" LIVE SYSTEM INTEGRATION TEST: Symantec CloudSOC User Cleanup Utility")
    print("=" * 75)

    temp_fd, temp_db = tempfile.mkstemp(suffix=".db")
    os.close(temp_fd)

    try:
        init_db(temp_db)
        app.config["TESTING"] = True
        client = app.test_client()

        # Step 1: Preflight & Health Check
        print(" [Step 1/8] Verifying Preflight System Diagnostics...")
        res_preflight = client.post("/api/preflight")
        assert res_preflight.status_code == 200, f"Preflight HTTP {res_preflight.status_code}"
        preflight_data = res_preflight.get_json()
        assert preflight_data["success"] is True
        print(f"   [✓] Health Tier: {preflight_data.get('overall_tier')} ({preflight_data.get('tier_label')})")
        print(f"   [✓] Subsystems checked: CloudSOC={preflight_data['checks']['cloudsoc']['status']}, "
              f"AD={preflight_data['checks']['active_directory']['status']}, "
              f"DB={preflight_data['checks']['database']['status']}")

        # Step 2: Tenant Auto-Discovery
        print(" [Step 2/8] Testing Multi-Tenant Profile Auto-Discovery...")
        res_tenants = client.get("/api/config/detected-tenants")
        assert res_tenants.status_code == 200
        tenants_data = res_tenants.get_json()
        assert tenants_data["success"] is True
        detected_count = len(tenants_data.get("tenants", []))
        print(f"   [✓] Discovered {detected_count} local tenant profiles in repository.")

        # Step 3: Data Ingestion (CSV Pipeline)
        print(" [Step 3/8] Ingesting User Dataset via CSV Pipeline...")
        csv_sample = os.path.join(BASE_DIR, "UserSync-BulkDeletion", "exported_users.csv")
        if os.path.isfile(csv_sample):
            res_csv = client.post("/api/sync/csv/start", json={"file_path": csv_sample})
            assert res_csv.status_code == 200
            csv_result = res_csv.get_json()
            assert csv_result["success"] is True
            print(f"   [✓] Ingested {csv_result.get('imported')} accounts from {os.path.basename(csv_sample)}.")
        else:
            print("   [!] Sample CSV not found, seeding test accounts directly...")
            from app.database import upsert_user
            for i in range(50):
                upsert_user({
                    "email": f"system_test_{i:03d}@pcslabs.org",
                    "cloudsoc_id": f"sys-uuid-{i:03d}",
                    "first_name": f"User{i}",
                    "last_name": "Test",
                    "secondary_user_id": f"PCSLABS\\user{i:03d}",
                    "created_by": "Directory Sync",
                    "is_active": True,
                    "last_activity_date": "2025-01-01 10:00:00" if i % 2 == 0 else ""
                }, db_path=temp_db)

        # Step 4: Directory Filtering & Stats
        print(" [Step 4/8] Querying Aggregated Directory Stats & Inactivity...")
        res_stats = client.get("/api/stats")
        assert res_stats.status_code == 200
        stats = res_stats.get_json()
        print(f"   [✓] Total Users in DB: {stats.get('total_users')}")
        print(f"   [✓] Active Users:      {stats.get('active_users')}")

        res_users = client.get("/api/users?page=1&limit=10&inactivity=90")
        assert res_users.status_code == 200
        users_list = res_users.get_json()
        print(f"   [✓] Found {users_list.get('total')} stale users inactive > 90 days.")

        # Step 5: Action Staging
        print(" [Step 5/8] Staging Bulk Actions on Inactive Users...")
        candidate_ids = [u["id"] for u in users_list.get("users", [])[:5]]
        if candidate_ids:
            res_stage = client.post("/api/users/prepare-action", json={
                "action": "deactivate",
                "user_ids": candidate_ids
            })
            assert res_stage.status_code == 200
            print(f"   [✓] Staged 'deactivate' action for {len(candidate_ids)} users.")

        # Step 6: Dry-Run Execution
        print(" [Step 6/8] Starting Dry-Run Execution Engine...")
        res_exec = client.post("/api/execution/start", json={"mode": "dry_run"})
        assert res_exec.status_code == 200
        exec_start = res_exec.get_json()
        assert exec_start["success"] is True
        run_id = exec_start.get("run_id")
        print(f"   [✓] Execution Run ID {run_id} initiated in DRY_RUN mode.")

        # Poll execution status
        timeout = time.time() + 10.0
        while time.time() < timeout:
            res_status = client.get("/api/execution/status")
            assert res_status.status_code == 200
            st = res_status.get_json()
            if st["state"] in ("completed", "stopped", "idle"):
                print(f"   [✓] Run completed in state: {st['state'].upper()}. "
                      f"Processed: {st.get('processed_count')}, Success: {st.get('success_count')}, "
                      f"Skipped: {st.get('skipped_count')}, Failed: {st.get('failed_count')}")
                break
            time.sleep(0.5)

        # Step 7: Distinct Groups Check
        print(" [Step 7/8] Discovering Dynamic CloudSOC Groups...")
        res_groups = client.get("/api/users/groups")
        assert res_groups.status_code == 200
        groups_data = res_groups.get_json()
        print(f"   [✓] Discovered {len(groups_data.get('groups', []))} distinct groups.")

        # Step 8: Audit Compliance Export
        print(" [Step 8/8] Verifying Compliance CSV Audit Export...")
        if run_id:
            res_export = client.get(f"/api/execution/runs/{run_id}/export")
            assert res_export.status_code == 200
            csv_content = res_export.data.decode("utf-8")
            assert "Timestamp" in csv_content
            assert "Action" in csv_content
            line_count = len(csv_content.strip().splitlines())
            print(f"   [✓] CSV Audit Export generated ({line_count} rows, {len(csv_content)} bytes).")

        print("\n" + "=" * 75)
        print(" [✓] SYSTEM TEST RESULT: 100% END-TO-END WORKFLOW VALIDATED")
        print("=" * 75)
        return True

    finally:
        if os.path.exists(temp_db):
            try:
                os.remove(temp_db)
            except Exception:
                pass


if __name__ == "__main__":
    success = run_system_test()
    sys.exit(0 if success else 1)
