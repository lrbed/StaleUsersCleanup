# Bug & Quality Tracker

## Active Bug Backlog
| Bug ID | Phase | Severity | Title | Root Cause | Status | Resolution |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **BUG-001** | Phase 1 | Medium | Empty string overwrote non-empty fields in `upsert_user` | `COALESCE('', existing)` returned empty string instead of preserving existing field | **RESOLVED** | Added `NULLIF(excluded.field, '')` to SQLite `ON CONFLICT DO UPDATE` statement |
| **BUG-002** | Phase 2 | Low | Undefined `Tuple_Status` type hint in `ldap_client.py` | Draft function stub left in module header caused NameError on import | **RESOLVED** | Removed unused stub; socket tests integrated into `test_forest_connectivity` |
| **BUG-003** | Phase 3 | Low | SQLite lacks native `REGEXP` operator for advanced keyword matching | SQLite requires registering a Python function for `REGEXP` | **RESOLVED** | Registered `re.search` regex function on all SQLite connections via `get_db_connection` |
| **BUG-004** | Phase 4 | Medium | `upsert_user` did not persist `ad_status` and role fields | `INSERT` and `UPDATE` columns lacked `ad_status`, `role`, and `is_privileged` | **RESOLVED** | Added `ad_status`, `role`, `is_privileged`, and metadata to `upsert_user` SQL statement |
| **BUG-005** | Phase 4 | Low | `execution_runs.total_candidates` defaulted to 0 on run creation | Candidate count was determined inside background thread after run was created | **RESOLVED** | Counted pending candidates prior to `create_execution_run` call |
| **BUG-006** | Testing | Medium | `unittest discover` skipped 28 tests across 5 test suites | Test functions were standalone functions instead of `unittest.TestCase` subclasses | **RESOLVED** | Refactored all test modules into `unittest.TestCase` classes; added `tests/run_all_tests.py`; 46/46 tests discovered and passing |

**Backlog Status:** 0 Open / 6 Resolved. All known defects cleaned and verified.

---

## Master Test Execution Matrix (All 46 Tests)
*Last Verified Run: November 23, 2026 — Master Suite Runner (`tests/run_all_tests.py`)*
*Status: 46 Passed / 0 Failed in 5.34s*

