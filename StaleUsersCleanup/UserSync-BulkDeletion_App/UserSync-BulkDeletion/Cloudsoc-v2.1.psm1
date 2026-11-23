<# 
  Version: 2.1
  Created: 2026-02-05 11:45:00
  Description: Backbone module with Explicit PowerShell User-Agent.
  
  CHANGELOG:
  - 2.1: Reverted to custom PowerShell User-Agent.
  - 2.1: Included standard placeholders for Browser/Location for future-proofing.
  - 2.0: Removed X-Script-Metadata header.
#>

function Show-Help {
    Write-Host "CloudSOC UserSync Module Help" -ForegroundColor Yellow
}

function Test-Requirements {
    param ($ConfigFile, $SourceType, $SourceValue)
    if (-not (Test-Path $ConfigFile)) { Write-Error "Missing config: $ConfigFile"; return $false }
    return (![string]::IsNullOrWhiteSpace($SourceValue))
}

function Import-Config {
    param ([Parameter(Mandatory)][string]$ConfigFile)
    $APIAttributes = Get-Content $ConfigFile | ConvertFrom-Json
    
    $psVersion = $PSVersionTable.PSVersion.ToString()
    $localIp = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notmatch 'Loopback|Virtual' } | Select-Object -First 1).IPAddress
    
    # Explicit PowerShell User-Agent with standard attribute placeholders
    # Structure: Application/Version (Platform; Browser/Unknown; Location/IP)
    $userAgent = "PowerShell/$psVersion (Windows NT 10.0; Browser/Script; SourceLocation/$localIp)"

    $authInfo = [System.Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes(("{0}:{1}" -f $APIAttributes.key_id, $APIAttributes.key_secret)))
    
    return @@{
        "server"  = $APIAttributes.api_server
        "tenant"  = $APIAttributes.tenant
        "metadata" = @@{ "PSVersion" = $psVersion; "SourceIP" = $localIp }
        "headers" = @@{
            "Authorization"              = "Basic $authInfo"
            "X-Elastica-Dbname-Resolved" = "True"
            "User-Agent"                 = $userAgent
            "tenant"                     = $APIAttributes.tenant
            "Content-Type"               = "application/json"
        }
    }
}

function Resolve-EmailColumnName {
    param($Row)
    $allHeaders = $Row.PSObject.Properties.Name
    $cleanHeaders = $allHeaders | ForEach-Object { $_ -replace '\s+', ' ' }
    $index = [array]::FindIndex($cleanHeaders, [Predicate[string]]{ param($s) $s.Trim() -match '^(Email|UserId|User ID)$' })
    if ($index -ge 0) { return $allHeaders[$index] } else { return $null }
}

function Classify-LastActivityDate {
    param($Row)
    $allHeaders = $Row.PSObject.Properties.Name
    $targetHeader = $allHeaders | Where-Object { ($_ -replace '\s+', ' ') -match 'Last Activity Date|LastActivityDate|last_activity_date' } | Select-Object -First 1
    if (-not $targetHeader -or [string]::IsNullOrWhiteSpace($Row.$targetHeader)) { return @@{ Status = 'blank'; Date = $null } }
    $raw = ([string]$Row.$targetHeader -replace '\s+', ' ').Trim()
    try { return @@{ Status = 'valid'; Date = ([datetime]$raw).Date } }
    catch {
        try { $cleanDate = $raw -replace '^[a-zA-Z]+,\s*', ''; return @@{ Status = 'valid'; Date = ([datetime]$cleanDate).Date } }
        catch { return @@{ Status = 'invalid'; Date = $null } }
    }
}

