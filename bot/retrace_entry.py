
# bot/retrace_entry.py — v2.11.0
# Retrace-Entry Strategy: Wait for impulse retrace, enter on confirmation
# v2.11.0: Proximity-filtered swing SL + TP cap (backtest validated)
from __future__ import annotations

import logging
import numpy as np
import pandas as pd
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timezone

logger = logging.getLogger("retrace_entry")

# ============================================================
# CONFIGURATION
# ============================================================
DEFAULT_CONFIG = {
    "impulse_len": 3,           # Min consecutive candles for impulse
    "min_retrace_pct": 50.0,    # Min retrace % toward origin to consider
    "max_retrace_wait": 15,     # Max candles to wait for retrace
    "engulf_body_ratio": 0.50,  # Min body/range ratio for entry candle
    "sl_buffer_pct": 0.20,      # SL buffer beyond origin (% of impulse range)
    "tp_extension": 0.50,       # TP extension beyond impulse (% of range)
    "min_rr": 1.5,              # Minimum risk:reward ratio (v2.11.0: raised from 1.0)
    "max_impulse_age": 20,      # Max candles to look back for origin
    "base_confluence": 8,       # Base confluence for retrace signals
    "max_signal_age": 20,       # Only return signals from last N candles (retrace needs ~15)
    "min_risk_atr": 0.3,        # Min risk as fraction of ATR (filters tiny SL)
    # v2.11.0: Swing-based SL (proximity-filtered)
    "swing_sl_enabled": True,   # Enable swing-based SL floor
    "swing_lookback": 5,        # Candles each side to define swing point
    "swing_buffer_pct": 0.0015, # Buffer beyond swing HH/LL (0.15%)
    "swing_max_dist_atr": 1.5,  # Only consider swings within 1.5×ATR of entry
    # v2.11.0: TP cap
    "tp_cap_rr": 4.0,           # Max R:R for TP (0 = disabled). Caps ambitious targets.
}


