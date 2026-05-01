"""
Capital.com Trading Bot — Signal Decision Engine (v2.10.0)

Central decision function that replaces scattered logic in scanner.py.
Consolidates: guardrails, regime filter, news filter, ML scoring,
dedup, and MTF into a single evaluate_signal_candidate() call.

Returns a structured decision dict with unified 0-100 scoring.
"""
import logging
import time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("signal_decision")

# ── Status Constants ────────────────────────────────────────────
BLOCK = "BLOCK"          # Hard-blocked or critically low score — never trade
WATCH = "WATCH"          # Interesting but too weak — log and monitor
ALERT = "ALERT"          # Decent signal — Telegram alert, no auto-execute
EXECUTABLE = "EXECUTABLE"  # High quality — Telegram alert with Execute buttons

# ── Score Thresholds (0-100 unified scale) ──────────────────────
SCORE_EXECUTABLE = 55    # Minimum to reach EXECUTABLE status
SCORE_ALERT = 35         # Minimum for ALERT (Telegram notification)
SCORE_WATCH = 15         # Minimum for WATCH (logged only)
                         # Below SCORE_WATCH → BLOCK

# ── Quality Labels ──────────────────────────────────────────────
QUALITY_ELITE = "elite"      # 80+
QUALITY_HIGH = "high"        # 60+
QUALITY_MEDIUM = "medium"    # 40+
QUALITY_LOW = "low"          # < 40

# ── Component Weights (sum to ~100 at max) ──────────────────────
W_GUARDRAIL = 50         # Guardrail score: 0-50 (from 0-20 raw, x2.5)
W_ML_MAX = 20            # ML confidence bonus: 0-20
W_NEWS_CLEAR = 5         # No news risk: +5
W_REGIME_OK = 10         # Regime alignment: +10
W_MTF_ALIGNED = 10       # MTF alignment: +10
W_RR_BONUS = 5           # R:R >= 2.0: +5

# ── Penalties ───────────────────────────────────────────────────
P_NEWS_HIGH = -100       # High-impact news: hard block
P_NEWS_MEDIUM = -15      # Medium-impact news: score penalty
P_NEWS_UNAVAIL = -5      # News data unavailable when required
P_ML_UNAVAIL = 0         # ML unavailable: no bonus (neutral)
P_ML_LOW = -10           # ML says low probability: penalty
P_REGIME_BLOCK = -100    # Regime hard block
P_MTF_BLOCK = -20        # MTF misaligned: penalty
P_STALE_SIGNAL = -100    # Signal entry price already passed


def _classify_quality(score: int) -> str:
    """Map unified score to quality label."""
    if score >= 80:
        return QUALITY_ELITE
    if score >= 60:
        return QUALITY_HIGH
    if score >= 40:
        return QUALITY_MEDIUM
    return QUALITY_LOW


def _classify_status(score: int, has_hard_blocks: bool) -> str:
    """Map unified score + blocks to decision status."""
    if has_hard_blocks or score < SCORE_WATCH:
        return BLOCK
    if score < SCORE_ALERT:
        return WATCH
    if score < SCORE_EXECUTABLE:
        return ALERT
    return EXECUTABLE


def _normalise_guardrail_score(raw_score: int, max_possible: int = 20) -> int:
    """Normalise guardrail 0-20 score to 0-50 component."""
    clamped = max(0, min(raw_score, max_possible))
    return int(round(clamped / max_possible * W_GUARDRAIL))


def _check_stale_signal(sig_data: Dict, df) -> Tuple[bool, str]:
    """Check if entry price has already been passed by current price."""
    entry = sig_data.get("entry") or sig_data.get("entry_price")
    direction = sig_data.get("direction", "")
    if entry is None or df is None or df.empty:
        return False, ""
    current = float(df["close"].iloc[-1])
    if direction == "BUY" and current > entry * 1.003:
        return True, f"BUY entry {entry:.5f} already passed (current {current:.5f})"
    if direction == "SELL" and current < entry * 0.997:
        return True, f"SELL entry {entry:.5f} already passed (current {current:.5f})"
    return False, ""


