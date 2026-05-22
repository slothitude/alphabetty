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
      - ALPHABETTY_TRANSMISSION_URL=http://transmission-vpn:9091/transmission/rpc
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

  transmission-vpn:
    image: haugene/transmission-openvpn:latest
    container_name: transmission-vpn
    cap_add:
      - NET_ADMIN
    devices:
      - /dev/net/tun
    environment:
      - OPENVPN_PROVIDER=CUSTOM
      - OPENVPN_CUSTOM_CONFIG=/etc/openvpn/custom/purevpn.ovpn
      - OPENVPN_USERNAME=purevpn0s9075328
      - OPENVPN_PASSWORD=5KLfSXhionRv3
      - LOCAL_NETWORK=172.17.0.0/16
      - TRANSMISSION_DOWNLOAD_DIR=/downloads/complete
      - TRANSMISSION_INCOMPLETE_DIR=/downloads/incomplete
      - TRANSMISSION_WEB_HOME=/transmission-web
      - TRANSMISSION_RPC_USERNAME=admin
      - TRANSMISSION_RPC_PASSWORD=alphabetty
      - CREATE_TUN_DEVICE=true
    volumes:
      - ./vpn-config:/etc/openvpn/custom:ro
      - ./torrents:/downloads
    ports:
      - "9091:9091"
    restart: unless-stopped
    mem_limit: 256m
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

  searxng:
    # SearXNG shares VPN network for torrent search access
    network_mode: "service:transmission-vpn"
YAML

# ─── Create .env from template ───
if [ ! -f .env ]; then
    cat > .env << 'ENV'
ALPHABETTY_LLM_URL=https://api.z.ai/api/coding/paas/v4/chat/completions
ALPHABETTY_LLM_MODEL=glm-5.1
ALPHABETTY_LLM_API_KEY=your-key-here
ALPHABETTY_SEARXNG_URL=http://transmission-vpn:8080
ALPHABETTY_OLLAMA_URL=http://localhost:11434/v1/chat/completions
ALPHABETTY_ROUTER_URL=http://localhost:4000
ALPHABETTY_TRANSMISSION_URL=http://transmission-vpn:9091/transmission/rpc
ENV
    echo "Created .env — EDIT IT with your API keys before starting"
fi

# ─── VPN config for Transmission ───
if [ ! -d vpn-config ]; then
    mkdir -p vpn-config
    # PureVPN OpenVPN config — Sydney/Melbourne servers, UDP port 53
    cat > vpn-config/purevpn.ovpn << 'OVPN'
client
dev tun
proto udp
remote au-syd.purevpn.net 53
remote au-mel.purevpn.net 53
resolv-retry infinite
nobind
persist-key
persist-tun
cipher AES-256-CBC
auth SHA256
comp-lzo no
route-method tap
route-delay 2
tun-mtu 1500
mssfix 1450
reneg-sec 0
remote-cert-tls server
auth-user-pass /etc/openvpn/custom/openvpn-credentials.txt
verb 1
OVPN
    cat > vpn-config/openvpn-credentials.txt << 'CREDS'
purevpn0s9075328
5KLfSXhionRv3
CREDS
    chmod 600 vpn-config/openvpn-credentials.txt
    echo "VPN config created (PureVPN AU servers)"
fi

# ─── Torrent download dir ───
mkdir -p torrents/complete torrents/incomplete

# ─── Open firewall ───
echo "Opening ports 7700, 9091..."
sudo firewall-cmd --permanent --add-port=7700/tcp 2>/dev/null || true
sudo firewall-cmd --permanent --add-port=9091/tcp 2>/dev/null || true
sudo firewall-cmd --reload 2>/dev/null || true
# Oracle Cloud also needs the security list / network security group updated in the OCI console
echo "IMPORTANT: Also open ports 7700 + 9091 in OCI Console → Networking → VCN → Security Lists"

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
