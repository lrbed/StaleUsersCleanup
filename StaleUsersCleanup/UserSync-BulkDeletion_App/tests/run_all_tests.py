#!/usr/bin/env python3
"""
Master Test Runner for Symantec CloudSOC User Cleanup Utility.
Discovers and executes all 46+ unit and UAT tests across the entire application,
providing clear terminal diagnostics, timing, and category breakdowns.
"""

import os
import sys
import time
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
os.chdir(BASE_DIR)


def main():
    print("=" * 75)
    print(" Symantec CloudSOC User Cleanup Utility — Master Test Suite Runner")
    print("=" * 75)
    print(f" [✓] Base Directory: {BASE_DIR}")
    print(f" [✓] Python Runtime: {sys.executable}")
    print(" [*] Discovering all test suites in ./tests/ ...\n")

    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=os.path.join(BASE_DIR, "tests"), pattern="test_*.py")

    start_time = time.time()
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    elapsed = round(time.time() - start_time, 2)

    total_run = result.testsRun
    failures = len(result.failures)
    errors = len(result.errors)
    skipped = len(result.skipped)
    passed = total_run - (failures + errors + skipped)

    print("\n" + "=" * 75)
    print(" TEST EXECUTION SUMMARY:")
    print("=" * 75)
    print(f" Total Tests Run:  {total_run}")
    print(f" Passed:           {passed} [✓]")
    print(f" Failures:         {failures}")
    print(f" Errors:           {errors}")
    print(f" Skipped:          {skipped}")
    print(f" Elapsed Time:     {elapsed} seconds")
    print("=" * 75)

    if failures > 0 or errors > 0:
        print("\n [!] STATUS: FAILED")
        print(" [!] Please review the failure tracebacks above and consult TEST_PLAN.md.")
        sys.exit(1)
    else:
        print("\n [✓] STATUS: ALL TESTS PASSED SUCCESSFULLY!")
        sys.exit(0)


if __name__ == "__main__":
    main()
