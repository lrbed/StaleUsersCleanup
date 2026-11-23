# System Implementation Architecture & Phase Blueprint

This document details the complete architectural implementation of the Broadcom Symantec CloudSOC User Cleanup Utility across all development phases and production enhancements.

---

## Phase 1: Foundation, Configuration & Preflight Connectivity
* **Core Objectives:** Establish backend runtime, local state persistence, credential management, and preflight connectivity health tiers.
* **Key Modules:**
  * `app/config.py`: Centralized configuration loading prioritizing SQLite `app_config`, local JSON (`config-*.json`, `ad_forests.json`), and hardcoded defaults. Supports credential masking (`mask_secret()`) and tenant discovery (`get_detected_tenants()`).
  * `app/database.py`: SQLite initialization, `WAL` journaling mode, performance indices (`idx_users_email`, `idx_users_cloudsoc_id`, etc.), and custom Python `REGEXP` registration.
  * `app/main.py` (`/api/preflight`): 3-tier health engine:
    * **Green (Healthy):** CloudSOC API, SQLite DB, and Active Directory responsive.
    * **Yellow (Partially Ready):** CloudSOC and DB healthy; AD offline (enforces Safety Lock).
    * **Red (Critical):** CloudSOC API or SQLite DB inaccessible.

---

## Phase 2: High-Throughput Ingestion & LDAP Delta Engine
* **Core Objectives:** Ingest massive datasets (100,000+ accounts) from CloudSOC without memory exhaustion and correlate status against Active Directory.
* **Key Modules:**
  * `app/cloudsoc_client.py`: Cursor-based pagination (`get_users_page(limit=100, offset=N)`).
  * `app/sync_service.py`: Multi-threaded ingestion pipelines:
    * **CloudSOC Tenant Ingestion:** In-memory criteria filtering (`matches_criteria`), including `stale_only` pre-filtering to discard active users before SQLite writes.
    * **Active Directory Delta Sync:** Batch queries against AD Global Catalog (port 3268) or direct DC (port 389). Maps user status to `Active`, `Disabled`, `NotFound`, or `Error`.
    * **CSV Ingestion:** Direct streaming of offline `exported_users.csv`.

---

## Phase 3: Directory Visualization & Targeted Preparation
* **Core Objectives:** Server-side paginated dashboard enabling operators to filter, search, and stage maintenance actions across 100,000+ users.
* **Key Modules:**
  * `app/database.py` (`query_users`): Dynamic SQL query constructor with server-side `LIMIT` and `OFFSET`. Filters by inactivity intervals (30d, 60d, 90d, 180d, 365d, 730d, blank, or custom numeric days), AD status, CloudSOC/AD groups, keyword search, and regex pattern matching.
  * `app/static/app.js`: Tab-based navigation with `localStorage` state persistence, collapsible preflight accordions, dynamic datalists, and select-all/clear controls.
  * **Role Guard Protection:** Accounts with administrative roles or group memberships display `🛡️ Exception: Role Guard Protected`, and their checkboxes are permanently locked from selection.

---

## Phase 4: Guarded Execution Engine, Rate Limiter & Disaster Recovery
* **Core Objectives:** Execute staged actions safely while enforcing CloudSOC rate limits, AD safety locks, and profile serialization.
* **Key Modules:**
  * `app/execution_service.py`: Thread-safe state machine (`idle` $\leftrightarrow$ `running` $\leftrightarrow$ `paused` $\rightarrow$ `stopped` / `completed` / `awaiting_reauth`).
  * `app/cloudsoc_client.py` (`RateLimiter`): Sliding-window rate limiter enforcing $\le 2.5$ calls/sec and 10,000 calls/hr. Slice-based sleep allowing immediate "Try Now" interruption.
  * **Fail-Closed AD Safety Lock:** Any user resolving to `Active`, `Error`, or `Unchecked` is skipped and logged with an audit alert.
  * **Undo Recovery Engine:**
    * **Soft Undo:** Dispatches `PATCH {"is_active": true}`.
    * **Hard Undo:** Reconstructs deleted user identity via `POST /api/admin/v1/user_management/` from serialized `users.raw_json`.
  * **Crash Checkpointing:** Progress committed after every user; server auto-detects interrupted runs on startup for one-click resumption.

---

## Phase 5: Auditing, Compliance & Zero-Install Portability
* **Core Objectives:** Historical execution tracking, compliance CSV generation, and zero-dependency Windows distribution.
* **Key Modules:**
  * `app/database.py` (`export_run_to_csv`): Formats historical run audit logs with timestamps, actions, HTTP codes, and failure contexts.
  * `tools/package_portable.py`: Compiles self-contained Windows package containing embedded Python 3.11 runtime, pre-installed wheels (`flask`, `ldap3`), batch launcher (`Launch-CloudSOC-Utility.bat`), PowerShell launcher (`Launch-CloudSOC-Utility.ps1`), and quickstart documentation.
