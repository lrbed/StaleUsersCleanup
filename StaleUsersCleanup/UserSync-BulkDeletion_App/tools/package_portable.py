#!/usr/bin/env python3
"""
Packaging script for Broadcom Symantec CloudSOC User Cleanup Utility.
Builds a 100% self-contained, zero-install portable Windows distribution
that requires NO Python and NO database installed on target machines.
"""

import os
import sys
import shutil
import zipfile
import subprocess
from datetime import datetime

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST_DIR = os.path.join(APP_ROOT, "dist")
PACKAGE_NAME = "CloudSOC-Cleanup-Utility-v2.3.0-Portable"
TARGET_DIR = os.path.join(DIST_DIR, PACKAGE_NAME)
ZIP_PATH = os.path.join(DIST_DIR, f"{PACKAGE_NAME}.zip")
SYSTEM_PYTHON_DIR = os.path.dirname(sys.executable)


def log(msg: str):
    print(f"[*] {msg}")


def clean_previous():
    log("Cleaning previous build artifacts...")
    runtime_dir = os.path.join(TARGET_DIR, "runtime")
    is_self_run = os.path.exists(runtime_dir) and (
        os.path.abspath(SYSTEM_PYTHON_DIR) == os.path.abspath(runtime_dir)
    )

    if os.path.exists(TARGET_DIR):
        if is_self_run:
            log("Packaging executed from bundled runtime — preserving active runtime directory.")
            for item in os.listdir(TARGET_DIR):
                if item not in ("runtime", "data"):
                    item_path = os.path.join(TARGET_DIR, item)
                    if os.path.isdir(item_path):
                        shutil.rmtree(item_path, ignore_errors=True)
                    else:
                        try:
                            os.remove(item_path)
                        except Exception:
                            pass
        else:
            shutil.rmtree(TARGET_DIR, ignore_errors=True)
            os.makedirs(TARGET_DIR, exist_ok=True)
    else:
        os.makedirs(TARGET_DIR, exist_ok=True)

    if os.path.exists(ZIP_PATH):
        try:
            os.remove(ZIP_PATH)
        except Exception:
            pass


def copy_python_runtime():
    runtime_dir = os.path.join(TARGET_DIR, "runtime")
    if os.path.exists(runtime_dir) and os.path.abspath(SYSTEM_PYTHON_DIR) == os.path.abspath(runtime_dir):
        log("Embedded Python runtime already present and active; skipping self-copy.")
        return

    log(f"Bundling embedded Python 3.11 runtime from: {SYSTEM_PYTHON_DIR}...")
    os.makedirs(runtime_dir, exist_ok=True)

    # 1. Core Executables and DLLs
    core_files = [
        "python.exe", "pythonw.exe", "python3.dll", "python311.dll",
        "vcruntime140.dll", "vcruntime140_1.dll", "LICENSE.txt"
    ]
    for cf in core_files:
        src = os.path.join(SYSTEM_PYTHON_DIR, cf)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(runtime_dir, cf))

    # 2. C Extensions (DLLs)
    src_dlls = os.path.join(SYSTEM_PYTHON_DIR, "DLLs")
    dst_dlls = os.path.join(runtime_dir, "DLLs")
    if os.path.exists(src_dlls):
        shutil.copytree(
            src_dlls, dst_dlls,
            ignore=shutil.ignore_patterns("tcl*", "tk*", "_tkinter*", "*.pdb")
        )

    # 3. Standard Library (Lib) excluding unneeded test suites
    src_lib = os.path.join(SYSTEM_PYTHON_DIR, "Lib")
    dst_lib = os.path.join(runtime_dir, "Lib")
    log("Copying standard library and site-packages (Flask, ldap3, Werkzeug)...")
    shutil.copytree(
        src_lib, dst_lib,
        ignore=shutil.ignore_patterns(
            "test", "idlelib", "tkinter", "turtledemo", "__pycache__", "*.pyc", "*.pdb"
        )
    )

    # 4. Ensure Flask and LDAP3 dependencies are installed in portable runtime
    log("Ensuring Flask and LDAP3 dependencies are installed in portable runtime...")
    site_packages = os.path.join(dst_lib, "site-packages")
    os.makedirs(site_packages, exist_ok=True)
    subprocess.run([
        sys.executable, "-m", "pip", "install", "flask", "ldap3", "--target", site_packages
    ], check=True, capture_output=True)

    log(f"Embedded Python runtime packaged: {runtime_dir}")


