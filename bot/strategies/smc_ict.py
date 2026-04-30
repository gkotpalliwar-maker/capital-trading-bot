"""SMC/ICT Concepts"""
from __future__ import annotations
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from typing import List, Dict
from strategies.base import BaseStrategy, Signal
from config import resolve_instrument, get_current_session, get_session_for_time
from data_fetcher import add_technical_indicators
from strategies.mss_bos import detect_market_structure_shift, best_mss

def _safe_bool(series):
    return series.fillna(False).astype(bool)


def _safe_bool(series):
    return series.fillna(False).astype(bool)


def detect_order_blocks(df: pd.DataFrame, atr_multiplier: float = 2.0,
                        max_age: int = 50) -> List[Dict]:
    """
    Detect Order Blocks: the last opposing candle before an impulse move.
    
    Bullish OB: last bearish candle before a strong bullish impulse
    Bearish OB: last bullish candle before a strong bearish impulse
    
    Returns list of OB zones with metadata.
    """
    order_blocks = []
    if "atr" not in df.columns:
        return order_blocks
    
    for i in range(2, len(df)):
        atr_val = df["atr"].iloc[i]
        if pd.isna(atr_val) or atr_val == 0:
            continue
        
        body = abs(df["close"].iloc[i] - df["open"].iloc[i])
        is_impulse = body > atr_multiplier * atr_val
        
        if not is_impulse:
            continue
        
        impulse_bullish = df["close"].iloc[i] > df["open"].iloc[i]
        
        # Look back for last opposing candle
        for j in range(i - 1, max(i - 6, 0), -1):
            candle_bullish = df["close"].iloc[j] > df["open"].iloc[j]
            
            if impulse_bullish and not candle_bullish:
                # Bullish OB = last bearish candle before bullish impulse
                order_blocks.append({
                    "index": j,
                    "time": df.index[j],
                    "type": "bullish_ob",
                    "direction": "BUY",
                    "top": df["high"].iloc[j],
                    "bottom": df["low"].iloc[j],
                    "mid": (df["high"].iloc[j] + df["low"].iloc[j]) / 2,
                    "impulse_index": i,
                    "impulse_strength": body / atr_val,
                    "status": "active",  # active, mitigated, broken
                    "tested_count": 0,
                })
                break
            
            elif not impulse_bullish and candle_bullish:
                # Bearish OB = last bullish candle before bearish impulse
                order_blocks.append({
                    "index": j,
                    "time": df.index[j],
                    "type": "bearish_ob",
                    "direction": "SELL",
                    "top": df["high"].iloc[j],
                    "bottom": df["low"].iloc[j],
                    "mid": (df["high"].iloc[j] + df["low"].iloc[j]) / 2,
                    "impulse_index": i,
                    "impulse_strength": body / atr_val,
                    "status": "active",
                    "tested_count": 0,
                })
                break
    
    # Update OB status based on subsequent price action
    for ob in order_blocks:
        for k in range(ob["impulse_index"] + 1, len(df)):
            if ob["direction"] == "BUY":
                # Price returned to OB zone
                if df["low"].iloc[k] <= ob["top"]:
                    ob["tested_count"] += 1
                # Price broke below OB -> broken
                if df["close"].iloc[k] < ob["bottom"]:
                    ob["status"] = "broken"
                    break
            else:  # SELL OB
                if df["high"].iloc[k] >= ob["bottom"]:
                    ob["tested_count"] += 1
                if df["close"].iloc[k] > ob["top"]:
                    ob["status"] = "broken"
                    break
    
    # Filter: only return recent active OBs
    recent_obs = [
        ob for ob in order_blocks
        if ob["status"] == "active" and ob["index"] >= len(df) - max_age
    ]
    return recent_obs


