
# bot/retrace_entry.py — v2.9.0
# Retrace-Entry Strategy: Wait for impulse retrace, enter on confirmation
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
    "min_rr": 1.0,              # Minimum risk:reward ratio
    "max_impulse_age": 20,      # Max candles to look back for origin
    "base_confluence": 8,       # Base confluence for retrace signals
    "max_signal_age": 20,       # Only return signals from last N candles (retrace needs ~15)
    "min_risk_atr": 0.3,        # Min risk as fraction of ATR (filters tiny SL)
}


class RetraceEntryScanner:
    """Detects retrace-entry opportunities after impulsive price moves.

    Pattern:
        1. Impulse: 3+ consecutive candles in same direction
        2. Origin: last opposing candle before the impulse
        3. Retrace: price pulls back >= 50% toward origin
        4. Entry: strong engulfing candle in impulse direction
        5. SL: beyond origin + buffer | TP: impulse extreme + extension
    """

    def __init__(self, config: Optional[Dict] = None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        logger.info(f"RetraceEntryScanner initialized: "
                    f"impulse={self.config['impulse_len']}, "
                    f"min_retrace={self.config['min_retrace_pct']}%, "
                    f"min_rr={self.config['min_rr']}")

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

            # ── STEP 4: Calculate SL & TP ──
            if direction == "SELL":
                sl = origin_price + impulse_range * sl_buffer
                tp = impulse_extreme - impulse_range * tp_ext
                risk = sl - entry_price
                reward = entry_price - tp
            else:
                sl = origin_price - impulse_range * sl_buffer
                tp = impulse_extreme + impulse_range * tp_ext
                risk = entry_price - sl
                reward = tp - entry_price

            if risk <= 0 or reward <= 0:
                i += 1
                continue

            rr_ratio = reward / risk
            if rr_ratio < min_rr:
                i += 1
                continue

            # Filter: minimum risk must be meaningful (avoid 0.66 pt SL)
            if has_atr and not pd.isna(df["atr"].iloc[entry_idx]):
                atr_at_entry = float(df["atr"].iloc[entry_idx])
                if atr_at_entry > 0 and risk < atr_at_entry * min_risk_atr:
                    i += 1
                    continue

            # ── STEP 5: Build signal ──
            # ATR-based position sizing hint
            atr_val = None
            if has_atr and not pd.isna(df["atr"].iloc[entry_idx]):
                atr_val = float(df["atr"].iloc[entry_idx])

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
