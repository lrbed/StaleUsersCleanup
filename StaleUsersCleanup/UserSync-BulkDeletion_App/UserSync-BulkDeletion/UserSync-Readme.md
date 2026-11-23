
### `README.md`

```markdown
# ☁️ CloudSOC UserSync & Bulk Deletion Tool (v4.7.1)
```
## Overview
The **CloudSOC UserSync Orchestrator (`UserSync.ps1`)** is an enterprise-grade PowerShell tool designed to manage user lifecycles in Broadcom CloudSOC (Elastica). It handles both bulk provisioning (Create/Overwrite) and automated, heavily guarded bulk deletions.


### 🔑 API Configuration (`config.json`)

To authenticate with the CloudSOC API, the script requires a JSON configuration file containing your specific tenant details and API keys. 

**File Format & Requirements:**
Create a file containing your `api_server`, `tenant` ID, and your generated `key_id` and `key_secret`. 

Example `config.json`:
```json
{
    "api_server": "[https://api-vip.elastica.net](https://api-vip.elastica.net)",
    "tenant": "examplecom",
    "key_id": "your_api_key_id_here",
    "key_secret": "your_api_key_secret_here"
}

```

* **Default Behavior:** By default, the script looks for a file exactly named `config.json` in the same directory where `UserSync.ps1` is executed.
* **Using Non-Default Locations (Multiple Tenants):** If you manage multiple CloudSOC tenants (e.g., a sandbox environment and a production environment), you can create multiple configuration files (like `config_prod.json` and `config_test.json`). You can then tell the script which one to use via the `-ConfigFile` parameter.

**Example: Running against a specific configuration file:**

```powershell
.\UserSync.ps1 -SourceType csv -SourceValue ".\users.csv" -SyncMode delete -DeleteBy inactivity -InactivityPeriod 90D -ConfigFile "C:\Secure\Keys\config_prod.json"

```


### ️ The Deletion Safety Engine (API-First & Fail-Closed)
To ensure no active or systemic users are accidentally removed, the tool uses a strict, SRE-hardened safety engine:
1. **API-First Lookup:** Verifies the user actually exists in CloudSOC first, extracting their exact `UUID`, `CreatedBy`, and `SecondaryUserId` attributes.
2. **Active Directory Safety Lock (Fail-Closed):** Connects to multiple AD forests. If a user is active in Windows, deletion is aborted. **Crucially, if AD is unreachable or unresponsive, the script "Fails Closed" and safely aborts the deletion to prevent accidental data loss.**
3. **API Guards:** Evaluates internal CloudSOC attributes to protect systemic service accounts or target specific domains.
4. **Smart Exponential Backoff:** Automatically handles API rate limits (HTTP 429) by smoothly pausing and retrying using exponential math (15s, 30s, 60s) to clear traffic jams.
5. **True Simulation (Dry-Run):** Generates a full simulation report by running all AD and API checks without sending the final delete command.

---

##  Core Enterprise Use Cases

This script was built to solve specific Identity and Access Management (IAM) state-drift challenges:

* **Use Case 1: Routine Offboarding Reconciliation (The "Orphan" Cleanup)**
  * *The Problem:* Your automated directory sync tool (e.g., Okta, Azure AD) failed to deprovision users who left the company months ago.
  * *The Solution:* Filter by 90+ days of inactivity, verify they are actually disabled/missing in Active Directory, and safely purge them.
* **Use Case 2: CloudSOC License Recovery**
  * *The Problem:* You are running out of licensed seats because thousands of historical contractors never logged in.
  * *The Solution:* Target users with a `BLANK` Last Activity Date or > 1 Year inactivity, and bulk delete them.
* **Use Case 3: Merger, Acquisition, or Divestiture Purges**
  * *The Problem:* You sold off a subsidiary business unit and need to purge all their users from your tenant immediately.
  * *The Solution:* Use the `DomainIs` API guard to delete thousands of users, but *only* if their Secondary User ID matches the divested company's domain.
* **Use Case 4: Sync Tool Migration**
  * *The Problem:* You are moving from an old sync agent to a new one, but need to wipe out the old agent's accounts first.
  * *The Solution:* Use `-RequireCreatedBy` to strictly delete only the users stamped with the old agent's name, leaving manually created admins untouched.

