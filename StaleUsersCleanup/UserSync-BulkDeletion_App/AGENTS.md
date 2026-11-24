# Symantec CloudSOC User Cleanup Utility: Coding Agent Guide

> **Agent Directive:** This document is the primary source of truth for coding agents working on this codebase. Read this file to understand the architecture, invariants, database schema, REST API contracts, and execution commands without needing to inspect peripheral scripts.

---

## 1. Project Overview & Quick Reference

* **Application Name:** Broadcom Symantec CloudSOC User Cleanup Utility (v2.3.0)
* **Tech Stack:**
  * **Backend:** Python 3.11+ / Flask
  * **Database:** SQLite 3 with Write-Ahead Logging (`WAL` mode)
  * **Frontend:** Vanilla JavaScript (`app.js`), Semantic HTML5 (`index.html`), Tailwind CSS (`style.css`)
  * **Protocols:** REST/JSON for web client; HTTPS Basic Auth for CloudSOC API; LDAP/LDAPS for Active Directory
* **Execution Location:**
  * Root Project Dir: `D:\Users\Ahmed\Projects\StaleUsersCleanup\StaleUsersCleanup\UserSync-BulkDeletion_App`
  * Portable Distribution: `.\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\`
* **Key Workflows:**
  * **Ingestion (Import & Sync):** CloudSOC Tenant API, Active Directory Direct Ingestion (LDAP filter/Base DN/count limit), and CSV Ingestion (Raw Users vs. Staged Cleanup Plan Restore).
  * **Cleanup Plan Builder:** Interactive sortable table headers (ASC/DESC), AD Delta Correlation with configurable matching attributes (`mail`, `userPrincipalName`, `sAMAccountName` with NetBIOS stripping), staged action management, and CSV plan export.
  * **Execution Engine:** Dry-run and live deactivation, group moving, and hard deletion with rate limiting, exponential backoff, and Role Guard protection.

---

## 2. Standard Development & Verification Commands

> **MANDATORY VERIFICATION DIRECTIVE:**
> Any agent modifying Python files, database queries, or endpoints **MUST** run the relevant test suite immediately after changes. Before declaring any turn or task complete, run the master test runner `tests/run_all_tests.py`.

Always run commands from the application directory:
`cd "D:\Users\Ahmed\Projects\StaleUsersCleanup\StaleUsersCleanup\UserSync-BulkDeletion_App"`

### Running Tests
The bundled Python environment contains all required packages (`flask`, `ldap3`, `sqlite3`):

```powershell
# 1. MASTER TEST RUNNER (Runs ALL 46 unit & UAT tests in ~5s with detailed summary)
& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" tests/run_all_tests.py

# 2. Standard unittest discovery (alternative)
& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" -m unittest discover -s tests -p "test_*.py"

# 3. Targeted test execution based on task (see TEST_PLAN.md for full Decision Matrix):
& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" tests/test_database.py       # DB / Schema changes
& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" tests/test_cloudsoc_client.py # CloudSOC API / Rate limit
& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" tests/test_ldap_client.py     # Active Directory / LDAP
& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" tests/test_api_endpoints.py   # Flask REST routes
& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" tests/test_uat_phase1_3.py    # Ingestion & Filtering
& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" tests/test_uat_phase4_5.py    # Execution & Undo
& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" tests/test_enhancements.py    # Crash recovery & Enhancements
```

### Launching the Application
```powershell
# Launch server with automatic default browser open:
& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" run_server.py