function Select-UsersForDeletionByInactivity {
    param($FilePath, $InactivityPeriod)
    $p = $InactivityPeriod.ToUpper().Trim()
    $mode = if ($p -eq 'BLANK') { @@{ Type = 'BlankOnly' } } else {
        $num = [int]($p -replace '[^0-9]', ''); $unit = $p -replace '[^A-Z]', ''
        $dt = switch($unit){ 'D'{(Get-Date).AddDays(-$num)} 'M'{(Get-Date).AddMonths(-$num)} 'Y'{(Get-Date).AddYears(-$num)} }
        @@{ Type = 'Threshold'; Threshold = $dt.Date }
    }
    $rows = Import-Csv $FilePath
    $list = New-Object System.Collections.Generic.List[pscustomobject]
    foreach ($r in $rows) {
        $col = Resolve-EmailColumnName -Row $r
        if (-not $col) { continue }
        $email = $r.$col
        $class = Classify-LastActivityDate -Row $r
        $match = if ($mode.Type -eq 'BlankOnly') { $class.Status -eq 'blank' } else { $class.Status -eq 'valid' -and $class.Date -lt $mode.Threshold }
        if ($match) { [void]$list.Add([pscustomobject]@{ Email=$email; Uuid=$null; Reason=$class.Status }) }
    }
    return $list
}

function Resolve-CloudSOCUuidByEmail {
    param($Email, $Config)
    $EncodedEmail = [uri]::EscapeDataString($Email.Trim())
    $url = "{0}/{1}/api/admin/v1/user_management/?email={2}" -f $Config.server.TrimEnd('/'), $Config.tenant.Trim('/'), $EncodedEmail
    $h = $Config.headers.Clone(); $h["Content-Type"] = "application/javascript"
    try {
        $res = Invoke-WebRequest -Uri $url -Method GET -Headers $h -UseBasicParsing -ErrorAction Stop
        $json = $res.Content | ConvertFrom-Json
        if ($json.meta.total_count -gt 0) {
            if ($json.objects[0].resource_uri -match '/user_management/([^/]+)/?') { return $matches[1] }
        }
    } catch {}
    return $null
}

function Remove-CloudSOCUsers {
    param($Candidates, $Config, $Action, $OutputFile, $Limit = 0, $FullCommand = "Unknown")
    $now = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    function Write-OutputChannel {
        param($Message, $Color = "White")
        Write-Host $Message -ForegroundColor $Color
        if ($OutputFile) { "[$now] $Message" | Out-File -FilePath $OutputFile -Append }
    }
    Write-OutputChannel "==========================================================" "Yellow"
    Write-OutputChannel "SESSION START: $now" "Yellow"
    Write-OutputChannel "AGENT IP:      $($Config.metadata.SourceIP)" "Gray"
    Write-OutputChannel "USER-AGENT:    $($Config.headers['User-Agent'])" "Gray"
    Write-OutputChannel "COMMAND:       $FullCommand" "Gray"
    Write-OutputChannel "==========================================================" "Yellow"

    if (-not $Candidates -or $Candidates.Count -eq 0) { Write-OutputChannel "No candidates found." "Cyan"; return }
    $FinalList = if ($Limit -gt 0) { $Candidates | Select-Object -First $Limit } else { $Candidates }
    
    if ($Action -eq 'list') {
        Write-OutputChannel "MODE: Dry Run (Listing Only)" "Cyan"
        $FinalList | Select-Object Email, Reason | Format-Table -AutoSize | Out-String | ForEach-Object { Write-OutputChannel $_ }
        return
    }

    $deleted = 0; $failed = 0
    foreach ($c in $FinalList) {
        $uuid = Resolve-CloudSOCUuidByEmail -Email $c.Email -Config $Config
        if ($uuid) {
            try {
                $uri = "{0}/{1}/api/admin/v1/user_management/{2}/" -f $Config.server.TrimEnd('/'), $Config.tenant.Trim('/'), $uuid
                $res = Invoke-WebRequest -Uri $uri -Method DELETE -Headers $Config.headers -UseBasicParsing
                if ($res.StatusCode -in 200..204) { Write-OutputChannel "SUCCESS: Deleted $($c.Email)" "Green"; $deleted++ }
            } catch { Write-OutputChannel "ERROR: $($c.Email) - $($_.Exception.Message)" "Red"; $failed++ }
        } else { Write-OutputChannel "SKIP: No UUID for $($c.Email)" "Yellow"; $failed++ }
    }
    Write-OutputChannel "==========================================================" "Yellow"
    Write-OutputChannel "SUMMARY: Attempted: $($FinalList.Count) | Success: $deleted | Failed: $failed" "Yellow"
}