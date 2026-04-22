import logging
from datetime import datetime, timezone

logger = logging.getLogger("structure_checker")


def detect_swing_points(highs, lows, lookback=5):
    """Detect swing highs and swing lows from price data."""
    swings = []
    n = len(highs)
    for i in range(lookback, n - lookback):
        # Swing high: highest high in lookback window
        if highs[i] == max(highs[i - lookback:i + lookback + 1]):
            swings.append({"index": i, "type": "high", "price": highs[i]})
        # Swing low: lowest low in lookback window
        if lows[i] == min(lows[i - lookback:i + lookback + 1]):
            swings.append({"index": i, "type": "low", "price": lows[i]})
    swings.sort(key=lambda s: s["index"])
    return swings


def check_structure_validity(direction, entry_price, current_price, highs, lows, lookback=5):
    """Check if the market structure that justified the trade is still valid.
    
    For a BUY trade (bullish MSS/BOS):
      - Structure valid if price hasn't broken below the swing low that preceded the entry
      - Invalidated if price makes a lower low below the entry swing structure
    
    For a SELL trade (bearish MSS/BOS):
      - Structure valid if price hasn't broken above the swing high that preceded the entry
      - Invalidated if price makes a higher high above the entry swing structure
    
    Returns: (is_valid, reason, invalidation_level)
    """
    swings = detect_swing_points(highs, lows, lookback)
    if not swings:
        return True, "Insufficient data for structure check", None

    # Find the swing point closest to (but before) entry price
    # This represents the structure level the trade was based on
    if direction == "BUY":
        # For BUY: find the swing low that the bullish MSS broke above
        # The invalidation is if price drops below this swing low
        recent_lows = [s for s in swings if s["type"] == "low"]
        if not recent_lows:
            return True, "No swing lows found", None
        
        # Find swing lows near entry price (within reasonable range)
        # Use the most recent swing low before or near entry
        relevant_lows = sorted(recent_lows, key=lambda s: s["index"], reverse=True)
        
        # The invalidation level is the lowest recent swing low
        # (the structure level that should hold for the trade to remain valid)
        invalidation = min(s["price"] for s in relevant_lows[:3])
        
        if current_price < invalidation:
            return False, f"Structure broken: price {current_price:.5f} below swing low {invalidation:.5f}", invalidation
        
        # Check if new lower lows are forming (bearish structure shift)
        last_3_lows = relevant_lows[:3]
        if len(last_3_lows) >= 2:
            if last_3_lows[0]["price"] < last_3_lows[1]["price"]:
                return True, f"Warning: lower lows forming ({last_3_lows[0]['price']:.5f} < {last_3_lows[1]['price']:.5f})", invalidation
        
        distance_pct = ((current_price - invalidation) / current_price) * 100
        return True, f"Structure intact (inv: {invalidation:.5f}, {distance_pct:.2f}% away)", invalidation

    else:  # SELL
        # For SELL: find the swing high the bearish MSS broke below
        # Invalidation is if price rises above this swing high
        recent_highs = [s for s in swings if s["type"] == "high"]
        if not recent_highs:
            return True, "No swing highs found", None
        
        relevant_highs = sorted(recent_highs, key=lambda s: s["index"], reverse=True)
        
        # Invalidation = highest recent swing high
        invalidation = max(s["price"] for s in relevant_highs[:3])
        
        if current_price > invalidation:
            return False, f"Structure broken: price {current_price:.5f} above swing high {invalidation:.5f}", invalidation
        
        # Check if new higher highs forming (bullish structure shift)
        last_3_highs = relevant_highs[:3]
        if len(last_3_highs) >= 2:
            if last_3_highs[0]["price"] > last_3_highs[1]["price"]:
                return True, f"Warning: higher highs forming ({last_3_highs[0]['price']:.5f} > {last_3_highs[1]['price']:.5f})", invalidation
        
        distance_pct = ((invalidation - current_price) / current_price) * 100
        return True, f"Structure intact (inv: {invalidation:.5f}, {distance_pct:.2f}% away)", invalidation


def get_structure_status_for_validate(client, epic, direction, entry_price, timeframe="HOUR"):
    """Fetch recent candles and check structure validity for /validate output.
    Returns list of display lines.
    """
    lines = []
    try:
        # Fetch recent candles from Capital.com API
        tf_map = {
            "M5": "MINUTE_5", "M15": "MINUTE_15", "H1": "HOUR",
            "H4": "HOUR_4", "D1": "DAY", "MINUTE_15": "MINUTE_15",
            "HOUR": "HOUR", "HOUR_4": "HOUR_4",
        }
        resolution = tf_map.get(timeframe, "HOUR")
        
        resp = client.get(f"/api/v1/prices/{epic}", {
            "resolution": resolution,
            "max": 100,
        })
        prices = resp.get("prices", [])
        if not prices or len(prices) < 20:
            lines.append("  \u2500\u2500\u2500 Structure \u2500\u2500\u2500")
            lines.append("  \u26a0\ufe0f Insufficient price data")
            return lines
        
        # Extract OHLC
        highs = [float(p.get("highPrice", {}).get("ask", 0) or p.get("highPrice", {}).get("bid", 0)) for p in prices]
        lows = [float(p.get("lowPrice", {}).get("ask", 0) or p.get("lowPrice", {}).get("bid", 0)) for p in prices]
        closes = [float(p.get("closePrice", {}).get("ask", 0) or p.get("closePrice", {}).get("bid", 0)) for p in prices]
        
        current_price = closes[-1] if closes else 0
        if current_price == 0:
            lines.append("  \u2500\u2500\u2500 Structure \u2500\u2500\u2500")
            lines.append("  \u26a0\ufe0f No current price data")
            return lines
        
        is_valid, reason, inv_level = check_structure_validity(
            direction, entry_price, current_price, highs, lows
        )
        
        lines.append("  \u2500\u2500\u2500 Structure \u2500\u2500\u2500")
        if not is_valid:
            lines.append(f"  \U0001f534 INVALIDATED: {reason}")
            lines.append(f"  \u26a0\ufe0f Consider closing this position")
        elif "Warning" in reason:
            lines.append(f"  \U0001f7e1 {reason}")
        else:
            lines.append(f"  \U0001f7e2 {reason}")
    
    except Exception as e:
        logger.warning("Structure check failed for %s: %s", epic, e)
        lines.append("  \u2500\u2500\u2500 Structure \u2500\u2500\u2500")
        lines.append(f"  \u26a0\ufe0f Check failed: {e}")
    
    return lines
