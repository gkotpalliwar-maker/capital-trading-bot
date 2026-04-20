# Capital.com Trading Bot v2.3.3

## Features
- SMC/ICT signal scanner + MSS/BOS
- ML signal scoring (RandomForest, auto-retrain)
- Breakeven + Partial TP management
- Signal revalidation + retry buttons
- **Signal recall** (`/recall`) with live validation
- **Market hours filter** (crypto 24/7, forex/index weekdays only)
- **Conflict filter** (BUY+SELL same instrument -> highest confluence wins)
- **Regime enforcement** (hard block, not advisory)
- P&L recording via Capital.com transaction API

## Install
```bash
cd /opt/trading-bot
git clone https://github.com/gkotpalliwar-maker/capital-trading-bot.git /tmp/v233
bash /tmp/v233/install.sh
rm -rf /tmp/v233
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
**Info**: `/about` `/help`
