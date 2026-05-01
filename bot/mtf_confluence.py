import logging
from datetime import datetime, timezone
from typing import Dict, Tuple, Optional

logger = logging.getLogger("mtf_confluence")

# Cache: {epic: {"bias": "bullish"|"bearish"|"neutral", "confidence": float, "last_update": datetime}}
_bias_cache: Dict[str, dict] = {}
_cache_ttl_seconds = 900  # 15 minutes


def get_htf_bias(epic: str, client, timeframe: str = "HOUR_4", lookback: int = 20) -> dict:
    """Determine higher-timeframe bias for an instrument using market structure.
    
    Returns: {"bias": "bullish"|"bearish"|"neutral", "confidence": float 0-1,
              "structure": "HH/HL"|"LH/LL"|"mixed", "last_mss": "bullish"|"bearish"|"none"}
    """
    # v2.10.0: Resolve lowercase instrument keys to uppercase API epics
    from config import resolve_instrument
    epic = resolve_instrument(epic)

    now = datetime.now(timezone.utc)
    
    # Check cache
    if epic in _bias_cache:
        cached = _bias_cache[epic]
        age = (now - cached["last_update"]).total_seconds()
        if age < _cache_ttl_seconds:
            return cached
    
    result = {"bias": "neutral", "confidence": 0.0, "structure": "mixed",
              "last_mss": "none", "last_update": now, "epic": epic}
    
    try:
        # Fetch H4 candles from Capital.com
        resp = client.get(f"/api/v1/prices/{epic}", {
            "resolution": timeframe, "max": str(lookback + 1)
        })
        prices = resp.get("prices", [])
        
        if len(prices) < 6:
            logger.warning(f"MTF {epic}: insufficient data ({len(prices)} candles)")
            _bias_cache[epic] = result
            return result
        
        # Extract swing highs and lows
        highs = [float(p.get("highPrice", {}).get("mid", 0) or
                       p.get("highPrice", {}).get("ask", 0)) for p in prices]
        lows = [float(p.get("lowPrice", {}).get("mid", 0) or
                      p.get("lowPrice", {}).get("ask", 0)) for p in prices]
        closes = [float(p.get("closePrice", {}).get("mid", 0) or
                        p.get("closePrice", {}).get("ask", 0)) for p in prices]
        
        if not all(highs) or not all(lows):
            _bias_cache[epic] = result
            return result
        
        # Find swing points (simple: compare 3-bar windows)
        swing_highs = []
        swing_lows = []
        for i in range(1, len(highs) - 1):
            if highs[i] > highs[i-1] and highs[i] > highs[i+1]:
                swing_highs.append((i, highs[i]))
            if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
                swing_lows.append((i, lows[i]))
        
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            _bias_cache[epic] = result
            return result
        
        # Analyze last 3 swing points for structure
        recent_highs = [sh[1] for sh in swing_highs[-3:]]
        recent_lows = [sl[1] for sl in swing_lows[-3:]]
        
        # Higher highs and higher lows = bullish
        hh_count = sum(1 for i in range(1, len(recent_highs)) if recent_highs[i] > recent_highs[i-1])
        hl_count = sum(1 for i in range(1, len(recent_lows)) if recent_lows[i] > recent_lows[i-1])
        lh_count = sum(1 for i in range(1, len(recent_highs)) if recent_highs[i] < recent_highs[i-1])
        ll_count = sum(1 for i in range(1, len(recent_lows)) if recent_lows[i] < recent_lows[i-1])
        
        bullish_score = hh_count + hl_count
        bearish_score = lh_count + ll_count
        total = max(bullish_score + bearish_score, 1)
        
        if bullish_score > bearish_score:
            result["bias"] = "bullish"
            result["structure"] = "HH/HL"
            result["confidence"] = bullish_score / total
        elif bearish_score > bullish_score:
            result["bias"] = "bearish"
            result["structure"] = "LH/LL"
            result["confidence"] = bearish_score / total
        else:
            result["bias"] = "neutral"
            result["structure"] = "mixed"
            result["confidence"] = 0.5
        
        # Detect last MSS (Market Structure Shift)
        # MSS = break of last significant swing high (bullish) or swing low (bearish)
        last_close = closes[-1]
        if swing_highs and last_close > swing_highs[-1][1]:
            result["last_mss"] = "bullish"
        elif swing_lows and last_close < swing_lows[-1][1]:
            result["last_mss"] = "bearish"
        
        # Boost confidence if MSS aligns with structure
        if result["last_mss"] == result["bias"]:
            result["confidence"] = min(result["confidence"] + 0.2, 1.0)
        
        logger.info(f"MTF {epic}: {result['bias']} ({result['confidence']:.0%}) "
                    f"structure={result['structure']} mss={result['last_mss']}")
    
    except Exception as e:
        logger.warning(f"MTF {epic} error: {e}")
    
    _bias_cache[epic] = result
    return result


def check_mtf_alignment(epic: str, direction: str, client,
                         mtf_required: bool = False,
                         bonus_confluence: int = 2) -> Tuple[bool, int, str]:
    """Check if a signal direction aligns with the higher-timeframe bias.
    
    Args:
        epic: Instrument epic (e.g., "EURUSD")
        direction: Signal direction ("BUY" or "SELL")
        client: Capital.com API client
        mtf_required: If True, block counter-trend signals
        bonus_confluence: Extra confluence points for aligned signals
    
    Returns: (is_aligned: bool, confluence_adjustment: int, reason: str)
    """
    bias = get_htf_bias(epic, client)
    bias_dir = bias["bias"]
    confidence = bias["confidence"]
    structure = bias["structure"]
    
    # Determine alignment
    if bias_dir == "neutral":
        return True, 0, f"H4 neutral ({structure})"
    
    aligned = (direction == "BUY" and bias_dir == "bullish") or \
              (direction == "SELL" and bias_dir == "bearish")
    
    if aligned:
        bonus = bonus_confluence if confidence >= 0.6 else bonus_confluence - 1
        return True, max(bonus, 0), f"H4 aligned {bias_dir} ({confidence:.0%})"
    else:
        # Counter-trend
        if mtf_required:
            return False, 0, f"MTF BLOCKED: {direction} vs H4 {bias_dir} ({confidence:.0%})"
        else:
            # Advisory: reduce confluence by 1 as penalty
            return True, -1, f"H4 counter-trend ({direction} vs {bias_dir} {confidence:.0%})"


def get_all_biases(instrument_map: dict, client) -> Dict[str, dict]:
    """Get H4 bias for all instruments. Used by /mtf command."""
    biases = {}
    for name, epic in instrument_map.items():
        bias = get_htf_bias(epic, client)
        biases[name] = bias
    return biases


def clear_cache():
    """Clear the bias cache (called at start of each scan cycle)."""
    global _bias_cache
    _bias_cache = {}
