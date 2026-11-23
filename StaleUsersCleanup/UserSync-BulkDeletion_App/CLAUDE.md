# Symantec CloudSOC User Cleanup Utility: Claude Agent Guide

This project follows the architecture, invariants, and testing standards defined in [AGENTS.md](./AGENTS.md).

## Quick Summary
- **Primary Guide:** See [AGENTS.md](./AGENTS.md) for full architecture, DB schema, REST endpoints, and coding rules.
- **API Specification:** See [.agents/skills/cloudsoc/SKILL.md](./.agents/skills/cloudsoc/SKILL.md) for CloudSOC and Active Directory protocols.
- **Python Runtime:** Always use `.\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe` which contains pre-installed Flask and LDAP3.

### Commands
```powershell
# Run unit tests
& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" -m unittest discover -s tests -p "test_*.py"

# Run UAT test suites
& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" tests/test_uat_phase1_3.py
& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" tests/test_uat_phase4_5.py
& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" tests/test_enhancements.py

# Run application
& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" run_server.py
```