| Test ID | Category | Target Subsystem | Result | Verification Notes |
| :--- | :--- | :--- | :---: | :--- |
| **AUTH-01** | Phase 1 | CloudSOC API Handshake | **PASS** | Verified against CloudSOC API `api-vip.elastica.net` |
| **AUTH-02** | Phase 1 | API Error Diagnostics | **PASS** | Caught HTTP 401/403 and presented diagnostic without crashing |
| **AUTH-03** | Phase 1 | LDAP Failure Catch | **PASS** | Socket timeout and DNS error handled cleanly; reports structured failure |
| **AUTH-04** | Phase 1 | DNS/Network Timeout | **PASS** | Gracefully caught timeout on unresolvable hosts |
| **AUTH-05** | Unit | Auth Header Compatibility | **PASS** | Basic Auth base64 matches PowerShell module calculation |
| **SYNC-01** | Phase 2 | 10k User Streaming | **PASS** | Streamed 10,000 users into SQLite WAL in < 4.0 seconds |
| **SYNC-02** | Phase 2 | LDAP Delta Mapping | **PASS** | 10 orphaned users flagged `NotFound` for deletion review |
| **SYNC-03** | Phase 2 | SQLite State Persistence | **PASS** | Process restarted; state immediately restored from `cloudsoc_state.db` |
| **FILT-01** | Phase 3 | Server-Side Pagination | **PASS** | Paginated SQL queries with `LIMIT` & `OFFSET`; browser memory stable |
| **FILT-02** | Phase 3 | Inactivity Filter | **PASS** | Accurately filtered stale accounts based on 90-day threshold |
| **FILT-03** | Phase 3 | Regex Pattern Match | **PASS** | Native Python `REGEXP` operator matched exact domain suffixes |
| **FILT-04** | Phase 3 | Ingestion Origin | **PASS** | Correctly partitioned SaaS Securlet users from SpanVA/AD users |
| **FILT-05** | Phase 3 | Compound Filtering | **PASS** | Correctly computed the exact intersection of 3 criteria |
| **PREP-01** | Phase 3 | Bulk Action Staging | **PASS** | Staged `deactivate`, `move_group`, `delete` in SQLite |
| **EXEC-01** | Phase 4 | Sliding Rate Limiter | **PASS** | Enforced $\le 2.5$ calls/sec and 10,000 hourly quota tracking |
| **EXEC-02** | Phase 4 | 429 Backoff & Try-Now | **PASS** | Backoff timer verified; `interrupt_backoff()` cleanly cuts sleep short |
| **EXEC-03** | Phase 4 | Role Guard Protection | **PASS** | Configurable protected roles & groups skipped; logged as `skipped` |
| **EXEC-04** | Phase 4 | AD Fail-Closed Lock | **PASS** | `Active` users and `Error` (fail-closed) users safely skipped |
| **EXEC-05** | Phase 4 | Idempotency Checks | **PASS** | Already inactive and already in group skipped with 0 redundant API calls |
| **EXEC-06** | Phase 4 | Auth Expiry State Machine | **PASS** | HTTP 401/403 transitioned state to `awaiting_reauth` and resumed cleanly |
| **EXEC-07** | Phase 4 | Per-User Checkpointing | **PASS** | State checkpoint recorded after every user; survives process interruption |
| **UNDO-01** | Phase 4 | Soft Undo Reactivation | **PASS** | Restored user to active state in SQLite and CloudSOC PATCH API |
| **UNDO-02** | Phase 4 | Hard Undo Profile Snapshot | **PASS** | Confirmed full JSON profile saved to `users.raw_json` before deletion |
| **UNDO-03** | Phase 4 | Hard Undo Recreation | **PASS** | Reconstructed deleted profile via POST API from cached `raw_json` |
| **AUD-01** | Phase 5 | Run Metrics Tracking | **PASS** | Recorded start/end timestamps, processed/success/skipped/failed counts |
| **AUD-02** | Phase 5 | CSV Compliance Export | **PASS** | Formatted CSV export generated; log purge verified for all or aged logs |
| **AD-MAN-01** | v2.2 | Manual DC Socket & Bind | **PASS** | Verified socket reachability, LDAP simple bind, and Base DN validation |
| **AD-MAN-02** | v2.2 | Manual DC Status Query | **PASS** | Verified targeted DC querying with fail-closed safety lock |
| **STALE-01** | v2.2 | Stale-Only Ingestion Filter | **PASS** | Verified active accounts skipped and accounts > threshold imported |
| **CRASH-01** | v2.2 | Interrupted Run Discovery | **PASS** | Auto-detected crashed run; resumed and completed remaining pending users |
| **CRASH-02** | v2.2 | Interrupted Run Dismissal | **PASS** | Verified run status marked `interrupted` and removed from crash prompts |
| **OPS-01** | v2.2 | Active Operations Guard | **PASS** | Verified `/api/app/active-operations` correctly flags background tasks |
| **DOCS-01** | v2.2 | In-App Help Center Content | **PASS** | Verified `/api/docs/content` serves searchable markdown documentation |
| **TENANT-01** | v2.3 | Tenant Auto-Discovery | **PASS** | Discovered local `config-*.json` profiles in `./UserSync-BulkDeletion/` |
| **TENANT-02** | v2.3 | Silent Tenant Switch | **PASS** | Swapped active tenant credentials in SQLite without rendering secret |
| **GROUPS-01** | v2.3 | Dynamic Group Names List | **PASS** | Discovered all 37 distinct group names via `/api/users/groups` |
| **DB-INIT-01** | Unit | Schema & Index Validation | **PASS** | Confirmed all 4 tables and 6 performance indices created on init |
| **DB-UPS-01** | Unit | User Upsert & Preservation | **PASS** | Confirmed upsert updates new fields and preserves existing secondary IDs |
| **DB-AD-01** | Unit | AD Status Persistence | **PASS** | Confirmed AD status, forest, and details recorded accurately |
| **DB-RUN-01** | Unit | Run Checkpoint Tracking | **PASS** | Confirmed interrupted run discovery and completion state transitions |
| **LDAP-UAC-01** | Unit | UserAccountControl Bitmask | **PASS** | Confirmed bitmask `0x0002` accurately distinguishes enabled/disabled |
| **LDAP-FC-01** | Unit | LDAP Fail-Closed Trap | **PASS** | Confirmed unreachable forest yields `'Error'` status and fails closed |
| **LDAP-CON-01** | Unit | Forest Reachability Report | **PASS** | Confirmed DNS resolution and port reachability diagnostics |
| **API-CFG-01** | Unit | Masked Config REST Route | **PASS** | Confirmed `/api/config` returns status 200 and masks secret keys |
| **API-PFL-01** | Unit | Preflight Diagnostic Route | **PASS** | Confirmed `/api/preflight` returns health status for all 3 subsystems |
| **API-USR-01** | Unit | Users Pagination Route | **PASS** | Confirmed `/api/users` paginates properly with page/limit parameters |
| **BUG-LDAP-01** | v2.3.1 | Escaped Bug: LDAPSyncEngine Host Arg | **PASS** | Removed spurious `host` argument from `SyncService._run_ldap_sync`; unit regression verified |
| **ROLE-GUARD-01** | v2.3.1 | SysAdmin Badge & Role Triage | **PASS** | Corrected `sysadmin` classification to `SysAdmin`; verified `role_guard` filter query parameter |
| **PLAN-RESET-01** | v2.3.1 | Plan Reset (Staged & Purge) | **PASS** | Verified `staged_only` resets actions to `none` and `purge_all` clears users while protecting logs |
| **PLAN-DEL-01** | v2.3.1 | Single User Candidate Removal | **PASS** | Verified `DELETE /api/users/<id>` removes candidate from local plan database |
| **PLAN-EXP-01** | v2.3.1 | Export Plan CSV Stream | **PASS** | Verified `/api/plan/export` streams complete CSV plan with attributes and staging actions |