def evaluate_signal_candidate(
    signal: Dict,
    df,
    client=None,
    instrument: str = "",
    timeframe: str = "",
    regime: Dict = None,
    guardrails=None,
    risk_manager=None,
    news_filter_mod=None,
    ml_scorer_mod=None,
    mtf_func=None,
) -> Dict:
    """
    Central signal decision function.

    Args:
        signal: Signal data dict (entry, sl, tp, direction, zone_types, etc.)
        df: Price DataFrame with indicators
        client: Capital.com API client (for MTF)
        instrument: Resolved instrument epic (e.g. "GOLD")
        timeframe: Timeframe string (e.g. "H1")
        regime: Output from regime_filter.detect_regime()
        guardrails: SignalGuardrails instance
        risk_manager: RiskManager instance (for dedup)
        news_filter_mod: news_filter module
        ml_scorer_mod: signal_scorer module
        mtf_func: check_mtf_alignment function

    Returns:
        {
            "status": "BLOCK|WATCH|ALERT|EXECUTABLE",
            "score": int (0-100),
            "quality": "low|medium|high|elite",
            "reasons": [str],       # positive reasons
            "blocks": [str],        # hard block reasons
            "warnings": [str],      # soft warnings / penalties
            "modifiers": {          # applied modifiers with values
                "guardrail_raw": int,
                "guardrail_norm": int,
                "ml_score": float | None,
                "news_status": str,
                "regime_ok": bool,
                "mtf_aligned": bool | None,
                "is_duplicate": bool,
                "rr_ratio": float,
            },
            "guardrail_result": dict | None,
            "telegram_text": str,
        }
    """
    score = 0
    reasons = []
    blocks = []
    warnings = []
    modifiers = {
        "guardrail_raw": 0, "guardrail_norm": 0,
        "ml_score": None, "news_status": "unchecked",
        "regime_ok": True, "mtf_aligned": None,
        "is_duplicate": False, "rr_ratio": 0.0,
    }
    guardrail_result = None
    direction = signal.get("direction", "")
    zone_types = signal.get("zone_types", "")
    is_retrace = "retrace" in zone_types

    # ================================================================
    # 1. GUARDRAILS (0-50 points)
    # ================================================================
    if guardrails is not None:
        try:
            gr = guardrails.evaluate_signal(
                df=df, instrument=instrument, direction=direction,
                timeframe=timeframe, signal_metadata=signal.get("metadata"),
            )
            guardrail_result = gr
            raw = gr.get("final_score", 0)
            norm = _normalise_guardrail_score(raw, gr.get("max_possible_score", 20))
            modifiers["guardrail_raw"] = raw
            modifiers["guardrail_norm"] = norm
            score += norm

            # Propagate hard blocks
            for hb in gr.get("hard_blocks", []):
                blocks.append(f"Guardrail: {hb}")

            if gr.get("passed"):
                reasons.append(f"Guardrails PASSED (raw {raw}, norm {norm}/{W_GUARDRAIL})")
            else:
                warnings.append(f"Guardrails FAILED (raw {raw}, norm {norm}/{W_GUARDRAIL})")

        except Exception as e:
            logger.error("Guardrail evaluation error: %s", e)
            warnings.append(f"Guardrail error: {e}")
            # Give partial credit on error (don't hard-block)
            score += int(W_GUARDRAIL * 0.4)

    # ================================================================
    # 2. REGIME FILTER (+10 or block)
    # ================================================================
    if regime and not is_retrace:
        try:
            # Import here to avoid circular imports at module level
            from regime_filter import is_setup_allowed
            regime_ok, regime_reason = is_setup_allowed(regime, zone_types, direction)
            modifiers["regime_ok"] = regime_ok
            if regime_ok:
                score += W_REGIME_OK
                reasons.append(f"Regime OK: {regime.get('label', '?')} ({regime_reason})")
            else:
                blocks.append(f"Regime: {regime_reason}")
        except Exception as e:
            logger.warning("Regime filter error: %s", e)
            # Allow on error — don't block due to code issues
            modifiers["regime_ok"] = True
            score += W_REGIME_OK
            warnings.append(f"Regime error (allowing): {e}")
    elif is_retrace:
        # Retrace signals bypass regime — give full points
        score += W_REGIME_OK
        reasons.append("Retrace: regime bypass (own quality scoring)")
        modifiers["regime_ok"] = True

    # ================================================================
    # 3. NEWS FILTER (+5 clear, -15 medium, block high)
    # ================================================================
    if news_filter_mod is not None:
        try:
            news_status, news_events, news_reason = news_filter_mod.check_news_risk(instrument)
            modifiers["news_status"] = news_status
            if news_status == "clear":
                score += W_NEWS_CLEAR
                reasons.append(f"News clear: {news_reason}")
            elif news_status == "blocked":
                score += P_NEWS_HIGH
                blocks.append(f"News HIGH: {news_reason}")
            elif news_status == "caution":
                score += P_NEWS_MEDIUM
                warnings.append(f"News MEDIUM: {news_reason} ({P_NEWS_MEDIUM:+d})")
        except Exception as e:
            logger.warning("News filter error: %s", e)
            modifiers["news_status"] = "error"
            warnings.append(f"News error: {e}")
            try:
                from news_filter import NEWS_REQUIRED as _news_req2
                if _news_req2:
                    score += P_NEWS_UNAVAIL
                    warnings.append(f"News REQUIRED but errored ({P_NEWS_UNAVAIL:+d})")
            except ImportError:
                pass
    else:
        # News module not loaded
        modifiers["news_status"] = "unavailable"
        try:
            from news_filter import NEWS_REQUIRED as _news_req
            if _news_req:
                score += P_NEWS_UNAVAIL
                warnings.append(f"News REQUIRED but unavailable ({P_NEWS_UNAVAIL:+d})")
        except ImportError:
            pass  # news_filter not installed at all — no penalty

    # ================================================================
    # 4. ML SCORING (+0-20 or penalty)
    # ================================================================
    if ml_scorer_mod is not None:
        try:
            ml_score = ml_scorer_mod.score_signal(signal)
            modifiers["ml_score"] = ml_score
            if ml_score is None or ml_score == 0.5:
                # Model unavailable / neutral — no bonus, no penalty
                warnings.append("ML unavailable (neutral)")
            elif ml_score >= 0.6:
                # Good ML confidence — scale bonus
                bonus = int(round((ml_score - 0.5) * 2 * W_ML_MAX))
                bonus = min(bonus, W_ML_MAX)
                score += bonus
                reasons.append(f"ML confident: {ml_score:.0%} (+{bonus})")
            elif ml_score < 0.35:
                score += P_ML_LOW
                warnings.append(f"ML low confidence: {ml_score:.0%} ({P_ML_LOW:+d})")
            else:
                # 0.35-0.6 — marginal, small bonus
                bonus = int(round((ml_score - 0.35) / 0.25 * (W_ML_MAX * 0.3)))
                score += bonus
                reasons.append(f"ML marginal: {ml_score:.0%} (+{bonus})")
        except Exception as e:
            logger.warning("ML scoring error: %s", e)
            modifiers["ml_score"] = None
            warnings.append(f"ML error: {e}")
    else:
        modifiers["ml_score"] = None

    # ================================================================
    # 5. MTF CONFLUENCE (+10 aligned, -20 misaligned)
    # ================================================================
    if mtf_func is not None and client is not None:
        try:
            aligned, mtf_adj, mtf_reason = mtf_func(instrument, direction, client)
            modifiers["mtf_aligned"] = aligned
            if aligned:
                score += W_MTF_ALIGNED
                reasons.append(f"MTF aligned: {mtf_reason}")
            elif mtf_adj < 0:
                score += max(P_MTF_BLOCK, mtf_adj * 3)  # Scale adj
                warnings.append(f"MTF misaligned: {mtf_reason}")
            # Neutral MTF — no bonus or penalty
        except Exception as e:
            logger.warning("MTF check error: %s", e)
            modifiers["mtf_aligned"] = None
            warnings.append(f"MTF error: {e}")

    # ================================================================
    # 6. R:R BONUS (+5 for R:R >= 2.0)
    # ================================================================
    rr = signal.get("rr") or signal.get("risk_reward_ratio") or 0
    if callable(rr):
        try:
            rr = rr()
        except Exception:
            rr = 0
    modifiers["rr_ratio"] = float(rr) if rr else 0.0
    if rr and float(rr) >= 2.0:
        score += W_RR_BONUS
        reasons.append(f"R:R {float(rr):.1f} >= 2.0 (+{W_RR_BONUS})")
    elif rr and float(rr) < 1.0:
        warnings.append(f"R:R {float(rr):.1f} < 1.0 (no penalty, but weak)")

    # ================================================================
    # 7. STALE SIGNAL CHECK (hard block)
    # ================================================================
    is_stale, stale_reason = _check_stale_signal(signal, df)
    if is_stale:
        blocks.append(f"Stale: {stale_reason}")
        score += P_STALE_SIGNAL

    # ================================================================
    # 8. DUPLICATE CHECK (hard block — checked last to avoid wasted work)
    # ================================================================
    if risk_manager is not None:
        try:
            is_dup, dup_reason = risk_manager.check_duplicate_signal(
                instrument, direction, timeframe)
            modifiers["is_duplicate"] = is_dup
            if is_dup:
                blocks.append(f"Duplicate: {dup_reason}")
        except Exception as e:
            logger.warning("Dedup check error: %s", e)

    # ================================================================
    # FINAL DECISION
    # ================================================================
    score = max(0, min(100, score))
    has_blocks = len(blocks) > 0
    status = _classify_status(score, has_blocks)
    quality = _classify_quality(score)

    # ── Build Telegram text ─────────────────────────────────────
    status_emoji = {
        BLOCK: "🚫", WATCH: "👁️", ALERT: "🔔", EXECUTABLE: "✅",
    }
    quality_emoji = {
        QUALITY_ELITE: "🌟", QUALITY_HIGH: "💪",
        QUALITY_MEDIUM: "🟡", QUALITY_LOW: "⚠️",
    }

    tg_lines = [
        f"\n📊 <b>Signal Decision</b> [{status_emoji.get(status, '?')} {status}]",
        f"Score: <b>{score}</b>/100 | Quality: {quality_emoji.get(quality, '')} {quality.upper()}",
    ]
    if reasons:
        tg_lines.append("\n<b>✅ Strengths:</b>")
        for r in reasons[:5]:
            tg_lines.append(f"  • {r}")
    if warnings:
        tg_lines.append("\n<b>⚠️ Warnings:</b>")
        for w in warnings[:5]:
            tg_lines.append(f"  • {w}")
    if blocks:
        tg_lines.append("\n<b>🚫 Blocks:</b>")
        for b in blocks:
            tg_lines.append(f"  • {b}")

    # Include guardrail detail if available
    if guardrail_result and guardrail_result.get("telegram_text"):
        tg_lines.append(guardrail_result["telegram_text"])

    telegram_text = "\n".join(tg_lines)

    decision = {
        "status": status,
        "score": score,
        "quality": quality,
        "reasons": reasons,
        "blocks": blocks,
        "warnings": warnings,
        "modifiers": modifiers,
        "guardrail_result": guardrail_result,
        "telegram_text": telegram_text,
    }

    logger.info(
        "Decision: %s %s %s %s → %s (score=%d, quality=%s, blocks=%d)",
        instrument, direction, timeframe, zone_types,
        status, score, quality, len(blocks),
    )

    return decision