def detect_fair_value_gaps(df: pd.DataFrame, min_gap_atr: float = 0.3,
                           max_age: int = 50) -> List[Dict]:
    """
    Detect Fair Value Gaps (FVGs): 3-candle imbalance patterns.
    
    Bullish FVG: candle[i-1].high < candle[i+1].low (gap up)
    Bearish FVG: candle[i-1].low > candle[i+1].high (gap down)
    
    Returns list of FVG zones.
    """
    fvgs = []
    
    for i in range(1, len(df) - 1):
        prev_high = df["high"].iloc[i - 1]
        prev_low = df["low"].iloc[i - 1]
        next_high = df["high"].iloc[i + 1]
        next_low = df["low"].iloc[i + 1]
        
        atr_val = df["atr"].iloc[i] if "atr" in df.columns and not pd.isna(df["atr"].iloc[i]) else None
        
        # Bullish FVG: gap between candle 1's high and candle 3's low
        if prev_high < next_low:
            gap_size = next_low - prev_high
            if atr_val and gap_size < min_gap_atr * atr_val:
                continue
            fvgs.append({
                "index": i,
                "time": df.index[i],
                "type": "bullish_fvg",
                "direction": "BUY",
                "top": next_low,       # upper edge of gap
                "bottom": prev_high,   # lower edge of gap
                "mid": (next_low + prev_high) / 2,
                "gap_size": gap_size,
                "status": "active",    # active, tested, filled, inverted
                "fill_pct": 0.0,
            })
        
        # Bearish FVG: gap between candle 1's low and candle 3's high
        if prev_low > next_high:
            gap_size = prev_low - next_high
            if atr_val and gap_size < min_gap_atr * atr_val:
                continue
            fvgs.append({
                "index": i,
                "time": df.index[i],
                "type": "bearish_fvg",
                "direction": "SELL",
                "top": prev_low,       # upper edge of gap
                "bottom": next_high,   # lower edge of gap
                "mid": (prev_low + next_high) / 2,
                "gap_size": gap_size,
                "status": "active",
                "fill_pct": 0.0,
            })
    
    # Track FVG lifecycle: tested, filled, or inverted
    for fvg in fvgs:
        for k in range(fvg["index"] + 2, len(df)):
            if fvg["direction"] == "BUY":
                # Price entered the FVG zone
                if df["low"].iloc[k] <= fvg["top"]:
                    fvg["status"] = "tested"
                    penetration = fvg["top"] - df["low"].iloc[k]
                    fvg["fill_pct"] = max(fvg["fill_pct"],
                                          min(penetration / fvg["gap_size"], 1.0))
                # Price closed below FVG -> filled/violated
                if df["close"].iloc[k] < fvg["bottom"]:
                    fvg["status"] = "filled"
                    break
            else:  # SELL FVG
                if df["high"].iloc[k] >= fvg["bottom"]:
                    fvg["status"] = "tested"
                    penetration = df["high"].iloc[k] - fvg["bottom"]
                    fvg["fill_pct"] = max(fvg["fill_pct"],
                                          min(penetration / fvg["gap_size"], 1.0))
                if df["close"].iloc[k] > fvg["top"]:
                    fvg["status"] = "filled"
                    break
    
    # Return active/tested FVGs within max_age
    recent_fvgs = [
        fvg for fvg in fvgs
        if fvg["status"] in ("active", "tested") and fvg["index"] >= len(df) - max_age
    ]
    return recent_fvgs


