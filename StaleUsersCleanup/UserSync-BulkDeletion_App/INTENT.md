# Product Requirements Document (PRD)
## Symantec Cloudsoc User Cleanup Utility

### 1. Objective
Build a local, stateful web application providing Symantec Cloudsoc Administrators with a visual interface to identify, filter, and manage (deactivate, move, or delete) stale users. The utility must safely handle massive datasets (100,000+ users) and strictly adhere to Cloudsoc API rate limits.

### 2. User Context & Stale Definition
Users are synced to Cloudsoc via:
* **Gatelet (Data-in-Motion):** AD Sync (SpanVA), Manual Creation, Batch Import, API.
* **Securlet (Data-at-Rest):** Auto-imported via SaaS integrations (O365, Google Workspace).

**The "Stale" Condition:**
A user is considered stale if they have generated **zero activities** in the `INVESTIGATE` application within an admin-defined timeframe (e.g., the last 90 days) OR if they exist in Cloudsoc but are missing/disabled in the on-prem Active Directory (LDAP).

### 3. Core Features
1. **Preflight Checks:** Validate Cloudsoc API credentials and LDAP connectivity before starting.
2. **Data Import & Caching:** Pull all users from Cloudsoc and LDAP into a local database for memory-efficient delta comparisons.
3. **Visual Preparation:** A UI table (server-side paginated) allowing admins to filter users based on inactivity, AD groups, Cloudsoc groups, and Regex/Keyword matching on User IDs.
4. **Execution Actions:** Dry-run (log only), Deactivate, Move to Group (e.g., `Pending_Deletion`), and Hard Delete.
5. **The "Undo" Safety Net:** Before any Hard Delete, the system MUST serialize and save the user's complete Cloudsoc JSON profile locally. Undo relies on recreating the user via API (with warnings that historical logs/Securlet data are not restored).
