"""Chrome Manager — Launches undetected Chrome with anti-fingerprint patches.

Run as: python core/chrome.py
- Starts Chrome directly via subprocess (no Selenium — avoids version mismatch issues)
- Runs NON-headless on Xvfb virtual display (looks like a real desktop browser)
- Persists user profile (cookies, localStorage, extensions survive restarts)
- Exposes CDP on port 9222 for Alphabetty's API to control
- Injects stealth JS via CDP after launch to patch fingerprint vectors
"""

import json
import logging
import os
import subprocess
import sys
import time
import urllib.request

logging.basicConfig(level=logging.INFO, format="%(asctime)s [chrome] %(message)s")
logger = logging.getLogger(__name__)

CDP_PORT = 9222
PROFILE_DIR = os.environ.get("ALPHABETTY_CHROME_PROFILE", "/chrome-profile")
WINDOW_SIZE = os.environ.get("ALPHABETTY_CHROME_WINDOW_SIZE", "1920,1080")
LOW_RAM = os.environ.get("ALPHABETTY_LOW_RAM", "").lower() in ("1", "true", "yes")
CDP_URL = f"http://localhost:{CDP_PORT}"

# chrome.py runs as standalone script — add /app to path for imports
if os.path.dirname(__file__).endswith("core"):
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Import consolidated stealth JS from cdp_bridge
from core.cdp_bridge import STEALTH_JS


def wait_for_cdp(timeout: int = 30) -> bool:
    """Wait for Chrome CDP to be ready."""
    for _ in range(timeout * 2):
        try:
            resp = urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=2)
            data = json.loads(resp.read())
            logger.info(f"CDP ready: {data.get('Browser', 'unknown')}")
            return True
        except Exception:
            time.sleep(0.5)
    return False


def inject_stealth_js():
    """Inject stealth JS into all new pages via CDP."""
    import websocket
    # Get a page target
    try:
        resp = urllib.request.urlopen(f"{CDP_URL}/json/list", timeout=5)
        tabs = json.loads(resp.read())
        page = next((t for t in tabs if t.get("type") == "page"), tabs[0])
        ws_url = page["webSocketDebuggerUrl"]

        ws = websocket.create_connection(ws_url, max_size=10 * 1024 * 1024)
        # Inject stealth JS that runs on every new document
        ws.send(json.dumps({
            "id": 1,
            "method": "Page.addScriptToEvaluateOnNewDocument",
            "params": {"source": STEALTH_JS},
        }))
        ws.recv()  # consume response
        ws.close()
        logger.info("Stealth JS injected via CDP")
    except Exception as e:
        logger.warning(f"Stealth JS injection failed (non-fatal): {e}")


def launch_chrome() -> subprocess.Popen:
    """Launch Chrome directly with anti-detection flags."""
    chrome_binary = "google-chrome-stable"

    args = [
        chrome_binary,
        # NOT headless — runs on Xvfb virtual display
        f"--remote-debugging-port={CDP_PORT}",
        "--remote-debugging-address=0.0.0.0",
        "--remote-allow-origins=*",
        f"--user-data-dir={PROFILE_DIR}",
        "--profile-directory=Default",
        f"--window-size={WINDOW_SIZE}",
        "--start-maximized",
        # Anti-detection
        "--disable-blink-features=AutomationControlled",
        "--disable-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-client-side-phishing-detection",
        "--disable-default-apps",
        "--disable-hang-monitor",
        "--disable-popup-blocking",
        "--disable-prompt-on-repost",
        "--disable-sync",
        "--metrics-recording-only",
        "--safebrowsing-disable-auto-update",
        "--lang=en-US",
        # Performance
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--no-sandbox",
    ]

    # Low-RAM mode: fewer processes, less memory
    if LOW_RAM:
        args.extend([
            "--renderer-process-limit=1",
            "--disable-features=site-per-process",
            "--disable-background-timer-throttling",
            "--js-flags=--max-old-space-size=256",
        ])

    args.append("about:blank")

    logger.info(f"Launching Chrome: {' '.join(args[:6])}...")

    # Set DISPLAY for Xvfb
    env = os.environ.copy()
    env["DISPLAY"] = ":99"

    proc = subprocess.Popen(
        args,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    return proc


def main():
    """Entry point — launches Chrome and keeps it running."""
    logger.info("=== Alphabetty Chrome Manager ===")

    proc = launch_chrome()

    logger.info("Waiting for Chrome CDP...")
    if not wait_for_cdp(timeout=30):
        # Check stderr
        stderr_output = ""
        try:
            proc.stderr.setblocking(False)
            stderr_output = proc.stderr.read(4096).decode(errors="replace")
        except Exception:
            pass
        logger.error(f"Chrome CDP not responding. stderr: {stderr_output}")
        proc.terminate()
        sys.exit(1)

    logger.info("Chrome ready on :9222")

    # Inject stealth JS
    inject_stealth_js()

    # Verify stealth
    try:
        resp = urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=5)
        data = json.loads(resp.read())
        logger.info(f"Browser: {data.get('Browser', 'unknown')}")
        ua = data.get("User-Agent", "unknown")
        logger.info(f"User-Agent: {ua[:100]}")
    except Exception:
        pass

    logger.info("Chrome manager running. Waiting for exit...")

    try:
        proc.wait()
    except KeyboardInterrupt:
        logger.info("Shutting down Chrome...")
        proc.terminate()
        proc.wait()

    exit_code = proc.returncode
    logger.info(f"Chrome exited with code {exit_code}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
