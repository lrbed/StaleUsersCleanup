# Workspace Agent Guide: StaleUsersCleanup Repository

Welcome to the **StaleUsersCleanup** workspace.

## Workspace Layout
```
D:\Users\Ahmed\Projects\StaleUsersCleanup\
├── AGENTS.md                                       # Workspace entry point (this file)
└── StaleUsersCleanup\
    └── UserSync-BulkDeletion_App\                  # Primary Application Root
        ├── AGENTS.md                               # Detailed application & coding guide
        ├── .agents\skills\cloudsoc\SKILL.md        # CloudSOC API and Active Directory spec
        ├── .antigravityrules                       # System architectural rules & invariants
        ├── BUG_TRACKER.md                          # Quality tracking & UAT test matrix
        ├── CHANGELOG.md                            # Version history
        ├── IMPLEMENTATION_PLAN.md                  # Comprehensive architectural blueprint
        ├── INTENT.md                               # Product Requirements Document
        ├── TEST_PLAN.md                            # User Acceptance Testing specification
        ├── run_server.py                           # Application web server entry point
        ├── app\                                    # Flask backend and static assets
        ├── tests\                                  # Unit and UAT test suites
        ├── dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe # Bundled Python 3.11
        └── UserSync-BulkDeletion\                  # Legacy PowerShell reference suite
```

## Quick Start for Agents
* **Application Root:** `D:\Users\Ahmed\Projects\StaleUsersCleanup\StaleUsersCleanup\UserSync-BulkDeletion_App`
* **Python Runtime:** Always use the embedded Python runtime in `dist/CloudSOC-Cleanup-Utility-v2.3.0-Portable/runtime/python.exe` to run tests and scripts, as it includes all pre-installed dependencies (`flask`, `ldap3`, `sqlite3`).

### Essential Commands
```powershell
# Navigate to application root
cd "D:\Users\Ahmed\Projects\StaleUsersCleanup\StaleUsersCleanup\UserSync-BulkDeletion_App"

# Run master test suite (All 46 unit and UAT tests in ~5s)
& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" tests/run_all_tests.py

# Launch application
& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" run_server.py
```

Refer to the primary [UserSync-BulkDeletion_App/AGENTS.md](file:///D:/Users/Ahmed/Projects/StaleUsersCleanup/StaleUsersCleanup/UserSync-BulkDeletion_App/AGENTS.md) for full database schemas, REST APIs, rate limiting rules, and architectural invariants.
