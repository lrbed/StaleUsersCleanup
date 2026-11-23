let currentPage = 1;
let currentLimit = 25;
let searchDebounceTimer = null;
let syncPollingInterval = null;
let executionPollingInterval = null;
let telemetryPollingInterval = null;
let selectedUserIds = new Set();
let hasQueuedDeletions = false;
let currentInterruptedRunId = null;
let allHelpDocs = [];

document.addEventListener("DOMContentLoaded", () => {
    initTheme();
    setupExitGuard();
    loadConfig();
    loadRoleGuardConfig();
    checkInterruptedRun();
    loadDetectedTenants();
    loadGroups();
    restoreStickyState();
    loadUsers(1);
    loadExecutionSummary();
    loadExecutionRuns();
    loadHelpDocs();
    startSyncPolling();
    startExecutionPolling();
    startTelemetryPolling();
});

// -------------------------------------------------------------
// THEME MANAGEMENT (Slate, Midnight, Charcoal, Light)
// -------------------------------------------------------------
function initTheme() {
    const savedTheme = localStorage.getItem("cloudsoc_theme") || "theme-slate";
    setTheme(savedTheme);
    const sel = document.getElementById("theme-selector");
    if (sel) sel.value = savedTheme;
}

function changeTheme(themeName) {
    setTheme(themeName);
    localStorage.setItem("cloudsoc_theme", themeName);
}

function setTheme(themeName) {
    document.body.className = `${themeName} min-h-screen flex flex-col transition-colors duration-200`;
}

// -------------------------------------------------------------
// EXIT GUARD (beforeunload WARNING DURING ACTIVE EXECUTION)
// -------------------------------------------------------------
function setupExitGuard() {
    window.addEventListener("beforeunload", (e) => {
        const stateBadge = document.getElementById("exec-state-badge");
        const execState = stateBadge ? stateBadge.innerText.toLowerCase() : "";
        const isExecuting = execState.includes("running") || execState.includes("paused") || execState.includes("awaiting_reauth");

        const syncMsg = document.getElementById("sync-status-msg");
        const isSyncing = syncMsg && !syncMsg.innerText.includes("Idle") && !syncMsg.innerText.includes("completed") && !syncMsg.innerText.includes("Cancelled");

        if (isExecuting || isSyncing) {
            e.preventDefault();
            e.returnValue = "An operation is currently active. Leaving may interrupt the run (progress is safely checkpointed).";
            return e.returnValue;
        }
    });
}

// -------------------------------------------------------------
// NAVIGATION TABS & STICKY STATE
// -------------------------------------------------------------
function switchTab(tabId) {
    document.querySelectorAll(".tab-content").forEach(el => el.classList.add("hidden"));
    const target = document.getElementById(tabId);
    if (target) target.classList.remove("hidden");

    document.querySelectorAll(".nav-tab").forEach(tab => {
        tab.classList.remove("border-blue-500", "text-blue-400");
        tab.classList.add("border-transparent", "text-slate-400");
    });

    const activeNav = document.getElementById(`nav-${tabId}`);
    if (activeNav) {
        activeNav.classList.remove("border-transparent", "text-slate-400");
        activeNav.classList.add("border-blue-500", "text-blue-400");
    }

    try {
        localStorage.setItem("cloudsoc_active_tab", tabId);
    } catch (e) {}

    if (tabId === "tab-users") {
        loadUsers(currentPage);
    } else if (tabId === "tab-execution") {
        loadExecutionSummary();
        loadRoleGuardConfig();
    } else if (tabId === "tab-logs") {
        refreshLogs();
        loadExecutionRuns();
    } else if (tabId === "tab-help") {
        renderHelpDocs(allHelpDocs);
    }
}

// Sub-Tab Switcher for Import & Sync Engine Channels
function switchSyncChannel(channelId) {
    document.querySelectorAll(".sync-channel-panel").forEach(el => el.classList.add("hidden"));
    const target = document.getElementById(channelId);
    if (target) target.classList.remove("hidden");

    document.querySelectorAll(".sync-channel-btn").forEach(btn => {
        btn.classList.remove("bg-blue-600", "text-white", "shadow");
        btn.classList.add("text-slate-400");
    });

    const activeBtn = document.getElementById(`btn-${channelId}`);
    if (activeBtn) {
        activeBtn.classList.remove("text-slate-400");
        activeBtn.classList.add("bg-blue-600", "text-white", "shadow");
    }

    try {
        localStorage.setItem("cloudsoc_sync_channel", channelId);
    } catch (e) {}
}

// Collapsible Accordions
function toggleAccordion(contentId, chevronId) {
    const content = document.getElementById(contentId);
    const chevron = document.getElementById(chevronId);
    if (!content) return;
    const isHidden = content.classList.contains("hidden");
    if (isHidden) {
        content.classList.remove("hidden");
        if (chevron) chevron.classList.add("chevron-rotate");
    } else {
        content.classList.add("hidden");
        if (chevron) chevron.classList.remove("chevron-rotate");
    }
}

// Restore Sticky UI Selections from LocalStorage
function restoreStickyState() {
    try {
        // 1. Active Tab
        const savedTab = localStorage.getItem("cloudsoc_active_tab");
        if (savedTab && document.getElementById(savedTab)) {
            switchTab(savedTab);
        }

        // 2. Active Sync Channel
        const savedChannel = localStorage.getItem("cloudsoc_sync_channel") || "channel-cloudsoc";
        if (savedChannel && document.getElementById(savedChannel)) {
            switchSyncChannel(savedChannel);
        }

        // 3. User Directory Filters
        const savedFilters = localStorage.getItem("cloudsoc_filter_state");
        if (savedFilters) {
            const f = JSON.parse(savedFilters);
            const setVal = (id, val) => { const el = document.getElementById(id); if (el && val !== undefined) el.value = val; };
            setVal("filter-search", f.search);
            setVal("filter-regex", f.regex);
            setVal("filter-ad-status", f.ad_status);
            setVal("filter-inactivity", f.inactivity);
            setVal("filter-origin", f.origin);
            setVal("filter-group", f.group);
            setVal("filter-is-active", f.is_active);
            setVal("filter-selected-action", f.selected_action);
            if (f.inactivity === "custom" && f.custom_days) {
                const cInput = document.getElementById("filter-custom-inactivity-days");
                if (cInput) {
                    cInput.value = f.custom_days;
                    cInput.classList.remove("hidden");
                }
            }
        }

        // 4. Targeted Ingestion Criteria
        const savedCriteria = localStorage.getItem("cloudsoc_target_criteria");
        if (savedCriteria) {
            const c = JSON.parse(savedCriteria);
            const setChk = (id, val) => { const el = document.getElementById(id); if (el && val !== undefined) el.checked = !!val; };
            const setVal = (id, val) => { const el = document.getElementById(id); if (el && val !== undefined) el.value = val; };
            setChk("target-stale-only", c.stale_only);
            setVal("target-inactivity-days", c.inactivity_threshold_days || 90);
            setChk("target-include-blank", c.include_blank_activity !== undefined ? c.include_blank_activity : true);
            setVal("target-count-limit", c.count_limit || "");
            setVal("target-match-mode", c.match_mode || "all");
            setVal("target-email-domains", c.email_domains || "");
            setVal("target-netbios-domains", c.netbios_domains || "");
            setVal("target-groups", c.cloudsoc_groups || "");
            setVal("target-created-by", c.created_by_filter || "");
        }
    } catch (e) {
        console.warn("Could not restore sticky UI state:", e);
    }
}

function saveFilterState() {
    try {
        const state = {
            search: document.getElementById("filter-search")?.value || "",
            regex: document.getElementById("filter-regex")?.value || "",
            ad_status: document.getElementById("filter-ad-status")?.value || "",
            inactivity: document.getElementById("filter-inactivity")?.value || "",
            custom_days: document.getElementById("filter-custom-inactivity-days")?.value || "",
            origin: document.getElementById("filter-origin")?.value || "",
            group: document.getElementById("filter-group")?.value || "",
            is_active: document.getElementById("filter-is-active")?.value || "",
            selected_action: document.getElementById("filter-selected-action")?.value || "",
        };
        localStorage.setItem("cloudsoc_filter_state", JSON.stringify(state));
    } catch (e) {}
}

