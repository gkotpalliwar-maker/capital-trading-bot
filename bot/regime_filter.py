"""
Capital.com Trading Bot v2.2 - Market Regime Filter
Detects market regime (trending/ranging, high/low volatility) and
controls which strategy setups are allowed in each regime.
"""
import pandas as pd
import numpy as np
import logging
from typing import Dict, Tuple
from config import (ADX_TREND_THRESHOLD, ADX_RANGE_THRESHOLD,
                    VOL_HIGH_MULTIPLIER, VOL_LOW_MULTIPLIER,
                    REGIME_RULES)

logger = logging.getLogger(__name__)


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute Average Directional Index (ADX) for trend strength."""
    high = df["high"]
    low = df["low"]
    close = df["close"]

    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1/period, min_periods=period).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1/period, min_periods=period).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1/period, min_periods=period).mean() / atr)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1/period, min_periods=period).mean()
    return adx


def compute_bb_width(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Bollinger Band width as % of mid band (squeeze indicator)."""
    mid = df["close"].rolling(period).mean()
    std = df["close"].rolling(period).std()
    width = (2 * std / mid) * 100
    return width


def compute_volatility_ratio(df: pd.DataFrame, fast: int = 5, slow: int = 50) -> pd.Series:
    """Ratio of recent volatility to longer-term volatility."""
    if "atr" not in df.columns:
        return pd.Series(1.0, index=df.index)
    fast_vol = df["atr"].rolling(fast).mean()
    slow_vol = df["atr"].rolling(slow).mean()
    return (fast_vol / slow_vol.replace(0, np.nan)).fillna(1.0)


def detect_regime(df: pd.DataFrame) -> Dict:
    """
    Detect the current market regime from price data.
    Returns dict with trend, volatility, adx, bb_width, vol_ratio, label.
    """
    if len(df) < 60:
        return {"trend": "unknown", "volatility": "unknown",
                "adx": 0, "bb_width": 0, "vol_ratio": 1.0,
                "label": "insufficient_data"}

    adx = compute_adx(df)
    bb_width = compute_bb_width(df)
    vol_ratio = compute_volatility_ratio(df)

    current_adx = float(adx.iloc[-1]) if not pd.isna(adx.iloc[-1]) else 0
    current_bb = float(bb_width.iloc[-1]) if not pd.isna(bb_width.iloc[-1]) else 0
    current_vr = float(vol_ratio.iloc[-1]) if not pd.isna(vol_ratio.iloc[-1]) else 1.0

    if current_adx >= ADX_TREND_THRESHOLD:
        trend = "trending"
    elif current_adx <= ADX_RANGE_THRESHOLD:
        trend = "ranging"
    else:
        trend = "weak_trend"

    if current_vr >= VOL_HIGH_MULTIPLIER:
        volatility = "high"
    elif current_vr <= VOL_LOW_MULTIPLIER:
        volatility = "low"
    else:
        volatility = "normal"

    label = f"{trend}+{volatility}_vol"

    return {
        "trend": trend, "volatility": volatility,
        "adx": round(current_adx, 1), "bb_width": round(current_bb, 3),
        "vol_ratio": round(current_vr, 2), "label": label,
    }


def is_setup_allowed(regime: Dict, setup_type: str, direction: str) -> Tuple[bool, str]:
    """
    Check if a specific setup type is allowed in the current regime.
    BOS = continuation (prefers trend), MSS = reversal (prefers range/displacement).
    """
    trend = regime.get("trend", "unknown")
    volatility = regime.get("volatility", "unknown")

    if trend == "unknown":
        return True, "Regime unknown, allowing"

    setup_lower = setup_type.lower()
    is_bos = "bos" in setup_lower
    is_mss = "mss" in setup_lower

    rules = REGIME_RULES.get(trend, {})
    vol_rules = rules.get(volatility, rules.get("normal", {}))

    if is_bos and not vol_rules.get("allow_bos", True):
        return False, f"BOS blocked in {trend}+{volatility}_vol (ADX={regime['adx']})"

    if is_mss and not vol_rules.get("allow_mss", True):
        return False, f"MSS blocked in {trend}+{volatility}_vol (ADX={regime['adx']})"

    if volatility == "low" and regime.get("bb_width", 99) < 0.5:
        return False, f"BB squeeze too tight ({regime['bb_width']:.3f}%), no entry"

    return True, "OK"
