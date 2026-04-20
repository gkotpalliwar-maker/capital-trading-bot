# Capital.com Trading Bot — Operations Guide
**Version**: v2.4.0 | **Account**: SGD | **VPS**: `/opt/trading-bot`

---

## Table of Contents
1. [Quick Start](#quick-start)
2. [Signal Scoring & When to Trade](#signal-scoring--when-to-trade)
3. [Risk Management Rules](#risk-management-rules)
4. [Commands Reference](#commands-reference)
5. [Features Deep-Dive](#features-deep-dive)
6. [Monitoring & Troubleshooting](#monitoring--troubleshooting)
7. [Deployment & Maintenance](#deployment--maintenance)

---

## Quick Start

### Starting the Bot
```bash
sudo systemctl start trading-bot
```

### Stopping the Bot
```bash
sudo systemctl stop trading-bot
```

### Checking Status
```bash
sudo systemctl status trading-bot
tail -50 /opt/trading-bot/bot.log
```

### Telegram Commands (Essentials)
| Action | Command |
|--------|---------|
| Start scanning | `/start` |
| Stop scanning | `/stop` |
| Check positions | `/positions` |
| Account balance | `/balance` |
| Quick risk check | `/risk` |
| H4 trend bias | `/mtf` |

---

## Signal Scoring & When to Trade

### How Confluence Scoring Works

The bot scores every signal on a **confluence scale (0-15+)**. Higher = more confirmation factors aligned.

| Component | Points | Description |
|-----------|--------|-------------|
| Zone type (OB/FVG/BB) | +1 to +3 | Smart money concept zones detected |
| MSS/BOS confirmation | +2 to +3 | Market structure shift or break of structure |
| Multiple zone overlap | +1 to +2 | Multiple zones converge at same price |
| RSI neutral range | +1 | RSI between 30-70 (not overbought/oversold) |
| Session alignment | +1 | Signal during active session (London/NY) |
| Regime alignment | +1 | Trending + normal volatility |
| MTF alignment | +2 | H4 bias confirms signal direction |
| MTF counter-trend | -1 | H4 bias opposes signal direction |

### Signal Quality Tiers

| Confluence | Quality | Action |
|------------|---------|--------|
| **10+** | Excellent | Auto-executes (high confidence) |
| **8-9** | Good | Auto-executes (solid setup) |
| **6-7** | Moderate | Auto-executes (acceptable) |
| **4-5** | Low | Typically filtered out (below threshold) |
| **0-3** | Noise | Never executed |

### When to TAKE a Trade (Safe Conditions)
- Confluence >= 8 with MTF alignment (H4 bias matches direction)
- Active market session (London 07:00-16:00 UTC or NY 13:00-21:00 UTC)
- Regime: trending + normal volatility
- No conflicting open position on same instrument
- Daily loss limit not exceeded
- R:R ratio >= 1:1.5 (ideally 1:2+)

### When to AVOID Trading
- **MTF counter-trend** — Signal opposes H4 bias (especially if MTF confidence >= 75%)
- **Weekend gap risk** — Friday after 20:00 UTC (close positions or reduce exposure)
- **Ranging + high volatility regime** — Choppy markets whipsaw signals
- **Daily loss limit hit** — Bot auto-blocks but manual trades should also stop
- **Low session hours** — Asian session for non-JPY/AUD pairs (low liquidity)
- **Spread too wide** — If bot reports "spread too far", wait for better conditions
- **Multiple losses in a row** — After 3 consecutive losses, consider pausing for the session

### MTF (Multi-Timeframe) Alignment Guide

Check `/mtf` before manual trades:

| MTF Bias | Your Signal | Recommendation |
|----------|-------------|----------------|
| 🟢 Bullish (75%+) | BUY | ✅ Strong alignment — take with confidence |
| 🟢 Bullish (50-74%) | BUY | ✅ Aligned — normal position |
| 🔴 Bearish (75%+) | BUY | ⚠️ Counter-trend — avoid or reduce size |
| ⚪ Neutral | Any | Okay — no bonus/penalty, use other factors |
| 🔴 Bearish (75%+) | SELL | ✅ Strong alignment — take with confidence |

---

## Risk Management Rules

### Position Sizing
- Default lot sizes per instrument are in `/instruments`
- All sizes calibrated for ~1-2% account risk per trade
- With ~93 SGD balance: each loss should be < 2 SGD (1-2%)

### Daily Loss Limit
- **Hard limit**: -5% of account per day
- Bot auto-blocks new trades when daily loss hits limit
- Reset: midnight UTC
- If triggered: do NOT override — wait for next day

### Maximum Open Positions
- Recommended: 2-3 simultaneous positions maximum
- Avoid: 3+ positions in same direction (correlation risk)
- Avoid: Same currency exposure (e.g., EURUSD BUY + GBPUSD BUY = double short-USD)

### Breakeven Rules
- When trade reaches +1.0R profit → SL moves to entry (risk-free)
- Notification sent via Telegram when triggered
- Don't manually move SL back after breakeven is hit

### Partial Take Profit
- At +1.5R → 50% of position closed automatically
- Remaining 50% trails with 1.5x ATR stop
- Don't manually close the trailing portion unless invalidated

### Weekend Protocol
1. **Friday 20:00 UTC**: Run `/risk` to assess exposure
2. **If gap risk HIGH**: Close non-crypto positions
3. **If gap risk MEDIUM**: Consider reducing to 1 position
4. **Crypto**: Can hold over weekend (24/7 market) but expect volatility
5. **Auto-report**: Bot sends risk report Friday 21:50 UTC (when enabled)

---

## Commands Reference

### Scanner Control
| Command | Description |
|---------|-------------|
| `/start` | Start the signal scanner |
| `/stop` | Stop scanning (positions stay open) |
| `/scan` | Force immediate scan cycle |
| `/status` | Show scanner status, uptime, scan count |

### Position Management
| Command | Description |
|---------|-------------|
| `/positions` | Show all open positions with live P&L |
| `/balance` | Account balance and margin info |
| `/pending` | Pending orders (if any) |

### Risk & Analytics
| Command | Description |
|---------|-------------|
| `/risk` | Weekend risk report (weekly P&L, positions, gap risk) |
| `/stats` | All-time trading statistics |
| `/journal` | Trade journal (last N trades) |
| `/regime` | Current market regime per instrument |

### MTF Analysis
| Command | Description |
|---------|-------------|
| `/mtf` | H4 bias for all scanning instruments |

Output shows: Instrument | Bias | Structure (HH/HL or LH/LL) | Confidence % | Last MSS

### Signal Recall
| Command | Description |
|---------|-------------|
| `/recall` | Last 4 hours of signals with live validation |
| `/recall 8` | Last 8 hours of signals |
| `/recall 2d` | Last 2 days of signals |

Recalled signals show:
- Original confluence score
- Whether the setup is still valid (zone not mitigated)
- Execute button if still tradeable

### ML Scoring
| Command | Description |
|---------|-------------|
| `/mlstats` | Model accuracy, feature importance, trade count |
| `/retrain` | Force model retrain on current data |
| `/mlthreshold 0.4` | Adjust confidence threshold (0.0-1.0) |

**Note**: ML requires 30+ closed trades to activate. Until then, all signals pass through.

### Instrument Management
| Command | Description |
|---------|-------------|
| `/instruments` | List all instruments with lot/pip sizes |
| `/add <name> <epic> <pip> <lot>` | Add new instrument |
| `/remove <name>` | Remove instrument from scanning |
| `/lotsize <name> <size>` | Change lot size |
| `/pip <name> <size>` | Change pip size |

### Trade Management
| Command | Description |
|---------|-------------|
| `/breakeven` | Status of breakeven triggers |
| `/partialtp` | Toggle partial TP, show settings |
| `/trademanage <deal_id>` | Manual breakeven/partial for specific trade |
| `/validate` | Run pattern validation on all open trades |

### P&L Management
| Command | Description |
|---------|-------------|
| `/fixpnl` | Fix P&L for recent trades (auto-detect) |
| `/fixpnl <id>` | Fix P&L for specific trade ID |

### Info
| Command | Description |
|---------|-------------|
| `/about` | Bot version and feature summary |
| `/help` | Command list |

---

## Features Deep-Dive

### 1. SMC/ICT Signal Scanner
The core engine scans 9 instruments across multiple timeframes (M15, H1, H4) every 5 minutes.

**Detection**: Order Blocks, Fair Value Gaps, Breaker Blocks, Inversion FVGs
**Confirmation**: MSS (Market Structure Shift) or BOS (Break of Structure)
**Filtering**: RSI, regime, session, conflict, market hours, deduplication

### 2. MTF Confluence (v2.4.0)
Checks H4 candle structure to determine trend bias:
- **HH/HL pattern** = Bullish bias
- **LH/LL pattern** = Bearish bias
- **Mixed** = Neutral

Signals aligned with H4 get +2 confluence. Counter-trend signals get -1.
When `MTF_REQUIRED=true`: counter-trend signals are blocked entirely.

### 3. ML Signal Scoring
RandomForest classifier trained on your own trade history:
- Features: confluence, RSI, ADX, ATR ratio, regime, session, timeframe, hour, day
- Predicts win probability (0-1)
- Blocks signals below threshold (default 0.35)
- Auto-retrains every 24h or after 10 new closed trades
- Requires 30+ closed trades to activate

### 4. Breakeven & Partial Take Profit
Automatic position management:
- **Breakeven**: SL → entry when profit reaches 1.0R
- **Partial TP**: Close 50% at 1.5R, trail remainder with 1.5 ATR
- Reduces risk-to-reward while locking in profits

### 5. Signal Recall & Revalidation
`/recall` lets you review recent signals and execute ones that are still valid.
Checks:
- Is the entry zone (OB/FVG) still unmitigated?
- Has the MSS/BOS been invalidated?
- Is the R:R still acceptable at current price?

### 6. Market Hours Filter
- **Forex/Commodities/Indices**: Blocked Friday 22:00 → Sunday 22:00 UTC
- **Crypto**: Always open (24/7), but flagged for low weekend volume
- Prevents false signals during closed markets

### 7. Conflict Resolution
If BUY and SELL signals fire for the same instrument in the same scan:
- Higher confluence wins
- Lower confluence gets marked "skipped"
- Prevents hedging against yourself

### 8. Regime Enforcement
Market classified as: trending/ranging + normal_vol/high_vol/low_vol
- **Trending + normal_vol**: Best conditions, signals pass
- **Ranging + high_vol**: Signals BLOCKED (choppy = whipsaws)
- Others: Signals pass but may have reduced confluence

---

## Monitoring & Troubleshooting

### Log Location
```bash
tail -f /opt/trading-bot/bot.log
```

### Key Log Patterns
| Pattern | Meaning |
|---------|---------|
| `TOP5: EURUSD BUY [M15]` | Signal executed |
| `DEDUP: EURUSD BUY [M15]` | Duplicate signal skipped |
| `MTF COUNTER: gbpusd BUY conf 10->9` | MTF reduced confluence |
| `MTF BLOCKED: gbpusd BUY` | Counter-trend signal blocked |
| `ML BLOCKED: ... score=0.28` | ML model rejected signal |
| `CONFLICT: ... lower confluence` | Conflict filter resolved |
| `REGIME BLOCKED` | Market conditions unsuitable |
| `Market closed` | Instrument outside trading hours |

### Common Issues

| Issue | Solution |
|-------|----------|
| Bot not responding to commands | `sudo systemctl restart trading-bot` |
| "API client not ready" | Wait 30s after restart for session to establish |
| "Spread too far" error | Market has wide spread; wait or trade later |
| Positions not syncing | Run `/fixpnl` to reconcile |
| P&L showing 0 | Run `/fixpnl <trade_id>` to backfill from transaction API |
| Daily loss limit hit | Wait until midnight UTC reset; do NOT override |
| `/mtf` shows wrong instruments | Check `/instruments` — scan list may need updating |

### Database Inspection
```bash
cd /opt/trading-bot
venv/bin/python3 -c "
import sqlite3
conn = sqlite3.connect('data/bot.db')
conn.row_factory = sqlite3.Row

# Recent trades
for r in conn.execute('SELECT id, epic, direction, status, pnl, pnl_r FROM trades ORDER BY id DESC LIMIT 5'):
    print(dict(r))

# Signal stats today
r = conn.execute('SELECT COUNT(*) as total FROM signals WHERE date(timestamp)=date("now")').fetchone()
print(f'Signals today: {r[0]}')
conn.close()
"
```

---

## Deployment & Maintenance

### Deployment Procedure
```bash
cd /opt/trading-bot
rm -rf /tmp/v240
git clone https://github.com/gkotpalliwar-maker/capital-trading-bot.git /tmp/v240
bash /tmp/v240/install.sh
rm -rf /tmp/v240
sudo systemctl restart trading-bot
```

### Hotfix (single file)
```bash
cd /opt/trading-bot
rm -rf /tmp/fix && git clone https://github.com/gkotpalliwar-maker/capital-trading-bot.git /tmp/fix
cp /tmp/fix/bot/<filename>.py bot/
rm -rf /tmp/fix
sudo systemctl restart trading-bot
```

### Configuration (.env)
Key settings in `/opt/trading-bot/.env`:
```bash
# Risk Management
DAILY_LOSS_LIMIT_PCT=5          # Max daily loss as % of balance
MAX_OPEN_POSITIONS=3            # Maximum simultaneous positions

# ML Signal Scoring
ML_CONFIDENCE_THRESHOLD=0.35   # Min win probability (0-1)
ML_MIN_TRADES=30               # Min trades before ML activates

# Breakeven & Partial TP
BREAKEVEN_TRIGGER_R=1.0        # Move SL to entry after 1R profit
PARTIAL_TP_ENABLED=true        # Enable partial TP
PARTIAL_TP_TARGET_R=1.5        # Close partial at 1.5R
PARTIAL_TP_RATIO=0.5           # Close 50% at target

# MTF Confluence
MTF_REQUIRED=false             # true=block counter-trend, false=advisory
MTF_BONUS_CONFLUENCE=2         # Bonus for aligned signals
```

### Backup & Recovery
```bash
# Backups are in:
ls /opt/trading-bot/backups/

# Restore scanner.py from backup:
cp backups/pre_v240_*/scanner.py bot/scanner.py
sudo systemctl restart trading-bot
```

### Service Management
```bash
sudo systemctl start trading-bot    # Start
sudo systemctl stop trading-bot     # Stop
sudo systemctl restart trading-bot  # Restart
sudo systemctl status trading-bot   # Status
journalctl -u trading-bot -n 50    # System logs
```

---

## Scanning Instruments (Default)

| Name | Epic | Category |
|------|------|----------|
| eurusd | EURUSD | Forex |
| gbpusd | GBPUSD | Forex |
| usdjpy | USDJPY | Forex |
| gold | GOLD | Commodity |
| crude | OIL_CRUDE | Commodity |
| btcusd | BTCUSD | Crypto |
| ethusd | ETHUSD | Crypto |
| nas100 | US100 | Index |
| spx500 | US500 | Index |

Manage with: `/instruments`, `/add`, `/remove`

---

## Decision Flowchart

```
Signal Detected (M15/H1)
    │
    ├─ Confluence >= threshold? ──No──→ SKIP
    │
    ├─ Market hours open? ──No──→ SKIP
    │
    ├─ Regime suitable? ──No──→ BLOCKED
    │
    ├─ Duplicate check? ──Yes──→ DEDUP SKIP
    │
    ├─ Conflict resolution? ──Lower──→ SKIP
    │
    ├─ ML score >= threshold? ──No──→ ML BLOCKED
    │
    ├─ MTF alignment check:
    │   ├─ Aligned ──→ +2 confluence
    │   ├─ Neutral ──→ no change
    │   └─ Counter-trend:
    │       ├─ MTF_REQUIRED=true ──→ BLOCKED
    │       └─ MTF_REQUIRED=false ──→ -1 confluence
    │
    ├─ Daily loss limit OK? ──No──→ BLOCKED
    │
    └─ EXECUTE TRADE
        ├─ Set SL/TP
        ├─ Save to DB
        ├─ Notify Telegram (with Execute button)
        └─ Monitor for Breakeven/Partial TP
```

---

## Performance Benchmarks (Your Account)

| Metric | Value | Target |
|--------|-------|--------|
| All-time trades | 12 | — |
| Win rate | 42% | > 45% |
| Total P&L | -11.56 SGD | Positive |
| Avg R per trade | ~-0.3R | > +0.3R |
| Best instrument | TBD (need more data) | — |

**Key insight**: With MTF filtering now active, counter-trend losers should decrease.
Monitor for 2-3 weeks to see improvement before enabling `MTF_REQUIRED=true`.

---

*Last updated: 20 April 2026 | v2.4.0*
