import sys
import os
import threading
import time
import webbrowser

# Ensure current directory is in sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
os.chdir(BASE_DIR)

from app.main import app, init_db, DEFAULT_DB_PATH


def open_browser(url: str, delay: float = 1.2):
    time.sleep(delay)
    try:
        webbrowser.open(url)
    except Exception:
        pass


if __name__ == "__main__":
    init_db(DEFAULT_DB_PATH)
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "127.0.0.1")
    app_url = f"http://{host}:{port}"

    print("=" * 70)
    print(" Broadcom Symantec CloudSOC User Cleanup Utility (v2.3.0)")
    print(" Enterprise Zero-Install Portable Distribution")
    print("=" * 70)
    print(f" [✓] SQLite Database Path: {DEFAULT_DB_PATH}")
    print(f" [✓] Server Endpoint:      {app_url} ({host}:{port})")

    # Launch browser automatically unless suppressed
    if not os.environ.get("NO_BROWSER"):
        print(f" [*] Opening web dashboard in default browser...")
        threading.Thread(target=open_browser, args=(app_url,), daemon=True).start()

    print("=" * 70)
    print(" Press CTRL+C in this console to safely shut down the server.")
    print("=" * 70)

    app.run(host=host, port=port, debug=False, threaded=True)
