"""
Capital.com Trading Bot v2.1 - Risk Manager
Portfolio-level risk controls, duplicate suppression, and execution validation.
"""
import time
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from config import (PIP_SIZE, DEFAULT_SIZE, get_current_session,
                    MAX_DAILY_LOSS, MAX_OPEN_TRADES, MAX_TRADES_PER_INSTRUMENT,
                    SIGNAL_EXPIRY_SEC, MAX_PRICE_DRIFT_PCT, MAX_SPREAD_MULTIPLIER,
                    COOLDOWN_AFTER_LOSSES, COOLDOWN_MINUTES, DEDUP_HOURS,
                     DEDUP_HOURS_MAP)
import persistence as db

logger = logging.getLogger(__name__)

# Callback lock: track recently executed sig_ids to prevent double-tap
_executed_callbacks = {}  # sig_id -> timestamp
CALLBACK_LOCK_SEC = 30


def check_risk_allowed(client, instrument: str, direction: str,
                       epic: str = "") -> Tuple[bool, str]:
    """
    Master risk check before any trade. Returns (allowed, reason).
    """
    # 1. Max daily loss
    daily_pnl = db.get_today_closed_pnl()
    if daily_pnl < -abs(MAX_DAILY_LOSS):
        return False, f"Daily loss limit hit ({daily_pnl:.2f})"

    # 2. Max open trades
    open_trades = db.get_open_trades()
    if len(open_trades) >= MAX_OPEN_TRADES:
        return False, f"Max open trades ({MAX_OPEN_TRADES}) reached"

    # 3. Max per instrument
    inst_trades = [t for t in open_trades
                   if t.get("epic") == epic or t.get("instrument") == instrument]
    if len(inst_trades) >= MAX_TRADES_PER_INSTRUMENT:
        return False, f"Max trades for {epic} ({MAX_TRADES_PER_INSTRUMENT}) reached"

    # 4. Block duplicate direction on same instrument
    same_dir = [t for t in inst_trades if t.get("direction") == direction]
    if same_dir:
        return False, f"Already have {direction} on {epic}"

    # 5. Cooldown after consecutive losses
    today_trades = db.get_today_trades()
    closed_today = [t for t in today_trades if t.get("status") == "closed"]
    if len(closed_today) >= COOLDOWN_AFTER_LOSSES:
        recent = closed_today[-COOLDOWN_AFTER_LOSSES:]
        all_losses = all((t.get("pnl") or 0) <= 0 for t in recent)
        if all_losses:
            last_close = recent[-1].get("close_time", "")
            if last_close:
                from datetime import datetime as dt
                try:
                    close_dt = dt.fromisoformat(last_close)
                    elapsed = (datetime.now(timezone.utc) - close_dt).total_seconds() / 60
                    if elapsed < COOLDOWN_MINUTES:
                        return False, f"Cooldown: {COOLDOWN_AFTER_LOSSES} consecutive losses, wait {int(COOLDOWN_MINUTES - elapsed)}m"
                except Exception:
                    pass

    return True, "OK"


def check_signal_fresh(sig_data: Dict) -> Tuple[bool, str]:
    """Check if a signal is still fresh enough to execute."""
    sig_time = sig_data.get("_created_at", 0)
    if sig_time and (time.time() - sig_time) > SIGNAL_EXPIRY_SEC:
        return False, f"Signal expired ({SIGNAL_EXPIRY_SEC}s)"
    return True, "OK"


def check_duplicate_signal(instrument: str, direction: str,
                           timeframe: str) -> Tuple[bool, str]:
    """Check for duplicate signals in recent history."""
    # v2.7.2: Per-timeframe dedup TTL
    dedup_ttl = DEDUP_HOURS_MAP.get(timeframe, DEDUP_HOURS)
    count = db.get_pending_signal_count(instrument, direction, timeframe, dedup_ttl)
    if count > 0:
        return True, f"Duplicate: {instrument} {direction} {timeframe} already signaled"
    return False, "OK"


def check_execution_valid(client, sig_data: Dict) -> Tuple[bool, str]:
    """
    Validate execution conditions:
    - Price drift from signal
    - Spread acceptable
    - SL/TP still valid
    """
    from data_fetcher import get_current_price

    try:
        epic = sig_data.get("epic", sig_data.get("inst_name", ""))
        price_info = get_current_price(client, sig_data.get("instrument", epic))
    except Exception as e:
        return False, f"Cannot get price: {e}"

    direction = sig_data.get("direction", "")
    entry = sig_data.get("entry", 0)
    sl = sig_data.get("sl", 0)
    tp = sig_data.get("tp", 0)

    if not entry or not sl:
        return True, "OK"  # No entry/SL to validate

    current = price_info["ask"] if direction == "BUY" else price_info["bid"]
    spread = price_info["spread"]

    # Price drift check
    drift_pct = abs(current - entry) / entry * 100
    if drift_pct > MAX_PRICE_DRIFT_PCT:
        return False, f"Price drifted {drift_pct:.2f}% from signal entry"

    # Spread check
    pip = PIP_SIZE.get(epic, 0.0001)
    normal_spread = pip * 3  # rough baseline
    if spread > normal_spread * MAX_SPREAD_MULTIPLIER:
        return False, f"Spread too wide: {spread:.5f} (normal ~{normal_spread:.5f})"

    # SL/TP still valid
    if direction == "BUY":
        if current <= sl:
            return False, "Price already at/below SL"
        if tp and current >= tp:
            return False, "Price already at/above TP"
    else:
        if current >= sl:
            return False, "Price already at/above SL"
        if tp and current <= tp:
            return False, "Price already at/below TP"

    return True, "OK"


def check_callback_lock(sig_id: str) -> Tuple[bool, str]:
    """Prevent double-tap on Telegram buttons."""
    now = time.time()
    # Cleanup old locks
    expired = [k for k, v in _executed_callbacks.items() if now - v > CALLBACK_LOCK_SEC]
    for k in expired:
        del _executed_callbacks[k]

    if sig_id in _executed_callbacks:
        return False, "Already executing (double-tap prevented)"

    _executed_callbacks[sig_id] = now
    return True, "OK"


def release_callback_lock(sig_id: str):
    """Release callback lock after execution attempt."""
    _executed_callbacks.pop(sig_id, None)


def get_risk_status() -> Dict:
    """Get current risk status for display."""
    open_trades = db.get_open_trades()
    daily_pnl = db.get_today_closed_pnl()
    today_trades = db.get_today_trades()
    closed_today = [t for t in today_trades if t.get("status") == "closed"]

    # Check consecutive losses
    consec_losses = 0
    for t in reversed(closed_today):
        if (t.get("pnl") or 0) <= 0:
            consec_losses += 1
        else:
            break

    return {
        "open_trades": len(open_trades),
        "max_open": MAX_OPEN_TRADES,
        "daily_pnl": daily_pnl,
        "max_daily_loss": MAX_DAILY_LOSS,
        "trades_today": len(today_trades),
        "closed_today": len(closed_today),
        "consecutive_losses": consec_losses,
        "cooldown_threshold": COOLDOWN_AFTER_LOSSES,
    }
