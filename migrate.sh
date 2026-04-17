#!/bin/bash
set -e

# ============================================================
#  PRODUCTION MIGRATION
#  From: /root/trading-bot (root user)
#  To:   /opt/trading-bot (gkotpalliwar user)
# ============================================================

NEW_USER="gkotpalliwar"
OLD_DIR="/root/trading-bot"
NEW_DIR="/opt/trading-bot"

echo "=========================================================="
echo "  Trading Bot — Production Migration"
echo "  $OLD_DIR (root) -> $NEW_DIR ($NEW_USER)"
echo "=========================================================="

# ── Pre-flight checks ─────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    echo "❌ Must run as root"; exit 1
fi
if ! id "$NEW_USER" &>/dev/null; then
    echo "❌ User $NEW_USER does not exist"; exit 1
fi
if [ ! -d "$OLD_DIR" ]; then
    echo "❌ $OLD_DIR not found"; exit 1
fi
if [ -d "$NEW_DIR" ]; then
    echo "⚠️  $NEW_DIR already exists. Backing up..."
    mv "$NEW_DIR" "${NEW_DIR}.bak.$(date +%Y%m%d_%H%M%S)"
fi

# ── Step 1: Stop service ──────────────────────────────────────
echo ""
echo "1/7 Stopping trading-bot service..."
systemctl stop trading-bot 2>/dev/null || true
sleep 2
echo "  ✅ Stopped"

# ── Step 2: Copy project (exclude venv — will recreate) ──────
echo ""
echo "2/7 Copying project files..."
mkdir -p "$NEW_DIR"
rsync -a --exclude="venv/" --exclude="__pycache__/" --exclude="*.pyc" \
    "$OLD_DIR/" "$NEW_DIR/"
echo "  ✅ Copied to $NEW_DIR"

# ── Step 3: Recreate venv (hardcoded paths don\'t transfer) ───
echo ""
echo "3/7 Creating virtual environment..."
cd "$NEW_DIR"
python3 -m venv venv
source venv/bin/activate
if [ -f requirements.txt ]; then
    pip install --upgrade pip -q
    pip install -r requirements.txt -q
    echo "  ✅ Installed $(pip list --format=freeze | wc -l) packages"
else
    echo "  ⚠️  No requirements.txt — installing known dependencies"
    pip install --upgrade pip -q
    pip install python-telegram-bot python-dotenv requests flask \
                scikit-learn numpy pandas ta -q
    echo "  ✅ Installed core packages"
fi
deactivate

# ── Step 4: Fix symlink & data directory ──────────────────────
echo ""
echo "4/7 Fixing data directory..."
mkdir -p "$NEW_DIR/data"
cd "$NEW_DIR/data"
[ -L trading.db ] && rm trading.db
ln -sf bot.db trading.db
echo "  ✅ Symlink: trading.db -> bot.db"

# ── Step 5: Set ownership & permissions ───────────────────────
echo ""
echo "5/7 Setting ownership & permissions..."
chown -R "$NEW_USER:$NEW_USER" "$NEW_DIR"
chmod 600 "$NEW_DIR/.env" 2>/dev/null || true
chmod 755 "$NEW_DIR"
chmod -R 755 "$NEW_DIR/venv/bin/"
echo "  ✅ Owner: $NEW_USER | .env: 600"

# ── Step 6: Create systemd service ────────────────────────────
echo ""
echo "6/7 Creating systemd service..."

# Backup old service file
[ -f /etc/systemd/system/trading-bot.service ] && \
    cp /etc/systemd/system/trading-bot.service \
       /etc/systemd/system/trading-bot.service.bak

cat > /etc/systemd/system/trading-bot.service << SVCEOF
[Unit]
Description=Capital.com Trading Bot v2.3.0
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$NEW_USER
Group=$NEW_USER
WorkingDirectory=$NEW_DIR
EnvironmentFile=$NEW_DIR/.env
ExecStart=$NEW_DIR/venv/bin/python bot/scanner.py
Restart=always
RestartSec=10
StartLimitIntervalSec=300
StartLimitBurst=5

# Logging
StandardOutput=append:$NEW_DIR/bot.log
StandardError=append:$NEW_DIR/bot.log

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$NEW_DIR
PrivateTmp=true

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable trading-bot
echo "  ✅ Service created & enabled"

# ── Step 7: Start & verify ────────────────────────────────────
echo ""
echo "7/7 Starting service..."

# Ensure log file exists and is writable
touch "$NEW_DIR/bot.log"
chown "$NEW_USER:$NEW_USER" "$NEW_DIR/bot.log"

systemctl start trading-bot
sleep 3

if systemctl is-active --quiet trading-bot; then
    echo "  ✅ Service is RUNNING"
else
    echo "  ❌ Service failed to start. Check:"
    echo "     journalctl -u trading-bot -n 20"
    echo "     tail -20 $NEW_DIR/bot.log"
fi

# ── Summary ───────────────────────────────────────────────────
echo ""
echo "=========================================================="
echo "  ✅ MIGRATION COMPLETE"
echo "=========================================================="
echo ""
echo "  Location:  $NEW_DIR"
echo "  User:      $NEW_USER"
echo "  Service:   systemctl {start|stop|restart|status} trading-bot"
echo "  Logs:      tail -f $NEW_DIR/bot.log"
echo "             journalctl -u trading-bot -f"
echo ""
echo "  Useful commands:"
echo "    sudo systemctl restart trading-bot"
echo "    sudo journalctl -u trading-bot -f"
echo "    tail -f $NEW_DIR/bot.log"
echo ""
echo "  Old dir ($OLD_DIR) left intact as backup."
echo "  Remove when satisfied: rm -rf $OLD_DIR"
echo "=========================================================="
