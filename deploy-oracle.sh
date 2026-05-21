#!/bin/bash
# Alphabetty — Oracle Cloud Free Tier x86 Setup
# Run on the Oracle instance as: bash deploy-oracle.sh
# Prereqs: Oracle Linux 8/9 with Docker + docker-compose

set -e

echo "=== Alphabetty — Oracle Cloud Deploy ==="

# ─── 1. Create 4GB swap file ───
echo "[1/4] Setting up 4GB swap..."
if [ ! -f /swapfile ]; then
    sudo dd if=/dev/zero of=/swapfile bs=1M count=4096 status=progress
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    echo "Swap created and enabled"
else
    echo "Swap file already exists"
fi
free -h

# ─── 2. Install Docker (if not present) ───
echo "[2/4] Installing Docker..."
if ! command -v docker &>/dev/null; then
    sudo dnf install -y dnf-utils
    sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
    sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    sudo systemctl enable --now docker
    sudo usermod -aG docker $USER
    echo "Docker installed — you may need to re-login for group changes"
else
    echo "Docker already installed"
fi

# ─── 3. Clone repo ───
echo "[3/4] Cloning Alphabetty..."
if [ ! -d /opt/alphabetty ]; then
    sudo git clone https://github.com/slothitude/alphabetty.git /opt/alphabetty
    sudo chown -R $USER:$USER /opt/alphabetty
else
    cd /opt/alphabetty && git pull
fi

# ─── 4. Create low-RAM docker-compose override ───
echo "[4/4] Writing Oracle docker-compose.override.yml..."
cd /opt/alphabetty

cat > docker-compose.override.yml << 'YAML'
services:
  alphabetty:
    # Low-RAM optimizations for Oracle free tier (1GB RAM + 4GB swap)
    environment:
      - ALPHABETTY_CHROME_WINDOW_SIZE=1280,720
      - ALPHABETTY_LOW_RAM=true
    deploy:
      resources:
        limits:
          memory: 1536M   # 1.5GB cap (RAM + swap)
        reservations:
          memory: 512M
    # Reduce Xvfb resolution for less RAM usage
    command:
      - bash
      - -c
      - |
        rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
        rm -f /chrome-profile/SingletonLock /chrome-profile/SingletonSocket /chrome-profile/SingletonCookie
        export DISPLAY=:99
        Xvfb :99 -screen 0 1280x720x16 -ac +extension GLX +render -noreset &
        sleep 1
        mkdir -p /run/dbus
        dbus-daemon --system --fork 2>/dev/null || true
        sleep 0.5
        python /app/core/chrome.py &
        for i in $$(seq 1 60); do
          curl -s http://localhost:9222/json/version > /dev/null 2>&1 && break
          sleep 0.5
        done
        exec uvicorn app:app --host 0.0.0.0 --port 7700 --workers 1
YAML

# ─── Create .env from template ───
if [ ! -f .env ]; then
    cat > .env << 'ENV'
ALPHABETTY_LLM_URL=https://api.z.ai/api/coding/paas/v4/chat/completions
ALPHABETTY_LLM_MODEL=glm-5.1
ALPHABETTY_LLM_API_KEY=your-key-here
ALPHABETTY_SEARXNG_URL=http://localhost:8888
ALPHABETTY_OLLAMA_URL=http://localhost:11434/v1/chat/completions
ALPHABETTY_ROUTER_URL=http://localhost:4000
ENV
    echo "Created .env — EDIT IT with your API keys before starting"
fi

# ─── Open firewall ───
echo "Opening port 7700..."
sudo firewall-cmd --permanent --add-port=7700/tcp 2>/dev/null || true
sudo firewall-cmd --reload 2>/dev/null || true
# Oracle Cloud also needs the security list / network security group updated in the OCI console
echo "IMPORTANT: Also open port 7700 in OCI Console → Networking → VCN → Security Lists"

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit /opt/alphabetty/.env with your API keys"
echo "  2. cd /opt/alphabetty && docker compose up --build -d"
echo "  3. Open http://<oracle-ip>:7700"
echo ""
echo "Memory usage estimate:"
echo "  Chrome:    ~300-500MB"
echo "  Xvfb:      ~20MB"
echo "  FastAPI:   ~80MB"
echo "  Total:     ~400-600MB (with 4GB swap as safety net)"
