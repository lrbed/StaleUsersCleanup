# Changelog

All notable changes to the **Symantec CloudSOC User Cleanup Utility** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [2.4.0] - 2026-11-23

### Added
- **Interactive Click-to-Sort Table Headers:** All column headers in the Cleanup Plan Builder table (`User Email / ID`, `Name`, `Role`, `Secondary ID`, `CloudSOC`, `Active Directory`, `Last Activity`, and `Planned Action`) can be clicked to toggle between Ascending (`▲`) and Descending (`▼`) sort order.
- **Server-Side SQL Sorting:** `GET /api/users` now supports `sort_by` and `sort_dir` parameters with a secure column whitelist (`ALLOWED_SORT_COLUMNS`) ensuring instant, accurate sorting across large datasets.
- **Selectable AD Matching Attribute in Cleanup Plan Builder:** Operator can choose the matching attribute used in Active Directory Delta Correlation:
  - `Email or UserPrincipalName` (Default - Recommended)
  - `sAMAccountName` (Standalone Domain - Extracts username from `DOMAIN\USER` or email prefix)
  - `userPrincipalName Only`
  - `mail Only`
  - `Custom Active Directory Attribute` (e.g. `employeeID`)
- **Standalone Domain NetBIOS Username Extraction:** In standalone domain environments (LDAP 389 / LDAPS 636), strips the `DOMAIN\` prefix from NetBIOS identities (`DOMAIN\USER` $\rightarrow$ `USER`) to query `(sAMAccountName=USER)`.
- **Cleanup Plan Export to CSV:** Direct CSV download link (`/api/plan/export`) in the Cleanup Plan Builder toolbar to save the current plan for audit, approval, or backup.
- **CSV Plan Restore Mode:** Channel 3 (CSV File Ingestion) now features a dual-mode selector:
  - *Option A: Import Raw Users* (standard user accounts for triage)
  - *Option B: Restore Saved Cleanup Plan* (re-ingests users and restores their staged actions and AD check statuses).
- **Custom CSV File Upload:** Direct file upload endpoint (`/api/sync/csv/upload`) allowing operators to upload and import any raw user list or saved cleanup plan.

---

## [2.3.0] - 2026-11-20

### Added
- **Multi-Tenant Profile Auto-Discovery & Silent Switching:** Discovers local `config-*.json` profiles in `./UserSync-BulkDeletion/` (`GET /api/config/detected-tenants`), allowing administrators to switch active tenant configurations with 1 click without displaying secrets on-screen (`POST /api/config/switch-tenant`).
- **Silent JSON File Credential Upload:** Direct drag-and-drop or file picker ingestion of custom tenant JSON files silently saving to SQLite without plaintext token rendering (`POST /api/config/upload-silent`).
- **Channel Sub-Tabs for Import & Sync Engine:** Segmented channel navigation (`CloudSOC Tenant Ingestion`, `Active Directory Delta Sync`, and `CSV File Ingestion`) showing only relevant channel controls and optimizing screen real estate.
- **Sticky UI State Persistence:** LocalStorage tracking for active navigation tab, sync channel, targeted import parameters, and user directory filter criteria.
- **Role Guard Exception UI Lock:** Accounts flagged as privileged (`Admin`, `System Admin`, `Domain Admins`, `Administrators`, etc.) are explicitly marked with `🛡️ Exception: Role Guard Protected` badges, and their checkboxes are permanently disabled to prevent accidental selection or staging for deletion.
- **Dynamic Group Name Dropdown & Autocomplete:** Discovers all 37 distinct CloudSOC and Active Directory group memberships across stored users (`GET /api/users/groups`), exposing them via both filter dropdowns and targeted sync datalists.
- **Customizable Inactivity Filter:** Support for 30, 60, 90, 180 (6 Mos), 365 (1 Year), 730 (2 Years), Blank/Never, plus a custom numeric days input for exact inactivity threshold filtering.

### Changed
- **Collapsible Landing Page Accordions:** CloudSOC API Credentials and Active Directory / LDAP sections are now collapsed by default with clear status summaries and rotation chevrons.
- **Subdued AD Connected State:** Connected Active Directory container visually signals healthy status (`🟢 Connected`) with subdued borders to eliminate visual clutter.
- **Emerald Database Status Badge:** Updated SQLite local database indicator from neutral grey to vivid emerald green (`● Connected & Ready`).
- **Theme Light Contrast Resolution:** Overhauled all card backgrounds, callouts, and warning banners in Classic Light theme, specifically fixing the "Import Stale Users ONLY" banner contrast.
- **Search & Regex Label Clarification:** Renamed "Search Email / Name" to "Keyword Search (Email / Name / ID)" and clarified regex pattern matching targets.

---

## [2.2.0] - 2026-11-20

### Added
- **Manual Active Directory Connectivity Fallback:** Direct host/IP, port, SSL/TLS, and Search Base DN targeting when multi-forest DNS auto-discovery is offline or unresolvable.
- **On-Demand Manual AD Diagnostic Testing:** Interactive preflight endpoint (`/api/preflight/ad-manual-test`) to test TCP reachability, LDAP bind, and Search Base DN validation.
- **Stale-Only Ingestion Filter:** Selective ingestion pipeline that pre-filters users during CloudSOC API pagination, saving only accounts exceeding configurable inactivity thresholds (or blank activity date).
- **Multi-Theme Engine:** Enterprise visual switcher supporting Slate Dark, Midnight Blue, Cyber Charcoal, and Classic Light with `localStorage` persistence.
- **Browser Exit Guard (`beforeunload`):** Warns administrators when closing or refreshing the browser while an execution run or synchronization task is active.
- **Crash Auto-Discovery & Checkpoint Resumption:** Detects interrupted historical runs on startup and enables seamless, one-click resumption from the exact last SQLite checkpoint.
- **In-App Searchable User Guide:** Built-in operations documentation center with live search covering CloudSOC API limits, AD safety rules, filtering, and undo recovery.
- **Enterprise SVG Iconography:** Compliant UI icons for enhanced operational clarity.

### Changed
- Enhanced 3-tier preflight status engine to recognize manual Domain Controller health.
- Updated `matches_criteria` in `SyncService` to parse standard ISO/string timestamps and evaluate inactivity age.

---

## [2.1.0] - 2026-11-20

### Added
- **Configurable Role Safety Guard:** Customizable protected roles and protected groups to shield privileged accounts (e.g. System Admin, Super Admin, Domain Admins) from deletion or modification.
- **Sliding-Window Telemetry & 429 Counters:** Live hourly quota tracking and persistent 429 response counters.
- **"Try Now" Rate Limit Override:** Administrator button to bypass active exponential backoff sleep via `RateLimiter.interrupt_backoff()`.
- **Targeted Ingestion Builder:** Filter downloads by count limit, email domain, NetBIOS domain (`DOMAIN\user`), and CloudSOC groups.
- **3-Tier Preflight Status:** Green (Healthy), Yellow (Partially Ready / AD Offline with Safety Lock on), and Red (CloudSOC/DB Error).

---

## [2.0.0] - 2026-11-20

### Added
- **Phase 4 Execution Engine (`execution_service.py`):** Multi-threaded state machine (`idle`, `running`, `paused`, `stopped`, `awaiting_reauth`) for safe bulk deactivations, group assignments, and deletions.
- **Active Directory Safety Lock (Fail-Closed):** Protects active users and halts deletion if AD reports `Active`, `Error`, or `Unchecked`.
- **Idempotency Layer:** Prevents redundant CloudSOC API calls if a user is already inactive or already in the target group.
- **Soft Undo & Hard Undo:** Re-activates deactivated accounts or reconstructs deleted profiles using the cached `raw_json` profile snapshot.
- **Phase 5 Auditing & Compliance Exporter:** Execution run history tracking with per-run CSV download and safe log cleanup.

---

## [1.0.0] - 2026-11-19

### Added
- Initial release of Symantec CloudSOC Cleanup Utility.
- Phase 1 Foundation: Flask backend, SQLite schema with WAL mode, credentials management, and preflight checks.
- Phase 2 Import & Sync Engine: CloudSOC cursor pagination streaming and LDAP delta correlation engine.
- Phase 3 User Directory: Server-side paginated table with regex matching, inactivity thresholds, and bulk action staging.