def detect_breaker_blocks(df: pd.DataFrame, order_blocks: List[Dict] = None,
                          max_age: int = 50) -> List[Dict]:
    """
    Detect Breaker Blocks: Order Blocks that were broken (violated),
    then flipped polarity.
    
    A bullish OB that gets broken becomes a bearish Breaker Block, and vice versa.
    """
    if order_blocks is None:
        # Get ALL order blocks including broken ones
        all_obs = []
        if "atr" not in df.columns:
            return all_obs
        for i in range(2, len(df)):
            atr_val = df["atr"].iloc[i]
            if pd.isna(atr_val) or atr_val == 0:
                continue
            body = abs(df["close"].iloc[i] - df["open"].iloc[i])
            is_impulse = body > 2.0 * atr_val
            if not is_impulse:
                continue
            impulse_bullish = df["close"].iloc[i] > df["open"].iloc[i]
            for j in range(i - 1, max(i - 6, 0), -1):
                candle_bullish = df["close"].iloc[j] > df["open"].iloc[j]
                if impulse_bullish and not candle_bullish:
                    all_obs.append({
                        "index": j, "type": "bullish_ob", "direction": "BUY",
                        "top": df["high"].iloc[j], "bottom": df["low"].iloc[j],
                        "impulse_index": i, "status": "active",
                    })
                    break
                elif not impulse_bullish and candle_bullish:
                    all_obs.append({
                        "index": j, "type": "bearish_ob", "direction": "SELL",
                        "top": df["high"].iloc[j], "bottom": df["low"].iloc[j],
                        "impulse_index": i, "status": "active",
                    })
                    break
        order_blocks = all_obs
    
    breaker_blocks = []
    for ob in order_blocks:
        broken = False
        break_index = None
        
        for k in range(ob.get("impulse_index", ob["index"]) + 1, len(df)):
            if ob["direction"] == "BUY":
                # Bullish OB broken = price closes below its bottom
                if df["close"].iloc[k] < ob["bottom"]:
                    broken = True
                    break_index = k
                    break
            else:
                # Bearish OB broken = price closes above its top
                if df["close"].iloc[k] > ob["top"]:
                    broken = True
                    break_index = k
                    break
        
        if broken and break_index is not None:
            # Flipped direction: bullish OB broken -> bearish breaker, etc.
            new_direction = "SELL" if ob["direction"] == "BUY" else "BUY"
            bb = {
                "index": ob["index"],
                "break_index": break_index,
                "time": df.index[ob["index"]],
                "type": f"{new_direction.lower()}_breaker",
                "direction": new_direction,
                "top": ob["top"],
                "bottom": ob["bottom"],
                "mid": (ob["top"] + ob["bottom"]) / 2,
                "original_ob_direction": ob["direction"],
                "status": "active",
            }
            
            # Check if price has returned to the breaker zone
            for m in range(break_index + 1, len(df)):
                if new_direction == "BUY" and df["low"].iloc[m] <= bb["top"]:
                    bb["status"] = "tested"
                    break
                elif new_direction == "SELL" and df["high"].iloc[m] >= bb["bottom"]:
                    bb["status"] = "tested"
                    break
            
            if bb["index"] >= len(df) - max_age:
                breaker_blocks.append(bb)
    
    return breaker_blocks


def detect_mitigation_blocks(df: pd.DataFrame, max_age: int = 50,
                              displacement_atr: float = 1.5) -> List[Dict]:
    """
    Detect Mitigation Blocks: swing points (HL in downtrend, LH in uptrend)
    followed by displacement, where price returns to mitigate.
    """
    mit_blocks = []
    if "atr" not in df.columns:
        return mit_blocks
    
    # Use swing point detection (same as BaseStrategy)
    swing_points = []
    lookback = 5
    for i in range(lookback, len(df) - lookback):
        if df["high"].iloc[i] == df["high"].iloc[i-lookback:i+lookback+1].max():
            swing_points.append({"index": i, "price": df["high"].iloc[i], "swing": "high"})
        if df["low"].iloc[i] == df["low"].iloc[i-lookback:i+lookback+1].min():
            swing_points.append({"index": i, "price": df["low"].iloc[i], "swing": "low"})
    
    swing_points.sort(key=lambda x: x["index"])
    
    # Classify swings
    last_high, last_low = None, None
    for sp in swing_points:
        if sp["swing"] == "high":
            sp["type"] = "HH" if (last_high is None or sp["price"] > last_high) else "LH"
            last_high = sp["price"]
        else:
            sp["type"] = "HL" if (last_low is None or sp["price"] > last_low) else "LL"
            last_low = sp["price"]
    
    # Find mitigation blocks:
    # In downtrend: LH (lower high) followed by displacement down = bearish MB
    # In uptrend: HL (higher low) followed by displacement up = bullish MB
    for sp in swing_points:
        idx = sp["index"]
        if idx >= len(df) - 2:
            continue
        
        # Check for displacement after the swing point
        has_displacement = False
        for d in range(idx + 1, min(idx + 8, len(df))):
            atr_val = df["atr"].iloc[d]
            if pd.isna(atr_val) or atr_val == 0:
                continue
            body = abs(df["close"].iloc[d] - df["open"].iloc[d])
            if body > displacement_atr * atr_val:
                disp_bullish = df["close"].iloc[d] > df["open"].iloc[d]
                has_displacement = True
                break
        
        if not has_displacement:
            continue
        
        if sp["type"] == "HL" and disp_bullish:
            # Bullish mitigation block: HL + bullish displacement
            mit_blocks.append({
                "index": idx,
                "time": df.index[idx],
                "type": "bullish_mitigation",
                "direction": "BUY",
                "top": df["high"].iloc[idx],
                "bottom": df["low"].iloc[idx],
                "mid": (df["high"].iloc[idx] + df["low"].iloc[idx]) / 2,
                "swing_type": sp["type"],
                "status": "active",
            })
        elif sp["type"] == "LH" and not disp_bullish:
            # Bearish mitigation block: LH + bearish displacement
            mit_blocks.append({
                "index": idx,
                "time": df.index[idx],
                "type": "bearish_mitigation",
                "direction": "SELL",
                "top": df["high"].iloc[idx],
                "bottom": df["low"].iloc[idx],
                "mid": (df["high"].iloc[idx] + df["low"].iloc[idx]) / 2,
                "swing_type": sp["type"],
                "status": "active",
            })
    
    # Track mitigation
    for mb in mit_blocks:
        for k in range(mb["index"] + 1, len(df)):
            if mb["direction"] == "BUY" and df["low"].iloc[k] <= mb["top"]:
                mb["status"] = "tested"
                break
            elif mb["direction"] == "SELL" and df["high"].iloc[k] >= mb["bottom"]:
                mb["status"] = "tested"
                break
    
    return [mb for mb in mit_blocks if mb["index"] >= len(df) - max_age]