function saveTargetCriteriaState() {
    try {
        const c = {
            stale_only: document.getElementById("target-stale-only")?.checked,
            inactivity_threshold_days: document.getElementById("target-inactivity-days")?.value,
            include_blank_activity: document.getElementById("target-include-blank")?.checked,
            count_limit: document.getElementById("target-count-limit")?.value,
            match_mode: document.getElementById("target-match-mode")?.value,
            email_domains: document.getElementById("target-email-domains")?.value,
            netbios_domains: document.getElementById("target-netbios-domains")?.value,
            cloudsoc_groups: document.getElementById("target-groups")?.value,
            created_by_filter: document.getElementById("target-created-by")?.value,
        };
        localStorage.setItem("cloudsoc_target_criteria", JSON.stringify(c));
    } catch (e) {}
}

// -------------------------------------------------------------
// MULTI-TENANT PROFILES & SILENT CONFIGURATION
// -------------------------------------------------------------
async function loadDetectedTenants() {
    try {
        const res = await fetch("/api/config/detected-tenants");
        const data = await res.json();
        const select = document.getElementById("sel-detected-tenant");
        if (!select) return;

        if (data.success && Array.isArray(data.tenants) && data.tenants.length > 0) {
            select.innerHTML = `<option value="">-- Quick Switch Detected Profile (${data.tenants.length} available) --</option>`;
            data.tenants.forEach(t => {
                const opt = document.createElement("option");
                opt.value = t.filename;
                opt.innerText = `${t.profile_name.toUpperCase()} (${t.tenant}) [${t.filename}]`;
                select.appendChild(opt);
            });
        } else {
            select.innerHTML = '<option value="">No local config-*.json detected</option>';
        }
    } catch (e) {
        console.error("Failed to load detected tenants", e);
    }
}

async function onSwitchTenantProfile(filename) {
    if (!filename) return;
    try {
        const res = await fetch("/api/config/switch-tenant", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ filename })
        });
        const data = await res.json();
        const statusEl = document.getElementById("silent-upload-status");
        const msgEl = document.getElementById("silent-upload-msg");

        if (data.success) {
            if (msgEl) msgEl.innerText = `Switched to profile: ${data.tenant} (${data.api_server}) silently.`;
            if (statusEl) statusEl.classList.remove("hidden");
            loadConfig();
        } else {
            alert("Failed to switch tenant: " + (data.error || "Unknown error"));
        }
    } catch (e) {
        alert("Error switching tenant: " + e.message);
    }
}

async function handleSilentConfigUpload(file) {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async (e) => {
        try {
            const parsed = JSON.parse(e.target.result);
            const res = await fetch("/api/config/upload-silent", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(parsed)
            });
            const data = await res.json();
            const statusEl = document.getElementById("silent-upload-status");
            const msgEl = document.getElementById("silent-upload-msg");

            if (data.success) {
                if (msgEl) msgEl.innerText = `Silently imported configuration for tenant: ${data.tenant}`;
                if (statusEl) statusEl.classList.remove("hidden");
                loadConfig();
            } else {
                alert("Upload failed: " + (data.error || "Invalid file format"));
            }
        } catch (err) {
            alert("Failed to parse JSON file: " + err.message);
        }
    };
    reader.readAsText(file);
}

// -------------------------------------------------------------
// GROUPS DROPDOWN & DATALIST AUTOCOMPLETE
// -------------------------------------------------------------
async function loadGroups() {
    try {
        const res = await fetch("/api/users/groups");
        const data = await res.json();
        if (data.success && Array.isArray(data.groups)) {
            const select = document.getElementById("filter-group");
            if (select) {
                const currentVal = select.value;
                select.innerHTML = '<option value="">All Groups (Auto-Discovered)</option>';
                data.groups.forEach(g => {
                    const opt = document.createElement("option");
                    opt.value = g;
                    opt.innerText = g;
                    select.appendChild(opt);
                });
                if (currentVal) select.value = currentVal;
            }

            const datalist = document.getElementById("available-groups-list");
            if (datalist) {
                datalist.innerHTML = "";
                data.groups.forEach(g => {
                    const opt = document.createElement("option");
                    opt.value = g;
                    datalist.appendChild(opt);
                });
            }
        }
    } catch (e) {
        console.error("Failed to load groups", e);
    }
}

function onInactivityFilterChange() {
    const sel = document.getElementById("filter-inactivity");
    const customInput = document.getElementById("filter-custom-inactivity-days");
    if (!sel || !customInput) return;

    if (sel.value === "custom") {
        customInput.classList.remove("hidden");
        customInput.focus();
    } else {
        customInput.classList.add("hidden");
        loadUsers(1);
    }
}

// -------------------------------------------------------------
// CONFIGURATION & CREDENTIALS
// -------------------------------------------------------------
async function loadConfig() {
    try {
        const res = await fetch("/api/config");
        const data = await res.json();
        if (data.success && data.config) {
            const cfg = data.config;
            const setVal = (id, val) => { const el = document.getElementById(id); if (el) el.value = val || ""; };
            setVal("cfg-api-server", cfg.api_server);
            setVal("cfg-tenant", cfg.tenant);
            setVal("cfg-key-id", cfg.key_id);
            if (cfg.key_secret_masked) {
                const el = document.getElementById("cfg-key-secret");
                if (el) el.placeholder = cfg.key_secret_masked;
            }

            // AD Mode
            const manualMode = !!cfg.ad_manual_mode;
            toggleADMode(manualMode ? "manual" : "auto");
            const rManual = document.getElementById("ad-mode-manual");
            const rAuto = document.getElementById("ad-mode-auto");
            if (manualMode && rManual) rManual.checked = true;
            else if (rAuto) rAuto.checked = true;

            setVal("cfg-manual-ad-host", cfg.ad_manual_host);
            setVal("cfg-manual-ad-port", cfg.ad_manual_port || 389);
            const chkSSL = document.getElementById("cfg-manual-ad-ssl");
            if (chkSSL) chkSSL.checked = !!cfg.ad_manual_use_ssl;
            setVal("cfg-manual-ad-base", cfg.ad_manual_base_dn);

            if (Array.isArray(cfg.ad_forests)) {
                setVal("cfg-ad-forests", cfg.ad_forests.join(", "));
            }
            setVal("cfg-ad-user", cfg.ad_username);

            const srv = document.getElementById("lbl-api-server");
            if (srv) srv.innerText = cfg.api_server || "-";
            const ten = document.getElementById("lbl-tenant");
            if (ten) ten.innerText = cfg.tenant || "-";
            const tenHeader = document.getElementById("header-active-tenant");
            if (tenHeader) tenHeader.innerText = cfg.tenant || "-";
            const adModeLbl = document.getElementById("lbl-ad-mode");
            if (adModeLbl) adModeLbl.innerText = manualMode ? "Manual DC Fallback" : "Auto-Discovery";
        }
    } catch (e) {
        console.error("Failed to load configuration", e);
    }
}

function toggleADMode(mode) {
    const pAuto = document.getElementById("panel-ad-auto");
    const pManual = document.getElementById("panel-ad-manual");
    if (mode === "manual") {
        if (pAuto) pAuto.classList.add("hidden");
        if (pManual) pManual.classList.remove("hidden");
    } else {
        if (pAuto) pAuto.classList.remove("hidden");
        if (pManual) pManual.classList.add("hidden");
    }
}