class RetraceEntryScanner:
    """Detects retrace-entry opportunities after impulsive price moves.

    Pattern:
        1. Impulse: 3+ consecutive candles in same direction
        2. Origin: last opposing candle before the impulse
        3. Retrace: price pulls back >= 50% toward origin
        4. Entry: strong engulfing candle in impulse direction
        5. SL: max(origin+buffer, nearest_swing+buffer) | TP: capped at 4R
    """

    def __init__(self, config: Optional[Dict] = None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        logger.info(f"RetraceEntryScanner initialized: "
                    f"impulse={self.config['impulse_len']}, "
                    f"min_retrace={self.config['min_retrace_pct']}%, "
                    f"min_rr={self.config['min_rr']}, "
                    f"swing_sl={self.config['swing_sl_enabled']}, "
                    f"tp_cap={self.config['tp_cap_rr']}R")

    def scan(self, df: pd.DataFrame, instrument: str = "",
             timeframe: str = "") -> List[Dict]:
        """Scan dataframe for retrace-entry signals.

        Args:
            df: OHLC DataFrame with columns: open, high, low, close
                Optionally: atr, rsi
            instrument: instrument name (for logging)
            timeframe: timeframe string (for logging)

        Returns:
            List of signal dicts compatible with scanner.py sig_data format
        """
        cfg = self.config
        n = len(df)
        impulse_len = cfg["impulse_len"]
        min_retrace = cfg["min_retrace_pct"]
        max_wait = cfg["max_retrace_wait"]
        engulf_ratio = cfg["engulf_body_ratio"]
        sl_buffer = cfg["sl_buffer_pct"]
        tp_ext = cfg["tp_extension"]
        min_rr = cfg["min_rr"]
        max_age = cfg["max_impulse_age"]
        base_conf = cfg["base_confluence"]
        max_signal_age = cfg.get("max_signal_age", 5)
        min_risk_atr = cfg.get("min_risk_atr", 0.3)
        swing_sl_enabled = cfg.get("swing_sl_enabled", True)
        tp_cap_rr = cfg.get("tp_cap_rr", 4.0)

        if n < impulse_len + max_wait + 5:
            return []

        has_atr = "atr" in df.columns
        has_rsi = "rsi" in df.columns
        signals = []
        i = impulse_len + 2

        while i < n - 2:
            # ── STEP 1: Detect impulse ──
            bearish_impulse = self._is_impulse(df, i, impulse_len, "bearish")
            bullish_impulse = self._is_impulse(df, i, impulse_len, "bullish")

            if not bearish_impulse and not bullish_impulse:
                i += 1
                continue

            direction = "SELL" if bearish_impulse else "BUY"

            # ── STEP 2: Find origin candle ──
            origin = self._find_origin(df, i, impulse_len, direction, max_age)
            if origin is None:
                i += 1
                continue

            origin_idx, origin_price, impulse_extreme, impulse_range = origin

            if impulse_range <= 0:
                i += 1
                continue

            # ── STEP 3: Wait for retrace + confirmation ──
            entry = self._find_retrace_entry(
                df, i, direction, origin_price, impulse_extreme,
                impulse_range, min_retrace, max_wait, engulf_ratio
            )

            if entry is None:
                i += 1
                continue

            entry_idx, entry_price, retrace_pct = entry

            # ── STEP 4: Calculate SL & TP (v2.11.0: swing SL + TP cap) ──
            # 4a. Origin-based SL (original approach)
            if direction == "SELL":
                origin_sl = origin_price + impulse_range * sl_buffer
            else:
                origin_sl = origin_price - impulse_range * sl_buffer

            # 4b. Swing-based SL floor (v2.11.0)
            swing_sl = None
            atr_at_entry = None
            if has_atr and not pd.isna(df["atr"].iloc[entry_idx]):
                atr_at_entry = float(df["atr"].iloc[entry_idx])

            if swing_sl_enabled and atr_at_entry and atr_at_entry > 0:
                swing_sl = self._find_nearest_swing_sl(
                    df, entry_idx, entry_price, direction, atr_at_entry, cfg
                )

            # 4c. Final SL = max protection level
            if direction == "SELL":
                sl = origin_sl
                if swing_sl is not None and swing_sl > sl:
                    sl = swing_sl
                    logger.debug(f"Swing SL widened: {origin_sl:.5f} → {sl:.5f} ({instrument})")
                tp = impulse_extreme - impulse_range * tp_ext
                risk = sl - entry_price
                reward = entry_price - tp
            else:
                sl = origin_sl
                if swing_sl is not None and swing_sl < sl:
                    sl = swing_sl
                    logger.debug(f"Swing SL widened: {origin_sl:.5f} → {sl:.5f} ({instrument})")
                tp = impulse_extreme + impulse_range * tp_ext
                risk = entry_price - sl
                reward = tp - entry_price

            if risk <= 0 or reward <= 0:
                i += 1
                continue

            # 4d. TP cap (v2.11.0): limit ambitious targets
            rr_ratio = reward / risk
            if tp_cap_rr > 0 and rr_ratio > tp_cap_rr:
                if direction == "SELL":
                    tp = entry_price - risk * tp_cap_rr
                else:
                    tp = entry_price + risk * tp_cap_rr
                reward = risk * tp_cap_rr
                rr_ratio = tp_cap_rr
                logger.debug(f"TP capped at {tp_cap_rr}R: tp={tp:.5f} ({instrument})")

            # 4e. Min R:R gate (after swing adjustment)
            if rr_ratio < min_rr:
                i += 1
                continue

            # Filter: minimum risk must be meaningful (avoid tiny SL)
            if atr_at_entry and atr_at_entry > 0 and risk < atr_at_entry * min_risk_atr:
                i += 1
                continue

            # ── STEP 5: Build signal ──
            atr_val = atr_at_entry
            rsi_val = None
            if has_rsi and not pd.isna(df["rsi"].iloc[entry_idx]):
                rsi_val = float(df["rsi"].iloc[entry_idx])

            # Confluence scoring
            confluence = base_conf
            # Bonus: high retrace means price really tested the zone
            if retrace_pct >= 80:
                confluence += 1
            # Bonus: R:R >= 2.0
            if rr_ratio >= 2.0:
                confluence += 1
            # Bonus: RSI confirms direction
            if rsi_val is not None:
                if direction == "SELL" and rsi_val > 55:
                    confluence += 1  # selling into overbought retrace
                elif direction == "BUY" and rsi_val < 45:
                    confluence += 1  # buying into oversold retrace
            # Bonus: ATR confirms volatility is reasonable
            if atr_val and impulse_range < atr_val * 3:
                confluence += 1  # not a crazy spike
            # v2.11.0: Bonus for swing-protected SL
            if swing_sl is not None:
                confluence += 1  # structural SL = higher confidence

            # Determine SL mode used for metadata
            sl_mode = "origin"
            if swing_sl is not None:
                if direction == "SELL" and swing_sl >= origin_sl:
                    sl_mode = "swing"
                elif direction == "BUY" and swing_sl <= origin_sl:
                    sl_mode = "swing"

            signal = {
                "strategy": "retrace_entry",
                "direction": direction,
                "entry": round(float(entry_price), 5),
                "entry_price": round(float(entry_price), 5),
                "sl": round(float(sl), 5),
                "tp": round(float(tp), 5),
                "rr_ratio": round(rr_ratio, 2),
                "confluence": confluence,
                "retrace_pct": round(retrace_pct, 1),
                "impulse_range": round(float(impulse_range), 5),
                "origin_price": round(float(origin_price), 5),
                "impulse_extreme": round(float(impulse_extreme), 5),
                "candle_index": entry_idx,
                "instrument": instrument,
                "timeframe": timeframe,
                "rsi": rsi_val,
                "atr": atr_val,
                "zone_types": f"retrace+{direction.lower()}",
                "mss_type": "retrace_entry",
                "sl_mode": sl_mode,  # v2.11.0: track which SL was used
            }

            signals.append(signal)

            # Skip past entry to avoid overlapping signals
            i = entry_idx + 2
            continue

        # Only return signals from the last max_signal_age candles
        pre_filter = len(signals)
        if max_signal_age > 0:
            signals = [s for s in signals if s["candle_index"] >= n - max_signal_age]
        if signals:
            for s in signals:
                logger.info(
                    f"Retrace {s['direction']}: {instrument} {timeframe} "
                    f"entry={s['entry']:.5f} R:R={s['rr_ratio']:.1f} "
                    f"retrace={s['retrace_pct']:.0f}% conf={s['confluence']} "
                    f"sl_mode={s['sl_mode']} "
                    f"(filtered {pre_filter}->{len(signals)})"
                )
        return signals

    # ================================================================
    # INTERNAL METHODS
    # ================================================================

    def _is_impulse(self, df: pd.DataFrame, i: int,
                    impulse_len: int, direction: str) -> bool:
        """Check if there are impulse_len consecutive candles in direction."""
        if direction == "bearish":
            return all(
                df["close"].iloc[i - j] < df["open"].iloc[i - j]
                for j in range(impulse_len)
            )
        else:
            return all(
                df["close"].iloc[i - j] > df["open"].iloc[i - j]
                for j in range(impulse_len)
            )

    def _find_origin(
        self, df: pd.DataFrame, i: int, impulse_len: int,
        direction: str, max_age: int
    ) -> Optional[Tuple[int, float, float, float]]:
        """Find the origin candle (last opposing candle before impulse).

        Returns:
            (origin_idx, origin_price, impulse_extreme, impulse_range)
            or None if not found.
        """
        if direction == "SELL":
            # After bearish impulse: origin = last green candle
            origin_idx = i - impulse_len
            while origin_idx > max(0, i - max_age):
                if df["close"].iloc[origin_idx] > df["open"].iloc[origin_idx]:
                    break
                origin_idx -= 1
            if df["close"].iloc[origin_idx] <= df["open"].iloc[origin_idx]:
                return None

            origin_price = float(df["high"].iloc[origin_idx])
            impulse_extreme = float(
                min(df["low"].iloc[i - j] for j in range(impulse_len))
            )
            impulse_range = origin_price - impulse_extreme
        else:
            # After bullish impulse: origin = last red candle
            origin_idx = i - impulse_len
            while origin_idx > max(0, i - max_age):
                if df["close"].iloc[origin_idx] < df["open"].iloc[origin_idx]:
                    break
                origin_idx -= 1
            if df["close"].iloc[origin_idx] >= df["open"].iloc[origin_idx]:
                return None

            origin_price = float(df["low"].iloc[origin_idx])
            impulse_extreme = float(
                max(df["high"].iloc[i - j] for j in range(impulse_len))
            )
            impulse_range = impulse_extreme - origin_price

        return (origin_idx, origin_price, impulse_extreme, impulse_range)

    def _find_nearest_swing_sl(
        self, df: pd.DataFrame, entry_idx: int, entry_price: float,
        direction: str, atr_val: float, cfg: Dict
    ) -> Optional[float]:
        """Find nearest swing-based SL level (proximity-filtered).

        v2.11.0: Only considers swings within swing_max_dist_atr × ATR
        of the entry price. Returns the nearest qualifying swing + buffer.

        For SELL: nearest swing HIGH above entry (structural invalidation)
        For BUY: nearest swing LOW below entry (structural invalidation)
        """
        swing_lookback = cfg.get("swing_lookback", 5)
        swing_buffer_pct = cfg.get("swing_buffer_pct", 0.0015)
        max_dist_atr = cfg.get("swing_max_dist_atr", 1.5)
        max_dist = atr_val * max_dist_atr

        # Scan candles before entry for swing points
        start_idx = max(swing_lookback, entry_idx - 50)
        end_idx = entry_idx  # Don't include entry candle itself

        if end_idx - start_idx < swing_lookback * 2:
            return None

        if direction == "SELL":
            # Find swing highs above entry, within max_dist
            candidates = []
            for idx in range(start_idx + swing_lookback, end_idx - swing_lookback):
                window = df.iloc[idx - swing_lookback:idx + swing_lookback + 1]
                if float(df["high"].iloc[idx]) == float(window["high"].max()):
                    price = float(df["high"].iloc[idx])
                    dist = price - entry_price
                    if dist > 0 and dist < max_dist:
                        candidates.append(price)

            if candidates:
                # Use nearest (smallest distance above entry)
                nearest = min(candidates)
                return nearest * (1 + swing_buffer_pct)
            else:
                # Fallback: highest high in last 10 bars before entry + buffer
                recent_high = float(df["high"].iloc[max(0, entry_idx - 10):entry_idx].max())
                dist = recent_high - entry_price
                if dist > 0 and dist < max_dist:
                    return recent_high * (1 + swing_buffer_pct)
                return None
        else:
            # Find swing lows below entry, within max_dist
            candidates = []
            for idx in range(start_idx + swing_lookback, end_idx - swing_lookback):
                window = df.iloc[idx - swing_lookback:idx + swing_lookback + 1]
                if float(df["low"].iloc[idx]) == float(window["low"].min()):
                    price = float(df["low"].iloc[idx])
                    dist = entry_price - price
                    if dist > 0 and dist < max_dist:
                        candidates.append(price)

            if candidates:
                # Use nearest (smallest distance below entry)
                nearest = max(candidates)
                return nearest * (1 - swing_buffer_pct)
            else:
                # Fallback: lowest low in last 10 bars before entry - buffer
                recent_low = float(df["low"].iloc[max(0, entry_idx - 10):entry_idx].min())
                dist = entry_price - recent_low
                if dist > 0 and dist < max_dist:
                    return recent_low * (1 - swing_buffer_pct)
                return None

    def _find_retrace_entry(
        self, df: pd.DataFrame, impulse_end: int, direction: str,
        origin_price: float, impulse_extreme: float,
        impulse_range: float, min_retrace: float,
        max_wait: int, engulf_ratio: float
    ) -> Optional[Tuple[int, float, float]]:
        """Wait for retrace toward origin, then find confirmation entry.

        Returns:
            (entry_idx, entry_price, retrace_pct) or None
        """
        n = len(df)
        retrace_reached = False

        for k in range(impulse_end + 1, min(impulse_end + max_wait + 1, n - 1)):
            # Calculate how far price has retraced
            if direction == "SELL":
                retrace_high = max(
                    float(df["high"].iloc[j])
                    for j in range(impulse_end + 1, k + 1)
                )
                retrace_pct = (
                    (retrace_high - impulse_extreme) / impulse_range * 100
                    if impulse_range > 0 else 0
                )
            else:
                retrace_low = min(
                    float(df["low"].iloc[j])
                    for j in range(impulse_end + 1, k + 1)
                )
                retrace_pct = (
                    (impulse_extreme - retrace_low) / impulse_range * 100
                    if impulse_range > 0 else 0
                )

            if retrace_pct < min_retrace:
                continue

            retrace_reached = True

            # Look for confirmation engulfing candle
            c_close = float(df["close"].iloc[k])
            c_open = float(df["open"].iloc[k])
            c_high = float(df["high"].iloc[k])
            c_low = float(df["low"].iloc[k])
            c_body = abs(c_close - c_open)
            c_range = c_high - c_low

            if c_range <= 0:
                continue

            body_ratio = c_body / c_range

            if direction == "SELL":
                # Need bearish engulfing after the retrace up
                is_confirm = (c_close < c_open and body_ratio >= engulf_ratio)
                if is_confirm:
                    prev_low = float(df["low"].iloc[k - 1])
                    if c_close <= prev_low or c_body > c_range * 0.6:
                        return (k, c_close, retrace_pct)
            else:
                # Need bullish engulfing after the retrace down
                is_confirm = (c_close > c_open and body_ratio >= engulf_ratio)
                if is_confirm:
                    prev_high = float(df["high"].iloc[k - 1])
                    if c_close >= prev_high or c_body > c_range * 0.6:
                        return (k, c_close, retrace_pct)

        return None


# ============================================================
# MODULE-LEVEL SCANNER INSTANCE (for import by scanner.py)
# ============================================================
retrace_scanner = None

def init_retrace_scanner(config: Optional[Dict] = None) -> RetraceEntryScanner:
    """Initialize the global retrace scanner."""
    global retrace_scanner
    retrace_scanner = RetraceEntryScanner(config)
    return retrace_scanner

def scan_retrace_entry(
    df: pd.DataFrame, instrument: str = "", timeframe: str = ""
) -> List[Dict]:
    """Convenience function for scanner.py integration."""
    global retrace_scanner
    if retrace_scanner is None:
        init_retrace_scanner()
    return retrace_scanner.scan(df, instrument, timeframe)
