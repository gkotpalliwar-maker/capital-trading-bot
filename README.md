# Capital.com Trading Bot v2.4.0

## Features
- SMC/ICT signal scanner + MSS/BOS
- ML signal scoring (RandomForest, auto-retrain)
- Breakeven + Partial TP management
- Signal revalidation + retry buttons
- Signal recall (`/recall`) with live validation
- Market hours filter (crypto 24/7, forex/index weekdays only)
- Conflict filter (BUY+SELL same instrument -> highest confluence wins)
- Regime enforcement (hard block, not advisory)
- P&L recording via Capital.com transaction API (lastPeriod)
- **MTF Confluence**: H4 structure bias confirms M15/M5 entries (+2 confluence)

## Install
```bash
cd /opt/trading-bot
git clone https://github.com/gkotpalliwar-maker/capital-trading-bot.git /tmp/v240
bash /tmp/v240/install.sh
rm -rf /tmp/v240
sudo systemctl restart trading-bot
```

## All Commands
**Scanner**: `/start` `/stop` `/scan` `/status`
**Trading**: `/positions` `/balance` `/pending`
**Analytics**: `/stats` `/journal` `/risk` `/regime`
**Instruments**: `/instruments` `/add` `/remove` `/lotsize` `/pip`
**Validation**: `/validate`
**Signal Recall**: `/recall` `/recall 8` `/recall 2d`
**ML Scoring**: `/mlstats` `/retrain` `/mlthreshold`
**Trade Mgmt**: `/breakeven` `/partialtp` `/trademanage`
**MTF Analysis**: `/mtf` - H4 bias for all instruments
**P&L Fix**: `/fixpnl` `/fixpnl <id>`
**Info**: `/about` `/help`
