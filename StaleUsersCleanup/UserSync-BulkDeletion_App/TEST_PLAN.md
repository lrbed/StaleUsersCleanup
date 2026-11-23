# Comprehensive Test Plan, Verification Matrix & Agent Quality Playbook

> **MANDATORY DIRECTIVE FOR ALL CODING AGENTS:**
> After making ANY modification to Python code, SQL queries, templates, or JavaScript, you **MUST** run the relevant unit/UAT tests and confirm they pass before declaring the task complete. Never assume changes work without test verification.

---

## 1. Task-to-Test Decision Matrix

Use this matrix to immediately identify which test suite to run based on the files or subsystem you are modifying:

| If You Are Modifying... | Target Files | Run This Targeted Test Command | Expected Runtime |
| :--- | :--- | :--- | :--- |
| **SQLite Schema, Indexes, or SQL Queries** | `app/database.py` | `& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" tests/test_database.py` | ~0.6s |
| **CloudSOC HTTP Client, Auth Headers, Rate Limiter, 429 Backoff** | `app/cloudsoc_client.py`, `app/config.py` | `& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" tests/test_cloudsoc_client.py` | ~0.5s |
| **Active Directory Integration, LDAP/LDAPS, Bitmasks, DC Connections** | `app/ldap_client.py` | `& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" tests/test_ldap_client.py` | ~0.4s |
| **Flask REST Endpoints, Routing, Payloads, JSON Responses** | `app/main.py` | `& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" tests/test_api_endpoints.py` | ~0.8s |
| **Data Ingestion, Pagination, Staging, Filters, Regex, Preflight** | `app/sync_service.py`, `app/database.py` | `& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" tests/test_uat_phase1_3.py` | ~2.5s |
| **Bulk Execution Engine, AD Safety Lock, Role Guard, Soft/Hard Undo** | `app/execution_service.py` | `& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" tests/test_uat_phase4_5.py` | ~2.2s |
| **Crash Recovery, Manual AD Fallback, Multi-Tenants, UI Datalists** | `app/main.py`, `app/execution_service.py` | `& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" tests/test_enhancements.py` | ~0.6s |
| **FINAL REGRESSION CHECK (Before Any Task Completion)** | **Any File** | `& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" tests/run_all_tests.py` | **~5.5s** |

---

## 2. Feature & Workflow Test Index (Where to Find Specific Tests)

All test files reside in `D:\Users\Ahmed\Projects\StaleUsersCleanup\StaleUsersCleanup\UserSync-BulkDeletion_App\tests\`.

### A. Authentication & Preflight Checks
* **Test File:** `tests/test_uat_phase1_3.py` & `tests/test_cloudsoc_client.py`
  * `test_auth_01_valid_credentials`: Confirms Basic Auth header generation and valid CloudSOC handshake.
  * `test_auth_02_invalid_credentials`: Confirms clean error reporting without crash on HTTP 401/403.
  * `test_auth_03_ldap_connectivity_failure`: Confirms structured diagnostic failure when Domain Controller is unreachable.
  * `test_auth_04_network_timeout`: Tests unresolvable DNS handling and network timeout catch.
  * `test_auth_token_matches_powershell`: Verifies exact base64 compatibility with `Cloudsoc-v2.1.psm1`.

### B. High-Throughput Ingestion & LDAP Delta Mapping
* **Test File:** `tests/test_uat_phase1_3.py` & `tests/test_enhancements.py`
  * `test_sync_01_massive_pagination_simulation`: Inserts 10,000 user records into SQLite in $<4.0$ seconds.
  * `test_sync_02_ldap_delta_mapping`: Verifies users missing from AD are flagged as `ad_status = 'NotFound'`.
  * `test_sync_03_state_persistence_on_restart`: Verifies cached accounts reload instantly from `cloudsoc_state.db` on boot.
  * `test_stale_only_ingestion_criteria`: Verifies in-memory pre-filtering that skips active users during download.

### C. Directory Search, Regex & Action Staging
* **Test File:** `tests/test_uat_phase1_3.py`
  * `test_filt_01_server_side_pagination`: Verifies SQL `LIMIT` and `OFFSET` server-side paging.
  * `test_filt_02_inactivity_filter`: Verifies inactivity cutoffs (90d, 180d, 365d, blank).
  * `test_filt_03_keyword_and_regex_matching`: Verifies native Python `REGEXP` matching against emails.
  * `test_filt_04_gatelet_vs_securlet_origin`: Partitions SaaS Securlet accounts from Gatelet AD accounts.
  * `test_filt_05_compound_filtering`: Tests intersection logic (`NotFound` AND Inactive AND keyword).
  * `test_prepare_action_staging`: Verifies staging of `deactivate`, `move_group`, and `delete`.

### D. Execution State Machine, Rate Limiting & Safety Locks
* **Test File:** `tests/test_uat_phase4_5.py`
  * `test_exec01_rate_limiter_and_telemetry`: Enforces $\le 2.5$ calls/sec and 10,000 calls/hr quota tracking.
  * `test_exec02_backoff_and_try_now_override`: Tests 429 exponential backoff ($15\text{s}, 30\text{s}, \dots$) and Try-Now interruption.
  * `test_role_guard_skips_privileged_users`: Shields `System Admin`, `Super Admin`, `Domain Admins` from deletion.
  * `test_ad_safety_lock_fail_closed`: Ensures accounts with `ad_status = 'Active'` or `'Error'` are NEVER deleted.
  * `test_idempotency_checks`: Confirms redundant mutations (already inactive or already in group) make 0 API calls.
  * `test_auth_recovery_state_machine`: Verifies state transition to `awaiting_reauth` on HTTP 401/403.
  * `test_per_user_checkpoint_crash_recovery`: Confirms state saving to SQLite after each individual user.

### E. Disaster Recovery (Soft & Hard Undo)
* **Test File:** `tests/test_uat_phase4_5.py`
  * `test_soft_and_hard_undo`: 
    - Tests **Soft Undo**: Dispatches `PATCH {"is_active": true}` to re-enable deactivated user.
    - Tests **Hard Undo**: Retrieves cached profile snapshot from `users.raw_json` and calls `POST /api/v1/users/` to recreate profile.

### F. Enterprise Enhancements & Operational Resilience
* **Test File:** `tests/test_enhancements.py`
  * `test_manual_ad_connection_diagnostics`: Validates manual Domain Controller socket, bind, and Base DN.
  * `test_manual_ad_mode_in_user_status`: Confirms direct DC querying when multi-forest discovery is offline.
  * `test_interrupted_run_discovery_and_resumption`: Auto-detects interrupted run on boot and resumes from last user.
  * `test_dismiss_interrupted_run`: Tests clearing interrupted run prompts.
  * `test_detected_tenants_and_tenant_switching`: Discovers local `config-*.json` files and tests silent tenant switching.
  * `test_users_groups_endpoint`: Tests auto-discovery of all 37 distinct CloudSOC/AD groups.
  * `test_active_operations_and_docs_endpoints`: Verifies in-app searchable guide and browser close-warning guards.

---

## 3. Expected Test Output Signatures (What "PASS" Looks Like)

Before running a test, agents should know what output constitutes success:

### Master Test Suite (`tests/run_all_tests.py`)
```
===========================================================================
 Symantec CloudSOC User Cleanup Utility — Master Test Suite Runner