# Launch server in headless mode (no browser popup):
$env:NO_BROWSER="1"; & ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" run_server.py
```

### Building the Portable Windows Distribution
```powershell
# Packages embedded Python runtime, app files, launchers into dist/ and creates zip
& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" tools/package_portable.py
```

---

## 3. Core Architectural Invariants & Safety Rules

Every agent modifying this codebase MUST uphold these non-negotiable invariants:

1. **Zero External Telemetry (Privacy Lock):**
   - No HTTP/HTTPS traffic may leave the local machine except directed calls to the configured CloudSOC tenant API server and LDAP queries to configured Domain Controllers. No third-party trackers, CDNs, or external analytics.
2. **Sliding-Window Rate Limiter ($\le 2.5$ calls/sec, 10,000 calls/hr):**
   - All CloudSOC outbound requests MUST pass through `RateLimiter.acquire()`.
   - 429 Too Many Requests MUST trigger exponential backoff ($15\text{s}, 30\text{s}, 60\text{s}, \dots$).
   - The backoff sleep loop MUST be interruptible via `RateLimiter.interrupt_backoff()` (Try-Now button).
3. **Fail-Closed Active Directory Safety Lock:**
   - Active Directory status of `Active`, `Error`, or `Unchecked` strictly blocks live deletion.
   - If LDAP is unreachable, down, or times out, status MUST be recorded as `Error` and the deletion skipped.
4. **Role Safety Guard:**
   - Accounts with privileged roles (`Admin`, `System Admin`, `Super Admin`, etc.) or belonging to privileged groups (`Administrators`, `Domain Admins`, `Enterprise Admins`, `Schema Admins`, `Cloudsoc_PowerAdmins`) have `is_privileged = 1`.
   - Privileged account checkboxes in the UI are permanently disabled.
   - The execution engine MUST skip privileged accounts even if staged.
5. **Hard Undo Safety Net (`raw_json` serialization):**
   - The complete original user JSON profile MUST be stored in `users.raw_json` before deletion. Hard Undo relies on `POST /api/v1/users/` using this serialized JSON.
6. **Per-User State Checkpointing:**
   - SQLite `users.action_status` and `execution_runs` progress MUST be committed after every single user mutation.
   - On server startup, `get_latest_interrupted_run()` detects aborted runs for resumption.

---

## 4. SQLite Database Schema & Conventions

The application state is maintained in `cloudsoc_state.db` (or `data/cloudsoc_state.db`).
Database connections MUST use WAL mode:
```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
```
Connections must register the custom `REGEXP` function via `conn.create_function("REGEXP", 2, _regexp)`.

### Table: `users`
| Column | Type | Constraints / Defaults | Description |
| :--- | :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | Local row ID |
| `cloudsoc_id` | TEXT | UNIQUE | CloudSOC GUID / UUID |
| `email` | TEXT | UNIQUE NOT NULL | Primary email (lowercased) |
| `first_name` | TEXT | | First name |
| `last_name` | TEXT | | Last name |
| `secondary_user_id` | TEXT | | NetBIOS ID (e.g. `DOMAIN\username`) |
| `created_by` | TEXT | | Provisioning agent (e.g. `Directory Sync`) |
| `created_on` | TEXT | | Creation ISO timestamp |
| `modified_by` | TEXT | | Last modifier |
| `modified_on` | TEXT | | Modification ISO timestamp |
| `is_active` | INTEGER | DEFAULT 1 | 1 = Active, 0 = Inactive in CloudSOC |
| `groups` | TEXT | DEFAULT '[]' | JSON array of group objects |
| `title` | TEXT | | Job title |
| `work_phone` | TEXT | | Work phone number |
| `cell_phone` | TEXT | | Mobile phone number |
| `last_activity_date` | TEXT | | ISO timestamp or blank |
| `ad_status` | TEXT | DEFAULT 'Unchecked' | `'Unchecked'`, `'Active'`, `'Disabled'`, `'NotFound'`, `'Error'` |
| `ad_forest` | TEXT | | Forest or DC that returned status |
| `ad_details` | TEXT | | Diagnostic or DN info |
| `ad_checked_at` | TEXT | | ISO timestamp of last AD sync |
| `stale_reason` | TEXT | | Classification reason |
| `selected_action` | TEXT | DEFAULT 'none' | `'none'`, `'deactivate'`, `'move_group'`, `'delete'` |
| `action_status` | TEXT | DEFAULT 'pending' | `'pending'`, `'success'`, `'failed'`, `'skipped'` |
| `action_result` | TEXT | | Detailed log or error message |
| `role` | TEXT | DEFAULT 'End User' | CloudSOC role |
| `is_privileged` | INTEGER | DEFAULT 0 | 1 = Protected by Role Guard, 0 = Standard |
| `raw_json` | TEXT | | Full serialized CloudSOC profile snapshot |
| `created_at` | TEXT | DEFAULT CURRENT_TIMESTAMP | Row insertion timestamp |
| `updated_at` | TEXT | DEFAULT CURRENT_TIMESTAMP | Last record update timestamp |

### Table: `execution_runs`
Tracks bulk execution jobs, mode (`dry_run` / `live`), and checkpoint counters.
* Status values: `'running'`, `'paused'`, `'completed'`, `'stopped'`, `'interrupted'`, `'dismissed'`, `'awaiting_reauth'`.

### Table: `logs`
Structured audit log entries linked to `run_id` with levels (`'INFO'`, `'WARNING'`, `'ERROR'`).

### Table: `app_config`
Key-value store (`key` TEXT PRIMARY KEY, `value` TEXT, `updated_at` TEXT).

---

## 5. Flask Internal REST API Reference

The backend runs on Flask and exposes these core endpoints:

### Configuration & Tenant Management
* `GET /api/config` $\rightarrow$ Returns current configuration with masked secrets.
* `POST /api/config` $\rightarrow$ Updates configuration keys in SQLite.
* `POST /api/config/upload` $\rightarrow$ Ingests `config.json` or `ad_forests.json`.
* `GET /api/config/detected-tenants` $\rightarrow$ Lists local `config-*.json` profiles in `./UserSync-BulkDeletion/`.
* `POST /api/config/switch-tenant` $\rightarrow$ Payload: `{"filename": "config-prod.json"}`. Silently swaps credentials in SQLite.
* `POST /api/config/upload-silent` $\rightarrow$ Uploads JSON credentials directly into SQLite without frontend rendering.

### Preflight Diagnostics
* `GET /api/preflight` $\rightarrow$ Verifies DB, CloudSOC API credentials, and AD connectivity. Returns health tier (`healthy`, `partial`, `critical`).
* `POST /api/preflight/ad-manual-test` $\rightarrow$ Tests direct TCP socket, LDAP bind, and Base DN for manual Domain Controller.

### Ingestion & Synchronization
* `POST /api/sync/cloudsoc` $\rightarrow$ Triggers background pagination loop. Accepts optional `criteria`: `stale_only`, `inactivity_threshold_days`, `email_domains`, `netbios_domains`, `cloudsoc_groups`.
* `POST /api/sync/ad` $\rightarrow$ Triggers background Active Directory delta sync.
* `POST /api/sync/csv` $\rightarrow$ Multipart upload of `exported_users.csv`.
* `GET /api/sync/status` $\rightarrow$ Returns progress, phase, processed counts.
* `POST /api/sync/cancel` $\rightarrow$ Signals running sync thread to stop.

### Directory & Staging
* `GET /api/users` $\rightarrow$ Server-side paginated query. Supports: `page`, `page_size`, `inactivity` (days), `ad_status`, `role_filter`, `selected_action`, `action_status`, `keyword`, `regex`, `group`.
* `GET /api/users/stats` $\rightarrow$ Aggregated counts (total, active, inactive, stale, AD status distribution).
* `GET /api/users/groups` $\rightarrow$ Distinct group names across all cached accounts.
* `POST /api/users/action` $\rightarrow$ Stages action: `{"action": "deactivate"|"move_group"|"delete"|"none", "user_ids": [...]}`.
* `POST /api/users/reset-actions` $\rightarrow$ Resets all staged actions to `'none'`.
* `POST /api/users/undo` $\rightarrow$ Reverses an action: Soft Undo (reactivates) or Hard Undo (recreates from `raw_json`).

### Execution State Machine
* `POST /api/execution/start` $\rightarrow$ Starts job: `{"mode": "dry_run"|"live"}`.
* `POST /api/execution/pause` $\rightarrow$ Pauses execution loop.
* `POST /api/execution/resume` $\rightarrow$ Resumes execution loop.
* `POST /api/execution/stop` $\rightarrow$ Halts execution loop.
* `GET /api/execution/status` $\rightarrow$ Returns state, counts, telemetry, and last 15 logs.
* `POST /api/execution/try-now` $\rightarrow$ Bypasses active exponential backoff sleep.
* `POST /api/execution/reauth` $\rightarrow$ Updates credentials and probes API when state is `awaiting_reauth`.
* `GET /api/execution/interrupted` $\rightarrow$ Returns any incomplete execution run from previous sessions.
* `POST /api/execution/resume-interrupted` $\rightarrow$ Resumes interrupted run from last checkpoint.
* `POST /api/execution/dismiss-interrupted` $\rightarrow$ Dismisses interrupted run.

### Auditing & Compliance
* `GET /api/runs` $\rightarrow$ List of historical execution runs.
* `GET /api/runs/<id>/export` $\rightarrow$ Streams compliance audit report as CSV.
* `POST /api/logs/clean` $\rightarrow$ Clears all logs or purges entries older than 7 days.

---

## 6. Coding Standards & Deprecation Rules

When writing or editing Python code:
1. **Never use `datetime.utcnow()`:** It is deprecated in Python 3.12+. Always use:
   ```python
   from datetime import datetime, timezone
   now = datetime.now(timezone.utc).isoformat()
   ```
2. **Raw Strings in Regex:** Always use raw string literals `r"..."` for regular expressions or docstrings containing escape sequences (`\.`, `\d`, etc.).
3. **Database Concurrency:** Use the context manager `with get_db_connection(db_path) as conn:` to ensure connections and transactions close cleanly.
4. **Type Annotations:** Maintain strict type annotations (`Dict[str, Any]`, `List[str]`, `Optional[...]`) across all service layers.
