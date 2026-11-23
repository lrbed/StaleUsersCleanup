<# 
  Version: 2.1
  Created: 2026-02-05 11:45:00
  Description: Orchestrator with Explicit PowerShell User-Agent.
#>

[CmdletBinding(DefaultParameterSetName = "SyncSet")]
param (
    [Parameter(Mandatory, Position = 0)][ValidateSet("csv")][string]$SourceType,
    [Parameter(Mandatory, Position = 1)][string]$SourceValue,
    [Parameter(Mandatory)][ValidateSet("delete")][string]$SyncMode,
    [Parameter(Mandatory, ParameterSetName = "DeleteSet")][ValidateSet("inactivity")][string]$DeleteBy,
    [Parameter(ParameterSetName = "DeleteSet")][ValidateSet("list","delete")][string]$DeleteAction = "list",
    [Parameter(ParameterSetName = "DeleteSet")][string]$InactivityPeriod,
    [string]$LogPath,
    [int]$FirstN = 0,
    [string]$ConfigFile = "config.json"
)

# Capture Command String (using -join for PS 5.1 compatibility)
$boundParams = $MyInvocation.BoundParameters.GetEnumerator() | ForEach-Object { "-$($_.Key) '$($_.Value)'" }
$fullCmd = ".\UserSync.ps1 " + ($boundParams -join " ")

$ModulePath = Join-Path $PSScriptRoot "Cloudsoc.psm1"
Import-Module $ModulePath -Force

if (-not (Test-Requirements -ConfigFile $ConfigFile -SourceType $SourceType -SourceValue $SourceValue)) { exit 1 }
$config = Import-Config -ConfigFile $ConfigFile

if ($SyncMode -eq "delete") {
    try {
        $candidates = if ($DeleteBy -eq "inactivity") { 
            Select-UsersForDeletionByInactivity -FilePath $SourceValue -InactivityPeriod $InactivityPeriod 
        }
        
        Remove-CloudSOCUsers -Candidates $candidates `
                             -Config $config `
                             -Action $DeleteAction `
                             -OutputFile $LogPath `
                             -Limit $FirstN `
                             -FullCommand $fullCmd
    } catch {
        Write-Host "Execution Error: $($_.Exception.Message)" -ForegroundColor Red
    }
}