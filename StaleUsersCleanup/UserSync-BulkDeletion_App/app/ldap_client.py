import socket
import ssl
from typing import Dict, Any, List, Optional
try:
    import ldap3
    from ldap3 import Server, Connection, ALL, SUBTREE, NTLM, SIMPLE
    HAS_LDAP3 = True
except ImportError:
    HAS_LDAP3 = False

# Active Directory userAccountControl flags
UF_ACCOUNTDISABLE = 0x0002


class LDAPSyncEngine:
    """
    Active Directory / LDAP synchronization engine with Fail-Closed safety.
    Cross-checks CloudSOC users against enterprise Active Directory forests.
    """
    def __init__(
        self,
        forests: Optional[List[str]] = None,
        username: str = "",
        password: str = "",
        use_ssl: bool = False,
        default_port: int = 3268,  # Global Catalog by default
        extra_attributes: Optional[List[str]] = None,
        logger_func=None,
        manual_mode: bool = False,
        manual_host: str = "",
        manual_port: int = 389,
        manual_use_ssl: bool = False,
        manual_base_dn: str = "",
        manual_auth_type: str = "SIMPLE"
    ):
        self.forests = forests or []
        self.username = username
        self.password = password
        self.use_ssl = use_ssl
        self.default_port = default_port
        self.extra_attributes = extra_attributes or ["proxyAddresses", "sAMAccountName", "mail", "userPrincipalName"]
        self.logger = logger_func or (lambda lvl, msg: None)
        self.manual_mode = manual_mode
        self.manual_host = manual_host.strip()
        self.manual_port = manual_port or 389
        self.manual_use_ssl = manual_use_ssl
        self.manual_base_dn = manual_base_dn.strip()
        self.manual_auth_type = manual_auth_type.upper() if manual_auth_type else "SIMPLE"

    def test_manual_ad_connection(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        use_ssl: Optional[bool] = None,
        base_dn: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Tests direct connectivity, bind authentication, and base DN search against
        a specific Domain Controller host/IP.
        """
        target_host = (host or self.manual_host).strip()
        target_port = port if port is not None else self.manual_port
        target_user = username if username is not None else self.username
        target_pass = password if password is not None else self.password
        target_ssl = use_ssl if use_ssl is not None else self.manual_use_ssl
        target_base = (base_dn or self.manual_base_dn).strip()

        result = {
            "host": target_host,
            "port": target_port,
            "use_ssl": target_ssl,
            "socket_connected": False,
            "bind_success": False,
            "base_dn_valid": False,
            "status": "Failed",
            "message": ""
        }

        if not target_host:
            result["message"] = "No manual AD host or IP specified."
            return result

        # 1. TCP / TLS Socket Check
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(3.0)
            res = sock.connect_ex((target_host, target_port))
            if res != 0:
                result["message"] = f"TCP port {target_port} on {target_host} is closed or unreachable."
                return result
            result["socket_connected"] = True
        except Exception as ex:
            result["message"] = f"Socket connection error to {target_host}:{target_port}: {str(ex)}"
            return result
        finally:
            sock.close()

        if not HAS_LDAP3:
            result["status"] = "Connected"
            result["message"] = "TCP port reachable (ldap3 library not available for bind test)."
            return result

        # 2. LDAP Bind Check
        try:
            server = Server(target_host, port=target_port, use_ssl=target_ssl, get_info=ALL, connect_timeout=4)
            if target_user and target_pass:
                conn = Connection(server, user=target_user, password=target_pass, authentication=SIMPLE, auto_bind=True)
            else:
                conn = Connection(server, auto_bind=True)

            result["bind_success"] = True

            # 3. Search Base DN Check (if specified)
            if target_base:
                try:
                    conn.search(search_base=target_base, search_filter="(objectClass=*)", search_scope=SUBTREE, size_limit=1)
                    result["base_dn_valid"] = True
                except Exception as b_ex:
                    result["base_dn_valid"] = False
                    result["message"] = f"Bind succeeded, but Search Base DN query failed: {str(b_ex)}"
                    conn.unbind()
                    result["status"] = "Warning"
                    return result

            conn.unbind()
            result["status"] = "Passed"
            result["message"] = f"Successfully authenticated to {target_host}:{target_port} via LDAP."
            return result

        except Exception as ex:
            result["message"] = f"LDAP Bind Failed: {str(ex)}"
            return result

    def test_forest_connectivity(self, forest: str) -> Dict[str, Any]:
        """
        Tests DNS resolution and TCP port connectivity for an AD forest.
        Checks both Global Catalog (3268) and standard LDAP (389).
        """
        result = {
            "forest": forest,
            "dns_resolved": False,
            "ip_address": None,
            "port_3268_open": False,
            "port_389_open": False,
            "port_636_open": False,
            "status": "Failed",
            "message": ""
        }

        # 1. DNS Resolution
        try:
            ip = socket.gethostbyname(forest)
            result["dns_resolved"] = True
            result["ip_address"] = ip
        except socket.gaierror as e:
            result["status"] = "DNS_Failed"
            result["message"] = f"DNS resolution failed: {e.strerror or str(e)}"
            return result

        # 2. Port Checks
        for port, key in [(3268, "port_3268_open"), (389, "port_389_open"), (636, "port_636_open")]:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.settimeout(2.5)
                res = sock.connect_ex((result["ip_address"], port))
                if res == 0:
                    result[key] = True
            except Exception:
                pass
            finally:
                try:
                    sock.close()
                except Exception:
                    pass

        if result["port_3268_open"] or result["port_389_open"] or result["port_636_open"]:
            result["status"] = "Connected"
            result["message"] = "Host reachable and LDAP port responsive"
        else:
            result["status"] = "Port_Unreachable"
            result["message"] = "Host resolved, but LDAP ports (3268, 389, 636) are unreachable/closed"

        return result

    def get_ad_user_status(self, email: str, forests: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Replicates Get-ADUserSafetyStatus from Cloudsoc.psm1.
        Strictly FAILS CLOSED: Any connectivity/bind error returns status 'Error'.
        """
        target_forests = forests or self.forests
        if not target_forests:
            # Default to local machine domain check
            target_forests = [None]

        clean_email = email.strip()
        safe_email = clean_email.replace("'", "").replace(")", "").replace("(", "").replace("\\", "")

        # Fallback to pure socket check if ldap3 is not available
        if not HAS_LDAP3:
            return {
                "status": "Error",
                "forest": "-",
                "details": "ldap3 module not installed in Python environment"
            }

        # Construct LDAP search filter matching UserSync.ps1
        filter_parts = [
            f"(mail={safe_email})",
            f"(userPrincipalName={safe_email})",
            f"(sAMAccountName={safe_email})",
            f"(proxyAddresses=smtp:{safe_email})",
            f"(proxyAddresses=SMTP:{safe_email})"
        ]
        ldap_filter = f"(|{''.join(filter_parts)})"

        attributes_to_fetch = ["userAccountControl", "mail", "userPrincipalName", "sAMAccountName", "displayName"]
        for attr in self.extra_attributes:
            if attr not in attributes_to_fetch:
                attributes_to_fetch.append(attr)

        targets = []
        if self.manual_mode and self.manual_host:
            targets.append({
                "name": f"ManualDC ({self.manual_host})",
                "host": self.manual_host,
                "port": self.manual_port,
                "use_ssl": self.manual_use_ssl,
                "base_dn": self.manual_base_dn
            })
        else:
            for forest in target_forests:
                forest_name = forest if forest else "LocalDomain"
                server_host = forest if forest else "localhost"
                port = 636 if self.use_ssl else self.default_port
                base_dn = ",".join([f"DC={part}" for part in forest.split(".") if part]) if forest and "." in forest else ""
                targets.append({
                    "name": forest_name,
                    "host": server_host,
                    "port": port,
                    "use_ssl": self.use_ssl,
                    "base_dn": base_dn
                })

        for target in targets:
            forest_name = target["name"]
            server_host = target["host"]
            port = target["port"]
            target_ssl = target["use_ssl"]
            search_base = target["base_dn"]

            try:
                # Test basic socket first to fail fast with clean diagnostic
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    sock.settimeout(2.0)
                    conn_res = sock.connect_ex((server_host, port))
                finally:
                    sock.close()

                if conn_res != 0 and not self.manual_mode:
                    # Try port 389 fallback only for auto-discovery
                    port = 389
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    try:
                        sock.settimeout(2.0)
                        conn_res = sock.connect_ex((server_host, port))
                    finally:
                        sock.close()

                if conn_res != 0:
                    return {
                        "status": "Error",
                        "forest": forest_name,
                        "details": f"TCP connection failed to {server_host} on port {port}"
                    }

                server = Server(server_host, port=port, use_ssl=target_ssl, get_info=ALL, connect_timeout=3)
                
                # Bind (authenticated if credentials provided, else anonymous)
                if self.username and self.password:
                    conn = Connection(server, user=self.username, password=self.password, authentication=SIMPLE, auto_bind=True)
                else:
                    conn = Connection(server, auto_bind=True)
                
                conn.search(
                    search_base=search_base,
                    search_filter=ldap_filter,
                    search_scope=SUBTREE,
                    attributes=attributes_to_fetch,
                    size_limit=5
                )

                entries = conn.entries
                conn.unbind()

                if entries:
                    for entry in entries:
                        uac = 0
                        if hasattr(entry, "userAccountControl") and entry.userAccountControl.value is not None:
                            try:
                                uac = int(entry.userAccountControl.value)
                            except (ValueError, TypeError):
                                uac = 0

                        is_disabled = bool(uac & UF_ACCOUNTDISABLE)
                        if not is_disabled:
                            return {
                                "status": "Active",
                                "forest": forest_name,
                                "details": f"Active account found (UAC: {uac})"
                            }
                        else:
                            return {
                                "status": "Disabled",
                                "forest": forest_name,
                                "details": f"Disabled account found (UAC: {uac})"
                            }

            except Exception as e:
                # FAIL-CLOSED: Return error instead of pretending user isn't found
                err_msg = str(e)
                self.logger("WARNING", f"[AD-CHECK] Critical Error querying {forest_name}: {err_msg}")
                return {
                    "status": "Error",
                    "forest": forest_name,
                    "details": f"LDAP Query Exception: {err_msg}"
                }

        # If searched all forests without error and no records found:
        return {
            "status": "NotFound",
            "forest": "-",
            "details": "User not found in any Active Directory forest"
        }
