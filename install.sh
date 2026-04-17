#!/bin/bash
set -e
SD="$(cd "$(dirname "$0")" && pwd)"
# Auto-detect bot directory
if [ -d "/opt/trading-bot" ]; then
    BD="/opt/trading-bot"
elif [ -d "/root/trading-bot" ]; then
    BD="/root/trading-bot"
else
    echo "Bot directory not found"; exit 1
fi
echo "=== v2.3.2 Installer ($BD) ==="
BK="$BD/backups/pre_v232_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BK"
for f in bot/config.py bot/telegram_bot.py bot/scanner.py bot/execution.py; do
    [ -f "$BD/$f" ] && cp "$BD/$f" "$BK/"
done
echo "Backed up to $BK"
# Restore clean scanner.py from oldest backup before patching
OLDEST=$(ls -td "$BD/backups/v2.2.9_"* "$BD/backups/pre_v232_"* 2>/dev/null | tail -1)
if [ -n "$OLDEST" ] && [ -f "$OLDEST/scanner.py" ]; then
    cp "$OLDEST/scanner.py" "$BD/bot/scanner.py"
    echo "Restored clean scanner.py from $OLDEST"
fi
# Install v2.3.0 modules
for f in signal_scorer.py signal_scorer_commands.py trade_manager.py trade_manager_commands.py; do
    cp "$SD/bot/$f" "$BD/bot/$f" && echo "Installed bot/$f"
done
# Run patchers in order
echo ""
echo "Running v2.3.0 patcher (scanner + telegram + execution)..."
cd "$BD" && python3 "$SD/patches/v2.3.0_patcher.py"
echo ""
echo "Running v2.3.1 patcher (P&L fix)..."
python3 "$SD/patches/v2.3.1_pnl_fix.py"
echo ""
echo "Running v2.3.2 patcher (signal revalidation + retry buttons + help)..."
python3 "$SD/patches/v2.3.2_telegram_fix.py"
sed -i "s/v2\.[0-9]\.[0-9]*/v2.3.2/g" bot/scanner.py 2>/dev/null || true
echo ""
echo "Done! systemctl restart trading-bot"
echo "Commands: /mlstats /retrain /mlthreshold /breakeven /partialtp /trademanage /validate /instruments"
