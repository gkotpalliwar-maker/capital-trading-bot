# Capital.com Trading Bot v2.3.2

## Features
- SMC/ICT signal scanner (OB, FVG, BB, MB, IFVG) + MSS/BOS
- ML signal scoring (RandomForest, auto-retrain)
- Breakeven + Partial TP management
- Signal revalidation before execution
- Retry buttons on execution errors
- P&L recording via Capital.com transaction API
- Dynamic instrument management
- Risk-based position sizing (2%)
- Regime filter (ADX + ATR)

## Install (fresh or upgrade)
```bash
cd /opt/trading-bot
git clone https://github.com/gkotpalliwar-maker/capital-trading-bot.git /tmp/v232
bash /tmp/v232/install.sh
rm -rf /tmp/v232
sudo systemctl restart trading-bot
```

## Production Migration
```bash
bash migrate.sh  # Moves from /root to /opt, non-root user
```

## Commands
**Scanner**: `/start` `/stop` `/scan` `/status`
**Trading**: `/positions` `/balance` `/pending`
**Analytics**: `/stats` `/journal` `/risk` `/regime`
**Instruments**: `/instruments` `/add` `/remove` `/lotsize` `/pip`
**Validation**: `/validate`
**ML Scoring**: `/mlstats` `/retrain` `/mlthreshold`
**Trade Mgmt**: `/breakeven` `/partialtp` `/trademanage`
**Info**: `/about` `/help`