def copy_application():
    log("Bundling application source, static web assets, and runtime scripts...")

    # 1. Copy app package
    dst_app = os.path.join(TARGET_DIR, "app")
    shutil.copytree(
        os.path.join(APP_ROOT, "app"), dst_app,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
    )

    # 2. Copy run_server.py
    shutil.copy2(os.path.join(APP_ROOT, "run_server.py"), os.path.join(TARGET_DIR, "run_server.py"))

    # 3. Copy tenant configs
    dst_tenants = os.path.join(TARGET_DIR, "tenant_configs")
    os.makedirs(dst_tenants, exist_ok=True)

    src_bulk = os.path.join(APP_ROOT, "UserSync-BulkDeletion")
    if os.path.exists(src_bulk):
        for f in os.listdir(src_bulk):
            if f.lower().startswith("config") and f.lower().endswith(".json"):
                shutil.copy2(os.path.join(src_bulk, f), os.path.join(dst_tenants, f))
            elif f.lower().endswith(".csv"):
                shutil.copy2(os.path.join(src_bulk, f), os.path.join(dst_tenants, f))

    # 4. Create empty data directory for portable database
    data_dir = os.path.join(TARGET_DIR, "data")
    os.makedirs(data_dir, exist_ok=True)


def create_launchers():
    log("Generating zero-install Windows launchers...")

    # 1. Batch Launcher (Universal, No UAC required)
    bat_content = """@echo off
title Broadcom Symantec CloudSOC User Cleanup Utility (Portable)
cd /d "%~dp0"

echo ======================================================================
echo  Broadcom Symantec CloudSOC User Cleanup Utility (v2.3.0)
echo  Zero-Install Enterprise Portable Distribution
echo ======================================================================
echo  [✓] Initializing embedded runtime...
echo  [✓] Starting server on http://127.0.0.1:5000 ...
echo  [*] Opening dashboard in default web browser...
echo.
echo  Keep this console window open while using the application.
echo ======================================================================
echo.

"%~dp0runtime\\python.exe" run_server.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] The utility terminated unexpectedly (Exit Code: %ERRORLEVEL%).
    pause
)
"""
    with open(os.path.join(TARGET_DIR, "Launch-CloudSOC-Utility.bat"), "w", encoding="utf-8") as f:
        f.write(bat_content)

    # 2. PowerShell Launcher
    ps1_content = """# Broadcom Symantec CloudSOC User Cleanup Utility - PowerShell Launcher
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host " Broadcom Symantec CloudSOC User Cleanup Utility (v2.3.0)" -ForegroundColor Cyan
Write-Host " Zero-Install Enterprise Portable Distribution" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host " [✓] Embedded runtime loaded from $ScriptDir\\runtime" -ForegroundColor Green
Write-Host " [*] Launching server and browser interface..." -ForegroundColor Yellow
Write-Host "======================================================================" -ForegroundColor Cyan

& "$ScriptDir\\runtime\\python.exe" run_server.py
"""
    with open(os.path.join(TARGET_DIR, "Launch-CloudSOC-Utility.ps1"), "w", encoding="utf-8") as f:
        f.write(ps1_content)

    # 3. Portable Readme
    readme_content = f"""======================================================================
 Broadcom Symantec CloudSOC User Cleanup Utility (v2.3.0)
 100% Zero-Install Portable Windows Distribution
 Build Date: {datetime.now().strftime('%Y-%m-%d')}
======================================================================

QUICK START INSTRUCTIONS (NEW INSTALL):
----------------------------------------
1. Extract this entire zip file to any directory on your computer 
   (e.g., C:\\Tools\\CloudSOC-Utility or your Desktop).

2. Double-click "Launch-CloudSOC-Utility.bat" (or right-click -> Open).
   - NO Python installation required!
   - NO database installation required (embedded SQLite)!
   - NO administrative rights required!

3. Your default web browser (Edge or Chrome) will open automatically to:
   http://127.0.0.1:5000

4. Keep the console window open while performing operations.
   To stop the utility, simply close the console window or press CTRL+C.

UPGRADING AN EXISTING INSTALLATION:
-----------------------------------
1. Stop the currently running utility (close the console window).
2. Extract this new zip to a new folder (e.g., C:\\Tools\\CloudSOC-Utility-v2.3.0\\).
3. Copy your existing 'cloudsoc_state.db' (from the old folder or old 'data/' folder)
   into this new folder's 'data/' directory.
4. (Optional) Copy any custom 'config-*.json' files into 'tenant_configs/'.
5. Double-click "Launch-CloudSOC-Utility.bat" in the new folder.
   All historical users, staged actions, and audit logs will be loaded automatically!

FILES & DIRECTORIES:
--------------------
- Launch-CloudSOC-Utility.bat : Double-click to start the utility.
- runtime/                   : Pre-packaged Python 3.11 + Flask + LDAP3 runtime.
- app/                       : Web application frontend and backend logic.
- data/                      : Portable database location (cloudsoc_state.db).
- tenant_configs/            : Discovered tenant configuration JSON templates.

SUPPORT & AUDITING:
-------------------
All actions are logged locally in SQLite. Hard deletes require operator
confirmation, and Active Directory safety locks prevent unverified deletions.
======================================================================
"""
    with open(os.path.join(TARGET_DIR, "README_PORTABLE.txt"), "w", encoding="utf-8") as f:
        f.write(readme_content)


