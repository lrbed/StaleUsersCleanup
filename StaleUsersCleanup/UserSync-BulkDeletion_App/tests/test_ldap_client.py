import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ldap_client import LDAPSyncEngine, UF_ACCOUNTDISABLE


class TestLDAPClient(unittest.TestCase):
    def test_uac_flags(self):
        """Verify Active Directory bitmask logic: 0x0002 is UF_ACCOUNTDISABLE."""
        self.assertEqual(UF_ACCOUNTDISABLE, 2)

        # Standard enabled account (NORMAL_ACCOUNT = 512)
        uac_enabled = 512
        self.assertEqual(uac_enabled & UF_ACCOUNTDISABLE, 0)

        # Standard disabled account (NORMAL_ACCOUNT | ACCOUNTDISABLE = 514)
        uac_disabled = 514
        self.assertTrue(bool(uac_disabled & UF_ACCOUNTDISABLE))

        # Account with password not required + disabled (32 | 2 = 34)
        uac_disabled_pwd = 34
        self.assertTrue(bool(uac_disabled_pwd & UF_ACCOUNTDISABLE))

    def test_fail_closed_on_unreachable_forest(self):
        """
        CRITICAL REQUIREMENT:
        When LDAP server is unreachable or DNS fails, the engine MUST return status 'Error'
        and NEVER pretend the user is missing or safe to delete!
        """
        engine = LDAPSyncEngine(forests=["non-existent-domain-123456789.org"])
        res = engine.get_ad_user_status("test@domain.com")

        self.assertEqual(res["status"], "Error")
        self.assertIn("non-existent-domain-123456789.org", res["forest"])
        self.assertTrue("TCP connection failed" in res["details"] or "Exception" in res["details"] or "DNS" in res["details"])

    def test_forest_connectivity_reporting(self):
        """Verify test_forest_connectivity correctly reports DNS and port status."""
        engine = LDAPSyncEngine(forests=["non-existent-domain-123456789.org"])
        report = engine.test_forest_connectivity("non-existent-domain-123456789.org")

        self.assertFalse(report["dns_resolved"])
        self.assertEqual(report["status"], "DNS_Failed")


if __name__ == "__main__":
    unittest.main()
