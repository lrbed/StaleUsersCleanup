<# 
  UserSync Orchestrator v4.3 (API Guards Edition)
#>

[CmdletBinding(DefaultParameterSetName = "SyncSet")]
param (
    # --- 1. WHAT DATA? ---
    [Parameter(Mandatory, Position = 0)][ValidateSet("csv", "adgroup")][string]$SourceType,
    [Parameter(Mandatory, Position = 1)][string]$SourceValue,

    # --- 2. WHAT ACTION? ---
    [Parameter(Mandatory)][ValidateSet("create", "overwrite", "delete")][string]$SyncMode,

    # --- 3. DELETION SPECIFICS & GUARDS ---
    [Parameter(Mandatory, ParameterSetName = "DeleteSet")][ValidateSet("csv","inactivity")][string]$DeleteBy,
    [Parameter(ParameterSetName = "DeleteSet")][ValidateSet("list","delete")][string]$DeleteAction = "list",
    [Parameter(ParameterSetName = "DeleteSet")][string]$InactivityPeriod,
    [Parameter(ParameterSetName = "DeleteSet")][switch]$ADSafetyLock, 
    
    # NEW API GUARDS (Optional)
    [Parameter(ParameterSetName = "DeleteSet")][string]$ExcludeCreatedBy,
    [Parameter(ParameterSetName = "DeleteSet")][string]$RequireCreatedBy,
    [Parameter(ParameterSetName = "DeleteSet")][ValidateSet("Exists", "Contains", "DomainIs")][string]$GuardSecondaryUserCondition,
    [Parameter(ParameterSetName = "DeleteSet")][string]$GuardSecondaryUserValue,

    # --- 4. GLOBAL SETTINGS ---
    [string]$LogPath,
    [int]$FirstN = 0,
    [string]$ConfigFile = "config.json"
)

$ModulePath = Join-Path $PSScriptRoot "Cloudsoc.psm1"
if (Test-Path $ModulePath) { Import-Module $ModulePath -Force } else { throw "CRITICAL: Cloudsoc.psm1 not found." }

if (-not (Test-Requirements -ConfigFile $ConfigFile -SourceType $SourceType -SourceValue $SourceValue -ADSafetyLock $ADSafetyLock)) { exit 1 }
$config = Import-Config -ConfigFile $ConfigFile

$FinalReport = @()

if ($SyncMode -eq "delete") {
    try {
        $candidates = switch ($DeleteBy) {
            "inactivity" { Select-UsersForDeletionByInactivity -FilePath $SourceValue -InactivityPeriod $InactivityPeriod }
            "csv"        { Import-Csv $SourceValue }
        }
        
        $FinalReport = Remove-CloudSOCUsers -Candidates $candidates `
                                            -Config $config `
                                            -Action $DeleteAction `
                                            -OutputFile $LogPath `
                                            -Limit $FirstN `
                                            -ADSafetyLock $ADSafetyLock `
                                            -ExcludeCreatedBy $ExcludeCreatedBy `
                                            -RequireCreatedBy $RequireCreatedBy `
                                            -GuardSecondaryUserCondition $GuardSecondaryUserCondition `
                                            -GuardSecondaryUserValue $GuardSecondaryUserValue
    } catch { Write-Host "Execution Error: $($_.Exception.Message)" -ForegroundColor Red }
} 
else {
    try {
        if ($SourceType -eq "csv") {
            $users = Import-Csv $SourceValue
            $FinalReport = Sync-Users -Users $users -Config $config -Mode $SyncMode -OutputFile $LogPath
        }
    } catch { Write-Host "Sync Error: $($_.Exception.Message)" -ForegroundColor Red }
}

if ($FinalReport.Count -gt 0) {
    Write-Host "`n=== FINAL EXECUTION REPORT ===" -ForegroundColor Cyan
    $FinalReport | Select-Object User, Action, Status, Reason, Context | Format-Table -AutoSize
    
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $ReportFile = "Report_UserSync_${SyncMode}_${timestamp}.csv"
    $FinalReport | Export-Csv -Path $ReportFile -NoTypeInformation
    Write-Host "Full detailed report saved to: $ReportFile" -ForegroundColor Green
} else { Write-Host "No actions performed." -ForegroundColor Yellow }