#!/bin/bash
set -e

echo "============================================"
echo "  MEXC SMC Bot - VM Setup"
echo "============================================"
echo ""

# ── 1. System packages ──────────────────────────
echo "[1/7] Installing system packages..."
sudo apt update -qq
sudo apt install -y -qq python3 python3-venv python3-pip git net-tools

# ── 2. Firewall ─────────────────────────────────
echo "[2/7] Configuring firewall..."
sudo ufw allow 8000/tcp 2>/dev/null || echo "  (ufw not available, skipping)"

# ── 3. Create project directory ─────────────────
echo "[3/7] Setting up project directory..."
mkdir -p /home/ubuntu/mexc-bot
cd /home/ubuntu/mexc-bot

# ── 4. Python virtual environment ───────────────
echo "[4/7] Creating Python virtual environment..."
python3 -m venv .venv
source .venv/bin/activate

# ── 5. Install Python dependencies ─────────────
echo "[5/7] Installing Python dependencies..."
pip install --quiet --upgrade pip
pip install --quiet fastapi uvicorn[standard] websockets aiohttp pandas numpy \
    torch transformers ta sqlalchemy python-dotenv httpx pydantic huggingface_hub \
    einops

# ── 6. Create .env file ────────────────────────
echo "[6/7] Creating .env file..."
if [ ! -f .env ]; then
    cat > .env << 'ENVEOF'
MEXC_API_KEY=''
MEXC_SECRET_KEY=''
PAPER_TRADING='True'
KRONOS_MODEL='mini'
KRONOS_DEVICE='cpu'
ENVEOF
    echo "  .env created (edit it with your MEXC API keys)"
else
    echo "  .env already exists, skipping"
fi

# ── 7. Systemd service ─────────────────────────
echo "[7/7] Installing systemd service..."
if [ -f deploy/mexc-bot.service ]; then
    sudo cp deploy/mexc-bot.service /etc/systemd/system/mexc-bot.service
else
    # Create service file inline
    sudo tee /etc/systemd/system/mexc-bot.service > /dev/null << 'SERVEOF'
[Unit]
Description=MEXC SMC Bot
After=network.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/mexc-bot
ExecStart=/home/ubuntu/mexc-bot/.venv/bin/python run.py
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
sudo systemctl start mexc-bot

echo ""
echo "============================================"
echo "  SETUP COMPLETE"
echo "============================================"
echo ""
echo "Bot should be running. Check status with:"
echo "  sudo systemctl status mexc-bot"
echo ""
echo "View live logs with:"
echo "  journalctl -u mexc-bot -f"
echo ""
echo "Edit API keys:"
echo "  nano /home/ubuntu/mexc-bot/.env"
echo "  sudo systemctl restart mexc-bot"
echo ""
echo "Access dashboard:"
echo "  http://$(curl -s ifconfig.me):8000"
echo ""