async function saveConfiguration() {
    const forestsRaw = document.getElementById("cfg-ad-forests").value.trim();
    let forestsList = [];
    if (forestsRaw) {
        if (forestsRaw.startsWith("[") && forestsRaw.endsWith("]")) {
            try { forestsList = JSON.parse(forestsRaw); } catch (e) { forestsList = forestsRaw.split(",").map(s => s.trim()); }
        } else {
            forestsList = forestsRaw.split(",").map(s => s.trim()).filter(Boolean);
        }
    }

    const isManual = document.getElementById("ad-mode-manual").checked;

    const payload = {
        api_server: document.getElementById("cfg-api-server").value.trim(),
        tenant: document.getElementById("cfg-tenant").value.trim(),
        key_id: document.getElementById("cfg-key-id").value.trim(),
        ad_manual_mode: isManual,
        ad_manual_host: document.getElementById("cfg-manual-ad-host").value.trim(),
        ad_manual_port: parseInt(document.getElementById("cfg-manual-ad-port").value.trim()) || 389,
        ad_manual_use_ssl: document.getElementById("cfg-manual-ad-ssl").checked,
        ad_manual_base_dn: document.getElementById("cfg-manual-ad-base").value.trim(),
        ad_forests: forestsList,
        ad_username: document.getElementById("cfg-ad-user").value.trim()
    };

    const secret = document.getElementById("cfg-key-secret").value.trim();
    if (secret) payload.key_secret = secret;

    const pass = document.getElementById("cfg-ad-pass").value.trim();
    if (pass) payload.ad_password = pass;

    try {
        const res = await fetch("/api/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.success) {
            alert("Configuration saved successfully.");
            loadConfig();
        } else {
            alert("Error saving config: " + (data.error || "Unknown"));
        }
    } catch (e) {
        alert("Request error: " + e.message);
    }
}

// -------------------------------------------------------------
// DIRECT DOMAIN CONTROLLER DIAGNOSTIC TEST
// -------------------------------------------------------------
async function testManualADConnection() {
    const host = document.getElementById("cfg-manual-ad-host").value.trim();
    const port = parseInt(document.getElementById("cfg-manual-ad-port").value.trim()) || 389;
    const ssl = document.getElementById("cfg-manual-ad-ssl").checked;
    const baseDn = document.getElementById("cfg-manual-ad-base").value.trim();
    const user = document.getElementById("cfg-ad-user").value.trim();
    const pass = document.getElementById("cfg-ad-pass").value.trim();

    if (!host) {
        alert("Please enter a Domain Controller Host or IP address first.");
        return;
    }

    const btn = document.getElementById("btn-test-manual-ad");
    if (btn) btn.disabled = true;

    try {
        const res = await fetch("/api/preflight/ad-manual-test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                host: host,
                port: port,
                use_ssl: ssl,
                base_dn: baseDn,
                username: user,
                password: pass
            })
        });
        const data = await res.json();
        alert(`Direct DC Connection Test:\nStatus: ${data.status}\nMessage: ${data.message}\nTCP Socket: ${data.socket_connected ? 'OK' : 'Failed'}\nBind: ${data.bind_success ? 'OK' : 'Failed'}`);
    } catch (e) {
        alert("Diagnostic test error: " + e.message);
    } finally {
        if (btn) btn.disabled = false;
    }
}

// -------------------------------------------------------------
// PREFLIGHT CONNECTIVITY CHECKS (3-TIER SYSTEM)
// -------------------------------------------------------------
async function runPreflightChecks() {
    const btn = document.getElementById("btn-run-preflight");
    const spinner = document.getElementById("preflight-spinner");
    if (btn) btn.disabled = true;
    if (spinner) spinner.classList.remove("hidden");

    try {
        const res = await fetch("/api/preflight", { method: "POST" });
        const data = await res.json();

        // 1. Update 3-Tier Preflight Badge
        const pBadge = document.getElementById("preflight-badge");
        const pDot = document.getElementById("preflight-dot");
        const pText = document.getElementById("preflight-text");

        if (data.overall_tier === "green") {
            pBadge.className = "px-3 py-1 rounded-full border border-emerald-700 bg-emerald-950/80 text-emerald-300 flex items-center gap-1.5 transition";
            pDot.className = "w-2 h-2 rounded-full bg-emerald-400";
            pText.innerText = "Preflight: Healthy & Ready";
        } else if (data.overall_tier === "yellow") {
            pBadge.className = "px-3 py-1 rounded-full border border-amber-700 bg-amber-950/80 text-amber-300 flex items-center gap-1.5 transition";
            pDot.className = "w-2 h-2 rounded-full bg-amber-400";
            pText.innerText = "Preflight: Partially Ready (AD Offline)";
        } else {
            pBadge.className = "px-3 py-1 rounded-full border border-rose-700 bg-rose-950/80 text-rose-300 flex items-center gap-1.5 transition";
            pDot.className = "w-2 h-2 rounded-full bg-rose-400";
            pText.innerText = "Preflight: Not Ready";
        }

        // 2. Update AD Health Badge
        const adBadge = document.getElementById("ad-health-badge");
        const adDot = document.getElementById("ad-dot");
        const adText = document.getElementById("ad-health-text");
        const adHealth = data.ad_health || "Unknown";

        if (adHealth.includes("Healthy")) {
            adBadge.className = "px-3 py-1 rounded-full border border-emerald-700 bg-emerald-950/80 text-emerald-300 flex items-center gap-1.5 transition";
            adDot.className = "w-2 h-2 rounded-full bg-emerald-400";
            adText.innerText = `AD: ${adHealth}`;
        } else if (adHealth === "Partially Ready") {
            adBadge.className = "px-3 py-1 rounded-full border border-amber-700 bg-amber-950/80 text-amber-300 flex items-center gap-1.5 transition";
            adDot.className = "w-2 h-2 rounded-full bg-amber-400";
            adText.innerText = "AD: Offline (Safety Lock On)";
        } else {
            adBadge.className = "px-3 py-1 rounded-full border border-rose-700 bg-rose-950/80 text-rose-300 flex items-center gap-1.5 transition";
            adDot.className = "w-2 h-2 rounded-full bg-rose-400";
            adText.innerText = "AD: Unreachable (Safety Lock On)";
        }

        // 3. Update Preflight Detail Cards
        const cs = data.checks.cloudsoc;
        const csBadge = document.getElementById("card-cloudsoc-badge");
        const csStatus = document.getElementById("card-cloudsoc-status");
        if (cs.status === "passed") {
            csBadge.className = "px-2 py-0.5 text-xs rounded bg-emerald-950 text-emerald-400 border border-emerald-800";
            csBadge.innerText = "Connected";
            csStatus.innerText = cs.details;
            document.getElementById("lbl-latency").innerText = `${cs.latency_ms} ms`;
        } else {
            csBadge.className = "px-2 py-0.5 text-xs rounded bg-rose-950 text-rose-400 border border-rose-800";
            csBadge.innerText = "Failed";
            csStatus.innerText = cs.details;
        }

        const ad = data.checks.active_directory;
        const adCardBadge = document.getElementById("card-ad-badge");
        const adCardStatus = document.getElementById("card-ad-status");
        if (ad.status === "passed") {
            adCardBadge.className = "px-2 py-0.5 text-xs rounded bg-emerald-950 text-emerald-400 border border-emerald-800";
            adCardBadge.innerText = "Reachable";
            adCardStatus.innerText = ad.details || "Active Directory connection verified.";
        } else {
            adCardBadge.className = "px-2 py-0.5 text-xs rounded bg-amber-950 text-amber-400 border border-amber-800";
            adCardBadge.innerText = "Unreachable / Protected";
            adCardStatus.innerText = "AD offline. Fail-closed safety lock active.";
        }

        const csHdrBadge = document.getElementById("badge-cloudsoc-header-status");
        if (csHdrBadge) {
            if (cs.status === "passed") {
                csHdrBadge.className = "text-[11px] font-normal px-2 py-0.5 rounded bg-emerald-950 text-emerald-300 border border-emerald-800/60";
                csHdrBadge.innerText = "Validated (Connected)";
            } else {
                csHdrBadge.className = "text-[11px] font-normal px-2 py-0.5 rounded bg-rose-950 text-rose-300 border border-rose-800/60";
                csHdrBadge.innerText = "Connection Failed";
            }
        }

        const adHdrBadge = document.getElementById("badge-ad-header-status");
        const adContainer = document.getElementById("card-ad-container");
        if (adHdrBadge) {
            if (ad.status === "passed") {
                adHdrBadge.className = "text-[11px] font-normal px-2 py-0.5 rounded bg-emerald-950 text-emerald-300 border border-emerald-800/60";
                adHdrBadge.innerText = "Connected & Active";
                if (adContainer) adContainer.classList.add("ad-connected-subdued");
            } else {
                adHdrBadge.className = "text-[11px] font-normal px-2 py-0.5 rounded bg-amber-950 text-amber-300 border border-amber-800/60";
                adHdrBadge.innerText = "Offline (Safety Active)";
                if (adContainer) adContainer.classList.remove("ad-connected-subdued");
            }
        }

    } catch (e) {
        alert("Preflight check failed to execute: " + e.message);
    } finally {
        if (btn) btn.disabled = false;
        if (spinner) spinner.classList.add("hidden");
    }
}