def test_portable_runtime():
    log("Validating portable runtime self-sufficiency...")
    bundled_python = os.path.join(TARGET_DIR, "runtime", "python.exe")
    if not os.path.exists(bundled_python):
        raise FileNotFoundError(f"Bundled python not found at {bundled_python}")

    test_cmd = [
        bundled_python, "-c",
        "import flask, ldap3, sqlite3, importlib.metadata; print('Self-test OK: Flask', importlib.metadata.version('flask'), 'LDAP3', ldap3.__version__, 'SQLite', sqlite3.sqlite_version)"
    ]
    res = subprocess.run(test_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Portable runtime validation failed: {res.stderr}")
    log(f"Validation successful: {res.stdout.strip()}")


def create_zip_archive():
    log(f"Compressing into portable distribution archive: {ZIP_PATH}...")
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(TARGET_DIR):
            for file in files:
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, os.path.dirname(TARGET_DIR))
                zipf.write(file_path, rel_path)

    size_mb = os.path.getsize(ZIP_PATH) / (1024 * 1024)
    log(f"Package build complete! Total archive size: {size_mb:.1f} MB")
    print("\n" + "=" * 70)
    print(f" PORTABLE BUNDLE READY:")
    print(f" Folder:  {TARGET_DIR}")
    print(f" Archive: {ZIP_PATH} ({size_mb:.1f} MB)")
    print("=" * 70 + "\n")


def main():
    print("=" * 70)
    print(" BUILDING PORTABLE DISTRIBUTION: Symantec CloudSOC Cleanup Utility")
    print("=" * 70)
    clean_previous()
    copy_python_runtime()
    copy_application()
    create_launchers()
    test_portable_runtime()
    create_zip_archive()


if __name__ == "__main__":
    main()
