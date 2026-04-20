# Capital.com Trading Bot v2.3.4

## Features
- SMC/ICT signal scanner + MSS/BOS
- ML signal scoring (RandomForest, auto-retrain)
- Breakeven + Partial TP management
- Signal revalidation + retry buttons
- Signal recall (`/recall`) with live validation
- Market hours filter (crypto 24/7, forex/index weekdays only)
- Conflict filter (BUY+SELL same instrument -> highest confluence wins)
- Regime enforcement (hard block, not advisory)
- **P&L fix**: Correct currency conversion via deal_id+1 hex matching
- **`/fixpnl`**: Backfill incorrect P&L from Capital.com transaction API

## Install
```bash
cd /opt/trading-bot
git clone https://github.com/gkotpalliwar-maker/capital-trading-bot.git /tmp/v234
bash /tmp/v234/install.sh
rm -rf /tmp/v234
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
**P&L Fix**: `/fixpnl` `/fixpnl <id>`
**Info**: `/about` `/help`
