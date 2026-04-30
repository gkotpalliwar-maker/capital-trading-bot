"""Classic Strategies"""
from __future__ import annotations
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from typing import List, Dict
from strategies.base import BaseStrategy, Signal
from config import resolve_instrument, get_current_session, TradingSession
from data_fetcher import add_technical_indicators

def get_session_for_time(dt):
    return get_current_session(dt.hour if hasattr(dt,"hour") else datetime.now(timezone.utc).hour)

def _safe_bool(series):
    """Safely convert a series to boolean, filling NaN with False."""
    return series.fillna(False).astype(bool)


class ConsolidationBreakoutStrategy(BaseStrategy):
    """
    Detects consolidation zones and breakout patterns:
    1. Identifies consolidation (narrow range) zones
    2. Detects break of last LL (bullish) or HH (bearish)
    3. Confirms with engulfing candle pattern
    4. Confirms with SMA(9) crossing SMA(21)
    5. Generates signal with targets from swing structure
    """
    
    def __init__(self, consolidation_periods: int = 20, range_threshold: float = 0.3,
                 short_ma: int = 9, long_ma: int = 21, lookback_hours: float = 8.0):
        super().__init__(
            name="consolidation_breakout",
            description="Breakout from consolidation with engulfing + MA cross confirmation"
        )
        self.consolidation_periods = consolidation_periods
        self.range_threshold = range_threshold
        self.short_ma = short_ma
        self.long_ma = long_ma
        self.lookback_hours = lookback_hours
    
    def detect_pattern(self, df: pd.DataFrame, **kwargs) -> pd.DataFrame:
        """Add pattern detection columns to DataFrame."""
        df = add_technical_indicators(df, self.short_ma, self.long_ma)
        
        # Ensure boolean columns are clean
        df["is_bullish"] = _safe_bool(df["is_bullish"])
        df["ma_trend_bullish"] = _safe_bool(df["sma_short"] > df["sma_long"])
        
        # --- Detect Consolidation Zones ---
        df["rolling_range"] = df["high"].rolling(self.consolidation_periods).max() - \
                               df["low"].rolling(self.consolidation_periods).min()
        df["avg_range"] = df["atr"] * self.consolidation_periods * self.range_threshold
        df["in_consolidation"] = _safe_bool(df["rolling_range"] < df["avg_range"])
        
        # --- Detect Engulfing Patterns ---
        prev_bullish = _safe_bool(df["is_bullish"].shift(1))
        df["bullish_engulfing"] = (
            (~prev_bullish) &
            (df["is_bullish"]) &
            (df["close"] > df["open"].shift(1)) &
            (df["open"] < df["close"].shift(1))
        )
        df["bearish_engulfing"] = (
            (prev_bullish) &
            (~df["is_bullish"]) &
            (df["close"] < df["open"].shift(1)) &
            (df["open"] > df["close"].shift(1))
        )
        
        # --- MA Crossover ---
        sma_short_prev = df["sma_short"].shift(1)
        sma_long_prev = df["sma_long"].shift(1)
        df["ma_bullish_cross"] = _safe_bool(
            (df["sma_short"] > df["sma_long"]) &
            (sma_short_prev <= sma_long_prev)
        )
        df["ma_bearish_cross"] = _safe_bool(
            (df["sma_short"] < df["sma_long"]) &
            (sma_short_prev >= sma_long_prev)
        )
        
        # --- Swing Point Structure ---
        swing_points = self._find_swing_points(df, lookback=5)
        df["swing_type"] = None
        for sp in swing_points:
            if sp["type"]:
                df.iloc[sp["index"], df.columns.get_loc("swing_type")] = sp["type"]
        
        # --- Breakout Detection ---
        df["recent_ll"] = df["low"].rolling(self.consolidation_periods).min()
        df["recent_hh"] = df["high"].rolling(self.consolidation_periods).max()
        
        prev_consol = _safe_bool(df["in_consolidation"].shift(1))
        prev_consol2 = _safe_bool(df["in_consolidation"].shift(2))
        
        df["bullish_breakout"] = _safe_bool(
            (df["close"] > df["recent_hh"].shift(1)) &
            (prev_consol | prev_consol2)
        )
        df["bearish_breakout"] = _safe_bool(
            (df["close"] < df["recent_ll"].shift(1)) &
            (prev_consol | prev_consol2)
        )
        
        # --- Combined Pattern Score ---
        df["bull_score"] = (
            df["bullish_breakout"].astype(int) * 3 +
            df["bullish_engulfing"].astype(int) * 2 +
            df["ma_bullish_cross"].astype(int) * 2 +
            df["ma_trend_bullish"].astype(int) * 1 +
            _safe_bool(df["rsi"] > 50).astype(int) * 1 +
            _safe_bool(df["macd_hist"] > 0).astype(int) * 1
        )
        df["bear_score"] = (
            df["bearish_breakout"].astype(int) * 3 +
            df["bearish_engulfing"].astype(int) * 2 +
            df["ma_bearish_cross"].astype(int) * 2 +
            (~df["ma_trend_bullish"]).astype(int) * 1 +
            _safe_bool(df["rsi"] < 50).astype(int) * 1 +
            _safe_bool(df["macd_hist"] < 0).astype(int) * 1
        )
        
        df["pattern_detected"] = (df["bull_score"] >= 5) | (df["bear_score"] >= 5)
        df["pattern_type"] = "none"
        df.loc[df["bull_score"] >= 5, "pattern_type"] = "bullish_breakout"
        df.loc[df["bear_score"] >= 5, "pattern_type"] = "bearish_breakout"
        
        return df
    
    def generate_signals(self, df: pd.DataFrame, instrument: str,
                         timeframe: str, **kwargs) -> List[Signal]:
        """Generate signals from the latest data."""
        df = self.detect_pattern(df)
        signals = []
        
        tf_minutes = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}
        minutes_per_candle = tf_minutes.get(timeframe, 5)
        lookback_candles = int(self.lookback_hours * 60 / minutes_per_candle)
        recent = df.tail(min(lookback_candles, len(df)))
        
        patterns = recent[recent["pattern_detected"]]
        if patterns.empty:
            return signals
        
        latest = patterns.iloc[-1]
        latest_idx = patterns.index[-1]
        
        sessions = get_session_for_time(latest_idx if isinstance(latest_idx, datetime)
                                         else datetime.now(timezone.utc))
        session_str = ", ".join([s.value for s in sessions])
        
        entry = latest["close"]
        direction = "BUY" if latest["pattern_type"] == "bullish_breakout" else "SELL"
        sl, tp = self.calculate_targets(entry, direction, df)
        
        max_score = 10
        score = latest["bull_score"] if direction == "BUY" else latest["bear_score"]
        confidence = min(score / max_score, 0.95)
        
        signal = Signal(
            instrument=resolve_instrument(instrument),
            direction=direction, entry_price=entry,
            stop_loss=sl, take_profit=tp, confidence=confidence,
            strategy_name=self.name, timeframe=timeframe, session=session_str,
            metadata={
                "bull_score": float(latest["bull_score"]),
                "bear_score": float(latest["bear_score"]),
                "rsi": float(latest["rsi"]) if not pd.isna(latest["rsi"]) else None,
                "macd_hist": float(latest["macd_hist"]) if not pd.isna(latest["macd_hist"]) else None,
                "engulfing": bool(latest.get("bullish_engulfing", False) or latest.get("bearish_engulfing", False)),
                "ma_cross": bool(latest.get("ma_bullish_cross", False) or latest.get("ma_bearish_cross", False)),
            }
        )
        signals.append(signal)
        return signals
    
    def get_zones(self, df: pd.DataFrame) -> List[Dict]:
        """Extract consolidation zones for visualization."""
        df = self.detect_pattern(df) if "in_consolidation" not in df.columns else df
        zones = []
        in_zone = False
        start_idx = 0
        
        for i in range(len(df)):
            if df["in_consolidation"].iloc[i] and not in_zone:
                in_zone = True
                start_idx = i
            elif not df["in_consolidation"].iloc[i] and in_zone:
                in_zone = False
                zone_data = df.iloc[start_idx:i]
                zones.append({
                    "start_idx": start_idx, "end_idx": i - 1,
                    "start_time": df.index[start_idx], "end_time": df.index[i - 1],
                    "top": zone_data["high"].max(), "bottom": zone_data["low"].min(),
                    "type": "consolidation", "color": "#FFD700", "alpha": 0.15,
                })
        if in_zone:
            zone_data = df.iloc[start_idx:]
            zones.append({
                "start_idx": start_idx, "end_idx": len(df) - 1,
                "start_time": df.index[start_idx], "end_time": df.index[-1],
                "top": zone_data["high"].max(), "bottom": zone_data["low"].min(),
                "type": "consolidation_active", "color": "#00CED1", "alpha": 0.2,
            })
        return zones