// -------------------------------------------------------------
// LIVE TELEMETRY & 429 COUNTERS
// -------------------------------------------------------------
function startTelemetryPolling() {
    if (telemetryPollingInterval) clearInterval(telemetryPollingInterval);
    telemetryPollingInterval = setInterval(fetchTelemetry, 3500);
    fetchTelemetry();
}

async function fetchTelemetry() {
    try {
        const res = await fetch("/api/telemetry");
        const data = await res.json();
        if (data.success && data.telemetry) {
            const t = data.telemetry;
            const callsEl = document.getElementById("telemetry-calls");
            if (callsEl) {
                callsEl.innerText = `API Calls: ${t.calls_last_hour} (${t.hourly_quota_remaining}/hr left)`;
            }
            const count429El = document.getElementById("telemetry-429");
            if (count429El) {
                count429El.innerText = `429s: ${t.total_429_count}`;
            }

            // Backoff Alert Banner
            const banner = document.getElementById("backoff-banner");
            const countdownEl = document.getElementById("backoff-countdown");
            if (t.is_in_backoff) {
                banner.classList.remove("hidden");
                if (countdownEl) countdownEl.innerText = Math.ceil(t.backoff_seconds_remaining);
            } else {
                banner.classList.add("hidden");
            }
        }
    } catch (e) {
        // Silent polling error
    }
}

async function overrideBackoff() {
    try {
        const res = await fetch("/api/telemetry/override-backoff", { method: "POST" });
        const data = await res.json();
        if (data.success) {
            document.getElementById("backoff-banner").classList.add("hidden");
            fetchTelemetry();
        }
    } catch (e) {
        alert("Override error: " + e.message);
    }
}

// -------------------------------------------------------------
// SYNC ENGINE (FULL & STALE-ONLY TARGETED INGESTION)
// -------------------------------------------------------------
function startSyncPolling() {
    if (syncPollingInterval) clearInterval(syncPollingInterval);
    syncPollingInterval = setInterval(fetchSyncStatus, 2500);
}

async function fetchSyncStatus() {
    try {
        const res = await fetch("/api/sync/status");
        const data = await res.json();

        const pBar = document.getElementById("sync-progress-bar");
        const pPercent = document.getElementById("sync-progress-percent");
        const pMsg = document.getElementById("sync-status-msg");

        if (pBar) pBar.style.width = `${data.percent_complete || 0}%`;
        if (pPercent) pPercent.innerText = `${data.percent_complete || 0}%`;
        if (pMsg) pMsg.innerText = data.message || "Idle";

        const btnPause = document.getElementById("btn-sync-pause");
        const btnResume = document.getElementById("btn-sync-resume");
        const btnCancel = document.getElementById("btn-sync-cancel");

        if (btnCancel) {
            btnCancel.disabled = !data.is_running;
            btnCancel.classList.toggle("opacity-40", !data.is_running);
            btnCancel.classList.toggle("cursor-not-allowed", !data.is_running);
        }

        if (data.is_running) {
            if (data.is_paused) {
                if (btnPause) btnPause.classList.add("hidden");
                if (btnResume) {
                    btnResume.classList.remove("hidden");
                    btnResume.disabled = false;
                    btnResume.classList.remove("opacity-40", "cursor-not-allowed");
                }
            } else {
                if (btnPause) {
                    btnPause.classList.remove("hidden");
                    btnPause.disabled = false;
                    btnPause.classList.remove("opacity-40", "cursor-not-allowed");
                }
                if (btnResume) btnResume.classList.add("hidden");
            }
        } else {
            if (btnPause) {
                btnPause.classList.remove("hidden");
                btnPause.disabled = true;
                btnPause.classList.add("opacity-40", "cursor-not-allowed");
            }
            if (btnResume) btnResume.classList.add("hidden");
        }
    } catch (e) {
        // Silent fail
    }
}

async function startFullCloudsocSync() {
    if (!confirm("Start full user pagination sync for the entire CloudSOC tenant?")) return;
    try {
        const res = await fetch("/api/sync/cloudsoc/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ batch_size: 100 })
        });
        const data = await res.json();
        if (data.success) {
            fetchSyncStatus();
        } else {
            alert("Failed to start sync: " + (data.error || "Unknown"));
        }
    } catch (e) {
        alert("Request error: " + e.message);
    }
}

async function startTargetedSync() {
    saveTargetCriteriaState();
    const staleOnly = document.getElementById("target-stale-only").checked;
    const inactivityDays = parseInt(document.getElementById("target-inactivity-days").value) || 90;
    const includeBlank = document.getElementById("target-include-blank").checked;

    const countLimit = document.getElementById("target-count-limit").value.trim();
    const matchMode = document.getElementById("target-match-mode").value;
    const emailDomains = document.getElementById("target-email-domains").value.trim();
    const netbiosDomains = document.getElementById("target-netbios-domains").value.trim();
    const groups = document.getElementById("target-groups").value.trim();
    const createdBy = document.getElementById("target-created-by").value.trim();

    const criteria = {
        stale_only: staleOnly,
        inactivity_threshold_days: inactivityDays,
        include_blank_activity: includeBlank,
        match_mode: matchMode,
        count_limit: countLimit ? parseInt(countLimit) : null,
        email_domains: emailDomains ? emailDomains.split(",").map(s => s.trim()).filter(Boolean) : [],
        netbios_domains: netbiosDomains ? netbiosDomains.split(",").map(s => s.trim()).filter(Boolean) : [],
        cloudsoc_groups: groups ? groups.split(",").map(s => s.trim()).filter(Boolean) : [],
        created_by_filter: createdBy ? createdBy.split(",").map(s => s.trim()).filter(Boolean) : []
    };

    try {
        const res = await fetch("/api/sync/cloudsoc/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ batch_size: 100, criteria: criteria })
        });
        const data = await res.json();
        if (data.success) {
            fetchSyncStatus();
        } else {
            alert("Failed to start targeted sync: " + (data.error || "Unknown"));
        }
    } catch (e) {
        alert("Request error: " + e.message);
    }
}

async function startLdapSync(recheckAll = false) {
    try {
        const res = await fetch("/api/sync/ldap/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ recheck_all: recheckAll })
        });
        const data = await res.json();
        if (data.success) {
            fetchSyncStatus();
            // Switch to sync tab or poll automatically
            startSyncPolling();
        } else {
            alert("Failed to start Active Directory correlation: " + (data.error || "Unknown"));
        }
    } catch (e) {
        alert("Request error: " + e.message);
    }
}

function onAdIngestPresetChange() {
    const preset = document.getElementById("ad-ingest-preset")?.value || "all";
    const filterInput = document.getElementById("ad-ingest-filter");
    if (!filterInput) return;

    if (preset === "disabled") {
        filterInput.value = "(&(objectCategory=person)(objectClass=user)(userAccountControl:1.2.840.113556.1.4.803:=2))";
    } else if (preset === "all") {
        filterInput.value = "";
    }
}

async function startAdIngestion() {
    const filterVal = document.getElementById("ad-ingest-filter")?.value?.trim() || "";
    const baseDnVal = document.getElementById("ad-ingest-base-dn")?.value?.trim() || "";
    const limitVal = parseInt(document.getElementById("ad-ingest-limit")?.value?.trim() || "0") || 0;

    const btn = document.getElementById("btn-run-ad-ingest");
    if (btn) btn.disabled = true;

    try {
        const res = await fetch("/api/sync/ad-ingest/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                ldap_filter: filterVal,
                base_dn: baseDnVal,
                count_limit: limitVal
            })
        });
        const data = await res.json();
        if (data.success) {
            fetchSyncStatus();
            startSyncPolling();
        } else {
            alert("Failed to start Active Directory Ingestion: " + (data.error || "Unknown"));
        }
    } catch (e) {
        alert("Request error: " + e.message);
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function startCsvImport() {
    try {
        const res = await fetch("/api/sync/csv/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({})
        });
        const data = await res.json();
        if (data.success) {
            alert(`CSV Import Complete: ${data.imported} users imported, ${data.errors} errors.`);
            loadUsers(1);
        } else {
            alert("CSV Import failed: " + (data.error || "Unknown"));
        }
    } catch (e) {
        alert("Request error: " + e.message);
    }
}

