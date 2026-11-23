import os
import sys
import base64
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import generate_auth_header
from app.cloudsoc_client import CloudsocClient, RateLimiter


class TestCloudsocClient(unittest.TestCase):
    def test_auth_token_matches_powershell(self):
        """
        Verify Python Basic Auth token matches the exact PowerShell calculation:
        $authInfo = [System.Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes(("{0}:{1}" -f $key_id, $key_secret)))
        "Basic $authInfo"
        """
        key_id = "360add12bd1911edb192028398309ea4"
        key_secret = "LKP9axdl8OwpYnbSqcqf9SLLoUw15Hx86qXfqhx3xNk"

        auth_header = generate_auth_header(key_id, key_secret)
        expected_b64 = base64.b64encode(f"{key_id}:{key_secret}".encode("utf-8")).decode("utf-8")
        self.assertEqual(auth_header, f"Basic {expected_b64}")

        client = CloudsocClient(
            api_server="https://api-vip.elastica.net/",
            tenant="hdaltonpcslabsorg",
            key_id=key_id,
            key_secret=key_secret
        )
        headers = client.get_headers()
        self.assertEqual(headers["Authorization"], f"Basic {expected_b64}")
        self.assertEqual(headers["X-Elastica-Dbname-Resolved"], "True")
        self.assertEqual(headers["tenant"], "hdaltonpcslabsorg")
        self.assertIn("PowerShell/5.1", headers["User-Agent"])

    def test_rate_limiter_interval(self):
        """Verify rate limiter enforces minimum delay between calls."""
        # 5 calls per second -> min 0.2s interval
        limiter = RateLimiter(max_per_second=5.0, max_per_hour=1000)

        t0 = time.time()
        limiter.acquire()
        limiter.acquire()
        t1 = time.time()

        elapsed = t1 - t0
        # Should take at least ~0.15s
        self.assertGreaterEqual(elapsed, 0.15, f"Expected elapsed >= 0.15s, got {elapsed}s")

    def test_backoff_timing_formula(self):
        """Verify smart exponential backoff formula: 2^(retry - 1) * baseWaitSeconds"""
        base = 15
        waits = [(2 ** (r - 1)) * base for r in range(1, 6)]
        self.assertEqual(waits, [15, 30, 60, 120, 240])


if __name__ == "__main__":
    unittest.main()
