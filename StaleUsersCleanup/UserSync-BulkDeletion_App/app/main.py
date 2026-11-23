import os
import io
import csv
import json
import socket
from datetime import datetime, timedelta, timezone
from flask import Flask, jsonify, request, send_from_directory, Response

from app.database import (
    init_db,
    get_db_connection,
    get_latest_interrupted_run,
    get_all_interrupted_runs,
    dismiss_interrupted_run,
    set_users_action,
    get_distinct_groups,
    clean_logs,
    get_execution_runs,
    get_run_details,
    DEFAULT_DB_PATH
)
from app.config import (
    load_config,
    save_config,
    get_masked_config,
    BASE_DIR
)
from app.cloudsoc_client import CloudsocClient
from app.ldap_client import LDAPSyncEngine
from app.sync_service import SyncService
from app.execution_service import ExecutionService

app = Flask(__name__, static_folder="static")
sync_service = SyncService(DEFAULT_DB_PATH)
execution_service = ExecutionService(DEFAULT_DB_PATH)


@app.before_request
def startup_init():
    # Ensure database schema exists
    init_db(DEFAULT_DB_PATH)


# -------------------------------------------------------------
# STATIC FILES / DASHBOARD
# -------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/static/<path:filename>")
def serve_static(filename):
    return send_from_directory(app.static_folder, filename)


# -------------------------------------------------------------
# CONFIGURATION ENDPOINTS
# -------------------------------------------------------------
@app.route("/api/config", methods=["GET"])
def get_config():
    cfg = load_config(DEFAULT_DB_PATH)
    masked = get_masked_config(cfg)
    return jsonify({"success": True, "config": masked})


@app.route("/api/config", methods=["POST"])
def update_config():
    data = request.get_json() or {}

    # Protect secret from being overwritten by masked placeholder
    if "key_secret" in data and ("*" in str(data["key_secret"]) or "..." in str(data["key_secret"])):
        del data["key_secret"]

    save_config(data, DEFAULT_DB_PATH)
    updated = load_config(DEFAULT_DB_PATH)
    return jsonify({"success": True, "config": get_masked_config(updated)})


@app.route("/api/config/upload", methods=["POST"])
def upload_config():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file uploaded"}), 400

    f = request.files["file"]
    filename = f.filename.lower()

    try:
        content = json.loads(f.read().decode("utf-8"))
    except Exception as e:
        return jsonify({"success": False, "error": f"Invalid JSON file: {str(e)}"}), 400

    if "ad_forests" in filename:
        if isinstance(content, list):
            save_config({"ad_forests": content}, DEFAULT_DB_PATH)
            return jsonify({"success": True, "message": f"Loaded {len(content)} AD forests"})
    else:
        # Standard config.json
        save_config(content, DEFAULT_DB_PATH)
        return jsonify({"success": True, "message": "API credentials saved successfully"})

    return jsonify({"success": False, "error": "Unrecognized JSON format"}), 400