async function pauseSync() { await fetch("/api/sync/pause", { method: "POST" }); fetchSyncStatus(); }
async function resumeSync() { await fetch("/api/sync/resume", { method: "POST" }); fetchSyncStatus(); }
async function cancelSync() { await fetch("/api/sync/cancel", { method: "POST" }); fetchSyncStatus(); }

// -------------------------------------------------------------
// USER DIRECTORY, FILTERING & STAGING
// -------------------------------------------------------------
function debounceUserSearch() {
    clearTimeout(searchDebounceTimer);
    searchDebounceTimer = setTimeout(() => loadUsers(1), 300);
}

async function loadUsers(page = 1) {
    currentPage = page;

    const inactSel = document.getElementById("filter-inactivity")?.value || "";
    let inactMode = inactSel === "blank" ? "blank_only" : "";
    let inactDays = "";
    if (inactSel === "custom") {
        inactDays = document.getElementById("filter-custom-inactivity-days")?.value.trim() || "";
    } else if (inactSel !== "blank" && inactSel !== "") {
        inactDays = inactSel;
    }

    saveFilterState();

    const params = new URLSearchParams({
        page: page,
        limit: currentLimit,
        search: document.getElementById("filter-search").value.trim(),
        regex: document.getElementById("filter-regex").value.trim(),
        ad_status: document.getElementById("filter-ad-status").value,
        inactivity_mode: inactMode,
        inactivity_days: inactDays,
        origin: document.getElementById("filter-origin").value,
        group: document.getElementById("filter-group").value,
        is_active: document.getElementById("filter-is-active").value,
        selected_action: document.getElementById("filter-selected-action").value,
    });

    try {
        const res = await fetch(`/api/users?${params.toString()}`);
        const data = await res.json();

        const tbody = document.getElementById("users-table-body");
        tbody.innerHTML = "";

        if (!data.users || data.users.length === 0) {
            tbody.innerHTML = '<tr><td colspan="10" class="py-8 text-center text-slate-500 italic">No matching users found.</td></tr>';
            document.getElementById("lbl-page-range").innerText = "0 - 0";
            document.getElementById("lbl-total-users").innerText = "0";
            return;
        }

        const startIdx = (page - 1) * currentLimit + 1;
        const endIdx = Math.min(page * currentLimit, data.total);
        document.getElementById("lbl-page-range").innerText = `${startIdx} - ${endIdx}`;
        document.getElementById("lbl-total-users").innerText = data.total;
        document.getElementById("lbl-current-page").innerText = `Page ${page} of ${data.total_pages || 1}`;

        // Update User Directory KPI Metric Cards
        const kpiTotal = document.getElementById("kpi-total-users");
        if (kpiTotal) kpiTotal.innerText = data.total.toLocaleString();
        loadDirectoryKPIMetrics();

        document.getElementById("btn-prev-page").disabled = (page <= 1);
        document.getElementById("btn-next-page").disabled = (page >= data.total_pages);

        data.users.forEach(u => {
            const tr = document.createElement("tr");
            tr.className = "hover:bg-slate-800/40 transition";

            const csBadge = u.is_active
                ? '<span class="inline-flex items-center px-2 py-0.5 rounded text-[10px] bg-emerald-950 text-emerald-400 border border-emerald-800 whitespace-nowrap">Active</span>'
                : '<span class="inline-flex items-center px-2 py-0.5 rounded text-[10px] bg-slate-800 text-slate-400 border border-slate-700 whitespace-nowrap">Inactive</span>';

            let adBadge = '<span class="inline-flex items-center px-2 py-0.5 rounded text-[10px] bg-slate-800 text-slate-400 whitespace-nowrap">Unchecked</span>';
            if (u.ad_status === "Active") {
                adBadge = '<span class="inline-flex items-center px-2 py-0.5 rounded text-[10px] bg-emerald-950 text-emerald-300 border border-emerald-800 whitespace-nowrap">Active</span>';
            } else if (u.ad_status === "Disabled") {
                adBadge = '<span class="inline-flex items-center px-2 py-0.5 rounded text-[10px] bg-amber-950 text-amber-300 border border-amber-800 whitespace-nowrap">Disabled</span>';
            } else if (u.ad_status === "NotFound") {
                adBadge = '<span class="inline-flex items-center px-2 py-0.5 rounded text-[10px] bg-rose-950 text-rose-300 border border-rose-800 font-semibold whitespace-nowrap">NotFound</span>';
            } else if (u.ad_status === "Error") {
                adBadge = '<span class="inline-flex items-center px-2 py-0.5 rounded text-[10px] bg-rose-950 text-rose-400 border border-rose-900 whitespace-nowrap">Error (Lock)</span>';
            }

            let actBadge = '<span class="text-slate-500">-</span>';
            if (u.selected_action === "deactivate") {
                actBadge = '<span class="inline-flex items-center px-2 py-0.5 rounded text-[10px] bg-amber-950 text-amber-300 border border-amber-800 font-medium whitespace-nowrap">Deactivate</span>';
            } else if (u.selected_action === "move_group") {
                actBadge = '<span class="inline-flex items-center px-2 py-0.5 rounded text-[10px] bg-purple-950 text-purple-300 border border-purple-800 font-medium whitespace-nowrap">Move Group</span>';
            } else if (u.selected_action === "delete") {
                actBadge = '<span class="inline-flex items-center px-2 py-0.5 rounded text-[10px] bg-rose-950 text-rose-300 border border-rose-800 font-semibold whitespace-nowrap">Delete</span>';
            }

            const isPrivileged = !!u.is_privileged;
            let roleBadge = `<span class="text-slate-400 whitespace-nowrap">${u.role || 'End User'}</span>`;
            if (isPrivileged) {
                roleBadge = `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] bg-rose-950 text-rose-300 border border-rose-800 font-semibold whitespace-nowrap" title="Role Guard Active: Account protected from staging or deletion">
                    🛡️ ${u.role || 'Admin'}
                    <span class="bg-rose-900/90 text-rose-200 px-1 py-0.2 rounded text-[9px]">Exception</span>
                </span>`;
                // Ensure privileged users are never selected
                selectedUserIds.delete(u.id);
            }

            let undoBtn = '';
            if (u.action_status === 'completed' && u.selected_action === 'deactivate') {
                undoBtn = `<button onclick="performUndo(${u.id}, 'soft')" class="px-2 py-0.5 bg-blue-950 hover:bg-blue-900 text-blue-300 border border-blue-800 rounded text-[10px] whitespace-nowrap transition">Undo</button>`;
            } else if (u.action_status === 'completed' && u.selected_action === 'delete') {
                undoBtn = `<button onclick="performUndo(${u.id}, 'hard')" class="px-2 py-0.5 bg-purple-950 hover:bg-purple-900 text-purple-300 border border-purple-800 rounded text-[10px] whitespace-nowrap transition">Hard Undo</button>`;
            }

            // Clean formatted activity date without microseconds
            let displayActivity = u.last_activity_date || 'Never / Blank';
            if (displayActivity !== 'Never / Blank' && displayActivity.includes('.')) {
                displayActivity = displayActivity.split('.')[0];
            }

            const isChecked = !isPrivileged && selectedUserIds.has(u.id);
            const chkHtml = isPrivileged
                ? `<input type="checkbox" disabled class="user-row-chk rounded bg-slate-950 border-slate-700 text-slate-600 opacity-25 cursor-not-allowed" title="Protected by Role Guard — Deletion locked">`
                : `<input type="checkbox" data-user-id="${u.id}" ${isChecked ? "checked" : ""} onchange="toggleSelectUser(${u.id}, this.checked)" class="user-row-chk rounded bg-slate-950 border-slate-700 text-blue-600 focus:ring-0">`;

            tr.innerHTML = `
                <td class="py-2 px-3">
                    ${chkHtml}
                </td>
                <td class="py-2 px-3">
                    <div class="font-medium text-slate-200">${u.email}</div>
                    <div class="text-[10px] text-slate-500 font-mono">${u.cloudsoc_id || '-'}</div>
                </td>
                <td class="py-2.5 px-3 text-slate-300 text-xs whitespace-nowrap">${u.first_name || ''} ${u.last_name || ''}</td>
                <td class="py-2.5 px-3 whitespace-nowrap">${roleBadge}</td>
                <td class="py-2.5 px-3 text-slate-400 font-mono text-[11px] whitespace-nowrap">${u.secondary_user_id || '-'}</td>
                <td class="py-2.5 px-3 whitespace-nowrap">${csBadge}</td>
                <td class="py-2.5 px-3 whitespace-nowrap">${adBadge}</td>
                <td class="py-2.5 px-3 text-slate-400 whitespace-nowrap font-mono text-[11px]">${displayActivity}</td>
                <td class="py-2.5 px-3 whitespace-nowrap">${actBadge}</td>
                <td class="py-2.5 px-3 whitespace-nowrap">${undoBtn}</td>
            `;
            tbody.appendChild(tr);
        });

    } catch (e) {
        console.error("Failed to load users", e);
    }
}

