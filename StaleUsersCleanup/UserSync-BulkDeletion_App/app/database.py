import sqlite3
import json
import os
import re
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

def get_default_db_path() -> str:
    env_path = os.environ.get("CLOUDSOC_DB_PATH")
    if env_path:
        return env_path
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(base_dir, "data")
    if os.path.isdir(data_dir):
        return os.path.join(data_dir, "cloudsoc_state.db")
    return os.path.join(base_dir, "cloudsoc_state.db")

DEFAULT_DB_PATH = get_default_db_path()


def _regexp(pattern: str, val: Optional[str]) -> bool:
    if not pattern:
        return True
    if val is None:
        return False
    try:
        return bool(re.search(pattern, str(val), re.IGNORECASE))
    except Exception:
        return False


def get_db_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.create_function("REGEXP", 2, _regexp)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()

        # Users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cloudsoc_id TEXT UNIQUE,
                email TEXT UNIQUE NOT NULL,
                first_name TEXT,
                last_name TEXT,
                secondary_user_id TEXT,
                created_by TEXT,
                created_on TEXT,
                modified_by TEXT,
                modified_on TEXT,
                is_active INTEGER DEFAULT 1,
                groups TEXT DEFAULT '[]',
                title TEXT,
                work_phone TEXT,
                cell_phone TEXT,
                last_activity_date TEXT,
                ad_status TEXT DEFAULT 'Unchecked',
                ad_forest TEXT,
                ad_details TEXT,
                ad_checked_at TEXT,
                stale_reason TEXT,
                selected_action TEXT DEFAULT 'none',
                action_status TEXT DEFAULT 'pending',
                action_result TEXT,
                role TEXT DEFAULT 'End User',
                is_privileged INTEGER DEFAULT 0,
                raw_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Auto-migration for existing databases
        for col_def in [("role", "TEXT DEFAULT 'End User'"), ("is_privileged", "INTEGER DEFAULT 0")]:
            try:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {col_def[0]} {col_def[1]};")
            except Exception:
                pass

        # Performance Indexes for 100,000+ users
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_cloudsoc_id ON users(cloudsoc_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_ad_status ON users(ad_status);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_is_active ON users(is_active);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_last_activity ON users(last_activity_date);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_action_status ON users(action_status);")

        # Auto-classify privileged admin accounts based on role and administrative group memberships
        try:
            cursor.execute("""
                UPDATE users
                SET is_privileged = 1,
                    role = CASE WHEN role IS NULL OR role = 'End User' THEN 'Admin (Role Guard Protected)' ELSE role END
                WHERE is_privileged = 0 AND (
                    role LIKE '%Admin%' OR role LIKE '%System%' OR role LIKE '%Security%'
                    OR groups LIKE '%Administrators%' OR groups LIKE '%Domain Admins%'
                    OR groups LIKE '%Enterprise Admins%' OR groups LIKE '%Schema Admins%'
                    OR groups LIKE '%PowerAdmin%'
                    OR email LIKE 'admin@%' OR email LIKE 'administrator@%'
                    OR email LIKE 'coadmin@%' OR email LIKE 'sysadmin@%'
                );
            """)
        except Exception:
            pass

        # Execution Runs table (State management and checkpointing)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS execution_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_time TEXT DEFAULT CURRENT_TIMESTAMP,
                end_time TEXT,
                run_mode TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                total_candidates INTEGER DEFAULT 0,
                processed_count INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                skipped_count INTEGER DEFAULT 0,
                config_snapshot TEXT,
                last_checkpoint_at TEXT
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_runs_status ON execution_runs(status);")

        # System Logs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                level TEXT NOT NULL DEFAULT 'INFO',
                user_email TEXT,
                action TEXT,
                status TEXT,
                message TEXT,
                context TEXT,
                FOREIGN KEY (run_id) REFERENCES execution_runs(id) ON DELETE SET NULL
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_run_id ON logs(run_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(level);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp);")

        # Application Configuration key-value table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS app_config (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Outgoing API Telemetry table (tracks rolling hourly calls & 429 counts persistently)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                method TEXT,
                endpoint TEXT,
                status_code INTEGER,
                is_429 INTEGER DEFAULT 0
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_timestamp ON api_telemetry(timestamp);")


        conn.commit()


def upsert_user(user_data: Dict[str, Any], db_path: str = DEFAULT_DB_PATH) -> int:
    """
    Upsert user record from Cloudsoc API object or CSV row.
    Preserves raw JSON profile for Hard Undo safety net.
    """
    email = user_data.get("email", "").strip().lower()
    if not email:
        return 0

    cloudsoc_id = user_data.get("cloudsoc_id") or user_data.get("id")
    first_name = user_data.get("first_name", "")
    last_name = user_data.get("last_name", "")
    secondary_user_id = user_data.get("secondary_user_id") or user_data.get("Secondary ID", "")
    created_by = user_data.get("created_by") or user_data.get("Created By", "")
    created_on = user_data.get("created_on") or user_data.get("Creation Date", "")
    modified_by = user_data.get("modified_by") or user_data.get("Modified By", "")
    modified_on = user_data.get("modified_on") or user_data.get("Modified Date", "")
    title = user_data.get("title") or user_data.get("Title", "")
    work_phone = user_data.get("work_phone") or user_data.get("Work Phone", "")
    cell_phone = user_data.get("cell_phone") or user_data.get("Cell Phone", "")

    # Active status boolean/string
    is_active_val = user_data.get("is_active")
    if is_active_val is None:
        active_str = str(user_data.get("Active", "yes")).strip().lower()
        is_active = 1 if active_str in ("yes", "true", "1") else 0
    else:
        is_active = 1 if bool(is_active_val) else 0

    # Groups serialization
    groups_val = user_data.get("groups", [])
    if isinstance(groups_val, list):
        groups_json = json.dumps(groups_val)
    elif isinstance(groups_val, str):
        groups_json = groups_val
    else:
        groups_json = json.dumps([])

    last_activity_date = user_data.get("last_activity_date") or user_data.get("Last Activity Date", "")
    ad_status = user_data.get("ad_status") or "Unchecked"
    ad_forest = user_data.get("ad_forest", "")
    ad_details = user_data.get("ad_details", "")
    ad_checked_at = user_data.get("ad_checked_at", "")
    role = user_data.get("role") or user_data.get("Role") or user_data.get("Admin") or "End User"
    is_privileged_val = user_data.get("is_privileged")
    if is_privileged_val is not None:
        is_privileged = 1 if bool(is_privileged_val) else 0
    else:
        role_lower = str(role).strip().lower()
        groups_raw = str(user_data.get("groups", "")).lower()
        if any(pr in role_lower for pr in ["admin", "super", "system", "security", "co-admin"]) or any(pg in groups_raw for pg in ["admin", "poweradmin"]):
            is_privileged = 1
        else:
            is_privileged = 0

    raw_json = user_data.get("raw_json")
    if raw_json is None:
        raw_json = json.dumps(user_data)

    now = datetime.now(timezone.utc).isoformat()

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO users (
                cloudsoc_id, email, first_name, last_name, secondary_user_id,
                created_by, created_on, modified_by, modified_on, is_active,
                groups, title, work_phone, cell_phone, last_activity_date,
                ad_status, ad_forest, ad_details, ad_checked_at,
                role, is_privileged, raw_json, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?
            )
            ON CONFLICT(email) DO UPDATE SET
                cloudsoc_id = COALESCE(NULLIF(excluded.cloudsoc_id, ''), users.cloudsoc_id),
                first_name = COALESCE(NULLIF(excluded.first_name, ''), users.first_name),
                last_name = COALESCE(NULLIF(excluded.last_name, ''), users.last_name),
                secondary_user_id = COALESCE(NULLIF(excluded.secondary_user_id, ''), users.secondary_user_id),
                created_by = COALESCE(NULLIF(excluded.created_by, ''), users.created_by),
                created_on = COALESCE(NULLIF(excluded.created_on, ''), users.created_on),
                modified_by = COALESCE(NULLIF(excluded.modified_by, ''), users.modified_by),
                modified_on = COALESCE(NULLIF(excluded.modified_on, ''), users.modified_on),
                is_active = excluded.is_active,
                groups = CASE WHEN excluded.groups = '[]' AND users.groups != '[]' THEN users.groups ELSE excluded.groups END,
                title = COALESCE(NULLIF(excluded.title, ''), users.title),
                work_phone = COALESCE(NULLIF(excluded.work_phone, ''), users.work_phone),
                cell_phone = COALESCE(NULLIF(excluded.cell_phone, ''), users.cell_phone),
                last_activity_date = COALESCE(NULLIF(excluded.last_activity_date, ''), users.last_activity_date),
                ad_status = CASE WHEN excluded.ad_status != 'Unchecked' THEN excluded.ad_status ELSE users.ad_status END,
                ad_forest = COALESCE(NULLIF(excluded.ad_forest, ''), users.ad_forest),
                ad_details = COALESCE(NULLIF(excluded.ad_details, ''), users.ad_details),
                ad_checked_at = COALESCE(NULLIF(excluded.ad_checked_at, ''), users.ad_checked_at),
                role = COALESCE(NULLIF(excluded.role, ''), users.role),
                is_privileged = MAX(excluded.is_privileged, users.is_privileged),
                raw_json = COALESCE(NULLIF(excluded.raw_json, ''), users.raw_json),
                updated_at = excluded.updated_at
        """, (
            cloudsoc_id, email, first_name, last_name, secondary_user_id,
            created_by, created_on, modified_by, modified_on, is_active,
            groups_json, title, work_phone, cell_phone, last_activity_date,
            ad_status, ad_forest, ad_details, ad_checked_at,
            role, is_privileged, raw_json, now
        ))
        conn.commit()
        return cursor.lastrowid or 1


def update_user_ad_status(
    email: str,
    ad_status: str,
    ad_forest: str = "",
    ad_details: str = "",
    db_path: str = DEFAULT_DB_PATH
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_db_connection(db_path) as conn:
        conn.execute("""
            UPDATE users SET
                ad_status = ?,
                ad_forest = ?,
                ad_details = ?,
                ad_checked_at = ?,
                updated_at = ?
            WHERE email = ?
        """, (ad_status, ad_forest, ad_details, now, now, email.strip().lower()))
        conn.commit()


def add_log(
    level: str,
    message: str,
    run_id: Optional[int] = None,
    user_email: Optional[str] = None,
    action: Optional[str] = None,
    status: Optional[str] = None,
    context: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO logs (run_id, timestamp, level, user_email, action, status, message, context)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (run_id, now, level.upper(), user_email, action, status, message, context))
        conn.commit()
        return cursor.lastrowid


def create_execution_run(
    run_mode: str,
    total_candidates: int = 0,
    config_snapshot: Optional[Dict[str, Any]] = None,
    db_path: str = DEFAULT_DB_PATH
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    snapshot_json = json.dumps(config_snapshot or {})
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO execution_runs (
                start_time, run_mode, status, total_candidates,
                processed_count, success_count, failed_count, skipped_count,
                config_snapshot, last_checkpoint_at
            ) VALUES (?, ?, 'running', ?, 0, 0, 0, 0, ?, ?)
        """, (now, run_mode, total_candidates, snapshot_json, now))
        conn.commit()
        return cursor.lastrowid


