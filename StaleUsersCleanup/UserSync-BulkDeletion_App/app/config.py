import os
import json
import base64
from typing import Dict, Any, List, Optional
from app.database import get_db_connection, DEFAULT_DB_PATH

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEGACY_DIR = os.path.join(BASE_DIR, "UserSync-BulkDeletion")


def get_default_config_file() -> Optional[str]:
    candidates = [
        os.path.join(BASE_DIR, "config.json"),
        os.path.join(LEGACY_DIR, "config.json"),
        os.path.join(LEGACY_DIR, "config-prod.json"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def get_default_forests_file() -> Optional[str]:
    candidates = [
        os.path.join(BASE_DIR, "ad_forests.json"),
        os.path.join(LEGACY_DIR, "ad_forests.json"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def load_config(db_path: str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    """
    Loads config prioritizing:
    1. SQLite app_config table
    2. Local JSON config files (config.json, ad_forests.json)
    3. Defaults
    """
    cfg: Dict[str, Any] = {
        "api_server": "https://api-vip.elastica.net/",
        "tenant": "",
        "key_id": "",
        "key_secret": "",
        "user_id": "",
        "ad_forests": [],
        "rate_limit_per_second": 2.5,
        "hourly_call_limit": 10000,
        "max_retries": 5,
        "base_backoff_seconds": 15,
        "inactivity_period_days": 90,
        "ad_safety_lock": True,
        "extra_ad_attributes": ["proxyAddresses", "sAMAccountName", "mail", "userPrincipalName"],
        "ad_username": "",
        "ad_password": "",
        "ad_use_ssl": False,
        "ad_port": 389,
        "ad_manual_mode": False,
        "ad_manual_host": "",
        "ad_manual_port": 389,
        "ad_manual_use_ssl": False,
        "ad_manual_base_dn": "",
        "ad_manual_auth_type": "SIMPLE",
        "ui_theme": "theme-slate",
        "role_guard_enabled": True,
        "protected_roles": ["System Admin", "Admin", "Co-Admin", "Security Admin", "Super Admin"],
        "protected_groups": ["Administrators", "Domain Admins", "Enterprise Admins", "Schema Admins", "Cloudsoc_PowerAdmins"],
    }

    # 1. Fallback to json files if present
    cfg_file = get_default_config_file()
    if cfg_file:
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                file_data = json.load(f)
                for k, v in file_data.items():
                    if k in cfg and v:
                        cfg[k] = v
        except Exception:
            pass

    forests_file = get_default_forests_file()
    if forests_file:
        try:
            with open(forests_file, "r", encoding="utf-8") as f:
                forests_data = json.load(f)
                if isinstance(forests_data, list):
                    cfg["ad_forests"] = forests_data
        except Exception:
            pass

    # 2. Override with DB configs
    try:
        with get_db_connection(db_path) as conn:
            rows = conn.execute("SELECT key, value FROM app_config").fetchall()
            for row in rows:
                k, v = row["key"], row["value"]
                if not v:
                    continue
                try:
                    parsed = json.loads(v)
                    cfg[k] = parsed
                except Exception:
                    cfg[k] = v
    except Exception:
        pass

    return cfg


def save_config(new_config: Dict[str, Any], db_path: str = DEFAULT_DB_PATH) -> None:
    """Save configuration values into the local SQLite database."""
    with get_db_connection(db_path) as conn:
        for k, v in new_config.items():
            val_str = json.dumps(v) if isinstance(v, (dict, list, bool, int, float)) else str(v)
            conn.execute("""
                INSERT INTO app_config (key, value, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
            """, (k, val_str))
        conn.commit()


def mask_secret(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 8:
        return "********"
    return f"{secret[:4]}...{secret[-4:]}"


def get_masked_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    copy_cfg = dict(cfg)
    if "key_secret" in copy_cfg:
        copy_cfg["key_secret_masked"] = mask_secret(str(copy_cfg.get("key_secret", "")))
        copy_cfg["has_key_secret"] = bool(copy_cfg.get("key_secret"))
        del copy_cfg["key_secret"]
    if "ad_password" in copy_cfg:
        copy_cfg["has_ad_password"] = bool(copy_cfg.get("ad_password"))
        del copy_cfg["ad_password"]
    return copy_cfg


def generate_auth_header(key_id: str, key_secret: str) -> str:
    """
    Exact replication of Cloudsoc-v2.1.psm1 authentication:
    $authInfo = [System.Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes(("{0}:{1}" -f $key_id, $key_secret)))
    "Authorization" = "Basic $authInfo"
    """
    combined = f"{key_id}:{key_secret}"
    b64 = base64.b64encode(combined.encode("utf-8")).decode("utf-8")
    return f"Basic {b64}"
