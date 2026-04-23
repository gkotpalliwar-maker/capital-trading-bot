"""MSS/BOS Detection + Enhanced SMC/ICT Signal Filter.

v2.7.1: Fixed SL/TP swap + stronger MSS confirmation + swing_lookback=3.

Changes from v2.3.0:
  - risk <= 0 validation prevents zone-on-wrong-side SL/TP swap
  - MSS break candle must have body > 50% of range
  - MSS break candle body must exceed 0.3 * ATR
"""
import types as _types
import logging
import pandas as pd
from datetime import datetime, timezone

logger = logging.getLogger("mss_bos")

"""MSS/BOS Detection + Enhanced SMC/ICT Signal Filter.

v2.7.1: Fixed SL/TP swap + stronger MSS confirmation + swing_lookback=3.
"""
import types as _types
import logging

logger = logging.getLogger("mss_bos")


def detect_market_structure_shift(df, swing_lookback=3, max_age=30):
    """
    Detect Market Structure Shifts (reversals) and Breaks of Structure (continuations).

    v2.7.0 improvements:
      - Break candle must have body > 50% of range (no wick-dominated closes)
      - Break candle body must exceed 0.3 * ATR (meaningful move, not noise)
      - swing_lookback default changed to 3 (backtested on 1000 candles)
    """
    import pandas as pd
    events = []

    # Step 1: Find swing highs and lows
    swing_points = []
    for i in range(swing_lookback, len(df) - swing_lookback):
        window = df.iloc[i - swing_lookback:i + swing_lookback + 1]
        if df["high"].iloc[i] == window["high"].max():
            swing_points.append({"index": i, "price": df["high"].iloc[i], "swing": "high"})
        if df["low"].iloc[i] == window["low"].min():
            swing_points.append({"index": i, "price": df["low"].iloc[i], "swing": "low"})

    swing_points.sort(key=lambda x: x["index"])

    # Step 2: Classify swings as HH/HL/LH/LL
    last_high, last_low = None, None
    for sp in swing_points:
        if sp["swing"] == "high":
            sp["type"] = "HH" if (last_high is None or sp["price"] > last_high) else "LH"
            last_high = sp["price"]
        else:
            sp["type"] = "HL" if (last_low is None or sp["price"] > last_low) else "LL"
            last_low = sp["price"]

    # Step 3: Detect structure breaks with confirmation
    has_atr = "atr" in df.columns

    for i in range(2, len(swing_points)):
        current = swing_points[i]
        recent = swing_points[max(0, i - 6):i]
        bearish_count = sum(1 for sp in recent if sp.get("type") in ("LH", "LL"))
        bullish_count = sum(1 for sp in recent if sp.get("type") in ("HH", "HL"))
        prev_trend = "bearish" if bearish_count > bullish_count else "bullish"

        # Breaks of swing lows (bearish events)
        if current["swing"] == "low":
            for k in range(current["index"] + 1, min(current["index"] + 20, len(df))):
                if df["close"].iloc[k] < current["price"]:
                    # v2.7.0: Confirmation — strong body candle
                    body = abs(df["close"].iloc[k] - df["open"].iloc[k])
                    full_range = df["high"].iloc[k] - df["low"].iloc[k]
                    if full_range <= 0:
                        break
                    body_ratio = body / full_range
                    if body_ratio < 0.5:
                        continue  # Wick-dominated, not confirmed

                    if has_atr:
                        atr_val = df["atr"].iloc[k]
                        if not pd.isna(atr_val) and atr_val > 0 and body < 0.3 * atr_val:
                            continue  # Too small relative to ATR

                    if prev_trend == "bullish" and current.get("type") == "HL":
                        events.append({"index": k, "time": df.index[k],
                            "type": "bearish_mss", "direction": "SELL",
                            "break_level": current["price"],
                            "swing_index": current["index"],
                            "swing_type": current.get("type", ""),
                            "prev_trend": prev_trend, "is_reversal": True})
                    elif prev_trend == "bearish":
                        events.append({"index": k, "time": df.index[k],
                            "type": "bearish_bos", "direction": "SELL",
                            "break_level": current["price"],
                            "swing_index": current["index"],
                            "swing_type": current.get("type", ""),
                            "prev_trend": prev_trend, "is_reversal": False})
                    break

        # Breaks of swing highs (bullish events)
        if current["swing"] == "high":
            for k in range(current["index"] + 1, min(current["index"] + 20, len(df))):
                if df["close"].iloc[k] > current["price"]:
                    # v2.7.0: Confirmation — strong body candle
                    body = abs(df["close"].iloc[k] - df["open"].iloc[k])
                    full_range = df["high"].iloc[k] - df["low"].iloc[k]
                    if full_range <= 0:
                        break
                    body_ratio = body / full_range
                    if body_ratio < 0.5:
                        continue  # Wick-dominated, not confirmed

                    if has_atr:
                        atr_val = df["atr"].iloc[k]
                        if not pd.isna(atr_val) and atr_val > 0 and body < 0.3 * atr_val:
                            continue  # Too small relative to ATR

                    if prev_trend == "bearish" and current.get("type") == "LH":
                        events.append({"index": k, "time": df.index[k],
                            "type": "bullish_mss", "direction": "BUY",
                            "break_level": current["price"],
                            "swing_index": current["index"],
                            "swing_type": current.get("type", ""),
                            "prev_trend": prev_trend, "is_reversal": True})
                    elif prev_trend == "bullish":
                        events.append({"index": k, "time": df.index[k],
                            "type": "bullish_bos", "direction": "BUY",
                            "break_level": current["price"],
                            "swing_index": current["index"],
                            "swing_type": current.get("type", ""),
                            "prev_trend": prev_trend, "is_reversal": False})
                    break

    events.sort(key=lambda x: x["index"])
    return [e for e in events if e["index"] >= len(df) - max_age]



