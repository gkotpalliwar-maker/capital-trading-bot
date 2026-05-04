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
      - Structure valid if price hasn't broken below swing lows BELOW entry
      - Ignores swing lows at/above entry (those formed after entry)
    
    For a SELL trade (bearish MSS/BOS):
      - Structure valid if price hasn't broken above swing highs ABOVE entry
      - Ignores swing highs at/below entry (entry was near these by design)
    
    Returns: (is_valid, reason, invalidation_level)
    """
    swings = detect_swing_points(highs, lows, lookback)
    if not swings:
        return True, "Insufficient data for structure check", None

    # Buffer: 0.15% of entry price — avoids false triggers at entry-level swings
    buffer = entry_price * 0.0015

    if direction == "BUY":
        # For BUY: invalidation = price drops below swing lows that are BELOW entry
        recent_lows = [s for s in swings if s["type"] == "low"]
        if not recent_lows:
            return True, "No swing lows found", None
        
        # Only consider swing lows meaningfully BELOW entry price
        qualifying_lows = [s for s in recent_lows if s["price"] < entry_price - buffer]
        
        if not qualifying_lows:
            # All swing lows are near/above entry — trade just entered, structure OK
            return True, "Structure intact (recently entered, no qualifying swing lows below entry)", None
        
        relevant_lows = sorted(qualifying_lows, key=lambda s: s["index"], reverse=True)
        invalidation = min(s["price"] for s in relevant_lows[:3])
        
        if current_price < invalidation:
            return False, f"Structure broken: price {current_price:.5f} below swing low {invalidation:.5f}", invalidation
        
        # Check if new lower lows forming
        last_3_lows = relevant_lows[:3]
        if len(last_3_lows) >= 2:
            if last_3_lows[0]["price"] < last_3_lows[1]["price"]:
                return True, f"Warning: lower lows forming ({last_3_lows[0]['price']:.5f} &lt; {last_3_lows[1]['price']:.5f})", invalidation
        
        distance_pct = ((current_price - invalidation) / current_price) * 100
        return True, f"Structure intact (inv: {invalidation:.5f}, {distance_pct:.2f}% away)", invalidation

    else:  # SELL
        # For SELL: invalidation = price rises above swing highs ABOVE entry
        recent_highs = [s for s in swings if s["type"] == "high"]
        if not recent_highs:
            return True, "No swing highs found", None
        
        # Only consider swing highs meaningfully ABOVE entry price
        qualifying_highs = [s for s in recent_highs if s["price"] > entry_price + buffer]
        
        if not qualifying_highs:
            # All swing highs are near/below entry — trade just entered, structure OK
            return True, "Structure intact (recently entered, no qualifying swing highs above entry)", None
        
        relevant_highs = sorted(qualifying_highs, key=lambda s: s["index"], reverse=True)
        invalidation = max(s["price"] for s in relevant_highs[:3])
        
        if current_price > invalidation:
            return False, f"Structure broken: price {current_price:.5f} above swing high {invalidation:.5f}", invalidation
        
        # Check if new higher highs forming
        last_3_highs = relevant_highs[:3]
        if len(last_3_highs) >= 2:
            if last_3_highs[0]["price"] > last_3_highs[1]["price"]:
                return True, f"Warning: higher highs forming ({last_3_highs[0]['price']:.5f} &gt; {last_3_highs[1]['price']:.5f})", invalidation
        
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