def should_execute(decision: Dict) -> bool:
    """Convenience: is this signal executable?"""
    return decision.get("status") == EXECUTABLE


def should_notify(decision: Dict) -> bool:
    """Convenience: should this signal be sent to Telegram?"""
    return decision.get("status") in (ALERT, EXECUTABLE)


def should_log(decision: Dict) -> bool:
    """Convenience: should this signal be logged to DB?"""
    return decision.get("status") in (WATCH, ALERT, EXECUTABLE)


def format_decision_log(decision: Dict, sig_data: Dict) -> str:
    """Format a single-line log entry for CSV/DB logging."""
    return (
        f"{sig_data.get('instrument','?')}|{sig_data.get('direction','?')}|"
        f"{sig_data.get('tf','?')}|{decision['status']}|{decision['score']}|"
        f"{decision['quality']}|blocks={len(decision['blocks'])}|"
        f"ml={decision['modifiers'].get('ml_score','N/A')}|"
        f"news={decision['modifiers'].get('news_status','?')}|"
        f"regime={decision['modifiers'].get('regime_ok','?')}|"
        f"rr={decision['modifiers'].get('rr_ratio',0):.1f}"
    )

def sanitize_for_storage(decision: Dict) -> Dict:
    """Return a JSON-serializable copy of the decision dict.
    
    Strips GuardrailResult objects and any other non-serializable types
    so the decision can be safely passed to json.dumps() in persistence.
    """
    import copy
    clean = copy.deepcopy(decision)
    
    # Remove the raw guardrail_result (contains GuardrailResult objects)
    if "guardrail_result" in clean:
        gr = clean["guardrail_result"]
        if gr is not None:
            # Keep only the serializable parts
            clean["guardrail_result"] = {
                "passed": gr.get("passed"),
                "final_score": gr.get("final_score"),
                "base_score": gr.get("base_score"),
                "max_possible_score": gr.get("max_possible_score"),
                "hard_blocks": gr.get("hard_blocks", []),
                "quality": gr.get("quality", ""),
                "summary": gr.get("summary", ""),
                # Convert GuardrailResult objects to strings
                "results": [str(r) for r in gr.get("results", [])],
            }
    
    return clean
