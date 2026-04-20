#!/bin/bash
set -e
SD="$(cd "$(dirname "$0")" && pwd)"
if [ -d "/opt/trading-bot" ]; then BD="/opt/trading-bot"
elif [ -d "/root/trading-bot" ]; then BD="/root/trading-bot"
else echo "Bot dir not found"; exit 1; fi
echo "=== v2.4.0 Installer ($BD) ==="
BK="$BD/backups/pre_v240_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BK"
for f in bot/config.py bot/telegram_bot.py bot/scanner.py bot/execution.py; do
    [ -f "$BD/$f" ] && cp "$BD/$f" "$BK/"
done
echo "Backed up to $BK"
OLDEST=$(ls -td "$BD/backups/v2.2.9_"* "$BD/backups/pre_v23"* 2>/dev/null | tail -1)
if [ -n "$OLDEST" ] && [ -f "$OLDEST/scanner.py" ]; then
    cp "$OLDEST/scanner.py" "$BD/bot/scanner.py"
    echo "Restored clean scanner.py"
fi
for f in signal_scorer.py signal_scorer_commands.py trade_manager.py trade_manager_commands.py market_hours.py recall_commands.py pnl_commands.py mtf_confluence.py mtf_commands.py; do
    [ -f "$SD/bot/$f" ] && cp "$SD/bot/$f" "$BD/bot/$f" && echo "Installed bot/$f"
done
echo ""
echo "Running v2.3.0 patcher..."
cd "$BD" && python3 "$SD/patches/v2.3.0_patcher.py"
echo "Running v2.3.1 patcher (P&L fix)..."
python3 "$SD/patches/v2.3.1_pnl_fix.py"
echo "Running v2.3.2 patcher (telegram fixes)..."
python3 "$SD/patches/v2.3.2_telegram_fix.py"
echo "Running v2.3.3 patcher (market hours, conflict, regime, recall, fixpnl)..."
python3 "$SD/patches/v2.3.3_patcher.py"
echo "Running v2.3.4 patcher (P&L currency fix)..."
python3 "$SD/patches/v2.3.4_pnl_fix.py"
echo "Running v2.4.0 patcher (MTF Confluence)..."
python3 "$SD/patches/v2.4.0_mtf_patcher.py"
sed -i "s/v2\.[0-9]\.[0-9]*/v2.4.0/g" bot/scanner.py 2>/dev/null || true
echo ""
echo "Done! systemctl restart trading-bot"
echo "New: /mtf - Show H4 bias for all instruments"
