# Symantec CloudSOC User Cleanup Utility: Operator Manual

## 1. Introduction & Architecture
The **Symantec CloudSOC User Cleanup Utility** is an enterprise-grade administration application built for Broadcom CloudSOC (Elastica) IAM teams. It identifies, audits, stages, and executes bulk maintenance actions (Deactivate, Move to Group, Delete) against massive user datasets (100,000+ accounts) while guaranteeing strict operator safety.

### Architecture Highlights
- **Local SQLite State Database (`cloudsoc_state.db`):** Zero external database requirements. Operates in Write-Ahead Logging (WAL) mode for high-throughput streaming and crash resilience.
- **Fail-Closed Active Directory Safety Lock:** If Active Directory is unreachable or reports an error, account deletions are strictly blocked.
- **Sliding-Window Rate Limiter:** Enforces Broadcom's $\le 2.5$ API calls/sec rule (10,000 requests/hour) with exponential backoff and interactive Try-Now override.
- **Zero Privacy Leakage:** Absolutely zero telemetry or user data leaves the local machine except directed calls to the configured tenant API server.

---

## 2. Preflight & Configuration
Before executing data operations, validate all systems using the **Preflight & Settings** tab:
1. **Collapsible Accordions:**
   - Both CloudSOC API Credentials and Active Directory / LDAP Configuration sections are collapsed by default to maximize operational focus and save screen real estate.
   - Connected Active Directory containers display a subdued, green-badged indicator (`🟢 Connected`) confirming safety is active.
   - The local SQLite State Database displays an emerald green badge (`● Connected & Ready`).
2. **Multi-Tenant Profile Auto-Discovery & Silent Switching:**
   - Discovers local `config-*.json` tenant files located in `./UserSync-BulkDeletion/`.
   - Select any tenant profile (e.g. `raul`, `prod`, `ahmed`, `DLPCloud`) from the **Switch Tenant Profile** dropdown to silently update active credentials in SQLite without rendering secret tokens on screen.
   - **Silent JSON File Import:** Click **Select JSON File** to upload custom credentials directly into SQLite without exposing plaintext credentials in the browser.
3. **Active Directory Connection Mode:**
   - **Auto-Discovery:** Provide a list of AD Forest FQDNs. The utility queries Global Catalog (port 3268) and standard LDAP (port 389).
   - **Manual Direct Connection (Fallback):** If enterprise DNS fails or in isolated lab networks, enter the explicit Domain Controller IP/host, LDAP port (389 or 636 for LDAPS), Search Base DN (e.g. `DC=corp,DC=internal`), and service account credentials. Click **Test Direct DC Connection** for instant diagnostic validation.
4. **Preflight Health Tiers:**
   - **Green (Healthy & Ready):** CloudSOC, Database, and Active Directory are fully responsive.
   - **Yellow (Partially Ready):** CloudSOC and DB are healthy, but AD is offline. The utility operates in **Safety Lock** mode (no active users can be modified).
   - **Red (Not Ready):** CloudSOC credentials or SQLite database connection failed.

---

## 3. Data Ingestion & Sync Engine
The Import & Sync Engine utilizes segmented sub-tabs to preserve UI real estate:
1. **CloudSOC Tenant User Ingestion (Selective & Inactivity Filters):**
   - **Import Stale Users ONLY:** In-memory pre-filter that skips active users during pagination and only imports users exceeding the configured inactivity period (e.g. 90, 180, 365 days or blank activity date).
   - **Targeted Filters:** Restrict downloads by count limit, criteria match mode (ALL vs. ANY), email domain suffix (`domain.com`), NetBIOS domain (`DOMAIN\user`), and auto-discovered CloudSOC groups.
2. **Active Directory Delta Sync:**
   - Scans cached accounts against AD and flags users as `Active`, `Disabled`, `NotFound`, or `Error`.
   - Guaranteed fail-closed safety prevents modification if any DC connection fails.
3. **CSV File Ingestion:**
   - Load pre-staged user profiles from `exported_users.csv` directly into SQLite.
4. **Sticky UI State:**
   - Active sub-tabs, filter choices, and targeted ingestion parameters are automatically persisted in `localStorage` across sessions.

---

## 4. User Directory & Bulk Action Staging
1. **Role Safety Guard Exception & Selection Lock:**
   - Accounts with privileged roles (`Admin`, `System Admin`, `Super Admin`, etc.) or belonging to privileged groups (`Administrators`, `Domain Admins`, `Enterprise Admins`, `Schema Admins`, `Cloudsoc_PowerAdmins`) display a prominent `🛡️ Exception: Role Guard Protected` badge.
   - Checkboxes on privileged rows are permanently **disabled** and skipped during "Select Visible" to eliminate any possibility of accidental staging or deletion.
2. **Dynamic Group Name Dropdown & Autocomplete:**
   - Dropdown filters and targeted ingestion fields provide instant auto-completion across all 37 distinct CloudSOC and Active Directory groups.
3. **Customizable Inactivity Filter:**
   - Filter by standard intervals (30, 60, 90, 180 days, 365 days / 1 Year, 730 days / 2 Years, Blank/Never) or choose **Custom Days...** to enter exact numeric thresholds (e.g. 400 days).
4. **Keyword & Regex Search:**
   - **Keyword Search:** Case-insensitive search across email, first name, last name, and secondary user ID.
   - **Regex Filter:** Python regex matching applied against user email, secondary user ID, and CloudSOC GUID.
5. **Action Staging:** Select users via checkboxes and assign planned actions:
   - **Deactivate:** Disables the user in CloudSOC.
   - **Move Group:** Appends the user to `Pending_Deletion`.
   - **Hard Delete:** Permanently deletes the user profile.
   - **Clear Action:** Resets planned action to None.

---

## 5. Execution Engine & Safety Guards
1. **Execution Modes:**
   - **Start Dry-Run:** Executes all safety checks, checks rate limits, and logs intended mutations without issuing write calls to CloudSOC.
   - **Start Live Execution:** Issues live API calls with rate-limiting and per-user checkpointing.
2. **Hard Delete Safety Modal:** Live hard deletion requires typing `CONFIRM` before the engine will start.
3. **Role Safety Guard:** Configurable protected roles (`System Admin`, `Super Admin`, etc.) and protected groups (`Administrators`, etc.) are automatically skipped and logged as `skipped`.
4. **Rate Limit 429 Backoff & "Try Now":** If CloudSOC responds with HTTP 429, the engine pauses and displays a countdown timer. Click **TRY NOW** to bypass the sleep immediately.
5. **State Machine Controls:** Use **Pause**, **Resume**, **Stop**, or **Retry Failed** at any time.

---

## 6. Disaster Recovery & Undo Operations
1. **Soft Undo:** Re-activates a deactivated user via a `PATCH {"is_active": true}` API call.
2. **Hard Undo:** If a user was deleted, the engine retrieves their pre-deletion profile from `users.raw_json` and calls the CloudSOC `POST` Create User endpoint to restore the identity.
3. **Crash Recovery (Interrupted Runs):** If the application process or server is terminated during an execution run, the utility detects the interrupted state upon restart and displays a banner with **Resume from Checkpoint** to continue processing without repeating completed users.

---

## 7. Auditing & CSV Export
1. **Execution Runs Table:** Review historical runs with exact start times, processed counts, successes, skips, and failures.
2. **Export CSV:** Download full compliance audit logs for any run.
3. **Log Pruning:** Clear all logs or purge logs older than 7 days using the cleanup controls.