function prevPage() { if (currentPage > 1) loadUsers(currentPage - 1); }
function nextPage() { loadUsers(currentPage + 1); }

function toggleSelectUser(userId, checked) {
    if (checked) selectedUserIds.add(userId);
    else selectedUserIds.delete(userId);
    updateSelectedCount();
}

function toggleSelectAllHeader(headerChk) {
    selectAllVisible(headerChk.checked);
}

function selectAllVisible(selectAll) {
    document.querySelectorAll(".user-row-chk:not([disabled])").forEach(chk => {
        chk.checked = selectAll;
        const uid = parseInt(chk.getAttribute("data-user-id"));
        if (selectAll && uid) selectedUserIds.add(uid);
        else if (uid) selectedUserIds.delete(uid);
    });
    updateSelectedCount();
}

function updateSelectedCount() {
    const count = selectedUserIds.size;
    const el = document.getElementById("lbl-selected-count");
    if (el) el.innerText = count;

    // Contextually enable/disable staging action buttons
    const btnDeact = document.getElementById("btn-bulk-deactivate");
    const btnMove = document.getElementById("btn-bulk-move");
    const btnDel = document.getElementById("btn-bulk-delete");
    const btnClear = document.getElementById("btn-bulk-clear");

    const hasSelection = count > 0;
    [btnDeact, btnMove, btnDel, btnClear].forEach(btn => {
        if (btn) {
            btn.disabled = !hasSelection;
            btn.classList.toggle("opacity-40", !hasSelection);
            btn.classList.toggle("cursor-not-allowed", !hasSelection);
        }
    });
}

async function loadDirectoryKPIMetrics() {
    try {
        const res = await fetch("/api/stats/stale-breakdown");
        const data = await res.json();
        
        const elTotal = document.getElementById("kpi-total-users");
        const elActive = document.getElementById("kpi-active-users");
        const elStale = document.getElementById("kpi-stale-users");
        const elStaged = document.getElementById("kpi-staged-actions");

        if (data.total_users !== undefined && elTotal) {
            elTotal.innerText = data.total_users.toLocaleString();
        }
        if (data.total_users !== undefined && data.cloudsoc_inactive !== undefined && elActive) {
            const activeCount = Math.max(0, data.total_users - data.cloudsoc_inactive);
            elActive.innerText = activeCount.toLocaleString();
        }
        if (data.inactive_90d_or_blank !== undefined && elStale) {
            elStale.innerText = data.inactive_90d_or_blank.toLocaleString();
        }
        if (data.queued_actions && elStaged) {
            elStaged.innerText = (data.queued_actions.total_pending || 0).toLocaleString();
        }
    } catch (e) {
        // Silently ignore KPI refresh failure
    }
}

async function applyBulkAction(action) {
    if (selectedUserIds.size === 0) {
        alert("Please select at least one user from the table first.");
        return;
    }

    try {
        const res = await fetch("/api/users/prepare-action", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                action: action,
                user_ids: Array.from(selectedUserIds)
            })
        });
        const data = await res.json();
        if (data.success) {
            selectedUserIds.clear();
            updateSelectedCount();
            loadUsers(currentPage);
            loadExecutionSummary();
        } else {
            alert("Action failed: " + (data.error || "Unknown"));
        }
    } catch (e) {
        alert("Request error: " + e.message);
    }
}

// -------------------------------------------------------------
// EXECUTION ENGINE (PHASE 4)
// -------------------------------------------------------------
async function loadExecutionSummary() {
    try {
        const res = await fetch("/api/stats/stale-breakdown");
        const data = await res.json();
        if (data.queued_actions) {
            const qa = data.queued_actions;
            const setNum = (id, n) => { const el = document.getElementById(id); if (el) el.innerText = n || 0; };
            setNum("exec-summary-deactivate", qa.deactivate);
            setNum("exec-summary-move", qa.move_group);
            setNum("exec-summary-delete", qa.delete);
            setNum("exec-summary-total", qa.total_pending);

            hasQueuedDeletions = (qa.delete > 0);
        }
    } catch (e) {
        console.error("Failed to load execution summary", e);
    }
}

function startExecutionPolling() {
    if (executionPollingInterval) clearInterval(executionPollingInterval);
    executionPollingInterval = setInterval(fetchExecutionStatus, 2000);
}

async function fetchExecutionStatus() {
    try {
        const res = await fetch("/api/execution/status");
        const data = await res.json();

        const stateBadge = document.getElementById("exec-state-badge");
        if (stateBadge) {
            stateBadge.innerText = `State: ${data.state.toUpperCase()}`;
            if (data.state === "running") {
                stateBadge.className = "px-2.5 py-1 text-xs rounded-full bg-emerald-950 text-emerald-400 border border-emerald-800 font-mono font-medium animate-pulse";
            } else if (data.state === "paused") {
                stateBadge.className = "px-2.5 py-1 text-xs rounded-full bg-amber-950 text-amber-400 border border-amber-800 font-mono font-medium";
            } else if (data.state === "awaiting_reauth") {
                stateBadge.className = "px-2.5 py-1 text-xs rounded-full bg-purple-950 text-purple-400 border border-purple-800 font-mono font-medium animate-bounce";
            } else {
                stateBadge.className = "px-2.5 py-1 text-xs rounded-full bg-slate-800 text-slate-400 font-mono font-medium";
            }
        }

        const modeBadge = document.getElementById("exec-mode-badge");
        if (modeBadge) {
            modeBadge.innerText = `Mode: ${data.run_mode.toUpperCase()}`;
            modeBadge.className = (data.run_mode === "dry_run")
                ? "px-2.5 py-1 text-xs rounded-full bg-cyan-950 text-cyan-400 border border-cyan-800 font-mono"
                : "px-2.5 py-1 text-xs rounded-full bg-emerald-950 text-emerald-400 border border-emerald-800 font-mono font-bold";
        }

        const pBar = document.getElementById("exec-progress-bar");
        if (pBar) pBar.style.width = `${data.percent_complete || 0}%`;
        const pCount = document.getElementById("exec-progress-count");
        if (pCount) pCount.innerText = `${data.processed_count} / ${data.total_candidates}`;
        const pPercent = document.getElementById("exec-progress-percent");
        if (pPercent) pPercent.innerText = `${data.percent_complete || 0}%`;

        const setNum = (id, n) => { const el = document.getElementById(id); if (el) el.innerText = n || 0; };
        setNum("exec-success-count", data.success_count);
        setNum("exec-skipped-count", data.skipped_count);
        setNum("exec-failed-count", data.failed_count);

        const ticker = document.getElementById("exec-ticker");
        if (ticker) ticker.innerText = data.message || "Idle";

        // Dynamic Execution Button State Controls
        const btnDryRun = document.getElementById("btn-exec-dryrun");
        const btnLive = document.getElementById("btn-exec-live");
        const btnPause = document.getElementById("btn-exec-pause");
        const btnResume = document.getElementById("btn-exec-resume");
        const btnStop = document.getElementById("btn-exec-stop");

        const isRunning = (data.state === "running");
        const isPaused = (data.state === "paused" || data.state === "awaiting_reauth");
        const isBusy = isRunning || isPaused;

        if (btnDryRun) {
            btnDryRun.disabled = isBusy;
            btnDryRun.classList.toggle("opacity-40", isBusy);
            btnDryRun.classList.toggle("cursor-not-allowed", isBusy);
        }
        if (btnLive) {
            btnLive.disabled = isBusy;
            btnLive.classList.toggle("opacity-40", isBusy);
            btnLive.classList.toggle("cursor-not-allowed", isBusy);
        }
        if (btnPause) {
            btnPause.disabled = !isRunning;
            btnPause.classList.toggle("opacity-40", !isRunning);
            btnPause.classList.toggle("cursor-not-allowed", !isRunning);
        }
        if (btnResume) {
            btnResume.disabled = !isPaused;
            btnResume.classList.toggle("opacity-40", !isPaused);
            btnResume.classList.toggle("cursor-not-allowed", !isPaused);
        }
        if (btnStop) {
            btnStop.disabled = !isBusy;
            btnStop.classList.toggle("opacity-40", !isBusy);
            btnStop.classList.toggle("cursor-not-allowed", !isBusy);
        }

        const reauthBanner = document.getElementById("reauth-banner");
        if (reauthBanner) {
            if (data.state === "awaiting_reauth") reauthBanner.classList.remove("hidden");
            else reauthBanner.classList.add("hidden");
        }

        if (Array.isArray(data.recent_logs)) {
            const consoleEl = document.getElementById("exec-logs-console");
            if (consoleEl && data.recent_logs.length > 0) {
                consoleEl.innerHTML = "";
                data.recent_logs.forEach(l => {
                    const row = document.createElement("div");
                    const color = l.level === "ERROR" ? "text-rose-400" : (l.level === "WARNING" ? "text-amber-400" : "text-slate-300");
                    row.className = `py-0.5 ${color} whitespace-pre-wrap break-all`;

                    let ts = l.timestamp || '';
                    if (ts.includes('.')) ts = ts.split('.')[0];
                    if (ts.includes('T')) ts = ts.split('T')[1];

                    row.innerText = `[${ts}] [${l.level}] ${l.user_email ? '[' + l.user_email + '] ' : ''}${l.message}`;
                    consoleEl.appendChild(row);
                });
            }
        }
    } catch (e) {
        // Silent polling error
    }
}