# ============================================================
# STRATEGY 2: SUPPLY / DEMAND ZONES
# ============================================================

class SupplyDemandStrategy(BaseStrategy):
    """Identifies supply/demand zones from strong impulsive moves."""
    
    def __init__(self, impulse_threshold: float = 1.5, zone_lookback: int = 50):
        super().__init__(name="supply_demand", description="Supply/Demand zone trading")
        self.impulse_threshold = impulse_threshold
        self.zone_lookback = zone_lookback
    
    def detect_pattern(self, df: pd.DataFrame, **kwargs) -> pd.DataFrame:
        df = add_technical_indicators(df)
        df["is_bullish"] = _safe_bool(df["is_bullish"])
        df["body_size"] = abs(df["close"] - df["open"])
        df["avg_body"] = df["body_size"].rolling(20).mean()
        
        # Impulse candles
        df["bullish_impulse"] = _safe_bool(
            (df["is_bullish"]) & (df["body_size"] > self.impulse_threshold * df["avg_body"])
        )
        df["bearish_impulse"] = _safe_bool(
            (~df["is_bullish"]) & (df["body_size"] > self.impulse_threshold * df["avg_body"])
        )
        
        # Demand zone: base before bullish impulse
        prev_bullish = _safe_bool(df["is_bullish"].shift(1))
        df["demand_zone"] = _safe_bool(df["bullish_impulse"] & (~prev_bullish))
        df["supply_zone"] = _safe_bool(df["bearish_impulse"] & (prev_bullish))
        
        df["pattern_detected"] = False
        df["pattern_type"] = "none"
        
        for i in range(self.zone_lookback, len(df)):
            recent = df.iloc[max(0, i-self.zone_lookback):i]
            demand_hits = recent[recent["demand_zone"]]
            for _, dz in demand_hits.iterrows():
                zone_low = min(dz["open"], dz["close"]) if not dz["is_bullish"] else dz["low"]
                zone_high = max(dz["open"], dz["close"]) if not dz["is_bullish"] else min(dz["open"], dz["close"])
                if df["low"].iloc[i] <= zone_high and df["close"].iloc[i] > zone_low:
                    if df["is_bullish"].iloc[i]:
                        df.iloc[i, df.columns.get_loc("pattern_detected")] = True
                        df.iloc[i, df.columns.get_loc("pattern_type")] = "demand_bounce"
                        break
        return df
    
    def generate_signals(self, df: pd.DataFrame, instrument: str,
                         timeframe: str, **kwargs) -> List[Signal]:
        df = self.detect_pattern(df)
        signals = []
        patterns = df[df["pattern_detected"]].tail(1)
        if patterns.empty:
            return signals
        latest = patterns.iloc[-1]
        entry = latest["close"]
        direction = "BUY" if latest["pattern_type"] == "demand_bounce" else "SELL"
        sl, tp = self.calculate_targets(entry, direction, df)
        sessions = get_current_session()
        signal = Signal(
            instrument=resolve_instrument(instrument), direction=direction,
            entry_price=entry, stop_loss=sl, take_profit=tp,
            confidence=0.60, strategy_name=self.name,
            timeframe=timeframe, session=", ".join([s.value for s in sessions])
        )
        signals.append(signal)
        return signals
    
    def get_zones(self, df: pd.DataFrame) -> List[Dict]:
        df = self.detect_pattern(df) if "demand_zone" not in df.columns else df
        zones = []
        for i in range(len(df)):
            if df["demand_zone"].iloc[i]:
                zones.append({
                    "start_idx": max(0, i-2), "end_idx": i,
                    "start_time": df.index[max(0, i-2)], "end_time": df.index[i],
                    "top": max(df["open"].iloc[i], df["close"].iloc[i]),
                    "bottom": df["low"].iloc[max(0, i-2):i+1].min(),
                    "type": "demand", "color": "#00FF00", "alpha": 0.15,
                })
            if df["supply_zone"].iloc[i]:
                zones.append({
                    "start_idx": max(0, i-2), "end_idx": i,
                    "start_time": df.index[max(0, i-2)], "end_time": df.index[i],
                    "top": df["high"].iloc[max(0, i-2):i+1].max(),
                    "bottom": min(df["open"].iloc[i], df["close"].iloc[i]),
                    "type": "supply", "color": "#FF4444", "alpha": 0.15,
                })
        return zones


