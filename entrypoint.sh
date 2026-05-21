#!/bin/bash
set -e

# ─── Clean up stale locks (from container restart) ───
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
rm -f /chrome-profile/SingletonLock /chrome-profile/SingletonSocket /chrome-profile/SingletonCookie

# ─── Start virtual display (Xvfb) ───
export DISPLAY=:99
Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
sleep 1

if ! pgrep -x Xvfb > /dev/null; then
    echo "ERROR: Xvfb failed to start"
    exit 1
fi
echo "Xvfb running on :99"

# ─── Start dbus (Chrome uses it for notifications, etc.) ───
mkdir -p /run/dbus
dbus-daemon --system --fork 2>/dev/null || true
sleep 0.5

# ─── Launch Chrome via stealth manager ───
python /app/core/chrome.py &
CHROME_PID=$!

# Wait for Chrome CDP to be ready
echo "Waiting for Chrome CDP on :9222..."
for i in $(seq 1 60); do
    if curl -s http://localhost:9222/json/version > /dev/null 2>&1; then
        echo "Chrome ready on :9222 (undetected mode)"
        break
    fi
    if ! kill -0 $CHROME_PID 2>/dev/null; then
        echo "ERROR: Chrome process died during startup"
        exit 1
    fi
    sleep 0.5
done

if ! curl -s http://localhost:9222/json/version > /dev/null 2>&1; then
    echo "ERROR: Chrome CDP not responding after 30s"
    exit 1
fi

# ─── Start FastAPI ───
echo "Starting Alphabetty on :7700"
exec uvicorn app:app --host 0.0.0.0 --port 7700