---

## 🛠️ Parameters Reference

### 1. Source Data (Required)
| Parameter | Valid Values | Description |
| :--- | :--- | :--- |
| `-SourceType` | `csv` | The format of your input data. Must contain an `Email` column. |
| `-SourceValue` | `<File Path>` | The path to your input CSV file. |

### 2. Action Mode (Required)
| Parameter | Valid Values | Description |
| :--- | :--- | :--- |
| `-SyncMode` | `create`, `overwrite`, `delete` | **create:** Adds new, skips existing.<br>**overwrite:** Updates existing.<br>**delete:** Triggers removal workflow. |

### 3. Deletion Criteria (Required if `-SyncMode delete`)
| Parameter | Valid Values | Description |
| :--- | :--- | :--- |
| `-DeleteBy` | `csv`, `inactivity` | `csv`: Attempts to delete every user.<br>`inactivity`: Filters based on `Last Activity Date` first. |
| `-InactivityPeriod`| e.g., `90D`, `6M`, `2Y`, `BLANK` | Required if `-DeleteBy inactivity`. Targets inactivity duration. |
| `-DeleteAction` | `list`, `delete` | **list (Default):** Performs a True Simulation Dry-Run.<br>**delete:** Executes API deletions. |

### 4. Safety Guards (Optional but Highly Recommended)
| Parameter | Valid Values | Description |
| :--- | :--- | :--- |
| `-ADSafetyLock` | `[switch]` | Connects to AD. Aborts deletion if user is Active or if AD is down. |
| `-ExtraADAttributes`| `<string[]>` | Array of extra AD attributes to check (e.g., `"proxyAddresses", "EmployeeID"`). By default it searches Email and UserPrincipalName |
| `-RequireCreatedBy`| `<string>` | Targets only users created by a specific entity (e.g., `Directory Sync`). |
| `-ExcludeCreatedBy`| `<string>` | Protects users created by a specific entity (e.g., `slack@System`). |
| `-GuardSecondaryUserCondition` | `Exists`, `Contains`, `DomainIs` | How to evaluate the user's `Secondary User ID` field. |
| `-GuardSecondaryUserValue`| `<string>` | The value to check against (e.g., `CORP`). |

### 5 Multi-Forest Support (`ad_forests.json`)

When using the `-ADSafetyLock` switch in an environment with multiple Active Directory domains or forests, you need to define those domains in a file named `ad_forests.json`. This file must be located in the same directory as the script.

**File Format & Requirements:**
Create a simple JSON array containing the FQDNs of your domains or specific Domain Controllers. 

Example `ad_forests.json`:
```json
[
    "corp.example.com",
    "emea.example.com",
    "casbteamlabs.pcslabs.org"
]```
---

## Usage Examples

### 1. DRY RUN: Find Inactive Users (Safest Starting Point)
Reads a report, finds users inactive for over 28 Months, checks AD, and generates a simulation report showing who *would* be deleted. No data is altered.
```powershell
.\UserSync.ps1 -SourceType csv -SourceValue ".\cloudsoc_export.csv" -SyncMode delete -DeleteBy inactivity -InactivityPeriod 28M -DeleteAction list -ADSafetyLock

```

### 2. EXECUTE: Safe Deletion using Custom AD Attributes

Deletes users inactive for 6 months, but ensures they are disabled in AD by scanning their `EmailAddress`, `UserPrincipalName`, AND `proxyAddresses`.

```powershell
.\UserSync.ps1 -SourceType csv -SourceValue ".\export.csv" -SyncMode delete -DeleteBy inactivity -InactivityPeriod 6M -DeleteAction delete -ADSafetyLock -ExtraADAttributes "proxyAddresses"

```

### 3. EXECUTE: Targeted Domain Purge (Divestiture)

Deletes users straight from a CSV list, but **only** if their CloudSOC Secondary User ID belongs to the "GUEST" domain (e.g., `GUEST\jdoe`). Protects users created by "Admin".