# ============================================================
# STRATEGY 3: MOMENTUM (RSI + MACD + Volume)
# ============================================================

class MomentumStrategy(BaseStrategy):
    """Momentum-based strategy using RSI divergence, MACD cross, and volume."""
    
    def __init__(self):
        super().__init__(name="momentum", description="RSI/MACD momentum strategy")
    
    def detect_pattern(self, df: pd.DataFrame, **kwargs) -> pd.DataFrame:
        df = add_technical_indicators(df)
        df["is_bullish"] = _safe_bool(df["is_bullish"])
        df["ma_trend_bullish"] = _safe_bool(df["sma_short"] > df["sma_long"])
        
        df["vol_surge"] = _safe_bool(df["volume"] > df["volume"].rolling(20).mean() * 1.5)
        df["bull_momentum"] = _safe_bool(
            (df["rsi"] > 55) & (df["rsi"] < 80) &
            (df["macd_hist"] > 0) & (df["macd_hist"] > df["macd_hist"].shift(1)) &
            df["vol_surge"] & df["ma_trend_bullish"]
        )
        df["bear_momentum"] = _safe_bool(
            (df["rsi"] < 45) & (df["rsi"] > 20) &
            (df["macd_hist"] < 0) & (df["macd_hist"] < df["macd_hist"].shift(1)) &
            df["vol_surge"] & (~df["ma_trend_bullish"])
        )
        
        df["pattern_detected"] = df["bull_momentum"] | df["bear_momentum"]
        df["pattern_type"] = "none"
        df.loc[df["bull_momentum"], "pattern_type"] = "bull_momentum"
        df.loc[df["bear_momentum"], "pattern_type"] = "bear_momentum"
        return df
    
    def generate_signals(self, df: pd.DataFrame, instrument: str,
                         timeframe: str, **kwargs) -> List[Signal]:
        df = self.detect_pattern(df)
        signals = []
        patterns = df[df["pattern_detected"]].tail(1)
        if patterns.empty:
            return signals
        latest = patterns.iloc[-1]
        entry = latest["close"]
        direction = "BUY" if latest["pattern_type"] == "bull_momentum" else "SELL"
        sl, tp = self.calculate_targets(entry, direction, df)
        sessions = get_current_session()
        signals.append(Signal(
            instrument=resolve_instrument(instrument), direction=direction,
            entry_price=entry, stop_loss=sl, take_profit=tp,
            confidence=0.55, strategy_name=self.name,
            timeframe=timeframe, session=", ".join([s.value for s in sessions])
        ))
        return signals
    
    def get_zones(self, df: pd.DataFrame) -> List[Dict]:
        return []


# Re-register all strategies (clear and re-add)