def detect_inversion_fvgs(df: pd.DataFrame, fvgs: List[Dict] = None,
                          max_age: int = 50) -> List[Dict]:
    """
    Detect Inversion FVGs: FVGs that were fully violated and flipped role.
    
    A bullish FVG that price closes below becomes a bearish IFVG (resistance).
    A bearish FVG that price closes above becomes a bullish IFVG (support).
    """
    if fvgs is None:
        fvgs = detect_fair_value_gaps(df, min_gap_atr=0.2, max_age=100)
    
    ifvgs = []
    
    # We need to look at ALL fvgs including filled ones
    # Re-detect with broader criteria
    all_fvgs = []
    for i in range(1, len(df) - 1):
        prev_high = df["high"].iloc[i - 1]
        prev_low = df["low"].iloc[i - 1]
        next_high = df["high"].iloc[i + 1]
        next_low = df["low"].iloc[i + 1]
        
        if prev_high < next_low:
            all_fvgs.append({
                "index": i, "type": "bullish_fvg", "direction": "BUY",
                "top": next_low, "bottom": prev_high,
            })
        if prev_low > next_high:
            all_fvgs.append({
                "index": i, "type": "bearish_fvg", "direction": "SELL",
                "top": prev_low, "bottom": next_high,
            })
    
    for fvg in all_fvgs:
        violated = False
        violate_index = None
        
        for k in range(fvg["index"] + 2, len(df)):
            if fvg["direction"] == "BUY":
                # Bullish FVG violated = price closes below bottom
                if df["close"].iloc[k] < fvg["bottom"]:
                    violated = True
                    violate_index = k
                    break
            else:
                # Bearish FVG violated = price closes above top
                if df["close"].iloc[k] > fvg["top"]:
                    violated = True
                    violate_index = k
                    break
        
        if violated and violate_index is not None:
            # Flip: bullish FVG becomes bearish IFVG and vice versa
            new_direction = "SELL" if fvg["direction"] == "BUY" else "BUY"
            ifvg = {
                "index": fvg["index"],
                "violate_index": violate_index,
                "time": df.index[fvg["index"]],
                "type": f"{new_direction.lower()}_ifvg",
                "direction": new_direction,
                "top": fvg["top"],
                "bottom": fvg["bottom"],
                "mid": (fvg["top"] + fvg["bottom"]) / 2,
                "original_fvg_direction": fvg["direction"],
                "status": "active",
            }
            
            # Check if price has returned to test the IFVG
            for m in range(violate_index + 1, len(df)):
                if new_direction == "BUY" and df["low"].iloc[m] <= ifvg["top"]:
                    ifvg["status"] = "tested"
                    break
                elif new_direction == "SELL" and df["high"].iloc[m] >= ifvg["bottom"]:
                    ifvg["status"] = "tested"
                    break
            
            if ifvg["index"] >= len(df) - max_age:
                ifvgs.append(ifvg)
    
    return ifvgs