```powershell
.\UserSync.ps1 -SourceType csv -SourceValue ".\cleanup.csv" -SyncMode delete -DeleteBy csv -DeleteAction delete -ExcludeCreatedBy "Admin" -GuardSecondaryUserCondition DomainIs -GuardSecondaryUserValue "GUEST"

```

### 4. EXECUTE: Strict Orphan Cleanup (Sync Drift)

Deletes users inactive for 90 days, **BUT** applies three strict rules:

1. They must be disabled/missing in Active Directory (`-ADSafetyLock`).
2. They must have been created by the automated sync tool (`-RequireCreatedBy`).
3. Limit the run to just the first 10 users to safely test (`-FirstN`).

```powershell
.\UserSync.ps1 -SourceType csv -SourceValue ".\users.csv" -SyncMode delete -DeleteBy inactivity -InactivityPeriod 90D -DeleteAction delete -ADSafetyLock -RequireCreatedBy "AutoSyncAgent" -FirstN 10

```

### 5. EXECUTE: Complex Orphan Cleanup (Directory Sync + Secondary ID + AD Check)
Deletes users directly from a CSV list, but strictly applies a three-layer combination lock: the account must have been originally created by "Directory Sync", it must have a populated Secondary User ID, and the user must be disabled or completely missing from your local Active Directory.
```powershell
.\UserSync.ps1 -SourceType csv -SourceValue ".\orphans.csv" -SyncMode delete -DeleteBy csv -DeleteAction delete -ADSafetyLock -RequireCreatedBy "Directory Sync" -GuardSecondaryUserCondition Exists
```

### 6. EXECUTE: Cross-Domain Cleanup (Directory Sync + Domain Contains + AD Check)
Deletes users directly from a CSV list, but strictly enforces three conditions: the account must have been provisioned by "Directory Sync", the Secondary User ID must contain the string "XYZ" (e.g., `XYZ\username` or `username@XYZ.com`), and the user must be disabled or completely missing from Active Directory.
```powershell
.\UserSync.ps1 -SourceType csv -SourceValue ".\domain_cleanup.csv" -SyncMode delete -DeleteBy csv -DeleteAction delete -ADSafetyLock -RequireCreatedBy "Directory Sync" -GuardSecondaryUserCondition Contains -GuardSecondaryUserValue "XYZ"
```
---

## 🐛 Troubleshooting & Deep Trace Logging

If a user is not deleting as expected or API calls are failing, enable deep trace logging to see the exact HTTP requests and AD filter strings.

**1. Enable Global Debugging:**
Run this in your console *before* executing the script to force all debug logs to display without pausing the script:

```powershell
$DebugPreference = 'Continue'

```

**2. Run with Stream Redirection (Recommended for deep analysis):**
To save the massive flood of yellow debug text to a log file instead of your screen, redirect Stream `5` (the Debug stream) to a text file:

```powershell
.\UserSync.ps1 -SourceType csv -SourceValue ".\test.csv" -SyncMode delete -DeleteBy csv -DeleteAction list -ADSafetyLock -Debug 5>> ".\debug_trace.log"

```

**3. Turn Off Debugging:**
When troubleshooting is complete, return your console to normal silent mode:

```powershell
$DebugPreference = 'SilentlyContinue'

```

---

## 📄 Interpreting the Final Report

After every run, a file named `Report_UserSync_[Mode]_[Timestamp].csv` is generated.

### Status Outcomes:

* **DryRun** - You used `-DeleteAction list`. No changes were made, but the user passed all safety checks.
* **Skipped (Does not exist in CloudSOC)** - The user was not found via the API lookup.
* **Skipped (Active in AD)** - The AD Safety Lock caught an active employee and protected them.
* **Skipped (AD Connection Error)** - The script Failed-Closed because Active Directory was unreachable.
* **Skipped (Guard: CreatedBy/SecondaryID)** - The account was protected by your API Guard rules.
* **Success** - The user was safely deleted.
* **Failed** - An API error occurred (e.g., 500 Internal Server Error).

### The `CreatedBy` Column:

This column displays the entity that originally provisioned the user in CloudSOC. It is natively populated via the API identity lookup before any deletion attempt occurs, ensuring you have a full audit trail even if the user is skipped.

```

```