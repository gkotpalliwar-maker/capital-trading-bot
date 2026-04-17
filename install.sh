#!/bin/bash
set -e
SD="$(cd "$(dirname "$0")" && pwd)"
BD="/root/trading-bot"
echo "=== v2.3.0 Installer ==="
BK="$BD/backups/v2.2.9_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BK"
for f in bot/config.py bot/telegram_bot.py bot/scanner.py bot/execution.py; do
    [ -f "$BD/$f" ] && cp "$BD/$f" "$BK/"
done
echo "Backed up to $BK"
# Restore clean scanner.py from oldest backup before patching
OLDEST=$(ls -td "$BD/backups/v2.2.9_"* 2>/dev/null | tail -1)
if [ -n "$OLDEST" ] && [ -f "$OLDEST/scanner.py" ]; then
    cp "$OLDEST/scanner.py" "$BD/bot/scanner.py"
    echo "Restored clean scanner.py from $OLDEST"
fi
for f in signal_scorer.py signal_scorer_commands.py trade_manager.py trade_manager_commands.py; do
    cp "$SD/bot/$f" "$BD/bot/$f" && echo "Installed bot/$f"
done
cd "$BD" && python3 "$SD/patches/v2.3.0_patcher.py"
sed -i "s/v2\.2\.[0-9]*/v2.3.0/g" bot/scanner.py 2>/dev/null || true
echo ""
echo "Done! Now: add .env vars + systemctl restart trading-bot"
echo "Commands: /mlstats /retrain /mlthreshold /breakeven /partialtp /trademanage"