async function startExecution(mode = "dry_run") {
    try {
        const res = await fetch("/api/execution/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mode: mode })
        });
        const data = await res.json();
        if (data.success) {
            fetchExecutionStatus();
        } else {
            alert("Failed to start execution: " + (data.error || "Unknown"));
        }
    } catch (e) {
        alert("Request error: " + e.message);
    }
}

function requestLiveExecution() {
    if (hasQueuedDeletions) {
        document.getElementById("modal-confirm-delete").classList.remove("hidden");
        document.getElementById("input-confirm-delete").value = "";
        document.getElementById("btn-modal-proceed-delete").disabled = true;
    } else {
        if (confirm("Start LIVE execution for all queued actions (Deactivate / Move)?")) {
            startExecution("live");
        }
    }
}

function checkDeleteConfirmation(val) {
    const btn = document.getElementById("btn-modal-proceed-delete");
    if (btn) btn.disabled = (val.trim() !== "CONFIRM");
}

function closeDeleteModal() {
    document.getElementById("modal-confirm-delete").classList.add("hidden");
}

function proceedLiveExecution() {
    closeDeleteModal();
    startExecution("live");
}

async function pauseExecution() { await fetch("/api/execution/pause", { method: "POST" }); fetchExecutionStatus(); }
async function resumeExecution() { await fetch("/api/execution/resume", { method: "POST" }); fetchExecutionStatus(); }
async function stopExecution() { await fetch("/api/execution/stop", { method: "POST" }); fetchExecutionStatus(); }

async function retryFailedExecution() {
    try {
        const res = await fetch("/api/execution/retry-failed", { method: "POST" });
        const data = await res.json();
        if (data.success) {
            alert(`Reset ${data.reset_count} failed users back to pending. Starting execution...`);
            loadExecutionSummary();
            startExecution("live");
        }
    } catch (e) {
        alert("Retry failed error: " + e.message);
    }
}

async function performUndo(userId, undoType = "soft") {
    if (!confirm(`Perform ${undoType.toUpperCase()} Undo for this user?`)) return;
    try {
        const res = await fetch("/api/execution/undo", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ user_id: userId, type: undoType })
        });
        const data = await res.json();
        if (data.success) {
            alert(data.message);
            loadUsers(currentPage);
        } else {
            alert("Undo failed: " + (data.error || "Unknown"));
        }
    } catch (e) {
        alert("Request error: " + e.message);
    }
}

async function submitReauth() {
    const keyId = document.getElementById("reauth-key-id").value.trim();
    const keySecret = document.getElementById("reauth-key-secret").value.trim();
    try {
        const res = await fetch("/api/execution/reauth", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ key_id: keyId, key_secret: keySecret })
        });
        const data = await res.json();
        if (data.success) {
            document.getElementById("reauth-banner").classList.add("hidden");
            fetchExecutionStatus();
        } else {
            alert("Re-authentication signal failed: " + (data.message || "Unknown"));
        }
    } catch (e) {
        alert("Error: " + e.message);
    }
}

// -------------------------------------------------------------
// ROLE GUARD SAFETY CONFIGURATION
// -------------------------------------------------------------
async function loadRoleGuardConfig() {
    try {
        const res = await fetch("/api/config/role-guard");
        const data = await res.json();
        if (data.success) {
            const chk = document.getElementById("cfg-role-guard-enabled");
            if (chk) chk.checked = !!data.role_guard_enabled;
            const rolesInput = document.getElementById("cfg-protected-roles");
            if (rolesInput && Array.isArray(data.protected_roles)) {
                rolesInput.value = data.protected_roles.join(", ");
            }
            const groupsInput = document.getElementById("cfg-protected-groups");
            if (groupsInput && Array.isArray(data.protected_groups)) {
                groupsInput.value = data.protected_groups.join(", ");
            }
        }
    } catch (e) {
        console.error("Failed to load role guard config", e);
    }
}

async function saveRoleGuardConfig() {
    const enabled = document.getElementById("cfg-role-guard-enabled").checked;
    const rolesRaw = document.getElementById("cfg-protected-roles").value.trim();
    const groupsRaw = document.getElementById("cfg-protected-groups").value.trim();

    const rolesList = rolesRaw.split(",").map(s => s.trim()).filter(Boolean);
    const groupsList = groupsRaw.split(",").map(s => s.trim()).filter(Boolean);

    try {
        const res = await fetch("/api/config/role-guard", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                role_guard_enabled: enabled,
                protected_roles: rolesList,
                protected_groups: groupsList
            })
        });
        const data = await res.json();
        if (data.success) {
            alert("Role Guard configuration saved successfully.");
        } else {
            alert("Failed to save Role Guard: " + (data.error || "Unknown"));
        }
    } catch (e) {
        alert("Error saving Role Guard: " + e.message);
    }
}

// -------------------------------------------------------------
// FAULT TOLERANCE & CRASH RECOVERY
// -------------------------------------------------------------
async function checkInterruptedRun() {
    try {
        const res = await fetch("/api/runs/interrupted");
        const data = await res.json();
        if (data.has_interrupted_run && data.run) {
            currentInterruptedRunId = data.run.id;
            const banner = document.getElementById("interrupted-banner");
            const text = document.getElementById("interrupted-text");
            if (banner && text) {
                text.innerText = `Crash Recovery: Run #${data.run.id} (${data.run.run_mode.toUpperCase()}) was abruptly interrupted at checkpoint (${data.run.processed_count} of ${data.run.total_candidates} users). You can resume execution from where it stopped.`;
                banner.classList.remove("hidden");
            }
        }
    } catch (e) {
        console.error("Failed to check interrupted run", e);
    }
}

