<# 
  Version: 4.7.1 (SRE Error Handling + Deep Debug Tracing)
  Description: Core Logic Module for CloudSOC UserSync.
#>

function Show-Help { Write-Host "CloudSOC UserSync Module Help" -ForegroundColor Yellow }

function Test-Requirements {
    [CmdletBinding()]
    param ($ConfigFile, $SourceType, $SourceValue, $ADSafetyLock)
    if (-not (Test-Path $ConfigFile)) { Write-Error "CRITICAL: Config file '$ConfigFile' not found."; return $false }
    if ($SourceType -ne "csv" -or $ADSafetyLock) {
        if (-not (Get-Module -ListAvailable ActiveDirectory)) { Write-Error "CRITICAL: Active Directory module is missing."; return $false }
        if ($ADSafetyLock -and -not (Test-Path "ad_forests.json")) { Write-Warning "Safety Lock ON: 'ad_forests.json' missing. Defaulting to local." }
    }
    return (![string]::IsNullOrWhiteSpace($SourceValue))
}

function Import-Config {
    [CmdletBinding()]
    param ([Parameter(Mandatory)][string]$ConfigFile)
    $APIAttributes = Get-Content $ConfigFile | ConvertFrom-Json
    $forests = @()
    if (Test-Path "ad_forests.json") { try { $forests = Get-Content "ad_forests.json" -Raw | ConvertFrom-Json } catch { } }
    $psVersion = $PSVersionTable.PSVersion.ToString()
    $localIp = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notmatch 'Loopback|Virtual' } | Select-Object -First 1).IPAddress
    $authInfo = [System.Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes(("{0}:{1}" -f $APIAttributes.key_id, $APIAttributes.key_secret)))
    
    return @{
        "server"  = $APIAttributes.api_server; "tenant" = $APIAttributes.tenant; "ad_forests" = $forests
        "metadata" = @{ "PSVersion" = $psVersion; "SourceIP" = $localIp }
        "headers" = @{ "Authorization" = "Basic $authInfo"; "X-Elastica-Dbname-Resolved" = "True"; "User-Agent" = "PowerShell/$psVersion"; "tenant" = $APIAttributes.tenant; "Content-Type" = "application/json" }
    }
}

