#!/bin/bash
set -e
SD="$(cd "$(dirname "$0")" && pwd)"
if [ -d "/opt/trading-bot" ]; then BD="/opt/trading-bot"
elif [ -d "/root/trading-bot" ]; then BD="/root/trading-bot"
else echo "Bot dir not found"; exit 1; fi
echo "=== v2.5.0 Installer ($BD) ==="
BK="$BD/backups/pre_v250_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BK"
for f in bot/config.py bot/telegram_bot.py bot/scanner.py bot/execution.py; do
    [ -f "$BD/$f" ] && cp "$BD/$f" "$BK/"
done
echo "Backed up to $BK"
OLDEST=$(ls -td "$BD/backups/v2.2.9_"* "$BD/backups/pre_v23"* "$BD/backups/pre_v24"* 2>/dev/null | tail -1)
if [ -n "$OLDEST" ] && [ -f "$OLDEST/scanner.py" ]; then
    cp "$OLDEST/scanner.py" "$BD/bot/scanner.py"
    echo "Restored clean scanner.py"
fi
for f in signal_scorer.py signal_scorer_commands.py trade_manager.py trade_manager_commands.py market_hours.py recall_commands.py pnl_commands.py mtf_confluence.py mtf_commands.py risk_report.py risk_report_commands.py trade_validator.py trade_validator_commands.py news_filter.py news_commands.py; do
    [ -f "$SD/bot/$f" ] && cp "$SD/bot/$f" "$BD/bot/$f" && echo "Installed bot/$f"
done
echo ""
echo "Running patchers..."
cd "$BD"
for p in v2.3.0_patcher v2.3.1_pnl_fix v2.3.2_telegram_fix v2.3.3_patcher v2.3.4_pnl_fix v2.4.0_mtf_patcher v2.5.0_news_patcher; do
    if [ -f "$SD/patches/${p}.py" ]; then
        echo "  Running $p..."
        python3 "$SD/patches/${p}.py"
    fi
done
sed -i "s/v2\.[0-9]\.[0-9]*/v2.5.0/g" bot/scanner.py 2>/dev/null || true
# Add news config to .env if not present
grep -q "NEWS_FILTER_ENABLED" "$BD/.env" 2>/dev/null || cat >> "$BD/.env" << 'ENVEOF'

# v2.5.0 — News Filter
NEWS_FILTER_ENABLED=true
NEWS_BLOCK_MINUTES_BEFORE=30
NEWS_BLOCK_MINUTES_AFTER=15
NEWS_FILTER_REQUIRED=false
NEWS_CONFLUENCE_PENALTY=3
ENVEOF
echo ""
echo "Done! systemctl restart trading-bot"
echo "New commands: /news /activateguard /deactivateguard /guardstatus /summary"