===========================================================================
 [✓] Base Directory: D:\Users\Ahmed\Projects\StaleUsersCleanup\StaleUsersCleanup\UserSync-BulkDeletion_App
 [✓] Python Runtime: ...\runtime\python.exe
 [*] Discovering all test suites in ./tests/ ...

... [46 tests listed with ... ok] ...

----------------------------------------------------------------------
Ran 46 tests in 5.343s

OK
===========================================================================
 TEST EXECUTION SUMMARY:
===========================================================================
 Total Tests Run:  46
 Passed:           46 [✓]
 Failures:         0
 Errors:           0
 Skipped:          0
 Elapsed Time:     5.34 seconds
===========================================================================

 [✓] STATUS: ALL TESTS PASSED SUCCESSFULLY!
```
* **Exit Code:** `0` indicates 100% success.
* **Exit Code:** `1` indicates one or more failures or errors.

### Standard `unittest discover`
```
Ran 46 tests in 5.783s
OK
```

---

## 4. Agent Execution Playbook: What to Do on Success vs. Failure

### A. When All Tests Pass (SUCCESS)
1. Confirm that the test suite ran against the updated code and did not use cached bytecode (if in doubt, delete `__pycache__`).
2. If your change resolved a previously tracked defect, update `BUG_TRACKER.md` (set Status to `RESOLVED` and fill in Resolution).
3. Proceed to the next assigned task or complete the turn.

### B. When a Test Fails (FAILURE TRIAGE RUNBOOK)

Never ignore a test failure or delete a test to make the suite green. Follow this diagnostic procedure:

| Symptom / Error | Likely Root Cause | Triage & Remediation Step |
| :--- | :--- | :--- |
| `sqlite3.OperationalError: database is locked` | SQLite file connection was not closed, or concurrent thread is holding a transaction. | Ensure all DB access uses `with get_db_connection(...) as conn:`. Never leave an uncommitted transaction open. Check that `PRAGMA journal_mode=WAL` is active. |
| `SyntaxWarning: invalid escape sequence '\.'` | String literal contains regex backslashes without raw prefix. | Prefix the string with `r"..."` (e.g. `r".*@acquired-company\.com$"`). |
| `DeprecationWarning: datetime.datetime.utcnow()` | Code called `utcnow()` instead of timezone-aware UTC datetime. | Replace with `datetime.now(timezone.utc)`. |
| `ModuleNotFoundError: No module named 'flask'` | Command was run with system Python instead of the bundled runtime. | Re-run command using: `& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe"`. |
| `AssertionError: Expected elapsed >= 0.15s, got ...` | Rate limiter timer check ran faster than threshold due to system clock drift. | Verify `RateLimiter.min_interval` logic in `app/cloudsoc_client.py`. |
| `AssertionError: res['status'] == 'Error'` | LDAP fail-closed test expected 'Error' but got something else. | Verify `app/ldap_client.py` does not catch exceptions and silently return 'NotFound'. Active Directory connectivity errors MUST return `'Error'`. |

---

## 5. Bug Reporting Protocol (`BUG_TRACKER.md`)

When a new defect, edge-case failure, or regression is uncovered during testing or coding:

1. **Open `BUG_TRACKER.md`:** Located at `D:\Users\Ahmed\Projects\StaleUsersCleanup\StaleUsersCleanup\UserSync-BulkDeletion_App\BUG_TRACKER.md`.
2. **Assign Next Bug ID:** Follow sequence (e.g., `BUG-006`, `BUG-007`).
3. **Log the Bug Entry:** Use the standard schema:
   ```markdown
   | **BUG-XXX** | Phase <N> | <Low/Medium/High/Critical> | <Title> | <Root Cause Analysis> | **OPEN** | In triage / pending fix |
   ```
4. **Fix the Defect in Code.**
5. **Add / Update Regression Test:** Ensure a test case reproduces the bug and verifies the fix.
6. **Mark Resolved:** Change status to `RESOLVED` and write the concrete resolution in `BUG_TRACKER.md`.