def find_tenant_folder():
    candidates = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tenant_configs"),
        os.path.join(os.getcwd(), "tenant_configs"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "UserSync-BulkDeletion"),
        os.path.join(os.getcwd(), "UserSync-BulkDeletion"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "UserSync-BulkDeletion"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return os.path.abspath(c)
    return None


@app.route("/api/config/detected-tenants", methods=["GET"])
def get_detected_tenants():
    """Scan and list discovered CloudSOC tenant configuration JSON files."""
    folder = find_tenant_folder()
    if not folder:
        return jsonify({"success": True, "tenants": [], "folder": None})

    tenants = []
    for fname in sorted(os.listdir(folder)):
        if fname.lower().startswith("config") and fname.lower().endswith(".json"):
            fpath = os.path.join(folder, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    cfg_data = json.load(f)
                if isinstance(cfg_data, dict) and "tenant" in cfg_data:
                    base = fname[:-5]
                    profile_name = base[7:] if base.startswith("config-") else "default"
                    tenants.append({
                        "filename": fname,
                        "profile_name": profile_name,
                        "tenant": cfg_data.get("tenant", ""),
                        "api_server": cfg_data.get("api_server", ""),
                        "key_id": cfg_data.get("key_id", ""),
                        "user_id": cfg_data.get("user_id", "")
                    })
            except Exception:
                continue

    return jsonify({"success": True, "tenants": tenants, "folder": folder})


@app.route("/api/config/switch-tenant", methods=["POST"])
def switch_tenant():
    """Silently switch active credentials to a detected tenant JSON profile without exposing secret."""
    data = request.get_json() or {}
    filename = data.get("filename")
    if not filename:
        return jsonify({"success": False, "error": "Missing 'filename' parameter"}), 400

    safe_filename = os.path.basename(filename)
    folder = find_tenant_folder()
    if not folder:
        return jsonify({"success": False, "error": "UserSync-BulkDeletion directory not found"}), 404

    fpath = os.path.join(folder, safe_filename)
    if not os.path.exists(fpath):
        return jsonify({"success": False, "error": f"Profile file '{safe_filename}' not found"}), 404

    try:
        with open(fpath, "r", encoding="utf-8") as f:
            cfg_data = json.load(f)

        if not isinstance(cfg_data, dict) or "tenant" not in cfg_data:
            return jsonify({"success": False, "error": "Invalid tenant JSON format"}), 400

        save_config(cfg_data, DEFAULT_DB_PATH)
        return jsonify({
            "success": True,
            "message": f"Switched to tenant profile '{safe_filename}' ({cfg_data.get('tenant')})",
            "tenant": cfg_data.get("tenant", ""),
            "api_server": cfg_data.get("api_server", ""),
            "key_id": cfg_data.get("key_id", "")
        })
    except Exception as e:
        return jsonify({"success": False, "error": f"Failed to switch tenant: {str(e)}"}), 500


@app.route("/api/config/upload-silent", methods=["POST"])
def upload_config_silent():
    """Silently import CloudSOC credentials JSON file or payload without displaying secret in UI."""
    content = None
    if "file" in request.files:
        f = request.files["file"]
        try:
            content = json.loads(f.read().decode("utf-8"))
        except Exception as e:
            return jsonify({"success": False, "error": f"Invalid JSON file: {str(e)}"}), 400
    else:
        content = request.get_json()

    if not isinstance(content, dict) or "tenant" not in content:
        return jsonify({"success": False, "error": "Invalid JSON: missing required 'tenant' property"}), 400

    save_config(content, DEFAULT_DB_PATH)
    return jsonify({
        "success": True,
        "message": f"Tenant '{content.get('tenant')}' credentials imported silently.",
        "tenant": content.get("tenant"),
        "api_server": content.get("api_server"),
        "key_id": content.get("key_id")
    })



@app.route("/api/config/role-guard", methods=["GET"])
def get_role_guard_config():
    """Retrieve configurable Role Guard parameters."""
    cfg = load_config(DEFAULT_DB_PATH)
    return jsonify({
        "success": True,
        "role_guard_enabled": bool(cfg.get("role_guard_enabled", True)),
        "protected_roles": cfg.get("protected_roles", ["System Admin", "Admin", "Co-Admin", "Security Admin", "Super Admin"]),
        "protected_groups": cfg.get("protected_groups", ["Administrators", "Domain Admins", "Enterprise Admins", "Schema Admins", "Cloudsoc_PowerAdmins"])
    })


@app.route("/api/config/role-guard", methods=["POST"])
def update_role_guard_config():
    """Update configurable Role Guard parameters."""
    data = request.get_json() or {}
    updates = {}
    if "role_guard_enabled" in data:
        updates["role_guard_enabled"] = bool(data["role_guard_enabled"])
    if "protected_roles" in data:
        updates["protected_roles"] = data["protected_roles"]
    if "protected_groups" in data:
        updates["protected_groups"] = data["protected_groups"]

    save_config(updates, DEFAULT_DB_PATH)
    return jsonify({"success": True, "message": "Role Guard settings saved", "updates": updates})


# -------------------------------------------------------------
# PREFLIGHT CONNECTIVITY CHECKS (3-TIER STATUS)
# -------------------------------------------------------------
@app.route("/api/preflight", methods=["POST", "GET"])
def run_preflight():
    """
    Validates:
    1. CloudSOC DNS & Auth API
    2. Active Directory DNS & Port connectivity
    3. Local SQLite database readiness

    Status Tiers:
    - Green ("passed"): All healthy (CloudSOC + DB + AD)
    - Yellow ("warning"): CloudSOC + DB healthy, but AD unavailable / warning
    - Red ("failed"): Either CloudSOC or DB unavailable
    """
    cfg = load_config(DEFAULT_DB_PATH)
    checks = {}

    # 1. CloudSOC Connectivity & Auth
    cloudsoc_status = {
        "status": "pending",
        "dns_resolved": False,
        "auth_valid": False,
        "latency_ms": 0,
        "details": ""
    }

    server_host = cfg.get("api_server", "").replace("https://", "").replace("http://", "").split("/")[0]
    if not server_host or not cfg.get("tenant") or not cfg.get("key_id"):
        cloudsoc_status["status"] = "failed"
        cloudsoc_status["details"] = "Missing CloudSOC credentials (server, tenant, or key_id)"
    else:
        try:
            ip = socket.gethostbyname(server_host)
            cloudsoc_status["dns_resolved"] = True
            cloudsoc_status["resolved_ip"] = ip
        except Exception as e:
            cloudsoc_status["status"] = "failed"
            cloudsoc_status["details"] = f"DNS resolution failed for {server_host}: {str(e)}"

        if cloudsoc_status["dns_resolved"]:
            try:
                from app.cloudsoc_client import get_global_rate_limiter
                shared_limiter = get_global_rate_limiter(
                    max_per_second=float(cfg.get("rate_limit_per_second", 2.5)),
                    max_per_hour=int(cfg.get("hourly_call_limit", 10000)),
                    db_path=DEFAULT_DB_PATH
                )
                client = CloudsocClient(
                    api_server=cfg["api_server"],
                    tenant=cfg["tenant"],
                    key_id=cfg["key_id"],
                    key_secret=cfg.get("key_secret", ""),
                    rate_limiter=shared_limiter,
                    db_path=DEFAULT_DB_PATH
                )
                auth_res = client.test_connection()
                cloudsoc_status["auth_valid"] = True
                cloudsoc_status["latency_ms"] = auth_res["latency_ms"]
                cloudsoc_status["total_users_in_cloudsoc"] = auth_res["total_users_in_cloudsoc"]
                cloudsoc_status["status"] = "passed"
                cloudsoc_status["details"] = f"Authenticated OK. CloudSOC tenant has {auth_res['total_users_in_cloudsoc']} users."
            except Exception as e:
                cloudsoc_status["status"] = "failed"
                cloudsoc_status["details"] = f"CloudSOC Authentication Failed: {str(e)}"

    checks["cloudsoc"] = cloudsoc_status

    # 2. Active Directory / LDAP Connectivity
    manual_mode = bool(cfg.get("ad_manual_mode", False))
    if manual_mode:
        m_host = cfg.get("ad_manual_host", "")
        m_port = int(cfg.get("ad_manual_port", 389))
        m_ssl = bool(cfg.get("ad_manual_use_ssl", False))
        m_base = cfg.get("ad_manual_base_dn", "")
        m_user = cfg.get("ad_username", "")
        m_pass = cfg.get("ad_password", "")

        ldap_engine = LDAPSyncEngine(
            manual_mode=True,
            manual_host=m_host,
            manual_port=m_port,
            manual_use_ssl=m_ssl,
            manual_base_dn=m_base,
            username=m_user,
            password=m_pass
        )
        m_res = ldap_engine.test_manual_ad_connection(
            host=m_host,
            port=m_port,
            username=m_user,
            password=m_pass,
            use_ssl=m_ssl,
            base_dn=m_base
        )
        ad_status_code = "passed" if m_res["status"] == "Passed" else ("warning" if m_res["status"] == "Warning" else "failed")
        checks["active_directory"] = {
            "mode": "manual",
            "status": ad_status_code,
            "health": "Healthy (Manual DC)" if m_res["status"] == "Passed" else "Manual DC Check Failed",
            "details": m_res["message"],
            "manual_result": m_res,
            "ad_safety_lock": cfg.get("ad_safety_lock", True)
        }
    else:
        forests = cfg.get("ad_forests", [])
        ldap_engine = LDAPSyncEngine(forests=forests)
        ad_results = []
        ad_all_passed = True

        if not forests:
            ad_results.append({
                "forest": "None Configured",
                "status": "Warning",
                "message": "No AD forests defined. AD Safety Lock will fail-closed on external users."
            })
            ad_all_passed = False
        else:
            for f in forests:
                forest_check = ldap_engine.test_forest_connectivity(f)
                ad_results.append(forest_check)
                if forest_check["status"] not in ("Connected", "Passed"):
                    ad_all_passed = False

        ad_status_code = "passed" if ad_all_passed and forests else ("warning" if not forests else "warning")
        checks["active_directory"] = {
            "mode": "auto",
            "status": ad_status_code,
            "health": "Healthy" if ad_all_passed and forests else ("Partially Ready" if not forests else "Unavailable"),
            "forests": ad_results,
            "ad_safety_lock": cfg.get("ad_safety_lock", True)
        }

    # 3. Database Check
    try:
        with get_db_connection(DEFAULT_DB_PATH) as conn:
            user_count = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        checks["database"] = {
            "status": "passed",
            "details": f"SQLite database connected. Current cached users: {user_count}"
        }
    except Exception as e:
        checks["database"] = {
            "status": "failed",
            "details": f"Database initialization error: {str(e)}"
        }

    # 3-Tier Classification
    cs_ok = (checks["cloudsoc"]["status"] == "passed")
    db_ok = (checks["database"]["status"] == "passed")
    ad_ok = (checks["active_directory"]["status"] == "passed")

    if cs_ok and db_ok and ad_ok:
        overall_tier = "green"
        tier_label = "Healthy & Fully Ready"
        overall_ready = True
    elif cs_ok and db_ok:
        overall_tier = "yellow"
        tier_label = "Partially Ready (AD Unavailable - Safety Lock Active)"
        overall_ready = True
    else:
        overall_tier = "red"
        tier_label = "Not Ready (CloudSOC or DB Error)"
        overall_ready = False

    return jsonify({
        "success": True,
        "overall_ready": overall_ready,
        "overall_tier": overall_tier,
        "tier_label": tier_label,
        "ad_health": checks["active_directory"]["health"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks
    })


# -------------------------------------------------------------
# SYNC ENGINE ENDPOINTS (WITH TARGETED INGESTION)
# -------------------------------------------------------------
@app.route("/api/sync/cloudsoc/start", methods=["POST"])
def start_cloudsoc_sync():
    data = request.get_json() or {}
    batch_size = int(data.get("batch_size", 100))
    criteria = data.get("criteria")
    result = sync_service.start_cloudsoc_sync(batch_size=batch_size, criteria=criteria)
    status_code = 200 if result.get("success") else 400
    return jsonify(result), status_code


@app.route("/api/sync/ldap/start", methods=["POST"])
def start_ldap_sync():
    data = request.get_json() or {}
    recheck_all = bool(data.get("recheck_all", False))
    result = sync_service.start_ldap_sync(recheck_all=recheck_all)
    status_code = 200 if result.get("success") else 400
    return jsonify(result), status_code


@app.route("/api/sync/csv/start", methods=["POST"])
def start_csv_sync():
    data = request.get_json() or {}
    file_path = data.get("file_path")
    if not file_path:
        candidates = [
            os.path.join(BASE_DIR, "UserSync-BulkDeletion", "exported_users.csv"),
            os.path.join(BASE_DIR, "exported_users.csv")
        ]
        for c in candidates:
            if os.path.isfile(c):
                file_path = c
                break

    if not file_path or not os.path.isfile(file_path):
        return jsonify({"success": False, "error": f"CSV file not found: {file_path}"}), 400

    res = sync_service.import_csv_file(file_path)
    return jsonify(res)


@app.route("/api/sync/pause", methods=["POST"])
def pause_sync():
    ok = sync_service.pause()
    return jsonify({"success": ok})


@app.route("/api/sync/resume", methods=["POST"])
def resume_sync():
    ok = sync_service.resume()
    return jsonify({"success": ok})


@app.route("/api/sync/cancel", methods=["POST"])
def cancel_sync():
    ok = sync_service.cancel()
    return jsonify({"success": ok})


@app.route("/api/sync/status", methods=["GET"])
def get_sync_status():
    return jsonify(sync_service.get_status())


# -------------------------------------------------------------
# EXECUTION ENGINE ENDPOINTS (PHASE 4)
# -------------------------------------------------------------
@app.route("/api/execution/start", methods=["POST"])
def start_execution():
    """Start action plan execution in 'dry_run' or 'live' mode."""
    data = request.get_json() or {}
    mode = data.get("mode", "dry_run").lower()
    res = execution_service.start_execution(mode=mode)
    status_code = 200 if res.get("success") else 400
    return jsonify(res), status_code


@app.route("/api/execution/pause", methods=["POST"])
def pause_execution():
    ok = execution_service.pause()
    return jsonify({"success": ok})


@app.route("/api/execution/resume", methods=["POST"])
def resume_execution():
    ok = execution_service.resume()
    return jsonify({"success": ok})


@app.route("/api/execution/stop", methods=["POST"])
def stop_execution():
    ok = execution_service.stop()
    return jsonify({"success": ok})


@app.route("/api/execution/retry-failed", methods=["POST"])
def retry_failed_execution():
    res = execution_service.retry_failed()
    return jsonify(res)


@app.route("/api/execution/status", methods=["GET"])
def get_execution_status():
    return jsonify(execution_service.get_status())


@app.route("/api/execution/undo", methods=["POST"])
def undo_action():
    """
    Perform Soft Undo (re-activate) or Hard Undo (re-create in CloudSOC from raw_json).
    """
    data = request.get_json() or {}
    user_id = data.get("user_id")
    undo_type = data.get("type", "soft").lower()

    if not user_id:
        return jsonify({"success": False, "error": "user_id is required"}), 400

    res = execution_service.undo_action(user_id=int(user_id), undo_type=undo_type)
    status_code = 200 if res.get("success") else 400
    return jsonify(res), status_code


@app.route("/api/execution/reauth", methods=["POST"])
def execution_reauth():
    """Update credentials and signal execution engine to resume after 401/403."""
    data = request.get_json() or {}
    if data.get("key_id") or data.get("key_secret"):
        save_config(data, DEFAULT_DB_PATH)

    ok = execution_service.handle_reauth_complete()
    return jsonify({"success": ok, "message": "Credentials updated and queue resumed" if ok else "Queue not in awaiting_reauth state"})


# -------------------------------------------------------------
# TELEMETRY & BACKOFF OVERRIDE
# -------------------------------------------------------------
@app.route("/api/telemetry", methods=["GET"])
def get_telemetry():
    """Live rate limiting and API telemetry counters from the shared global rate limiter."""
    cfg = load_config(DEFAULT_DB_PATH)
    from app.cloudsoc_client import get_global_rate_limiter
    limiter = get_global_rate_limiter(
        max_per_second=float(cfg.get("rate_limit_per_second", 2.5)),
        max_per_hour=int(cfg.get("hourly_call_limit", 10000)),
        db_path=DEFAULT_DB_PATH
    )
    return jsonify({"success": True, "telemetry": limiter.get_telemetry()})


@app.route("/api/telemetry/override-backoff", methods=["POST"])
def override_backoff():
    """Administrator 'Try Now' button to bypass active backoff."""
    ok = execution_service.override_backoff()
    return jsonify({"success": ok, "message": "Backoff wait interrupted" if ok else "No active backoff found"})


# -------------------------------------------------------------
# AUDIT RUNS, LOGS & EXPORT (PHASE 5)
# -------------------------------------------------------------
@app.route("/api/execution/runs", methods=["GET"])
def list_execution_runs():
    limit = int(request.args.get("limit", 50))
    runs = get_execution_runs(limit=limit, db_path=DEFAULT_DB_PATH)
    return jsonify({"success": True, "runs": runs})


@app.route("/api/execution/runs/<int:run_id>/export", methods=["GET"])
def export_run_csv(run_id: int):
    """Export run audit log as a CSV file."""
    run_info = get_run_details(run_id, DEFAULT_DB_PATH)
    if not run_info:
        return jsonify({"success": False, "error": f"Run #{run_id} not found"}), 404

    logs = run_info.get("logs", [])
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Log ID", "Timestamp", "Level", "User Email", "Action", "Status", "Message"])
    for l in logs:
        writer.writerow([
            l.get("id"),
            l.get("timestamp"),
            l.get("level"),
            l.get("user_email") or "",
            l.get("action") or "",
            l.get("status") or "",
            l.get("message") or ""
        ])

    csv_data = output.getvalue()
    filename = f"cloudsoc_execution_run_{run_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.route("/api/logs", methods=["DELETE"])
def delete_logs():
    """Clear logs with optional 'older_than_days' filter."""
    older_days = request.args.get("older_than_days")
    days_int = int(older_days) if (older_days and older_days.isdigit()) else None
    deleted = clean_logs(older_than_days=days_int, db_path=DEFAULT_DB_PATH)
    return jsonify({"success": True, "deleted_logs": deleted})


# -------------------------------------------------------------
# USERS & METRICS ENDPOINTS
# -------------------------------------------------------------
@app.route("/api/stats", methods=["GET"])
def get_stats():
    with get_db_connection(DEFAULT_DB_PATH) as conn:
        total = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        active_cloudsoc = conn.execute("SELECT COUNT(*) as c FROM users WHERE is_active = 1").fetchone()["c"]
        inactive_cloudsoc = total - active_cloudsoc

        ad_active = conn.execute("SELECT COUNT(*) as c FROM users WHERE ad_status = 'Active'").fetchone()["c"]
        ad_disabled = conn.execute("SELECT COUNT(*) as c FROM users WHERE ad_status = 'Disabled'").fetchone()["c"]
        ad_notfound = conn.execute("SELECT COUNT(*) as c FROM users WHERE ad_status = 'NotFound'").fetchone()["c"]
        ad_error = conn.execute("SELECT COUNT(*) as c FROM users WHERE ad_status = 'Error'").fetchone()["c"]
        ad_unchecked = conn.execute("SELECT COUNT(*) as c FROM users WHERE ad_status = 'Unchecked'").fetchone()["c"]

        candidates_ad = ad_disabled + ad_notfound

    return jsonify({
        "total_users": total,
        "cloudsoc_active": active_cloudsoc,
        "cloudsoc_inactive": inactive_cloudsoc,
        "ad_status": {
            "active": ad_active,
            "disabled": ad_disabled,
            "not_found": ad_notfound,
            "error": ad_error,
            "unchecked": ad_unchecked
        },
        "potential_candidates": candidates_ad
    })


@app.route("/api/users", methods=["GET"])
def get_users():
    """
    Server-side paginated user listing with advanced compound filtering (Phase 3).
    Supports regex matching, inactivity thresholds, origin (gatelet/securlet),
    group membership, and Active Directory safety status.
    """
    page = int(request.args.get("page", 1))
    limit = min(int(request.args.get("limit", 25)), 100)
    search = request.args.get("search", "").strip()
    regex = request.args.get("regex", "").strip()
    ad_status = request.args.get("ad_status", "").strip()
    is_active = request.args.get("is_active", "").strip()
    stale_only = request.args.get("stale_only", "").strip().lower() == "true"
    created_by = request.args.get("created_by", "").strip()
    origin = request.args.get("origin", "").strip().lower()
    group = request.args.get("group", "").strip()
    inactivity_mode = request.args.get("inactivity_mode", "").strip().lower()
    inactivity_days = request.args.get("inactivity_days", "").strip()
    selected_action = request.args.get("selected_action", "").strip()

    offset = (page - 1) * limit
    query = "SELECT * FROM users WHERE 1=1"
    params = []

    # 1. Keyword search (email, name, secondary_user_id)
    if search:
        query += " AND (email LIKE ? OR first_name LIKE ? OR last_name LIKE ? OR secondary_user_id LIKE ?)"
        term = f"%{search}%"
        params.extend([term, term, term, term])

    # 2. Regex search on User ID / email / secondary_user_id (FILT-03)
    if regex:
        query += " AND (email REGEXP ? OR secondary_user_id REGEXP ?)"
        params.extend([regex, regex])

    # 3. Active Directory Status (Active, Disabled, NotFound, Error, Unchecked)
    if ad_status:
        query += " AND ad_status = ?"
        params.append(ad_status)

    # 4. CloudSOC Active Status
    if is_active != "":
        query += " AND is_active = ?"
        params.append(1 if is_active.lower() in ("1", "true") else 0)

    # 5. Creator filter
    if created_by:
        query += " AND created_by LIKE ?"
        params.append(f"%{created_by}%")

    # 6. Origin filter (Gatelet vs Securlet) (FILT-04)
    if origin == "gatelet":
        query += " AND (created_by LIKE '%Directory Sync%' OR created_by LIKE '%SpanVA%' OR created_by LIKE '%admin%' OR created_by = 'System' OR created_by LIKE '%API%' OR created_by LIKE '%manual%')"
    elif origin == "securlet":
        query += " AND (created_by LIKE '%Securlet%' OR created_by LIKE '%Office%' OR created_by LIKE '%Google%' OR created_by LIKE '%Box%' OR created_by LIKE '%Salesforce%' OR created_by LIKE '%OIDC%')"

    # 7. Group membership filter
    if group:
        query += " AND groups LIKE ?"
        params.append(f"%{group}%")

    # 8. Inactivity filtering (FILT-02)
    cutoff_date_str = None
    if inactivity_days and inactivity_days.isdigit() and int(inactivity_days) > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(inactivity_days))
        cutoff_date_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    if inactivity_mode == "blank_only":
        query += " AND (last_activity_date IS NULL OR TRIM(last_activity_date) = '')"
    elif cutoff_date_str:
        query += " AND (last_activity_date IS NULL OR TRIM(last_activity_date) = '' OR last_activity_date < ?)"
        params.append(cutoff_date_str)

    # 9. Selected action filter
    if selected_action:
        query += " AND selected_action = ?"
        params.append(selected_action)

    # 10. Compound Stale condition (FILT-05)
    if stale_only:
        stale_cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
        query += " AND (ad_status IN ('Disabled', 'NotFound') OR is_active = 0 OR last_activity_date IS NULL OR TRIM(last_activity_date) = '' OR last_activity_date < ?)"
        params.append(stale_cutoff)

    count_query = f"SELECT COUNT(*) as total FROM ({query})"

    with get_db_connection(DEFAULT_DB_PATH) as conn:
        total_count = conn.execute(count_query, params).fetchone()["total"]

        query += " ORDER BY id ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()
        users = [dict(r) for r in rows]

    return jsonify({
        "page": page,
        "limit": limit,
        "total": total_count,
        "total_pages": (total_count + limit - 1) // limit if limit > 0 else 0,
        "users": users
    })


@app.route("/api/users/prepare-action", methods=["POST"])
def prepare_action():
    """Mark users with a selected action (deactivate, move_group, delete, none)."""
    data = request.get_json() or {}
    action = data.get("action", "none").lower()
    user_ids = data.get("user_ids", [])

    if not isinstance(user_ids, list):
        return jsonify({"success": False, "error": "user_ids must be a list"}), 400

    try:
        updated_count = set_users_action(action=action, user_ids=user_ids, db_path=DEFAULT_DB_PATH)
        return jsonify({"success": True, "updated_count": updated_count, "action": action})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/groups", methods=["GET"])
@app.route("/api/users/groups", methods=["GET"])
def list_groups():
    """Returns list of distinct CloudSOC groups found in the cached user profiles."""
    groups = get_distinct_groups(DEFAULT_DB_PATH)
    return jsonify({"success": True, "groups": groups})


@app.route("/api/stats/stale-breakdown", methods=["GET"])
def get_stale_breakdown():
    """Detailed breakdown of stale user categories."""
    cutoff_90 = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection(DEFAULT_DB_PATH) as conn:
        total = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        missing_in_ad = conn.execute("SELECT COUNT(*) as c FROM users WHERE ad_status = 'NotFound'").fetchone()["c"]
        disabled_in_ad = conn.execute("SELECT COUNT(*) as c FROM users WHERE ad_status = 'Disabled'").fetchone()["c"]
        inactive_90d = conn.execute("SELECT COUNT(*) as c FROM users WHERE (last_activity_date IS NULL OR TRIM(last_activity_date) = '' OR last_activity_date < ?)", (cutoff_90,)).fetchone()["c"]
        cloudsoc_inactive = conn.execute("SELECT COUNT(*) as c FROM users WHERE is_active = 0").fetchone()["c"]

        # Action preparation counts
        queued_deactivate = conn.execute("SELECT COUNT(*) as c FROM users WHERE selected_action = 'deactivate' AND action_status = 'pending'").fetchone()["c"]
        queued_move = conn.execute("SELECT COUNT(*) as c FROM users WHERE selected_action = 'move_group' AND action_status = 'pending'").fetchone()["c"]
        queued_delete = conn.execute("SELECT COUNT(*) as c FROM users WHERE selected_action = 'delete' AND action_status = 'pending'").fetchone()["c"]

    return jsonify({
        "total_users": total,
        "missing_in_ad": missing_in_ad,
        "disabled_in_ad": disabled_in_ad,
        "inactive_90d_or_blank": inactive_90d,
        "cloudsoc_inactive": cloudsoc_inactive,
        "queued_actions": {
            "deactivate": queued_deactivate,
            "move_group": queued_move,
            "delete": queued_delete,
            "total_pending": queued_deactivate + queued_move + queued_delete
        }
    })


# -------------------------------------------------------------
# APPLICATION EXIT GUARD & ACTIVE OPERATIONS
# -------------------------------------------------------------
@app.route("/api/app/active-operations", methods=["GET"])
def get_active_operations():
    """Probe whether any sync or execution task is currently active (for beforeunload warning)."""
    sync_running = sync_service.is_running()
    exec_running = execution_service.current_state in ("running", "paused", "awaiting_reauth")
    return jsonify({
        "has_active_operations": sync_running or exec_running,
        "sync_running": sync_running,
        "execution_running": exec_running,
        "execution_state": execution_service.current_state,
        "execution_mode": execution_service.run_mode
    })


# -------------------------------------------------------------
# MANUAL ACTIVE DIRECTORY DIAGNOSTIC TEST
# -------------------------------------------------------------
@app.route("/api/preflight/ad-manual-test", methods=["POST"])
def test_manual_ad_endpoint():
    """Test connection to a specific Domain Controller manually."""
    data = request.get_json() or {}
    engine = LDAPSyncEngine()
    result = engine.test_manual_ad_connection(
        host=data.get("host"),
        port=int(data.get("port", 389)),
        username=data.get("username", ""),
        password=data.get("password", ""),
        use_ssl=bool(data.get("use_ssl", False)),
        base_dn=data.get("base_dn", "")
    )
    return jsonify(result)


# -------------------------------------------------------------
# FAULT TOLERANCE & CRASH RECOVERY
# -------------------------------------------------------------
@app.route("/api/runs/interrupted", methods=["GET"])
def check_interrupted():
    """Returns historical runs interrupted by crash/restart for auto-discovery."""
    runs = get_all_interrupted_runs(DEFAULT_DB_PATH)
    latest = runs[0] if runs else None
    return jsonify({
        "has_interrupted_run": len(runs) > 0,
        "run": latest,
        "all_interrupted": runs
    })


@app.route("/api/runs/<int:run_id>/resume", methods=["POST"])
def resume_interrupted_run_endpoint(run_id: int):
    """Resumes an interrupted run from its exact SQLite checkpoint."""
    res = execution_service.resume_interrupted_run(run_id)
    status_code = 200 if res.get("success") else 400
    return jsonify(res), status_code


@app.route("/api/runs/<int:run_id>/dismiss", methods=["POST"])
def dismiss_interrupted_run_endpoint(run_id: int):
    """Dismisses an interrupted run so it stops prompting."""
    ok = dismiss_interrupted_run(run_id, DEFAULT_DB_PATH)
    return jsonify({"success": ok})


# -------------------------------------------------------------
# IN-APP USER HELP & DOCUMENTATION
# -------------------------------------------------------------
@app.route("/api/docs/content", methods=["GET"])
def get_docs_content():
    """Provides structured user guide content for in-app search & help modal."""
    docs = [
        {
            "id": "quick-start",
            "category": "Getting Started",
            "title": "Quick Start & System Architecture",
            "summary": "Overview of local SQLite state architecture, zero third-party leakage, and workflow.",
            "content": "The Symantec CloudSOC User Cleanup Utility is a local, stateful web tool designed for Broadcom CloudSOC (Elastica) administrators. It cross-checks user directories against Active Directory forests to identify stale, deactivated, or deleted employees without risking production outages. All state is maintained locally in cloudsoc_state.db."
        },
        {
            "id": "api-rate-limits",
            "category": "CloudSOC API",
            "title": "API Credentials, Rate Limits & Exponential Backoff",
            "summary": "Understanding the 2.5 req/sec rate limit, 10k/hr quota, and Try-Now override.",
            "content": "Broadcom CloudSOC enforces a strict threshold of ~2.5 requests per second (10,000 calls/hour). The built-in RateLimiter token bucket ensures requests never exceed this boundary. If an HTTP 429 occurs, the system initiates an exponential backoff sequence (15s, 30s, 60s, 120s, 240s) and displays an alert banner with an instant 'Try Now' override button."
        },
        {
            "id": "ad-safety-lock",
            "category": "Active Directory",
            "title": "AD Safety Lock & Fail-Closed Protection",
            "summary": "How the fail-closed guarantee prevents accidental deletion of active employees.",
            "content": "When AD Safety Lock is enabled, the cleanup engine strictly FAILS CLOSED. Any user whose Active Directory status is 'Active' will be automatically skipped from deletion or deactivation. Furthermore, if Active Directory is unreachable or returns a network error, the utility marks the user as 'Error' and blocks execution."
        },
        {
            "id": "manual-ad-connection",
            "category": "Active Directory",
            "title": "Manual Domain Controller Configuration",
            "summary": "Direct host/IP fallback when automatic DNS forest discovery fails.",
            "content": "If multi-forest DNS auto-discovery fails due to enterprise routing or isolated lab environments, administrators can switch to 'Manual Direct Connection'. Enter the explicit Domain Controller IP or FQDN, LDAP port (389 or 636 for LDAPS), Search Base DN (e.g., DC=corp,DC=local), and bind credentials."
        },
        {
            "id": "targeted-ingestion",
            "category": "Data Ingestion",
            "title": "Targeted & Stale-Only Ingestion",
            "summary": "Conserving API quotas by importing only stale or specific user subsets.",
            "content": "Admins managing large directories (100,000+ users) can use Targeted Ingestion to filter accounts before writing to SQLite. Criteria can restrict downloads by maximum count (e.g. 1,000 users), email domain suffix, NetBIOS domain prefix (DOMAIN\\user), CloudSOC group, or Stale-Only (inactivity threshold > X days)."
        },
        {
            "id": "role-guard",
            "category": "Safety & Guards",
            "title": "Configurable Role Safety Guard",
            "summary": "Protecting administrators and high-privilege service accounts.",
            "content": "The Role Safety Guard prevents privileged accounts (e.g., System Admin, Super Admin, Domain Admins) from being modified or deleted. Administrators can customize the list of protected roles and protected groups from the Execution tab."
        },
        {
            "id": "disaster-recovery",
            "category": "Recovery & Undo",
            "title": "Soft Undo, Hard Undo & Checkpoint Recovery",
            "summary": "Re-activating deactivated accounts, reconstructing deleted profiles from raw JSON, and crash recovery.",
            "content": "Soft Undo re-activates a deactivated user via a PATCH call. Hard Undo reconstructs a permanently deleted user via POST using the cached raw_json profile snapshot stored prior to deletion. If the server is abruptly shut down during an execution run, the system auto-detects the interrupted job on startup and allows one-click resumption from the exact last checkpoint."
        }
    ]
    return jsonify({"success": True, "docs": docs})


if __name__ == "__main__":
    init_db(DEFAULT_DB_PATH)
    app.run(host="127.0.0.1", port=5000, debug=False)
