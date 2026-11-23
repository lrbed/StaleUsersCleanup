# Workspace Guide: StaleUsersCleanup

This workspace contains the **Symantec CloudSOC User Cleanup Utility**.

- **Application Directory:** `StaleUsersCleanup/UserSync-BulkDeletion_App`
- **Primary Agent Guide:** [UserSync-BulkDeletion_App/AGENTS.md](./StaleUsersCleanup/UserSync-BulkDeletion_App/AGENTS.md)
- **API Spec:** [UserSync-BulkDeletion_App/.agents/skills/cloudsoc/SKILL.md](./StaleUsersCleanup/UserSync-BulkDeletion_App/.agents/skills/cloudsoc/SKILL.md)

### Quick Test Command
```powershell
cd "D:\Users\Ahmed\Projects\StaleUsersCleanup\StaleUsersCleanup\UserSync-BulkDeletion_App"
& ".\dist\CloudSOC-Cleanup-Utility-v2.3.0-Portable\runtime\python.exe" tests/run_all_tests.py
```