function New-ReportEntry {
    param($User, $Action, $Status, $Reason, $Context = "-", $CreatedBy = "-")
    return [pscustomobject]@{ Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"; User = $User; Action = $Action; Status = $Status; Reason = $Reason; Context = $Context; CreatedBy = $CreatedBy }
}

function Write-OutputChannel {
    param($Message, $Color = "White", $OutputFile = $null)
    Write-Host $Message -ForegroundColor $Color
    if ($OutputFile) { "[$(Get-Date -Format 'HH:mm:ss')] $Message" | Out-File -FilePath $OutputFile -Append }
}

function Get-ADUserSafetyStatus {
    [CmdletBinding()]
    param(
        [string]$Email, 
        [array]$ForestList,
        [string[]]$ExtraADAttributes = @()
    )
    if (-not $ForestList -or $ForestList.Count -eq 0) { $ForestList = @($null) }
    
    $safeEmail = $Email -replace "'", "''"
    $filterConditions = New-Object System.Collections.Generic.List[string]
    $filterConditions.Add("EmailAddress -eq '$safeEmail'")
    $filterConditions.Add("UserPrincipalName -eq '$safeEmail'")
    
    foreach ($attr in $ExtraADAttributes) {
        if (-not [string]::IsNullOrWhiteSpace($attr)) { $filterConditions.Add("$attr -eq '$safeEmail'") }
    }
    
    $finalFilter = $filterConditions -join " -or "
    $propertiesToFetch = @("Enabled", "EmailAddress", "UserPrincipalName")
    foreach ($attr in $ExtraADAttributes) {
        if (-not [string]::IsNullOrWhiteSpace($attr) -and $attr -notin $propertiesToFetch) { $propertiesToFetch += $attr }
    }

    Write-Debug "[AD-CHECK] Verifying AD status for: $Email"
    Write-Debug "[AD-CHECK] Constructed Filter: $finalFilter"

    foreach ($server in $ForestList) {
        $target = if ($server) { "$($server):3268" } else { $null }
        $forestName = if ($server) { $server } else { "LocalDomain" }
        Write-Debug "[AD-CHECK] Querying forest: $forestName"

        try {
            $params = @{ Filter = $finalFilter; Properties = $propertiesToFetch; ErrorAction = "Stop" }
            if ($target) { $params["Server"] = $target }
            
            $adUsers = Get-ADUser @params
            
            if ($null -ne $adUsers) {
                $isActive = @($adUsers) | Where-Object { $_.Enabled -eq $true }
                if ($isActive) { 
                    Write-Debug "[AD-CHECK] Match found and is ACTIVE in $forestName"
                    return @{ Status="Active"; Forest=$forestName } 
                } else {
                    Write-Debug "[AD-CHECK] Match found, but they are DISABLED in $forestName"
                    return @{ Status="Disabled"; Forest=$forestName }
                }
            } else { Write-Debug "[AD-CHECK] User not found in $forestName." }
        } catch {
            # FAIL CLOSED: Return error instead of pretending user isn't found
            Write-Debug "[AD-CHECK] Critical Error querying $($forestName): $($_.Exception.Message)"
            return @{ Status="Error"; Forest=$forestName; Details=$_.Exception.Message }
        }
    }
    
    Write-Debug "[AD-CHECK] User not found in any AD forest."
    return @{ Status="NotFound"; Forest="-" }
}

function Invoke-CloudSOCRequest {
    [CmdletBinding()]
    param($Method, $Uri, $Headers, $Body = $null, $OutputFile = $null)
    
    Write-Debug "[HTTP] $Method $Uri"
    if ($Body) { Write-Debug "[BODY] $Body" }

    $retryCount = 0; $maxRetries = 5; $baseWaitSeconds = 15
    while ($retryCount -le $maxRetries) {
        try {
            $params = @{ Uri = $Uri; Method = $Method; Headers = $Headers; UseBasicParsing = $true; ErrorAction = "Stop" }
            if ($Body) { $params.Body = $Body; $params.ContentType = "application/json" }
            
            $res = Invoke-WebRequest @params
            Write-Debug "[RESPONSE] Status: $($res.StatusCode)"
            return $res
        } catch {
            $status = if ($_.Exception.Response) { $_.Exception.Response.StatusCode.value__ } else { "Unknown" }
            Write-Debug "[ERROR] HTTP $status - $($_.Exception.Message)"
            
            # SMART EXPONENTIAL BACKOFF ON 429
            if ($status -eq 429 -and $retryCount -lt $maxRetries) {
                $retryCount++
                $wait = [math]::Pow(2, $retryCount - 1) * $baseWaitSeconds # 15s, 30s, 60s...
                Write-OutputChannel "WARNING: API Threshold Hit (429). Smart Backoff: Retrying in $($wait)s (Attempt $retryCount of $maxRetries)" "Yellow" $OutputFile
                Start-Sleep -Seconds $wait; continue
            }
            throw $_
        }
    }
}

function Resolve-CloudSOCUuidByEmail {
    [CmdletBinding()]
    param($Email, $Config, $OutputFile)
    
    $cleanEmail = $Email.Trim().ToLower()
    $url = "{0}/{1}/api/admin/v1/user_management/?email={2}" -f $Config.server.TrimEnd('/'), $Config.tenant.Trim('/'), [uri]::EscapeDataString($cleanEmail)
    $h = $Config.headers.Clone(); $h["Content-Type"] = "application/json" 

    Write-Debug "[LOOKUP] Searching for email: $cleanEmail"
    
    try {
        $res = Invoke-CloudSOCRequest -Method GET -Uri $url -Headers $h -OutputFile $OutputFile
        $json = $res.Content | ConvertFrom-Json
        
        Write-Debug "[LOOKUP] Found $($json.meta.total_count) objects in CloudSOC"

        if ($json.meta.total_count -gt 0) {
            $userObj = $json.objects[0]
            Write-Debug "[LOOKUP] Top match email returned by API: $($userObj.email)"

            if ($userObj.email.ToLower() -eq $cleanEmail) {
                $uuid = if ($userObj.resource_uri -match '/user_management/([^/]+)/?') { $matches[1] } else { $null }
                Write-Debug "[LOOKUP] Exact match confirmed. Resolved UUID: $uuid"
                return [pscustomobject]@{ Uuid = $uuid; CreatedBy = $userObj.created_by; SecondaryUserId = $userObj.secondary_user_id; HasError = $false }
            } else { Write-Debug "[LOOKUP] API returned a result, but email '$($userObj.email)' did not match '$cleanEmail' exactly." }
        }
        return [pscustomobject]@{ Uuid = $null; HasError = $false } 
    } catch { 
        # BUBBLE UP ERROR: Do not swallow API exceptions
        Write-Debug "[LOOKUP] API Exception: $($_.Exception.Message)" 
        return [pscustomobject]@{ Uuid = $null; HasError = $true; ErrorMessage = $_.Exception.Message }
    }
}

function Resolve-EmailColumnName {
    param($Row)
    $cleanHeaders = @($Row.PSObject.Properties.Name | ForEach-Object { $_ -replace '\s+', ' ' })
    for ($i = 0; $i -lt $cleanHeaders.Count; $i++) { if ($cleanHeaders[$i].Trim() -match '^(Email|UserId|User ID)$') { return $Row.PSObject.Properties.Name[$i] } }
    return $null
}

function Classify-LastActivityDate {
    param($Row)
    $targetHeader = $Row.PSObject.Properties.Name | Where-Object { ($_ -replace '\s+', ' ') -match 'Last Activity Date|LastActivityDate|last_activity_date' } | Select-Object -First 1
    if (-not $targetHeader -or [string]::IsNullOrWhiteSpace($Row.$targetHeader)) { return @{ Status = 'blank'; Date = $null } }
    $raw = ([string]$Row.$targetHeader -replace '\s+', ' ').Trim()
    try { return @{ Status = 'valid'; Date = ([datetime]$raw).Date } }
    catch { try { return @{ Status = 'valid'; Date = ([datetime]($raw -replace '^[a-zA-Z]+,\s*', '')).Date } } catch { return @{ Status = 'invalid'; Date = $null } } }
}

function Select-UsersForDeletionByInactivity {
    [CmdletBinding()]
    param($FilePath, $InactivityPeriod)
    Write-Debug "[SELECT] Parsing file: $FilePath with period: $InactivityPeriod"

    $p = $InactivityPeriod.ToUpper().Trim()
    $mode = if ($p -eq 'BLANK') { @{ Type = 'BlankOnly' } } else {
        $num = [int]($p -replace '[^0-9]', ''); $unit = $p -replace '[^A-Z]', ''
        $dt = switch($unit){ 'D'{(Get-Date).AddDays(-$num)} 'M'{(Get-Date).AddMonths(-$num)} 'Y'{(Get-Date).AddYears(-$num)} }
        @{ Type = 'Threshold'; Threshold = $dt.Date }
    }
    $list = New-Object System.Collections.Generic.List[pscustomobject]
    foreach ($r in Import-Csv $FilePath) {
        $col = Resolve-EmailColumnName -Row $r; if (-not $col) { continue }
        $class = Classify-LastActivityDate -Row $r
        $match = if ($mode.Type -eq 'BlankOnly') { $class.Status -eq 'blank' } else { $class.Status -eq 'valid' -and $class.Date -lt $mode.Threshold }
        if ($match) { [void]$list.Add([pscustomobject]@{ Email=$r.$col; Reason=$class.Status }) }
    }
    Write-Debug "[SELECT] Found $($list.Count) candidates matching inactivity criteria."
    return @($list)
}

function New-CloudSOCUser {
    [CmdletBinding()]
    param ($User, $Config, $Action, $OutputFile)
    $url = "{0}/{1}/api/admin/v1/user_management/" -f $Config.server.TrimEnd('/'), $Config.tenant.Trim('/')
    $Payload = @{ "email" = $User.email; "first_name" = $User.first_name; "last_name" = $User.last_name; "is_active" = $true }
    $Method = if ($Action -eq "Update") { "PATCH" } else { "POST" }
    
    if ($Action -eq "Update") { 
        [void]$Payload.Remove("email")
        $lookup = Resolve-CloudSOCUuidByEmail -Email $User.email -Config $Config -OutputFile $OutputFile
        if ($lookup -and $lookup.Uuid) { $url = "$url$($lookup.Uuid)/" } else { $Method = "POST"; $Action = "Create (Fallback)" }
    }
    try {
        $res = Invoke-CloudSOCRequest -Method $Method -Uri $url -Headers $Config.headers -Body ($Payload | ConvertTo-Json) -OutputFile $OutputFile
        if ($res.StatusCode -in 200..204) { return New-ReportEntry -User $User.email -Action $Action -Status "Success" -Reason "API Request Completed" }
    } catch { return New-ReportEntry -User $User.email -Action $Action -Status "Failed" -Reason "API Error: $($_.Exception.Message)" }
    return New-ReportEntry -User $User.email -Action $Action -Status "Failed" -Reason "Unknown API Response"
}

function Sync-Users {
    [CmdletBinding()]
    param ($Users, $Config, $Mode, $OutputFile)
    $Report = New-Object System.Collections.Generic.List[pscustomobject]
    Write-Debug "[SYNC] Starting Sync Flow (Mode: $Mode)"
    foreach ($user in $Users) {
        if (-not $user.email) { continue }
        $lookup = Resolve-CloudSOCUuidByEmail -Email $user.email -Config $Config -OutputFile $OutputFile
        
        if ($lookup.HasError) {
            $Report.Add((New-ReportEntry -User $user.email -Action "Create" -Status "Failed" -Reason "API Error: $($lookup.ErrorMessage)"))
            continue
        }

        $uuid = if ($lookup) { $lookup.Uuid } else { $null }
        if ($Mode -eq "create" -and -not $uuid) { $Report.Add((New-CloudSOCUser -User $user -Config $Config -Action "Create" -OutputFile $OutputFile)) } 
        elseif ($Mode -eq "overwrite") { $Report.Add((New-CloudSOCUser -User $user -Config $Config -Action (if($uuid){"Update"}else{"Create"}) -OutputFile $OutputFile)) } 
        elseif ($Mode -eq "create" -and $uuid) { $Report.Add((New-ReportEntry -User $user.email -Action "Create" -Status "Skipped" -Reason "Already exists" -Context $uuid)) }
    }
    return @($Report)
}

function Remove-CloudSOCUsers {
    [CmdletBinding()]
    param(
        $Candidates, $Config, $Action, $OutputFile, $Limit = 0, $FullCommand = "Unknown", $ADSafetyLock = $false, 
        $ExcludeCreatedBy, $RequireCreatedBy, $GuardSecondaryUserCondition, $GuardSecondaryUserValue,
        [string[]]$ExtraADAttributes = @()
    )
    
    Write-Debug "[DELETE-FLOW] Starting deletion workflow. Total candidates: $($Candidates.Count)"
    $Report = New-Object System.Collections.Generic.List[pscustomobject]
    
    if ($Limit -gt 0) { $FinalList = @($Candidates | Select-Object -First $Limit) } 
    else { $FinalList = @($Candidates) }

    foreach ($c in $FinalList) {
        Write-Debug "[DELETE-FLOW] Processing user: $($c.Email)"
        
        # 1. API LOOKUP (With Error Handling)
        $lookup = Resolve-CloudSOCUuidByEmail -Email $c.Email -Config $Config -OutputFile $OutputFile
        
        if ($lookup.HasError) {
            Write-Debug "[DELETE-FLOW] API Lookup Failed due to network/auth error."
            $Report.Add((New-ReportEntry -User $c.Email -Action "Delete" -Status "Failed" -Reason "API Lookup Error: $($lookup.ErrorMessage)"))
            continue
        }
        if (-not $lookup.Uuid) {
            Write-Debug "[DELETE-FLOW] Identity Lookup returned no match."
            $Report.Add((New-ReportEntry -User $c.Email -Action "Delete" -Status "Skipped" -Reason "Does not exist in CloudSOC"))
            continue
        }

        $uuid = $lookup.Uuid
        $createdBy = if ([string]::IsNullOrWhiteSpace($lookup.CreatedBy)) { "Unknown/None" } else { $lookup.CreatedBy }
        $secUserId = $lookup.SecondaryUserId
        Write-Debug "[DELETE-FLOW] Extracted API Data -> UUID: $uuid | CreatedBy: $createdBy | SecUserID: $secUserId"

        # 2. AD SAFETY CHECK (With Fail-Closed Handling)
        $forestFound = "-"
        if ($ADSafetyLock) {
            Write-Debug "[DELETE-FLOW] AD Safety Lock is ON. Triggering check."
            $adResult = Get-ADUserSafetyStatus -Email $c.Email -ForestList $Config.ad_forests -ExtraADAttributes $ExtraADAttributes
            
            if ($adResult.Status -eq "Error") {
                Write-Debug "[DELETE-FLOW] AD Failure. Aborting to fail-closed."
                $Report.Add((New-ReportEntry -User $c.Email -Action "Delete" -Status "Skipped" -Reason "AD Connection Error: $($adResult.Details)" -Context "Network/Auth Issue" -CreatedBy $createdBy))
                continue
            }
            if ($adResult.Status -eq "Active") {
                Write-Debug "[DELETE-FLOW] Aborting: User is Active in AD."
                $Report.Add((New-ReportEntry -User $c.Email -Action "Delete" -Status "Skipped" -Reason "Active in AD" -Context "Found in $($adResult.Forest)" -CreatedBy $createdBy))
                continue
            }
            $forestFound = if ($adResult.Status -eq "Disabled") { "Disabled in $($adResult.Forest)" } else { "Not Found in AD" }
            Write-Debug "[DELETE-FLOW] AD Check Passed. Status: $forestFound"
        }

        # 3. API GUARDS
        if ($ExcludeCreatedBy -and $createdBy -eq $ExcludeCreatedBy) {
            Write-Debug "[DELETE-FLOW] Guard Triggered: CreatedBy matches exclusion ($ExcludeCreatedBy)."
            $Report.Add((New-ReportEntry -User $c.Email -Action "Delete" -Status "Skipped" -Reason "Guard: CreatedBy matched Exclude filter ($ExcludeCreatedBy)" -Context $uuid -CreatedBy $createdBy)); continue
        }
        if ($RequireCreatedBy -and $createdBy -ne $RequireCreatedBy) {
            Write-Debug "[DELETE-FLOW] Guard Triggered: CreatedBy ($createdBy) does not match requirement ($RequireCreatedBy)."
            $Report.Add((New-ReportEntry -User $c.Email -Action "Delete" -Status "Skipped" -Reason "Guard: CreatedBy didn't match Require filter ($RequireCreatedBy)" -Context $uuid -CreatedBy $createdBy)); continue
        }
        if ($GuardSecondaryUserCondition) {
            $secMatch = $true; $failReason = ""
            Write-Debug "[DELETE-FLOW] Evaluating SecUserID Guard: $GuardSecondaryUserCondition vs '$GuardSecondaryUserValue'"
            switch ($GuardSecondaryUserCondition) {
                "Exists" { if ([string]::IsNullOrWhiteSpace($secUserId)) { $secMatch = $false; $failReason = "SecondaryUserId is empty" } }
                "Contains" { if ($secUserId -notmatch [regex]::Escape($GuardSecondaryUserValue)) { $secMatch = $false; $failReason = "SecondaryUserId does not contain '$GuardSecondaryUserValue'" } }
                "DomainIs" {
                    $domainPart = if ($secUserId -match '\\') { $secUserId.Split('\')[0] } else { "" }
                    if ($domainPart -ne $GuardSecondaryUserValue) { $secMatch = $false; $failReason = "SecondaryUserId Domain ('$domainPart') is not '$GuardSecondaryUserValue'" }
                }
            }
            if (-not $secMatch) {
                Write-Debug "[DELETE-FLOW] Guard Triggered: $failReason"
                $Report.Add((New-ReportEntry -User $c.Email -Action "Delete" -Status "Skipped" -Reason "Guard: $failReason" -Context $uuid -CreatedBy $createdBy)); continue
            }
        }

        # 4. EXECUTION PHASE
        if ($Action -eq 'delete') {
            try {
                Write-Debug "[DELETE-FLOW] All guards passed. Executing API DELETE."
                $uri = "{0}/{1}/api/admin/v1/user_management/{2}/" -f $Config.server.TrimEnd('/'), $Config.tenant.Trim('/'), $uuid
                $res = Invoke-CloudSOCRequest -Method DELETE -Uri $uri -Headers $Config.headers -OutputFile $OutputFile
                if ($res.StatusCode -in 200..204) { 
                    Write-Debug "[DELETE-FLOW] Delete successful."
                    $Report.Add((New-ReportEntry -User $c.Email -Action "Delete" -Status "Success" -Reason "API 200 OK" -Context "$uuid ($forestFound)" -CreatedBy $createdBy)) 
                }
            } catch { 
                Write-Debug "[DELETE-FLOW] API Delete Failed: $($_.Exception.Message)"
                $Report.Add((New-ReportEntry -User $c.Email -Action "Delete" -Status "Failed" -Reason "API Error: $($_.Exception.Message)" -Context $uuid -CreatedBy $createdBy)) 
            }
        } else {
            Write-Debug "[DELETE-FLOW] Action is 'list'. Simulating delete for $uuid."
            $Report.Add((New-ReportEntry -User $c.Email -Action "Delete" -Status "DryRun" -Reason "Would be deleted (All guards passed)" -Context "$uuid ($forestFound)" -CreatedBy $createdBy)) 
        }
    }
    return @($Report)
}