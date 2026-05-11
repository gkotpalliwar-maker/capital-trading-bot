"""
Capital.com Trading Bot — Conflict Arbiter (v2.12.1)

Resolves directional conflicts when the same instrument produces
both BUY and SELL signals within a single scan cycle.

Design principles:
  - Only acts on ALERT/EXECUTABLE candidates (BLOCK/WATCH already eliminated).
  - When opposing signals exist, resolves using: score > timeframe > memory bias.
  - If resolution is unclear, downgrades both to ALERT (no auto-execute on conflict).
  - Never promotes a signal; only demotes or blocks conflicting losers.
  - Logs all arbitration decisions for transparency.

Integration:
  Called by scanner.py after all signals for an instrument are evaluated.
  Input: list of (sig_data, decision) tuples for one instrument.
  Output: list of (sig_data, decision, arbitration_action) tuples.
"""
import logging
from typing import Dict, List, Tuple, Optional

logger = logging.getLogger("conflict_arbiter")

# Timeframe priority (higher = stronger directional conviction)
TF_PRIORITY = {"H4": 3, "H1": 2, "M15": 1, "M5": 0}

# Minimum score difference to declare a clear winner
SCORE_MARGIN = 10

# Statuses that are eligible for conflict (non-blocked signals)
DISPATCHABLE = {"ALERT", "EXECUTABLE"}


def arbitrate_instrument(
    candidates: List[Tuple[Dict, Dict]],
    memory_bias: Optional[str] = None,
) -> List[Tuple[Dict, Dict, str]]:
    """
    Arbitrate conflicting signals for a single instrument.

    Args:
        candidates: List of (sig_data, decision) tuples from one instrument.
                    sig_data has: direction, tf, zone_types, confluence, etc.
                    decision has: status, score, quality, modifiers, etc.
        memory_bias: Optional market memory bias ("bullish", "bearish", "neutral", "choppy")

    Returns:
        List of (sig_data, decision, action) tuples where action is:
            "pass"      — no conflict, dispatch normally
            "winner"    — won arbitration, dispatch normally
            "demoted"   — lost arbitration, downgraded to WATCH
            "capped"    — conflict unresolved, capped at ALERT
            "blocked"   — conflict in choppy context, blocked
    """
    if not candidates:
        return []

    # Separate dispatchable (ALERT/EXECUTABLE) from already-resolved (BLOCK/WATCH)
    dispatchable = [(s, d) for s, d in candidates if d.get("status") in DISPATCHABLE]
    resolved = [(s, d) for s, d in candidates if d.get("status") not in DISPATCHABLE]

    # No conflict possible if 0-1 dispatchable signals
    if len(dispatchable) <= 1:
        return [(s, d, "pass") for s, d in candidates]

    # Check for directional conflict
    directions = set(s["direction"] for s, d in dispatchable)
    if len(directions) <= 1:
        # All same direction — no conflict
        return [(s, d, "pass") for s, d in candidates]

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CONFLICT DETECTED: opposing BUY + SELL signals on same instrument
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    buys = [(s, d) for s, d in dispatchable if s["direction"] == "BUY"]
    sells = [(s, d) for s, d in dispatchable if s["direction"] == "SELL"]

    instrument = dispatchable[0][0].get("inst_name", dispatchable[0][0].get("instrument", "?"))
    logger.info("CONFLICT: %s has %d BUY + %d SELL signals — arbitrating...",
                instrument, len(buys), len(sells))

    # Strategy 1: Choppy market — block all if memory says choppy
    if memory_bias == "choppy":
        logger.info("  Choppy memory → blocking all conflicting signals")
        results = [(s, d, "pass") for s, d in resolved]
        for s, d in dispatchable:
            d["status"] = "WATCH"
            d["warnings"] = d.get("warnings", []) + ["conflict_arbiter: choppy market, opposing signals blocked"]
            results.append((s, d, "blocked"))
        return results

    # Strategy 2: Score-based resolution
    # Pick the best BUY and best SELL, compare them
    best_buy = max(buys, key=lambda x: x[1].get("score", 0)) if buys else None
    best_sell = max(sells, key=lambda x: x[1].get("score", 0)) if sells else None

    buy_score = best_buy[1].get("score", 0) if best_buy else 0
    sell_score = best_sell[1].get("score", 0) if best_sell else 0

    # Strategy 3: Timeframe tiebreaker
    buy_tf = TF_PRIORITY.get(best_buy[0].get("tf", "M5"), 0) if best_buy else 0
    sell_tf = TF_PRIORITY.get(best_sell[0].get("tf", "M5"), 0) if best_sell else 0

    # Strategy 4: Memory bias tiebreaker
    memory_bonus = 0
    if memory_bias == "bullish":
        memory_bonus = 5  # Slight BUY preference
    elif memory_bias == "bearish":
        memory_bonus = -5  # Slight SELL preference

    # Composite scores for arbitration
    buy_composite = buy_score + (buy_tf * 3) + memory_bonus
    sell_composite = sell_score + (sell_tf * 3) - memory_bonus

    logger.info("  BUY: score=%d, tf_pri=%d, composite=%d", buy_score, buy_tf, buy_composite)
    logger.info("  SELL: score=%d, tf_pri=%d, composite=%d", sell_score, sell_tf, sell_composite)
    logger.info("  Memory bias: %s (bonus=%+d)", memory_bias or "none", memory_bonus)

    # Determine winner
    margin = abs(buy_composite - sell_composite)
    winner_dir = None

    if margin >= SCORE_MARGIN:
        winner_dir = "BUY" if buy_composite > sell_composite else "SELL"
        logger.info("  RESOLVED: %s wins by %d points", winner_dir, margin)
    else:
        # Too close to call — cap all at ALERT
        logger.info("  UNRESOLVED: margin=%d (<%d) — capping all at ALERT", margin, SCORE_MARGIN)

    # Build results
    results = [(s, d, "pass") for s, d in resolved]

    for s, d in dispatchable:
        if winner_dir is None:
            # Unresolved — cap at ALERT (prevent auto-execution)
            if d.get("status") == "EXECUTABLE":
                d["status"] = "ALERT"
                d["warnings"] = d.get("warnings", []) + [
                    f"conflict_arbiter: opposing signal exists, capped at ALERT (margin={margin})"
                ]
            results.append((s, d, "capped"))
        elif s["direction"] == winner_dir:
            # Winner — pass through normally
            d["reasons"] = d.get("reasons", []) + [
                f"conflict_arbiter: won vs opposing {_opposite(winner_dir)} (margin={margin})"
            ]
            results.append((s, d, "winner"))
        else:
            # Loser — demote to WATCH
            d["status"] = "WATCH"
            d["warnings"] = d.get("warnings", []) + [
                f"conflict_arbiter: lost to {winner_dir} signal (margin={margin})"
            ]
            results.append((s, d, "demoted"))

    return results


def _opposite(direction: str) -> str:
    return "SELL" if direction == "BUY" else "BUY"


def get_arbitration_summary(results: List[Tuple[Dict, Dict, str]]) -> str:
    """Generate a short summary string for logging."""
    actions = [action for _, _, action in results]
    if "winner" in actions or "demoted" in actions or "blocked" in actions or "capped" in actions:
        parts = []
        for s, d, action in results:
            if action in ("winner", "demoted", "capped", "blocked"):
                parts.append(f"{s.get('direction', '?')} {s.get('tf', '?')} → {action}")
        return " | ".join(parts)
    return ""