async function resumeCurrentInterruptedRun() {
    if (!currentInterruptedRunId) return;
    try {
        const res = await fetch(`/api/runs/${currentInterruptedRunId}/resume`, { method: "POST" });
        const data = await res.json();
        if (data.success) {
            document.getElementById("interrupted-banner").classList.add("hidden");
            switchTab("tab-execution");
            fetchExecutionStatus();
        } else {
            alert("Failed to resume run: " + (data.error || "Unknown"));
        }
    } catch (e) {
        alert("Resume error: " + e.message);
    }
}

async function dismissCurrentInterruptedRun() {
    if (!currentInterruptedRunId) return;
    try {
        await fetch(`/api/runs/${currentInterruptedRunId}/dismiss`, { method: "POST" });
        document.getElementById("interrupted-banner").classList.add("hidden");
    } catch (e) {
        console.error("Failed to dismiss interrupted run", e);
    }
}

// -------------------------------------------------------------
// AUDIT RUNS & LOGS (PHASE 5)
// -------------------------------------------------------------
async function loadExecutionRuns() {
    try {
        const res = await fetch("/api/execution/runs");
        const data = await res.json();
        const tbody = document.getElementById("runs-table-body");
        if (!tbody) return;
        tbody.innerHTML = "";

        if (!data.runs || data.runs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="9" class="py-6 text-center text-slate-500 italic">No execution runs recorded yet.</td></tr>';
            return;
        }

        data.runs.forEach(r => {
            const tr = document.createElement("tr");
            tr.className = "hover:bg-slate-800/40 transition";

            // Status Badge with realistic semantics: if completed with 100% failures, show red Failed
            let statusBadgeClass = "bg-slate-800 text-slate-400";
            let statusText = r.status || "unknown";

            if (r.status === "completed") {
                if (r.failed_count > 0 && r.success_count === 0 && r.skipped_count === 0) {
                    statusBadgeClass = "bg-rose-950 text-rose-400 border border-rose-800";
                    statusText = "failed";
                } else if (r.failed_count > 0) {
                    statusBadgeClass = "bg-amber-950 text-amber-300 border border-amber-800";
                    statusText = "partial";
                } else {
                    statusBadgeClass = "bg-emerald-950 text-emerald-400 border border-emerald-800";
                    statusText = "completed";
                }
            } else if (r.status === "running") {
                statusBadgeClass = "bg-blue-950 text-blue-400 border border-blue-800 animate-pulse";
            }

            // Clean ISO Timestamp without microseconds
            let displayTime = r.start_time || '-';
            if (displayTime.includes('.')) {
                displayTime = displayTime.split('.')[0].replace('T', ' ');
            } else if (displayTime.includes('T')) {
                displayTime = displayTime.replace('T', ' ');
            }

            tr.innerHTML = `
                <td class="py-2.5 px-3 font-bold text-white whitespace-nowrap">#${r.id}</td>
                <td class="py-2.5 px-3 uppercase text-blue-400 font-semibold whitespace-nowrap">${r.run_mode}</td>
                <td class="py-2.5 px-3 whitespace-nowrap">
                    <span class="inline-flex items-center px-2 py-0.5 rounded text-[10px] ${statusBadgeClass} font-medium">
                        ${statusText}
                    </span>
                </td>
                <td class="py-2.5 px-3 text-slate-300 whitespace-nowrap">${r.total_candidates}</td>
                <td class="py-2.5 px-3 text-emerald-400 font-semibold whitespace-nowrap">${r.success_count}</td>
                <td class="py-2.5 px-3 text-amber-400 whitespace-nowrap">${r.skipped_count}</td>
                <td class="py-2.5 px-3 text-rose-400 whitespace-nowrap">${r.failed_count}</td>
                <td class="py-2.5 px-3 text-slate-400 font-mono text-[11px] whitespace-nowrap">${displayTime}</td>
                <td class="py-2.5 px-3 whitespace-nowrap">
                    <a href="/api/execution/runs/${r.id}/export" download class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg border border-slate-700 text-xs transition inline-flex items-center gap-1 shadow-sm">
                        <span>📥 Export CSV</span>
                    </a>
                </td>
            `;
            tbody.appendChild(tr);
        });
    } catch (e) {
        console.error("Failed to load execution runs", e);
    }
}

async function refreshLogs() {
    try {
        const res = await fetch("/api/execution/status");
        const data = await res.json();
        const container = document.getElementById("logs-container");
        if (!container) return;
        container.innerHTML = "";

        if (!data.recent_logs || data.recent_logs.length === 0) {
            container.innerHTML = '<div class="text-slate-500 italic">No logs recorded yet.</div>';
            return;
        }

        data.recent_logs.forEach(l => {
            const div = document.createElement("div");
            const color = l.level === "ERROR" ? "text-rose-400" : (l.level === "WARNING" ? "text-amber-400" : "text-slate-300");
            div.className = `py-0.5 ${color} whitespace-pre-wrap break-all`;

            // Clean ISO Timestamp without microseconds
            let ts = l.timestamp || '';
            if (ts.includes('.')) {
                ts = ts.split('.')[0];
            }
            if (ts.includes('T')) {
                ts = ts.split('T')[1];
            }

            div.innerText = `[${ts}] [${l.level}] ${l.user_email ? '[' + l.user_email + '] ' : ''}${l.message}`;
            container.appendChild(div);
        });
    } catch (e) {
        console.error("Failed to refresh logs", e);
    }
}

async function cleanAllLogs() {
    if (!confirm("Are you sure you want to permanently delete ALL system audit logs?")) return;
    try {
        const res = await fetch("/api/logs", { method: "DELETE" });
        const data = await res.json();
        if (data.success) {
            alert(`Cleared ${data.deleted_logs} logs.`);
            refreshLogs();
        }
    } catch (e) {
        alert("Error cleaning logs: " + e.message);
    }
}

async function cleanLogsOlderThan(days) {
    if (!confirm(`Delete system audit logs older than ${days} days?`)) return;
    try {
        const res = await fetch(`/api/logs?older_than_days=${days}`, { method: "DELETE" });
        const data = await res.json();
        if (data.success) {
            alert(`Cleared ${data.deleted_logs} logs older than ${days} days.`);
            refreshLogs();
        }
    } catch (e) {
        alert("Error cleaning logs: " + e.message);
    }
}

// -------------------------------------------------------------
// IN-APP USER GUIDE & SEARCHABLE HELP
// -------------------------------------------------------------
async function loadHelpDocs() {
    try {
        const res = await fetch("/api/docs/content");
        const data = await res.json();
        if (data.success && Array.isArray(data.docs)) {
            allHelpDocs = data.docs;
            renderHelpDocs(allHelpDocs);
        }
    } catch (e) {
        console.error("Failed to load help docs", e);
    }
}

function renderHelpDocs(docs) {
    const container = document.getElementById("help-articles-container");
    if (!container) return;
    container.innerHTML = "";

    if (!docs || docs.length === 0) {
        container.innerHTML = '<div class="col-span-2 py-8 text-center text-slate-500 italic text-xs">No matching documentation topics found.</div>';
        return;
    }

    docs.forEach(doc => {
        const card = document.createElement("div");
        card.className = "bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-3";
        card.innerHTML = `
            <div class="flex items-center justify-between">
                <span class="text-[10px] font-bold uppercase tracking-wider text-blue-400 bg-blue-950/80 px-2 py-0.5 rounded border border-blue-800/60">${doc.category}</span>
                <span class="text-xs text-slate-500 font-mono">#${doc.id}</span>
            </div>
            <h3 class="text-sm font-bold text-white">${doc.title}</h3>
            <p class="text-xs text-slate-400 font-medium">${doc.summary}</p>
            <div class="text-xs text-slate-300 border-t border-slate-800 pt-3 leading-relaxed">
                ${doc.content}
            </div>
        `;
        container.appendChild(card);
    });
}

function filterHelpDocs(query) {
    const q = query.trim().toLowerCase();
    if (!q) {
        renderHelpDocs(allHelpDocs);
        return;
    }
    const filtered = allHelpDocs.filter(d => 
        d.title.toLowerCase().includes(q) ||
        d.category.toLowerCase().includes(q) ||
        d.summary.toLowerCase().includes(q) ||
        d.content.toLowerCase().includes(q)
    );
    renderHelpDocs(filtered);
}