# ============================================================
# SMC/ICT STRATEGY CLASS
# ============================================================

class SMCICTStrategy(BaseStrategy):
    """
    Smart Money Concepts / ICT Strategy combining:
    1. Order Blocks (institutional accumulation/distribution)
    2. Fair Value Gaps (price imbalance magnets)
    3. Breaker Blocks (failed OB polarity flip)
    4. Mitigation Blocks (swing point + displacement)
    5. Inversion FVGs (violated FVG role reversal)
    
    Confluence scoring: multiple SMC concepts aligning = higher confidence.
    """
    
    def __init__(self, ob_atr_mult: float = 2.0, fvg_min_gap: float = 0.3,
                 max_zone_age: int = 50, lookback_hours: float = 8.0):
        super().__init__(
            name="smc_ict",
            description="SMC/ICT: Order Blocks + FVG + Breaker + Mitigation + IFVG"
        )
        self.ob_atr_mult = ob_atr_mult
        self.fvg_min_gap = fvg_min_gap
        self.max_zone_age = max_zone_age
        self.lookback_hours = lookback_hours
    
    def detect_pattern(self, df: pd.DataFrame, **kwargs) -> pd.DataFrame:
        """Run all 5 SMC/ICT detectors and annotate the DataFrame."""
        df = add_technical_indicators(df)
        
        # Detect all SMC zones
        obs = detect_order_blocks(df, self.ob_atr_mult, self.max_zone_age)
        fvgs = detect_fair_value_gaps(df, self.fvg_min_gap, self.max_zone_age)
        bbs = detect_breaker_blocks(df, max_age=self.max_zone_age)
        mbs = detect_mitigation_blocks(df, self.max_zone_age)
        ifvgs = detect_inversion_fvgs(df, max_age=self.max_zone_age)
        
        # Store zones in DataFrame attrs for downstream use
        df.attrs["smc_order_blocks"] = obs
        df.attrs["smc_fvgs"] = fvgs
        df.attrs["smc_breaker_blocks"] = bbs
        df.attrs["smc_mitigation_blocks"] = mbs
        df.attrs["smc_inversion_fvgs"] = ifvgs
        
        # Mark pattern detected at candles near active zones
        df["smc_pattern"] = False
        df["smc_type"] = "none"
        df["smc_confluence"] = 0
        
        # For each recent candle, check proximity to SMC zones
        all_zones = []
        for ob in obs:
            all_zones.append({**ob, "category": "order_block", "weight": 3})
        for fvg in fvgs:
            all_zones.append({**fvg, "category": "fvg", "weight": 3})
        for bb in bbs:
            all_zones.append({**bb, "category": "breaker_block", "weight": 2})
        for mb in mbs:
            all_zones.append({**mb, "category": "mitigation_block", "weight": 2})
        for ifvg in ifvgs:
            all_zones.append({**ifvg, "category": "ifvg", "weight": 1})
        
        # Check last N candles for zone proximity
        check_range = min(20, len(df))
        for i in range(len(df) - check_range, len(df)):
            price = df["close"].iloc[i]
            low_price = df["low"].iloc[i]
            high_price = df["high"].iloc[i]
            
            confluence = 0
            zone_types = []
            zone_direction = None
            
            for zone in all_zones:
                # Check if price is at or near the zone
                in_zone = low_price <= zone["top"] and high_price >= zone["bottom"]
                near_zone = abs(price - zone["mid"]) <= (zone["top"] - zone["bottom"]) * 1.5
                
                if in_zone or near_zone:
                    confluence += zone["weight"]
                    zone_types.append(zone["category"])
                    if zone_direction is None:
                        zone_direction = zone["direction"]
            
            if confluence >= 3:  # Minimum confluence threshold
                df.iloc[i, df.columns.get_loc("smc_pattern")] = True
                df.iloc[i, df.columns.get_loc("smc_confluence")] = confluence
                types_str = "+".join(sorted(set(zone_types)))
                df.iloc[i, df.columns.get_loc("smc_type")] = types_str
        
        df["pattern_detected"] = _safe_bool(df["smc_pattern"])
        df["pattern_type"] = df["smc_type"]
        
        return df
    
    def generate_signals(self, df: pd.DataFrame, instrument: str,
                         timeframe: str, mss_lookback: int = 25,
                         require_mss: bool = True, **kwargs) -> List[Signal]:
        """Generate SMC/ICT signals with MSS/BOS confirmation (v2.2.2)."""
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

        # MSS/BOS detection
        mss_events = detect_market_structure_shift(df, max_age=mss_lookback)
        df.attrs["smc_mss_events"] = mss_events

        current_price = df["close"].iloc[-1]
        current_low = df["low"].iloc[-1]
        current_high = df["high"].iloc[-1]

        sessions = get_session_for_time(
            df.index[-1] if isinstance(df.index[-1], datetime)
            else datetime.now(timezone.utc)
        )
        session_str = ", ".join([s.value for s in sessions])

        max_confluence = 15  # OB(3)+FVG(3)+BB(2)+MB(2)+IFVG(1)+MSS(3)+RSI(1)

        buy_zones = [z for z in all_zones if z["direction"] == "BUY"]
        sell_zones = [z for z in all_zones if z["direction"] == "SELL"]

        bullish_mss = [e for e in mss_events if e["direction"] == "BUY"]
        bearish_mss = [e for e in mss_events if e["direction"] == "SELL"]

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
                    if (entry - sl) / entry > 0.02: sl = entry * 0.98
                    risk = entry - sl
                    tp = entry + risk * 2.5
                else:
                    sl = best_zone["top"] + (best_zone["top"] - best_zone["bottom"]) * 0.2
                    if (sl - entry) / entry > 0.02: sl = entry * 1.02
                    risk = sl - entry
                    tp = entry - risk * 2.5

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
                    "mss_break_level": mss_evt.get("break_level") if mss_evt else None,
                        "mss_boost": mss_boost,
                        "ob_count": len([z for z in zones if "ob" in z["type"]]),
                        "fvg_count": len([z for z in zones if "fvg" in z["type"]]),
                        "breaker_count": len([z for z in zones if "breaker" in z["type"]]),
                        "rsi": float(df["rsi"].iloc[-1]) if "rsi" in df.columns and not pd.isna(df["rsi"].iloc[-1]) else None,
                    })
            return None

        # BUY signals (require bullish MSS/BOS if require_mss=True)
        if not (require_mss and not bullish_mss):
            sig = _build_signal("BUY", buy_zones, bull_evt, bull_boost)
            if sig: signals.append(sig)

        # SELL signals
        if not (require_mss and not bearish_mss):
            sig = _build_signal("SELL", sell_zones, bear_evt, bear_boost)
            if sig: signals.append(sig)

        return signals
    
    def get_zones(self, df: pd.DataFrame) -> List[Dict]:
        """Return all SMC zones for visualization."""
        if not hasattr(df, 'attrs'):
            df = self.detect_pattern(df)
        
        zones = []
        color_map = {
            "order_block": {"BUY": "#2196F3", "SELL": "#FF5722"},   # Blue / Red-Orange
            "fvg": {"BUY": "#4CAF50", "SELL": "#E91E63"},          # Green / Pink
            "breaker_block": {"BUY": "#00BCD4", "SELL": "#FF9800"},  # Cyan / Orange
            "mitigation_block": {"BUY": "#9C27B0", "SELL": "#795548"},  # Purple / Brown
            "ifvg": {"BUY": "#CDDC39", "SELL": "#607D8B"},          # Lime / Blue-Grey
        }
        
        all_smc = [
            ("order_block", df.attrs.get("smc_order_blocks", [])),
            ("fvg", df.attrs.get("smc_fvgs", [])),
            ("breaker_block", df.attrs.get("smc_breaker_blocks", [])),
            ("mitigation_block", df.attrs.get("smc_mitigation_blocks", [])),
            ("ifvg", df.attrs.get("smc_inversion_fvgs", [])),
        ]
        
        for category, zone_list in all_smc:
            for z in zone_list:
                direction = z.get("direction", "BUY")
                zones.append({
                    "start_idx": z["index"],
                    "end_idx": min(z["index"] + 15, len(df) - 1),
                    "top": z["top"],
                    "bottom": z["bottom"],
                    "type": f"{category}_{direction}",
                    "color": color_map.get(category, {}).get(direction, "#888888"),
                })
        
        return zones


# Register the strategy