def update_execution_checkpoint(
    run_id: int,
    processed_count: int,
    success_count: int = 0,
    failed_count: int = 0,
    skipped_count: int = 0,
    status: str = "running",
    db_path: str = DEFAULT_DB_PATH
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_db_connection(db_path) as conn:
        conn.execute("""
            UPDATE execution_runs SET
                processed_count = ?,
                success_count = ?,
                failed_count = ?,
                skipped_count = ?,
                status = ?,
                last_checkpoint_at = ?,
                end_time = CASE WHEN ? IN ('completed', 'failed', 'interrupted') THEN ? ELSE end_time END
            WHERE id = ?
        """, (
            processed_count, success_count, failed_count, skipped_count,
            status, now, status, now, run_id
        ))
        conn.commit()


def get_latest_interrupted_run(db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    """Check for interrupted execution runs on application startup for state recovery."""
    with get_db_connection(db_path) as conn:
        row = conn.execute("""
            SELECT * FROM execution_runs
            WHERE status IN ('running', 'paused')
            ORDER BY id DESC LIMIT 1
        """).fetchone()
        if row:
            return dict(row)
    return None


def set_users_action(
    action: str,
    user_ids: Optional[List[int]] = None,
    filter_params: Optional[Dict[str, Any]] = None,
    db_path: str = DEFAULT_DB_PATH
) -> int:
    """
    Sets selected_action (deactivate, move_group, delete, none) for selected users
    or all users matching the filter criteria.
    """
    now = datetime.now(timezone.utc).isoformat()
    action = action.lower().strip()
    if action not in ("deactivate", "move_group", "delete", "none"):
        raise ValueError(f"Invalid action: {action}")

    with get_db_connection(db_path) as conn:
        if user_ids is not None:
            if not user_ids:
                return 0
            placeholders = ",".join(["?"] * len(user_ids))
            params = [action, "pending" if action != "none" else "none", now] + user_ids
            cursor = conn.execute(f"""
                UPDATE users SET
                    selected_action = ?,
                    action_status = ?,
                    updated_at = ?
                WHERE id IN ({placeholders})
            """, params)
            conn.commit()
            return cursor.rowcount
        return 0


def get_distinct_groups(db_path: str = DEFAULT_DB_PATH) -> List[str]:
    """Extract all unique group names across all cached CloudSOC user profiles."""
    import ast
    groups_set = set()
    with get_db_connection(db_path) as conn:
        rows = conn.execute("SELECT groups FROM users WHERE groups IS NOT NULL AND groups != ''").fetchall()
        for r in rows:
            raw = r["groups"]
            if not raw:
                continue
            parsed = None
            try:
                parsed = json.loads(raw)
            except Exception:
                try:
                    parsed = ast.literal_eval(raw)
                except Exception:
                    parsed = [g.strip() for g in raw.split(',') if g.strip()]
            if isinstance(parsed, list):
                for g in parsed:
                    if isinstance(g, str) and g.strip():
                        groups_set.add(g.strip())
            elif isinstance(parsed, str) and parsed.strip():
                groups_set.add(parsed.strip())
    return sorted(list(groups_set))


def clean_logs(older_than_days: Optional[int] = None, db_path: str = DEFAULT_DB_PATH) -> int:
    """Delete logs. If older_than_days is specified, delete only older logs; otherwise delete all."""
    with get_db_connection(db_path) as conn:
        if older_than_days is not None and older_than_days > 0:
            cursor = conn.execute("""
                DELETE FROM logs
                WHERE timestamp < datetime('now', '-' || ? || ' days')
            """, (older_than_days,))
        else:
            cursor = conn.execute("DELETE FROM logs")
        conn.commit()
        return cursor.rowcount


def get_execution_runs(limit: int = 50, db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """Retrieve execution run history."""
    with get_db_connection(db_path) as conn:
        rows = conn.execute("""
            SELECT id, start_time, end_time, run_mode, status, total_candidates,
                   processed_count, success_count, failed_count, skipped_count,
                   last_checkpoint_at
            FROM execution_runs
            ORDER BY id DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def get_run_details(run_id: int, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    """Retrieve details and logs for a single execution run."""
    with get_db_connection(db_path) as conn:
        run = conn.execute("SELECT * FROM execution_runs WHERE id = ?", (run_id,)).fetchone()
        if not run:
            return None
        logs = conn.execute("""
            SELECT id, timestamp, level, user_email, action, status, message
            FROM logs
            WHERE run_id = ?
            ORDER BY id ASC
        """, (run_id,)).fetchall()
        res = dict(run)
        res["logs"] = [dict(l) for l in logs]
        return res


def get_all_interrupted_runs(db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """Find all historical runs that were interrupted by crash or abrupt termination."""
    with get_db_connection(db_path) as conn:
        rows = conn.execute("""
            SELECT * FROM execution_runs
            WHERE status IN ('running', 'paused')
            ORDER BY id DESC
        """).fetchall()
        return [dict(r) for r in rows]


def dismiss_interrupted_run(run_id: int, db_path: str = DEFAULT_DB_PATH) -> bool:
    """Mark an interrupted run as dismissed/interrupted so it stops prompting."""
    with get_db_connection(db_path) as conn:
        cursor = conn.execute("""
            UPDATE execution_runs
            SET status = 'interrupted',
                end_time = datetime('now')
            WHERE id = ? AND status IN ('running', 'paused')
        """, (run_id,))
        conn.commit()
        return cursor.rowcount > 0


def record_api_call_db(timestamp: float, method: str = "", endpoint: str = "", status_code: int = 200, is_429: bool = False, db_path: str = DEFAULT_DB_PATH) -> None:
    """Record an outgoing API call timestamp into SQLite for persistent telemetry."""
    try:
        with get_db_connection(db_path) as conn:
            conn.execute("""
                INSERT INTO api_telemetry (timestamp, method, endpoint, status_code, is_429)
                VALUES (?, ?, ?, ?, ?)
            """, (timestamp, method, endpoint, status_code, 1 if is_429 else 0))
            conn.commit()
    except Exception:
        pass


def get_recent_api_timestamps(since_timestamp: float, db_path: str = DEFAULT_DB_PATH) -> List[float]:
    """Retrieve all API call timestamps since the given epoch time."""
    try:
        with get_db_connection(db_path) as conn:
            rows = conn.execute("SELECT timestamp FROM api_telemetry WHERE timestamp > ? ORDER BY timestamp ASC", (since_timestamp,)).fetchall()
            return [float(r["timestamp"]) for r in rows]
    except Exception:
        return []


def get_total_429_count_db(db_path: str = DEFAULT_DB_PATH) -> int:
    """Retrieve all-time or session count of 429 rate limit responses."""
    try:
        with get_db_connection(db_path) as conn:
            res = conn.execute("SELECT COUNT(*) as cnt FROM api_telemetry WHERE is_429 = 1").fetchone()
            return int(res["cnt"]) if res else 0
    except Exception:
        return 0



