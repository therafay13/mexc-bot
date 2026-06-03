#!/bin/bash
set -e

echo "============================================"
echo "  MEXC SMC Bot - VM Setup (1GB-safe)"
echo "============================================"
echo ""

# ── 0. Swap (prevents OOM during pip install) ──
echo "[0/8] Adding 2GB swap..."
if ! swapon --show | grep -q /swapfile; then
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab > /dev/null
    echo "  Swap added: 2GB"
else
    echo "  Swap already active, skipping"
fi

# ── 1. System packages ──────────────────────────
echo "[1/8] Installing system packages..."
sudo apt update -qq
sudo apt install -y -qq python3 python3-venv python3-pip git net-tools
# Clean apt cache to free disk space
sudo apt clean -qq

# ── 2. Firewall ─────────────────────────────────
echo "[2/8] Configuring firewall..."
sudo ufw allow 8000/tcp 2>/dev/null || echo "  (ufw not available, skipping)"

# ── 3. Project directory ────────────────────────
echo "[3/8] Setting up project directory..."
cd /home/ubuntu/mexc-bot

# ── 4. Python virtual environment ───────────────
echo "[4/8] Creating Python virtual environment..."
python3 -m venv .venv
source .venv/bin/activate

# ── 5. Install Python dependencies (sequential) ─
echo "[5/8] Installing Python dependencies..."

# Upgrade pip first (light)
pip install --quiet --upgrade pip

# Install core packages one-by-one to avoid memory spikes
# CPU-only PyTorch (no CUDA — saves ~600MB)
echo "  -> torch (CPU only)..."
pip install --quiet --no-cache-dir torch==2.1.0 --index-url https://download.python.org/whl/cpu 2>/dev/null || \
pip install --quiet --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

echo "  -> transformers + huggingface_hub..."
pip install --quiet --no-cache-dir transformers huggingface_hub

echo "  -> fastapi + uvicorn..."
pip install --quiet --no-cache-dir "fastapi>=0.110.0" "uvicorn[standard]>=0.29.0"

echo "  -> data libraries..."
pip install --quiet --no-cache-dir "pandas>=2.0.0" "numpy>=1.26.0"

echo "  -> remaining dependencies..."
pip install --quiet --no-cache-dir "websockets>=12.0" "aiohttp>=3.9.5" \
    "ta>=0.11.0" "sqlalchemy>=2.0.0" "python-dotenv>=1.0.0" \
    "httpx>=0.27.0" "pydantic>=2.0.0"

# Clean pip cache after installs to free disk space
pip cache purge 2>/dev/null || true

# ── 6. Create .env file ────────────────────────
echo "[6/8] Creating .env file..."
if [ ! -f .env ]; then
    cat > .env << 'ENVEOF'
MEXC_API_KEY=''
MEXC_SECRET_KEY=''
PAPER_TRADING='True'
KRONOS_MODEL='mini'
KRONOS_DEVICE='cpu'
ENVEOF
    echo "  .env created — edit with your MEXC API keys"
else
    echo "  .env exists, skipping"
fi

# ── 7. Systemd service ─────────────────────────
echo "[7/8] Installing systemd service..."
if [ -f deploy/mexc-bot.service ]; then
    sudo cp deploy/mexc-bot.service /etc/systemd/system/mexc-bot.service
else
    sudo tee /etc/systemd/system/mexc-bot.service > /dev/null << 'SERVEOF'
[Unit]
Description=MEXC SMC Bot
After=network.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/mexc-bot
ExecStart=/home/ubuntu/mexc-bot/.venv/bin/uvicorn backend.api.server:app --host 0.0.0.0 --port 8000 --workers 1 --log-level info
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SERVEOF
fi

sudo systemctl daemon-reload
sudo systemctl enable mexc-bot

# ── 8. Start bot ───────────────────────────────
echo "[8/8] Starting bot..."
sudo systemctl start mexc-bot

echo ""
echo "============================================"
echo "  SETUP COMPLETE"
echo "============================================"
echo ""
echo "Check status:  sudo systemctl status mexc-bot"
echo "View logs:     journalctl -u mexc-bot -f"
echo "Dashboard:     http://$(curl -s ifconfig.me):8000"
echo ""
echo "To restart:    sudo systemctl restart mexc-bot"
