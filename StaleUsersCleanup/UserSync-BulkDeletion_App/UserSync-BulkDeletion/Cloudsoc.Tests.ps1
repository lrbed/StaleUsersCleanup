<#
    Unit Tests for CloudSOC UserSync Module (v4.6.1 - Comprehensive)
    Coverage: Configuration, API Limits, Dynamic AD Filters, API-First Guards, Reporting
#>

Describe "CloudSOC Module Tests (Comprehensive Edition)" {
    
    BeforeAll {
        $RootPath = $PSScriptRoot; if (-not $RootPath) { $RootPath = $PWD.Path }
        $ModulePath = Join-Path $RootPath "Cloudsoc.psm1"
        if (-not (Test-Path $ModulePath)) { Throw "CRITICAL: Cloudsoc.psm1 not found at $ModulePath" }
        
        if (-not (Get-Command Get-ADUser -ErrorAction SilentlyContinue)) {
            function global:Get-ADUser { param($Filter, $Identity, $Properties, $Server, $ErrorAction) }
        }
        if (-not (Get-Command Get-Module -ErrorAction SilentlyContinue)) {
            function global:Get-Module { param($ListAvailable, $Name) }
        }

        Import-Module $ModulePath -Force
        
        $dummyConfig = @{ "api_server"="https://api.test"; "tenant"="123"; "key_id"="k"; "key_secret"="s" }
        $dummyConfig | ConvertTo-Json | Set-Content -Path "dummy.json"
        
        $dummyForests = @("forest1.com", "forest2.com")
        $dummyForests | ConvertTo-Json | Set-Content -Path "ad_forests.json"
        
        $fakeConfig = @{ server="https://api"; tenant="t"; headers=@{}; metadata=@{SourceIP="1.1"; PSVersion="5.1"}; ad_forests=@("forest1.com") }
    }

    AfterAll {
        if (Test-Path "dummy.json") { Remove-Item "dummy.json" -Force }
        if (Test-Path "ad_forests.json") { Remove-Item "ad_forests.json" -Force }
    }

    Context "1. Config & Requirements" {
        It "Loads AD Forests from external file" {
            Mock Get-NetIPAddress -MockWith { return [pscustomobject]@{ IPAddress="10.0"; InterfaceAlias="Eth" } } -ModuleName Cloudsoc
            $c = Import-Config -ConfigFile "dummy.json"
            $c.ad_forests.Count | Should -Be 2
        }
    }

    Context "2. Helpers & Selection" {
        It "Classifies valid, blank, and invalid dates correctly" {
            $valid = [pscustomobject]@{ "Last Activity Date" = "2023-01-01" }
            $blank = [pscustomobject]@{ "Last Activity Date" = "   " }
            (Classify-LastActivityDate -Row $valid).Status | Should -Be "valid"
            (Classify-LastActivityDate -Row $blank).Status | Should -Be "blank"
        }
    }

    Context "3. API Engine (Rate Limits)" {
        It "Retries on 429 and eventually succeeds" {
            Mock Start-Sleep -MockWith {} -ModuleName Cloudsoc
            $mock429 = [System.Net.WebException]::new("429", $null, [System.Net.WebExceptionStatus]::ProtocolError, $null)
            $mock429 | Add-Member -MemberType NoteProperty -Name Response -Value ([PSCustomObject]@{ StatusCode = [PSCustomObject]@{ value__ = 429 } }) -Force

            Mock Invoke-WebRequest -MockWith {
                $script:CallCount++
                if ($script:CallCount -le 2) { throw $mock429 }
                return [pscustomobject]@{ StatusCode = 200 }
            } -ModuleName Cloudsoc

            $script:CallCount = 0
            Invoke-CloudSOCRequest -Method GET -Uri "https://test" -Headers @{}
            $script:CallCount | Should -Be 3
        }
    }

    Context "4. AD Safety Engine & Filters" {
        It "Dynamically builds AD Filter with ExtraADAttributes" {
            Mock Get-ADUser -MockWith { return [pscustomobject]@{ Enabled = $true; FilterUsed = $Filter } } -ModuleName Cloudsoc
            $result = Get-ADUserSafetyStatus -Email "test@domain.com" -ForestList @("f1") -ExtraADAttributes @("EmployeeID", "proxyAddresses")
            
            $result.Status | Should -Be "Active"
            Assert-MockCalled Get-ADUser -ModuleName Cloudsoc -ParameterFilter { 
                $Filter -match "EmployeeID -eq 'test@domain.com'" -and $Filter -match "proxyAddresses -eq 'test@domain.com'"
            } -Exactly 1
        }
    }

    Context "5. Extraction Engine" {
        It "Extracts Uuid, CreatedBy, and SecondaryUserId correctly" {
             Mock Invoke-CloudSOCRequest -MockWith { 
                 return [pscustomobject]@{ Content = '{ "meta": {"total_count": 1}, "objects": [{"email":"e", "resource_uri": "/api/v1/user_management/999/", "created_by": "OktaSync", "secondary_user_id": "CORP\\jsmith"}] }' } 
             } -ModuleName Cloudsoc
             
             $result = Resolve-CloudSOCUuidByEmail -Email "e" -Config $fakeConfig
             $result.Uuid | Should -Be "999"
             $result.CreatedBy | Should -Be "OktaSync"
        }
    }

    Context "6. Sync-Users Flows" {
        It "Create Mode: Returns 'Success' Report Entry" {
            Mock Resolve-CloudSOCUuidByEmail -MockWith { return $null } -ModuleName Cloudsoc
            Mock Invoke-CloudSOCRequest -MockWith { return [pscustomobject]@{ StatusCode=201 } } -ModuleName Cloudsoc
            $users = @([pscustomobject]@{ Email="new@test.com"; first_name="N"; last_name="T" })
            $report = Sync-Users -Users $users -Config $fakeConfig -Mode "create"
            $report[0].Status | Should -Be "Success"
        }
    }

    Context "7. Base Deletion & AD Safety Lock (API-First Flow)" {
        
        It "Returns 'Skipped (Does not exist)' if API Lookup fails and bypasses AD check" {
            $c = @([pscustomobject]@{ Email="missing@test.com" })
            Mock Resolve-CloudSOCUuidByEmail -MockWith { return $null } -ModuleName Cloudsoc
            Mock Get-ADUserSafetyStatus -MockWith { return @{Status="Active"} } -ModuleName Cloudsoc # Should not be called
            
            $report = Remove-CloudSOCUsers -Candidates $c -Config $fakeConfig -Action "delete" -ADSafetyLock $true
            $report[0].Status | Should -Be "Skipped"
            $report[0].Reason | Should -Match "Does not exist in CloudSOC"
            Assert-MockCalled Get-ADUserSafetyStatus -ModuleName Cloudsoc -Times 0
        }

        It "Safety Lock: SKIPS deletion if User is ACTIVE in AD" {
            $c = @([pscustomobject]@{ Email="active@test.com" })
            Mock Resolve-CloudSOCUuidByEmail -MockWith { return [pscustomobject]@{ Uuid="123"; CreatedBy="Admin" } } -ModuleName Cloudsoc
            Mock Get-ADUserSafetyStatus -MockWith { return @{Status="Active"; Forest="local"} } -ModuleName Cloudsoc
            
            $report = Remove-CloudSOCUsers -Candidates $c -Config $fakeConfig -Action "delete" -ADSafetyLock $true
            $report[0].Status | Should -Be "Skipped"
            $report[0].Reason | Should -Match "Active in AD"
            $report[0].CreatedBy | Should -Be "Admin" # Verifies CreatedBy is populated even on skip
        }

        It "Safety Lock: PROCEEDS with deletion if User is DISABLED in AD" {
            $c = @([pscustomobject]@{ Email="disabled@test.com" })
            Mock Resolve-CloudSOCUuidByEmail -MockWith { return [pscustomobject]@{ Uuid="456"; CreatedBy="System" } } -ModuleName Cloudsoc
            Mock Get-ADUserSafetyStatus -MockWith { return @{Status="Disabled"; Forest="local"} } -ModuleName Cloudsoc
            Mock Invoke-CloudSOCRequest -MockWith { return [pscustomobject]@{ StatusCode=200 } } -ModuleName Cloudsoc
            
            $report = Remove-CloudSOCUsers -Candidates $c -Config $fakeConfig -Action "delete" -ADSafetyLock $true
            $report[0].Status | Should -Be "Success"
            $report[0].CreatedBy | Should -Be "System"
        }
    }

    Context "8. API Deletion Guards" {
        BeforeAll { 
            Mock Invoke-CloudSOCRequest -MockWith { return [pscustomobject]@{ StatusCode=200 } } -ModuleName Cloudsoc 
        }

        It "RequireCreatedBy: SKIPS if creator does NOT match" {
            Mock Resolve-CloudSOCUuidByEmail -MockWith { return [pscustomobject]@{ Uuid="1"; CreatedBy="System" } } -ModuleName Cloudsoc
            $c = @([pscustomobject]@{ Email="guard@test.com" })
            $report = Remove-CloudSOCUsers -Candidates $c -Config $fakeConfig -Action "delete" -RequireCreatedBy "OktaSync"
            $report[0].Status | Should -Be "Skipped"
            $report[0].Reason | Should -Match "didn't match Require filter"
        }

        It "SecondaryUser DomainIs: SKIPS if domain does NOT match" {
            Mock Resolve-CloudSOCUuidByEmail -MockWith { return [pscustomobject]@{ Uuid="4"; SecondaryUserId="LOCAL\user"; CreatedBy="Sys" } } -ModuleName Cloudsoc
            $c = @([pscustomobject]@{ Email="guard@test.com" })
            $report = Remove-CloudSOCUsers -Candidates $c -Config $fakeConfig -Action "delete" -GuardSecondaryUserCondition "DomainIs" -GuardSecondaryUserValue "CORP"
            $report[0].Status | Should -Be "Skipped"
            $report[0].Reason | Should -Match "Domain \('LOCAL'\) is not 'CORP'"
        }
    }
}