def best_mss(events):
    """Pick the best MSS/BOS event: prefer reversals, then most recent.
    Exported at module level for import by smc_ict.py.
    """
    if not events:
        return None, 0
    s = sorted(events, key=lambda e: (e["is_reversal"], e["index"]), reverse=True)
    return s[0], 3 if s[0]["is_reversal"] else 2

# ============================================================
# MSS-ENHANCED SIGNAL GENERATION (replaces base generate_signals)
# v2.7.0: Added risk > 0 validation to prevent SL/TP swap
# ============================================================

def _mss_enhanced_generate_signals(self, df, instrument, timeframe,
                                     mss_lookback=25, require_mss=True, **kwargs):
    """
    Enhanced generate_signals with MSS/BOS confirmation.
    MSS (reversal) = +3 confluence, BOS (continuation) = +2.
    Signals blocked without directional MSS/BOS alignment.

    v2.7.0 fix: validates risk > 0 (prevents SL/TP swap when zone is
    on wrong side of entry price).
    """
    import pandas as pd
    from datetime import datetime, timezone

    df = self.detect_pattern(df)
    signals = []

    obs = df.attrs.get("smc_order_blocks", [])
    fvgs = df.attrs.get("smc_fvgs", [])
    bbs = df.attrs.get("smc_breaker_blocks", [])
    mbs = df.attrs.get("smc_mitigation_blocks", [])
    ifvgs_list = df.attrs.get("smc_inversion_fvgs", [])
    all_zones = obs + fvgs + bbs + mbs + ifvgs_list
    if not all_zones:
        return signals

    mss_events = detect_market_structure_shift(df, max_age=mss_lookback)
    df.attrs["smc_mss_events"] = mss_events

    current_price = df["close"].iloc[-1]
    current_low = df["low"].iloc[-1]
    current_high = df["high"].iloc[-1]

    sessions = get_session_for_time(
        df.index[-1] if isinstance(df.index[-1], datetime) else datetime.now(timezone.utc))
    session_str = ", ".join([s.value for s in sessions])

    max_confluence = 15  # OB(3)+FVG(3)+BB(2)+MB(2)+IFVG(1)+MSS(3)+RSI(1)

    buy_zones = [z for z in all_zones if z["direction"] == "BUY"]
    sell_zones = [z for z in all_zones if z["direction"] == "SELL"]

    bullish_mss = [e for e in mss_events if e["direction"] == "BUY"]
    bearish_mss = [e for e in mss_events if e["direction"] == "SELL"]

    def best_mss(events):
        if not events:
            return None, 0
        s = sorted(events, key=lambda e: (e["is_reversal"], e["index"]), reverse=True)
        return s[0], 3 if s[0]["is_reversal"] else 2

    bull_evt, bull_boost = best_mss(bullish_mss)
    bear_evt, bear_boost = best_mss(bearish_mss)

    def _build_signal(direction, zones, mss_evt, mss_boost):
        confluence = 0
        zone_types = []
        best_zone = None

        for zone in zones:
            in_zone = current_low <= zone["top"] and current_high >= zone["bottom"]
            near = abs(current_price - zone["mid"]) <= (zone["top"] - zone["bottom"]) * 2
            if in_zone or near:
                cat = zone.get("category", zone["type"].split("_")[0])
                wt_map = {"bullish_ob": 3, "bearish_ob": 3, "bullish_fvg": 3,
                          "bearish_fvg": 3, "buy_breaker": 2, "sell_breaker": 2,
                          "bullish_mitigation": 2, "bearish_mitigation": 2,
                          "buy_ifvg": 1, "sell_ifvg": 1}
                confluence += wt_map.get(zone["type"], 1)
                zone_types.append(cat)
                if best_zone is None or zone.get("impulse_strength", 0) > best_zone.get("impulse_strength", 0):
                    best_zone = zone

        confluence += mss_boost
        if mss_boost > 0:
            zone_types.append("mss" if mss_boost == 3 else "bos")

        if confluence >= 3 and best_zone:
            entry = current_price
            if direction == "BUY":
                sl = best_zone["bottom"] - (best_zone["top"] - best_zone["bottom"]) * 0.2
                if (entry - sl) / entry > 0.02:
                    sl = entry * 0.98
                risk = entry - sl
            else:
                sl = best_zone["top"] + (best_zone["top"] - best_zone["bottom"]) * 0.2
                if (sl - entry) / entry > 0.02:
                    sl = entry * 1.02
                risk = sl - entry

            # v2.7.0 FIX: Validate risk > 0
            # If zone is on wrong side of entry, risk goes negative
            # -> SL/TP would be swapped. Skip this signal.
            if risk <= 0:
                logger.warning(
                    "Skipping %s signal: zone gives invalid SL "
                    "(sl=%.5f, entry=%.5f, risk=%.5f)",
                    direction, sl, entry, risk
                )
                return None

            tp = entry + risk * 2.5 if direction == "BUY" else entry - risk * 2.5

            rsi_bonus = 1 if ("rsi" in df.columns and not pd.isna(df["rsi"].iloc[-1]) and 40 < df["rsi"].iloc[-1] < 60) else 0
            conf = min((confluence + rsi_bonus) / max_confluence, 0.95)

            return Signal(
                instrument=resolve_instrument(instrument),
                direction=direction, entry_price=entry,
                stop_loss=sl, take_profit=tp, confidence=conf,
                strategy_name=self.name, timeframe=timeframe, session=session_str,
                metadata={
                    "smc_confluence": confluence,
                    "zone_types": "+".join(sorted(set(zone_types))),
                    "mss_type": mss_evt["type"] if mss_evt else "none",
                    "mss_boost": mss_boost,
                    "ob_count": len([z for z in zones if "ob" in z["type"]]),
                    "fvg_count": len([z for z in zones if "fvg" in z["type"]]),
                    "rsi": float(df["rsi"].iloc[-1]) if "rsi" in df.columns and not pd.isna(df["rsi"].iloc[-1]) else None,
                })
        return None

    # BUY signals (require bullish MSS/BOS if require_mss=True)
    if not (require_mss and not bullish_mss):
        sig = _build_signal("BUY", buy_zones, bull_evt, bull_boost)
        if sig:
            signals.append(sig)

    # SELL signals
    if not (require_mss and not bearish_mss):
        sig = _build_signal("SELL", sell_zones, bear_evt, bear_boost)
        if sig:
            signals.append(sig)

    return signals



def apply_mss_patch(strategy_registry, Signal, resolve_instrument, get_session_for_time):
    """Apply the MSS-enhanced generate_signals to smc_ict strategy.
    Called from scanner.py after strategy registration.
    """
    # Make Signal and helpers available in closure
    _mss_enhanced_generate_signals.__globals__["Signal"] = Signal
    _mss_enhanced_generate_signals.__globals__["resolve_instrument"] = resolve_instrument
    _mss_enhanced_generate_signals.__globals__["get_session_for_time"] = get_session_for_time

    smc = strategy_registry.get("smc_ict")
    if smc:
        smc.generate_signals = _types.MethodType(_mss_enhanced_generate_signals, smc)
        logger.info("v2.7.0: MSS-enhanced generate_signals applied to smc_ict")
    else:
        logger.warning("smc_ict strategy not found in registry")
