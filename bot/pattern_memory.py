"""Market memory for signal decisions.

This module does not create trades. It reads recent market behavior and
returns explainable context that can raise, lower, or block a candidate signal.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("pattern_memory")

# Conservative score adjustments. Memory should refine, not dominate.
STRONG_TAKEOVER_BONUS = 12
MODERATE_MEMORY_BONUS = 6
ZONE_RESPECT_BONUS = 5
FAILED_OPPOSITE_BONUS = 4
CHOPPY_PENALTY = -15
OVERTOUCHED_PENALTY = -8
CONTRADICTION_PENALTY = -12
RECENT_FAILURE_PENALTY = -10

LOOKBACK_DEFAULT = 120
ZONE_LOOKBACK = 50
TAKEOVER_LOOKBACK = 60
RANGE_LOOKBACK = 36
TOUCH_ATR_MULT = 0.35
BREAK_ATR_MULT = 0.25
RECLAIM_ATR_MULT = 0.15
DISPLACEMENT_ATR_MULT = 0.6
MAX_HEALTHY_TOUCHES = 2
OVERTOUCHED_COUNT = 4
CHOPPY_CROSS_COUNT = 8


def _empty_context() -> Dict:
    return {
        "bias": "neutral",
        "score_adj": 0,
        "reasons": [],
        "warnings": [],
        "blocks": [],
        "context": {},
    }


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _atr(df, idx: int = -1) -> float:
    if df is None or df.empty or "atr" not in df.columns:
        return 0.0
    try:
        val = df["atr"].iloc[idx]
        return _safe_float(val, 0.0)
    except Exception:
        return 0.0


def _recent_zone(df, direction: str, signal: Dict, lookback: int) -> Tuple[float, float, float]:
    """Return a pragmatic zone around recent support/demand or supply/resistance."""
    entry = _safe_float(signal.get("entry") or signal.get("entry_price"))
    sl = _safe_float(signal.get("sl") or signal.get("stop_loss"))
    if entry and sl:
        low, high = sorted((entry, sl))
        mid = (low + high) / 2
        return low, high, mid

    recent = df.tail(min(lookback, len(df)))
    if direction == "BUY":
        low = _safe_float(recent["low"].min())
        high = _safe_float(recent["low"].quantile(0.25))
    else:
        low = _safe_float(recent["high"].quantile(0.75))
        high = _safe_float(recent["high"].max())
    return low, high, (low + high) / 2


def _count_zone_touches(df, low: float, high: float, atr_val: float, lookback: int) -> int:
    if low <= 0 or high <= 0 or df is None or df.empty:
        return 0
    buffer = max(atr_val * TOUCH_ATR_MULT, abs(high - low) * 0.25)
    z_low, z_high = low - buffer, high + buffer
    recent = df.tail(min(lookback, len(df)))
    touches = 0
    was_inside = False
    for _, row in recent.iterrows():
        inside = _safe_float(row.get("low")) <= z_high and _safe_float(row.get("high")) >= z_low
        if inside and not was_inside:
            touches += 1
        was_inside = inside
    return touches


def _detect_takeover(df, direction: str, low: float, high: float, atr_val: float) -> Optional[Dict]:
    """Detect support/demand or supply/resistance takeover near the signal zone."""
    if low <= 0 or high <= 0 or df is None or len(df) < 12:
        return None

    recent = df.tail(min(TAKEOVER_LOOKBACK, len(df))).copy()
    if recent.empty:
        return None

    break_buffer = max(atr_val * BREAK_ATR_MULT, abs(high - low) * 0.15)
    reclaim_buffer = max(atr_val * RECLAIM_ATR_MULT, abs(high - low) * 0.1)

    if direction == "BUY":
        broken = recent[recent["close"] < low - break_buffer]
        if broken.empty:
            return None
        break_pos = recent.index.get_loc(broken.index[-1])
        after = recent.iloc[break_pos + 1:]
        reclaimed = after[after["close"] > high + reclaim_buffer]
        if reclaimed.empty:
            return None
        reclaim_pos = recent.index.get_loc(reclaimed.index[0])
        confirm = recent.iloc[reclaim_pos:]
        held = bool((confirm["low"] >= low - reclaim_buffer).tail(5).any())
        displacement = _has_displacement(confirm, "BUY", atr_val)
        strength = "strong" if held and displacement else "moderate"
        return {
            "type": "demand_takeover",
            "bias": "bullish",
            "strength": strength,
            "held": held,
            "displacement": displacement,
            "level": high,
        }

    broken = recent[recent["close"] > high + break_buffer]
    if broken.empty:
        return None
    break_pos = recent.index.get_loc(broken.index[-1])
    after = recent.iloc[break_pos + 1:]
    reclaimed = after[after["close"] < low - reclaim_buffer]
    if reclaimed.empty:
        return None
    reclaim_pos = recent.index.get_loc(reclaimed.index[0])
    confirm = recent.iloc[reclaim_pos:]
    held = bool((confirm["high"] <= high + reclaim_buffer).tail(5).any())
    displacement = _has_displacement(confirm, "SELL", atr_val)
    strength = "strong" if held and displacement else "moderate"
    return {
        "type": "supply_takeover",
        "bias": "bearish",
        "strength": strength,
        "held": held,
        "displacement": displacement,
        "level": low,
    }


def _has_displacement(df, direction: str, atr_val: float) -> bool:
    if df is None or df.empty:
        return False
    recent = df.tail(min(8, len(df)))
    threshold = atr_val * DISPLACEMENT_ATR_MULT if atr_val > 0 else 0
    for _, row in recent.iterrows():
        body = abs(_safe_float(row.get("close")) - _safe_float(row.get("open")))
        if threshold and body < threshold:
            continue
        if direction == "BUY" and _safe_float(row.get("close")) > _safe_float(row.get("open")):
            return True
        if direction == "SELL" and _safe_float(row.get("close")) < _safe_float(row.get("open")):
            return True
    return False


def _detect_failed_break(df, direction: str, atr_val: float) -> Optional[Dict]:
    if df is None or len(df) < RANGE_LOOKBACK + 5:
        return None
    recent = df.tail(RANGE_LOOKBACK)
    prior = recent.iloc[:-5]
    latest = recent.iloc[-5:]
    range_high = _safe_float(prior["high"].max())
    range_low = _safe_float(prior["low"].min())
    buffer = atr_val * BREAK_ATR_MULT if atr_val > 0 else (range_high - range_low) * 0.05

    broke_up = bool((latest["high"] > range_high + buffer).any())
    returned_below = _safe_float(latest["close"].iloc[-1]) < range_high
    broke_down = bool((latest["low"] < range_low - buffer).any())
    returned_above = _safe_float(latest["close"].iloc[-1]) > range_low

    if broke_up and returned_below:
        return {
            "type": "failed_breakout",
            "failed_direction": "BUY",
            "reversal_direction": "SELL",
            "level": range_high,
        }
    if broke_down and returned_above:
        return {
            "type": "failed_breakdown",
            "failed_direction": "SELL",
            "reversal_direction": "BUY",
            "level": range_low,
        }
    return None


def _detect_chop(df, atr_val: float) -> Tuple[bool, int]:
    if df is None or len(df) < RANGE_LOOKBACK:
        return False, 0
    recent = df.tail(RANGE_LOOKBACK)
    mid = (_safe_float(recent["high"].max()) + _safe_float(recent["low"].min())) / 2
    closes = [_safe_float(v) for v in recent["close"].tolist()]
    crosses = 0
    prev_side = None
    for close in closes:
        side = 1 if close >= mid else -1
        if prev_side is not None and side != prev_side:
            crosses += 1
        prev_side = side
    width = _safe_float(recent["high"].max()) - _safe_float(recent["low"].min())
    compressed = atr_val > 0 and width < atr_val * 4
    return crosses >= CHOPPY_CROSS_COUNT or (crosses >= 6 and compressed), crosses


def _recent_signal_memory(instrument: str, timeframe: str, direction: str) -> Dict:
    memory = {"same_direction_weak": 0, "opposite_direction_weak": 0}
    try:
        import persistence as db
        rows = db.get_recent_signals(instrument=instrument, timeframe=timeframe, hours=12, limit=25)
    except Exception as exc:
        logger.debug("Signal memory unavailable: %s", exc)
        return memory

    weak_status = {"blocked", "watch", "skipped", "expired", "mtf_blocked"}
    for row in rows:
        status = str(row.get("status", "")).lower()
        if status not in weak_status:
            continue
        if row.get("direction") == direction:
            memory["same_direction_weak"] += 1
        else:
            memory["opposite_direction_weak"] += 1
    return memory


def evaluate_market_memory(
    instrument: str,
    timeframe: str,
    direction: str,
    signal: Dict,
    df,
    lookback: int = LOOKBACK_DEFAULT,
) -> Dict:
    """Evaluate recent market context around a signal candidate."""
    result = _empty_context()
    if df is None or getattr(df, "empty", True) or len(df) < 30:
        result["warnings"].append("Market memory unavailable: insufficient candles")
        return result

    try:
        direction = (direction or "").upper()
        recent = df.tail(min(lookback, len(df)))
        atr_val = _atr(recent)
        low, high, mid = _recent_zone(recent, direction, signal, ZONE_LOOKBACK)
        touches = _count_zone_touches(recent, low, high, atr_val, ZONE_LOOKBACK)
        takeover = _detect_takeover(recent, direction, low, high, atr_val)
        failed_break = _detect_failed_break(recent, direction, atr_val)
        is_choppy, cross_count = _detect_chop(recent, atr_val)
        signal_mem = _recent_signal_memory(instrument, timeframe, direction)

        context = {
            "zone_low": round(low, 5) if low else 0,
            "zone_high": round(high, 5) if high else 0,
            "zone_mid": round(mid, 5) if mid else 0,
            "zone_touches": touches,
            "takeover": takeover,
            "failed_break": failed_break,
            "chop_crosses": cross_count,
            "same_direction_weak": signal_mem["same_direction_weak"],
            "opposite_direction_weak": signal_mem["opposite_direction_weak"],
        }
        result["context"] = context

        if takeover:
            aligned = (
                (direction == "BUY" and takeover["bias"] == "bullish") or
                (direction == "SELL" and takeover["bias"] == "bearish")
            )
            if aligned:
                if takeover["strength"] == "strong":
                    result["score_adj"] += STRONG_TAKEOVER_BONUS
                    result["reasons"].append(
                        f"Strong {takeover['type'].replace('_', ' ')} near {takeover['level']:.5f}"
                    )
                else:
                    result["score_adj"] += MODERATE_MEMORY_BONUS
                    result["reasons"].append(
                        f"Moderate {takeover['type'].replace('_', ' ')} context"
                    )
                result["bias"] = takeover["bias"]
            else:
                result["score_adj"] += CONTRADICTION_PENALTY
                result["warnings"].append(
                    f"Memory contradicts signal: {takeover['type'].replace('_', ' ')}"
                )
                result["bias"] = takeover["bias"]

        if failed_break:
            if failed_break["reversal_direction"] == direction:
                result["score_adj"] += FAILED_OPPOSITE_BONUS
                result["reasons"].append(
                    f"{failed_break['type'].replace('_', ' ')} supports reversal"
                )
            elif failed_break["failed_direction"] == direction:
                result["score_adj"] += CONTRADICTION_PENALTY
                result["warnings"].append(
                    f"{failed_break['type'].replace('_', ' ')} penalizes continuation"
                )

        if 1 <= touches <= MAX_HEALTHY_TOUCHES:
            result["score_adj"] += ZONE_RESPECT_BONUS
            result["reasons"].append(f"Zone respected on {touches} clean touch(es)")
        elif touches >= OVERTOUCHED_COUNT:
            result["score_adj"] += OVERTOUCHED_PENALTY
            result["warnings"].append(f"Zone over-touched ({touches} touches)")

        if is_choppy:
            result["score_adj"] += CHOPPY_PENALTY
            result["warnings"].append(f"Choppy memory: {cross_count} mid-range crosses")
            if result["bias"] == "neutral":
                result["bias"] = "choppy"

        if signal_mem["same_direction_weak"] >= 2:
            result["score_adj"] += RECENT_FAILURE_PENALTY
            result["warnings"].append(
                f"Recent weak same-direction signals: {signal_mem['same_direction_weak']}"
            )
        elif signal_mem["opposite_direction_weak"] >= 2:
            result["score_adj"] += FAILED_OPPOSITE_BONUS
            result["reasons"].append(
                f"Opposite side recently weak ({signal_mem['opposite_direction_weak']})"
            )

        if result["score_adj"] > 0 and result["bias"] == "neutral":
            result["bias"] = "bullish" if direction == "BUY" else "bearish"

    except Exception as exc:
        logger.warning("Market memory error: %s", exc)
        result = _empty_context()
        result["warnings"].append(f"Market memory error: {exc}")

    return result
