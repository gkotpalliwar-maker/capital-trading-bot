# Release Notes - v2.12.0 Market Memory

Commits:

- `6fca4fd` introduced the market-memory layer.
- `e42f439` added the signal backtest utility.
- `f955f52` added simple TP/SL outcome simulation.
- `17cb068` sped up backtest replay with fast guardrails and progress output.

## Summary

This release gives the signal decision engine a structured market-memory layer. The bot now evaluates whether recent price action has respected, broken, reclaimed, over-tested, or chopped through important zones before deciding whether a signal should be blocked, watched, alerted, or executable.

The goal is not to add more live strategies. The goal is to make existing strategy candidates more trader-like and context-aware.

## Theoretical Changes

### Market Memory Concept

The bot previously judged signals mostly from the current candidate and current filters: guardrails, regime, news, MTF, ML, duplicate checks, and R:R.

This release adds a contextual memory layer:

- Has demand/support recently been swept and reclaimed?
- Has supply/resistance recently been taken back by sellers?
- Did a breakout fail and return into range?
- Is a zone being respected or over-touched?
- Is the instrument chopping through the same range?
- Have recent same-direction signals been weak or blocked?

The decision engine now behaves more like:

```text
Strategy proposes a candidate.
Market memory adds recent context.
Decision engine approves, downgrades, blocks, or alerts.
```

Memory does not create new trades. It only modifies existing candidates.

### Demand/Support Takeover

A bullish memory bias can appear when price:

- Breaks below a support or demand area.
- Fails to continue lower.
- Reclaims above the area.
- Shows a hold or displacement after reclaim.

This can add confidence to BUY candidates and warn against SELL candidates.

### Supply/Resistance Takeover

A bearish memory bias can appear when price:

- Breaks above supply or resistance.
- Fails to continue higher.
- Reclaims below the area.
- Shows a hold or displacement after reclaim.

This can add confidence to SELL candidates and warn against BUY candidates.

### Failed Breaks

The memory layer identifies failed breakouts and failed breakdowns. If the current candidate aligns with the reversal side, it can receive a small bonus. If it tries to continue in the failed direction, it receives a penalty.

### Zone Health

The bot now distinguishes between:

- Clean first or second zone touch: small confidence bonus.
- Over-touched zone: confidence penalty.

This is intended to reduce late entries into weakening zones.

### Chop Awareness

If recent closes repeatedly cross the same mid-range area, memory marks the context as choppy and applies a penalty. This helps reduce executable alerts in noisy, non-directional conditions.

## Technical Changes

### Added `bot/pattern_memory.py`

New deterministic module exposing:

```python
evaluate_market_memory(
    instrument: str,
    timeframe: str,
    direction: str,
    signal: dict,
    df,
    lookback: int = 120,
) -> dict
```

Returns:

```python
{
    "bias": "bullish" | "bearish" | "neutral" | "choppy",
    "score_adj": int,
    "reasons": list[str],
    "warnings": list[str],
    "blocks": list[str],
    "context": dict,
}
```

### Integrated Memory Into `bot/signal_decision.py`

The decision engine now:

- Imports `pattern_memory` safely.
- Calls `evaluate_market_memory()` for every candidate.
- Adds `score_adj` to the unified decision score.
- Adds memory reasons and warnings to the Telegram decision text.
- Stores memory output under `decision["modifiers"]["market_memory"]`.

### Fixed Guardrail Metadata Input

The decision engine now passes the full signal dict into guardrails:

```python
signal_metadata=signal
```

This allows SL/TP direction checks to see top-level `entry`, `sl`, and `tp`, preventing inverted SL/TP signals from slipping through.

### Fixed Instrument/Epic Input To Decision Engine

`scanner.py` now passes the resolved epic into the decision engine:

```python
instrument=inst_name
```

This makes news, MTF, duplicate checks, and memory work with canonical symbols such as `GOLD`, `OIL_CRUDE`, and `BTCUSD`.

### Added Status Caps

The decision engine now supports status caps:

- If news is required but unavailable or errored, executable signals are capped at `ALERT`.
- Non-top5 setups are capped at `ALERT` unless explicitly allowed.

This prevents memory or other modifiers from making weak/non-preferred setups executable by accident.

### Version Update

`bot/version.py` updated:

```python
BOT_VERSION = "2.12.0"
__codename__ = "market-memory"
```

### Added `utils/backtest_signals.py`

New utility to replay recent Capital.com candles through the current strategy and decision stack.

It can:

- Fetch recent candles using the configured Capital.com credentials.
- Replay rolling historical windows.
- Generate SMC/ICT candidates.
- Optionally include retrace-entry candidates.
- Run the signal decision engine and market memory layer.
- Write signal classifications to CSV.
- Simulate simple TP/SL outcomes for `ALERT` and `EXECUTABLE` rows.

This is a signal replay tool, not a full broker simulator. It does not model spread, slippage, partial closes, trade management, or live news/MTF context unless separately wired in. Outcome simulation is conservative: if TP and SL are both touched in the same candle, SL is counted first.

Example fast H1 run:

```bash
python utils/backtest_signals.py \
  --instruments gold ethusd crude \
  --timeframes H1 \
  --candles 800 \
  --step 5 \
  --include-retrace \
  --outcome-bars 48 \
  --csv data/backtest_fast_h1.csv
```

Example expanded run:

```bash
python utils/backtest_signals.py \
  --instruments gold ethusd crude \
  --timeframes M15 H1 H4 \
  --candles 800 \
  --step 5 \
  --include-retrace \
  --outcome-bars 48 \
  --csv data/backtest_fast_m15_h1_h4.csv
```

Useful options:

```bash
--step 5              # Replay every 5 candles instead of every candle
--max-steps 30        # Limit replay work per instrument/timeframe
--progress-every 25   # Print progress while running
--full-guardrails     # Use slower external guardrails
--no-simulate-outcomes
```

Default backtest mode now uses fast price-action guardrails. This avoids slow candle-by-candle calls to external-style intelligence checks such as COT, TradingView, and fear/greed.

## Validation Performed

Commands run locally:

```bash
python -m compileall -q bot utils diag patches
git diff --cached --check
```

Also ran a synthetic smoke test through `signal_decision.evaluate_signal_candidate()` to confirm market memory is attached to the decision modifiers and Telegram text.

Backtest utility validation:

```bash
python -m compileall -q utils/backtest_signals.py
```

VPS backtest runs observed that edge is concentrated by instrument/timeframe/setup rather than universal. In sample runs, H1 performed better than the combined M15/H1/H4 basket, and broad executable promotion was not justified yet.

## Deployment Notes

Recommended VPS deployment:

```bash
cd /opt/trading-bot
sudo systemctl stop trading-bot
git fetch origin
git pull --ff-only origin main
source venv/bin/activate
pip install -r requirements.txt
python -m compileall -q bot utils
sudo systemctl start trading-bot
sudo systemctl status trading-bot --no-pager
```

If the bot lives somewhere else, replace `/opt/trading-bot` with the actual directory.

## Suggested Post-Deploy Checks

```bash
journalctl -u trading-bot -n 100 --no-pager
tail -n 100 bot.log
```

Look for:

- `Signal decision engine` loaded.
- No import error for `pattern_memory`.
- Telegram alerts include a `Market Memory` section.
- News guard active.
- No SL/TP direction guardrail errors.

## Things To Watch

This release is intentionally conservative, but memory thresholds may need tuning after observing real alerts.

Watch for:

- Too many `choppy` penalties.
- Too many over-touched-zone penalties on naturally ranging pairs.
- Memory adding confidence to retrace signals too often.
- Non-top5 setups appearing as alerts. They should not become executable unless explicitly allowed.
- Backtest results varying strongly by instrument/timeframe/setup.
- H4 and M15 alerts dragging down combined results if treated too broadly.
- Same-candle BUY/SELL conflicts, which should be handled by a future conflict arbitration layer.

## Next Possible Improvements

- Persist zone memory in SQLite instead of computing it only from recent candles.
- Track actual post-signal outcomes by memory context.
- Add `/memory` Telegram command to inspect current memory bias per instrument.
- Add config/env toggles for memory weights.
- Add unit tests for demand takeover, supply takeover, failed breakout, failed breakdown, and chop detection.
- Add adaptive `market_policy.py` based on rolling backtest outcomes.
- Store nightly backtest rows in SQLite and generate policy recommendations.
- Add conflict arbitration before allowing executable signals.
