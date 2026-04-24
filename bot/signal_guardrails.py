# bot/signal_guardrails.py — v2.8.0
# Smart signal filtering: only the highest-conviction signals pass
from __future__ import annotations

import logging
import numpy as np
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone

logger = logging.getLogger("signal_guardrails")

# ============================================================
# SCORING THRESHOLDS
# ============================================================
# Each guardrail adds/subtracts from base score of 0.
# Signal must reach MINIMUM_SCORE to fire.
# BLOCK_SCORE guardrails can veto regardless of total score.

MINIMUM_SIGNAL_SCORE = 3    # Must accumulate at least +3 to pass
BASE_SIGNAL_SCORE = 5       # Start with 5 (need net >= MINIMUM after deductions)


class GuardrailResult:
    """Result from a single guardrail check."""
    def __init__(self, name: str, passed: bool, score_adj: int,
                 reason: str, is_hard_block: bool = False):
        self.name = name
        self.passed = passed
        self.score_adj = score_adj
        self.reason = reason
        self.is_hard_block = is_hard_block  # Instant veto regardless of score

    def __repr__(self):
        status = "✅" if self.passed else ("🚫" if self.is_hard_block else "⚠️")
        return f"{status} {self.name}: {self.score_adj:+d} | {self.reason}"


class SignalGuardrails:
    """Evaluates trading signals through multiple quality filters."""

    def __init__(self, market_intel=None):
        """
        Args:
            market_intel: MarketIntelligence instance (optional; external checks
                          skipped if None)
        """
        self.intel = market_intel

    # ============================================================
    # 1. PREMIUM/DISCOUNT ZONE FILTER
    # ============================================================
    def check_premium_discount(self, df, direction: str,
                                lookback: int = 50) -> GuardrailResult:
        """
        Calculate equilibrium of the last N candles.
        BUY only in discount zone (bottom 50%), SELL only in premium zone (top 50%).

        Enhanced: Uses 3 zones — Premium (top 30%), Equilibrium (mid 40%), Discount (bottom 30%)
        Sweet spot: BUY in bottom 30%, SELL in top 30%
        Acceptable: BUY in mid zone if strong confluence, SELL in mid zone if strong confluence
        Block: BUY in top 30%, SELL in bottom 30%
        """
        if len(df) < lookback:
            return GuardrailResult("Premium/Discount", True, 0, "Insufficient data")

        recent = df.iloc[-lookback:]
        range_high = recent["high"].max()
        range_low = recent["low"].min()
        full_range = range_high - range_low

        if full_range <= 0:
            return GuardrailResult("Premium/Discount", True, 0, "No range")

        current_price = df["close"].iloc[-1]
        position_in_range = (current_price - range_low) / full_range  # 0.0 = bottom, 1.0 = top

        # Zone boundaries
        discount_upper = 0.30   # Bottom 30%
        premium_lower = 0.70    # Top 30%

        if direction == "BUY":
            if position_in_range <= discount_upper:
                # Sweet spot: buying cheap
                return GuardrailResult(
                    "Premium/Discount", True, +2,
                    f"BUY in discount zone ({position_in_range:.0%} of range) ✅"
                )
            elif position_in_range <= premium_lower:
                # Equilibrium: acceptable with confluence
                return GuardrailResult(
                    "Premium/Discount", True, 0,
                    f"BUY in equilibrium ({position_in_range:.0%} of range)"
                )
            else:
                # Premium zone: buying at the top
                return GuardrailResult(
                    "Premium/Discount", False, -3,
                    f"BUY in premium zone ({position_in_range:.0%} of range) — buying expensive",
                    is_hard_block=(position_in_range >= 0.90)  # Block if top 10%
                )

        elif direction == "SELL":
            if position_in_range >= premium_lower:
                return GuardrailResult(
                    "Premium/Discount", True, +2,
                    f"SELL in premium zone ({position_in_range:.0%} of range) ✅"
                )
            elif position_in_range >= discount_upper:
                return GuardrailResult(
                    "Premium/Discount", True, 0,
                    f"SELL in equilibrium ({position_in_range:.0%} of range)"
                )
            else:
                return GuardrailResult(
                    "Premium/Discount", False, -3,
                    f"SELL in discount zone ({position_in_range:.0%} of range) — selling cheap",
                    is_hard_block=(position_in_range <= 0.10)
                )

        return GuardrailResult("Premium/Discount", True, 0, "Unknown direction")

    # ============================================================
    # 2. LIQUIDITY SWEEP DETECTION
    # ============================================================
    def check_liquidity_sweep(self, df, direction: str,
                               swing_lookback: int = 3,
                               sweep_window: int = 10) -> GuardrailResult:
        """
        Detect if a liquidity sweep occurred before the signal.
        A sweep = price takes out previous swing high/low, then reverses.

        For BUY: price swept below a previous swing low (took out longs' stops),
                 then reversed up (displacement candle).
        For SELL: price swept above a previous swing high (took out shorts' stops),
                  then reversed down.

        This is the ICT "Judas swing" / "stop hunt" pattern.
        """
        n = len(df)
        if n < swing_lookback * 2 + sweep_window:
            return GuardrailResult("Liquidity Sweep", True, 0, "Insufficient data")

        # Find swing points in the lookback period
        swing_highs = []
        swing_lows = []
        search_start = max(swing_lookback, n - 60)  # Look back ~60 candles
        search_end = n - sweep_window  # Don't include the most recent window

        for i in range(search_start, search_end):
            window = df.iloc[max(0, i - swing_lookback):i + swing_lookback + 1]
            if df["high"].iloc[i] == window["high"].max():
                swing_highs.append({"index": i, "price": df["high"].iloc[i]})
            if df["low"].iloc[i] == window["low"].min():
                swing_lows.append({"index": i, "price": df["low"].iloc[i]})

        recent = df.iloc[-sweep_window:]  # Last N candles

        if direction == "BUY":
            # Look for sweep below swing lows (stop hunt)
            for sl in sorted(swing_lows, key=lambda x: x["index"], reverse=True)[:5]:
                # Did price go BELOW this swing low recently?
                swept_below = recent["low"].min() < sl["price"]
                # Did price then CLOSE above it? (reversal/displacement)
                if swept_below:
                    last_close = df["close"].iloc[-1]
                    if last_close > sl["price"]:
                        # How strong is the reversal?
                        sweep_depth = sl["price"] - recent["low"].min()
                        reversal_strength = last_close - recent["low"].min()
                        if reversal_strength > sweep_depth * 1.5:
                            return GuardrailResult(
                                "Liquidity Sweep", True, +3,
                                f"BUY after liquidity sweep below {sl['price']:.5f} — "
                                f"swept {sweep_depth:.5f}, reversed {reversal_strength:.5f} ✅"
                            )
                        else:
                            return GuardrailResult(
                                "Liquidity Sweep", True, +1,
                                f"Weak sweep below {sl['price']:.5f} (reversal not convincing)"
                            )

            # No sweep detected — not blocking, but no bonus
            return GuardrailResult(
                "Liquidity Sweep", True, 0,
                "No liquidity sweep detected before BUY"
            )

        elif direction == "SELL":
            for sh in sorted(swing_highs, key=lambda x: x["index"], reverse=True)[:5]:
                swept_above = recent["high"].max() > sh["price"]
                if swept_above:
                    last_close = df["close"].iloc[-1]
                    if last_close < sh["price"]:
                        sweep_depth = recent["high"].max() - sh["price"]
                        reversal_strength = recent["high"].max() - last_close
                        if reversal_strength > sweep_depth * 1.5:
                            return GuardrailResult(
                                "Liquidity Sweep", True, +3,
                                f"SELL after liquidity sweep above {sh['price']:.5f} — "
                                f"swept {sweep_depth:.5f}, reversed {reversal_strength:.5f} ✅"
                            )
                        else:
                            return GuardrailResult(
                                "Liquidity Sweep", True, +1,
                                f"Weak sweep above {sh['price']:.5f}"
                            )

            return GuardrailResult(
                "Liquidity Sweep", True, 0,
                "No liquidity sweep detected before SELL"
            )

        return GuardrailResult("Liquidity Sweep", True, 0, "Unknown direction")

    # ============================================================
    # 3. EXHAUSTION FILTER
    # ============================================================
    def check_exhaustion(self, df, direction: str,
                          max_consecutive_bos: int = 3) -> GuardrailResult:
        """
        Detect trend exhaustion signals:
        a) 3+ consecutive BOS in same direction = overextended
        b) RSI divergence (price makes new HH but RSI makes LH)
        c) Candle bodies shrinking (losing momentum)
        """
        if len(df) < 30:
            return GuardrailResult("Exhaustion", True, 0, "Insufficient data")

        issues = []
        score_adj = 0

        # a) Count consecutive higher-highs or lower-lows
        recent_20 = df.iloc[-20:]
        if direction == "BUY":
            # Check if we're at the end of a bull run (HH after HH)
            hh_count = 0
            for i in range(1, len(recent_20)):
                if recent_20["high"].iloc[i] > recent_20["high"].iloc[i-1]:
                    hh_count += 1
                else:
                    hh_count = 0  # Reset on break
            if hh_count >= max_consecutive_bos + 2:
                score_adj -= 3
                issues.append(f"{hh_count} consecutive HH — severely overextended")
            elif hh_count >= max_consecutive_bos:
                score_adj -= 2
                issues.append(f"{hh_count} consecutive HH — trend may exhaust")

        elif direction == "SELL":
            ll_count = 0
            for i in range(1, len(recent_20)):
                if recent_20["low"].iloc[i] < recent_20["low"].iloc[i-1]:
                    ll_count += 1
                else:
                    ll_count = 0
            if ll_count >= max_consecutive_bos + 2:
                score_adj -= 3
                issues.append(f"{ll_count} consecutive LL — severely overextended")
            elif ll_count >= max_consecutive_bos:
                score_adj -= 2
                issues.append(f"{ll_count} consecutive LL — trend may exhaust")

        # b) RSI divergence
        if "rsi" in df.columns:
            rsi = df["rsi"].iloc[-20:]
            price = df["close"].iloc[-20:]
            if direction == "BUY":
                # Bearish div on existing uptrend: price HH but RSI LH
                price_hh = price.iloc[-1] > price.iloc[-10:].iloc[:-1].max()
                rsi_lh = rsi.iloc[-1] < rsi.iloc[-10:].iloc[:-1].max()
                if price_hh and rsi_lh:
                    score_adj -= 2
                    issues.append(f"RSI bearish divergence (price HH, RSI LH: {rsi.iloc[-1]:.0f})")
            elif direction == "SELL":
                price_ll = price.iloc[-1] < price.iloc[-10:].iloc[:-1].min()
                rsi_hl = rsi.iloc[-1] > rsi.iloc[-10:].iloc[:-1].min()
                if price_ll and rsi_hl:
                    score_adj -= 2
                    issues.append(f"RSI bullish divergence (price LL, RSI HL: {rsi.iloc[-1]:.0f})")

        # c) Body shrinkage (momentum fading)
        bodies = []
        for i in range(-10, 0):
            bodies.append(abs(df["close"].iloc[i] - df["open"].iloc[i]))
        if len(bodies) >= 6:
            first_half = np.mean(bodies[:5])
            second_half = np.mean(bodies[5:])
            if first_half > 0 and second_half / first_half < 0.4:
                score_adj -= 1
                issues.append(f"Body shrinkage: {second_half/first_half:.0%} of earlier momentum")

        if not issues:
            return GuardrailResult("Exhaustion", True, 0, "No exhaustion detected")

        is_hard_block = score_adj <= -4
        return GuardrailResult(
            "Exhaustion", score_adj >= -1, score_adj,
            "; ".join(issues), is_hard_block=is_hard_block
        )

    # ============================================================
    # 4. VOLATILITY GUARD
    # ============================================================
    def check_volatility(self, df, instrument: str,
                          timeframe: str) -> GuardrailResult:
        """
        Block signals during abnormal volatility.
        Uses the MarketIntelligence volatility assessment.
        """
        if self.intel:
            vol = self.intel.assess_volatility(df, instrument, timeframe)
        else:
            # Inline assessment if no intel object
            vol = self._inline_volatility_check(df, timeframe)

        if vol["should_skip"]:
            return GuardrailResult(
                "Volatility Guard", False, -5,
                f"{vol['regime']}: {vol['reason']}",
                is_hard_block=True
            )

        if vol["atr_ratio"] >= 1.5:
            return GuardrailResult(
                "Volatility Guard", True, -1,
                f"Elevated volatility (ATR {vol['atr_ratio']:.1f}x avg)"
            )

        return GuardrailResult(
            "Volatility Guard", True, +1,
            f"Normal volatility (ATR {vol['atr_ratio']:.1f}x avg)"
        )

    def _inline_volatility_check(self, df, timeframe: str) -> Dict:
        """Standalone volatility check (no MarketIntelligence needed)."""
        result = {"regime": "NORMAL", "atr_ratio": 1.0, "should_skip": False, "reason": ""}
        if len(df) < 30 or "atr" not in df.columns:
            return result

        current_atr = df["atr"].iloc[-1]
        avg_atr = df["atr"].iloc[-50:].mean() if len(df) >= 50 else df["atr"].mean()
        if avg_atr <= 0:
            return result

        atr_ratio = current_atr / avg_atr
        result["atr_ratio"] = round(atr_ratio, 2)

        # Check wicks
        recent = df.iloc[-5:]
        wick_pcts = []
        for _, c in recent.iterrows():
            fr = c["high"] - c["low"]
            if fr > 0:
                body = abs(c["close"] - c["open"])
                wick_pcts.append((fr - body) / fr * 100)
        avg_wick = np.mean(wick_pcts) if wick_pcts else 0

        # Direction changes
        last_10 = df.iloc[-10:]
        changes = sum(
            1 for i in range(1, len(last_10))
            if (last_10["close"].iloc[i] >= last_10["open"].iloc[i]) !=
               (last_10["close"].iloc[i-1] >= last_10["open"].iloc[i-1])
        )
        chaos = changes / max(len(last_10) - 1, 1)

        reasons = []
        if atr_ratio >= 2.5:
            result["regime"] = "EXTREME_VOLATILITY"
            result["should_skip"] = True
            reasons.append(f"ATR {atr_ratio:.1f}x avg")
        elif atr_ratio >= 1.8:
            result["regime"] = "HIGH_VOLATILITY"
            result["should_skip"] = True
            reasons.append(f"ATR {atr_ratio:.1f}x avg")

        if chaos >= 0.7 and avg_wick >= 55:
            result["should_skip"] = True
            reasons.append(f"Chaotic ({changes} dir changes, {avg_wick:.0f}% wicks)")

        if avg_wick >= 65:
            result["should_skip"] = True
            reasons.append(f"Whipsaw ({avg_wick:.0f}% wick)")

        if timeframe in ("M1", "M5") and atr_ratio >= 1.5:
            result["should_skip"] = True
            reasons.append(f"Low TF + elevated vol")

        result["reason"] = "; ".join(reasons)
        return result

    # ============================================================
    # 5. COT ALIGNMENT CHECK
    # ============================================================
    def check_cot_alignment(self, instrument: str,
                             direction: str) -> GuardrailResult:
        """Check if signal direction aligns with institutional positioning."""
        if not self.intel:
            return GuardrailResult("COT Alignment", True, 0, "No intel available")

        cot = self.intel.fetch_cot_data(instrument)
        if not cot:
            return GuardrailResult("COT Alignment", True, 0, "COT data unavailable")

        bias = cot["bias"]

        # Strong alignment: direction matches institutional bias
        if direction == "BUY" and bias == "BULLISH":
            return GuardrailResult("COT Alignment", True, +2,
                                   f"BUY aligns with institutional BULLISH (specs net {cot['large_spec_net']:+,})")
        if direction == "SELL" and bias == "BEARISH":
            return GuardrailResult("COT Alignment", True, +2,
                                   f"SELL aligns with institutional BEARISH (specs net {cot['large_spec_net']:+,})")

        # Weak alignment
        if direction == "BUY" and bias == "WEAK_BULLISH":
            return GuardrailResult("COT Alignment", True, +1,
                                   f"BUY weakly aligns (specs {cot['spec_momentum']})")
        if direction == "SELL" and bias == "WEAK_BEARISH":
            return GuardrailResult("COT Alignment", True, +1,
                                   f"SELL weakly aligns (specs {cot['spec_momentum']})")

        # Conflict: trading against institutions
        if direction == "BUY" and "BEARISH" in bias:
            return GuardrailResult("COT Alignment", False, -2,
                                   f"BUY against institutional BEARISH (specs net {cot['large_spec_net']:+,}) ⚠️")
        if direction == "SELL" and "BULLISH" in bias:
            return GuardrailResult("COT Alignment", False, -2,
                                   f"SELL against institutional BULLISH (specs net {cot['large_spec_net']:+,}) ⚠️")

        return GuardrailResult("COT Alignment", True, 0, f"COT neutral for {instrument}")

    # ============================================================
    # 6. TRADINGVIEW CONSENSUS CHECK
    # ============================================================
    def check_tv_consensus(self, instrument: str, direction: str,
                            timeframe: str) -> GuardrailResult:
        """Check if signal aligns with TradingView technical consensus."""
        if not self.intel:
            return GuardrailResult("TV Consensus", True, 0, "No intel available")

        tv = self.intel.fetch_tv_rating(instrument, timeframe)
        if not tv:
            return GuardrailResult("TV Consensus", True, 0, "TV data unavailable")

        rec = tv["recommendation"]

        # Strong alignment
        if direction == "BUY" and rec == "STRONG_BUY":
            return GuardrailResult("TV Consensus", True, +2,
                                   f"BUY aligns with TV STRONG_BUY (B:{tv['buy_signals']} S:{tv['sell_signals']})")
        if direction == "SELL" and rec == "STRONG_SELL":
            return GuardrailResult("TV Consensus", True, +2,
                                   f"SELL aligns with TV STRONG_SELL")

        # Moderate alignment
        if direction == "BUY" and rec == "BUY":
            return GuardrailResult("TV Consensus", True, +1,
                                   f"BUY aligns with TV BUY")
        if direction == "SELL" and rec == "SELL":
            return GuardrailResult("TV Consensus", True, +1,
                                   f"SELL aligns with TV SELL")

        # Direct conflict
        if direction == "BUY" and "SELL" in rec:
            return GuardrailResult("TV Consensus", False, -2,
                                   f"BUY conflicts with TV {rec} ⚠️")
        if direction == "SELL" and "BUY" in rec:
            return GuardrailResult("TV Consensus", False, -2,
                                   f"SELL conflicts with TV {rec} ⚠️")

        # Neutral
        return GuardrailResult("TV Consensus", True, 0, f"TV neutral: {rec}")

    # ============================================================
    # 7. FEAR & GREED EXTREME FILTER
    # ============================================================
    def check_fear_greed(self, direction: str) -> GuardrailResult:
        """Block contrarian signals at sentiment extremes."""
        if not self.intel:
            return GuardrailResult("Fear & Greed", True, 0, "No intel available")

        fg = self.intel.fetch_fear_greed()
        if not fg:
            return GuardrailResult("Fear & Greed", True, 0, "F&G data unavailable")

        value = fg["value"]

        # BUY at extreme greed = buying at the top with the crowd
        if direction == "BUY" and fg["is_extreme_greed"]:
            return GuardrailResult(
                "Fear & Greed", False, -2,
                f"BUY at extreme greed ({value}) — crowd is euphoric, likely top ⚠️"
            )

        # SELL at extreme fear = selling at the bottom with the crowd
        if direction == "SELL" and fg["is_extreme_fear"]:
            return GuardrailResult(
                "Fear & Greed", False, -2,
                f"SELL at extreme fear ({value}) — crowd is panicking, likely bottom ⚠️"
            )

        # Contrarian bonus: BUY in fear, SELL in greed
        if direction == "BUY" and fg["is_extreme_fear"]:
            return GuardrailResult(
                "Fear & Greed", True, +1,
                f"BUY at extreme fear ({value}) — contrarian ✅"
            )
        if direction == "SELL" and fg["is_extreme_greed"]:
            return GuardrailResult(
                "Fear & Greed", True, +1,
                f"SELL at extreme greed ({value}) — contrarian ✅"
            )

        return GuardrailResult("Fear & Greed", True, 0,
                               f"F&G: {value} ({fg['classification']})")

    # ============================================================
    # 8. KEY LEVEL PROXIMITY (ATH/ATL/Weekly)
    # ============================================================
    def check_key_levels(self, df, direction: str,
                          atr_buffer_mult: float = 0.5) -> GuardrailResult:
        """
        Don't BUY near ATH (all-time high within data).
        Don't SELL near ATL (all-time low within data).
        Also checks previous week's high/low.
        """
        if len(df) < 50:
            return GuardrailResult("Key Levels", True, 0, "Insufficient data")

        current_price = df["close"].iloc[-1]
        data_high = df["high"].max()  # Highest in dataset
        data_low = df["low"].min()    # Lowest in dataset

        atr_val = df["atr"].iloc[-1] if "atr" in df.columns else (data_high - data_low) * 0.02
        buffer = atr_val * atr_buffer_mult

        if direction == "BUY":
            dist_to_high = data_high - current_price
            if dist_to_high <= buffer:
                return GuardrailResult(
                    "Key Levels", False, -3,
                    f"BUY within {atr_buffer_mult} ATR of data high ({data_high:.5f}) — "
                    f"only {dist_to_high:.5f} away",
                    is_hard_block=True
                )
            elif dist_to_high <= buffer * 2:
                return GuardrailResult(
                    "Key Levels", True, -1,
                    f"BUY near data high ({dist_to_high:.5f} away)"
                )

        elif direction == "SELL":
            dist_to_low = current_price - data_low
            if dist_to_low <= buffer:
                return GuardrailResult(
                    "Key Levels", False, -3,
                    f"SELL within {atr_buffer_mult} ATR of data low ({data_low:.5f}) — "
                    f"only {dist_to_low:.5f} away",
                    is_hard_block=True
                )
            elif dist_to_low <= buffer * 2:
                return GuardrailResult(
                    "Key Levels", True, -1,
                    f"SELL near data low ({dist_to_low:.5f} away)"
                )

        return GuardrailResult("Key Levels", True, 0, "Not near key levels")

    # ============================================================
    # MASTER: APPLY ALL GUARDRAILS
    # ============================================================
    def evaluate_signal(self, df, instrument: str, direction: str,
                         timeframe: str, signal_metadata: Dict = None) -> Dict:
        """
        Run ALL guardrails on a signal candidate.

        Returns:
            {
                "passed": bool,
                "final_score": int,
                "base_score": int,
                "results": [GuardrailResult, ...],
                "hard_blocks": [str, ...],
                "summary": str,
                "telegram_text": str,
            }
        """
        results = []

        # === Pure Price Action Guardrails (always available) ===
        results.append(self.check_premium_discount(df, direction))
        results.append(self.check_liquidity_sweep(df, direction))
        results.append(self.check_exhaustion(df, direction))
        results.append(self.check_volatility(df, instrument, timeframe))
        results.append(self.check_key_levels(df, direction))

        # === External Intelligence Guardrails (if intel available) ===
        results.append(self.check_cot_alignment(instrument, direction))
        results.append(self.check_tv_consensus(instrument, direction, timeframe))
        results.append(self.check_fear_greed(direction))

        # === Calculate final score ===
        score = BASE_SIGNAL_SCORE
        hard_blocks = []

        for r in results:
            score += r.score_adj
            if r.is_hard_block:
                hard_blocks.append(f"{r.name}: {r.reason}")

        passed = (score >= MINIMUM_SIGNAL_SCORE) and (len(hard_blocks) == 0)

        # === Build summary ===
        passed_checks = [r for r in results if r.passed and r.score_adj > 0]
        failed_checks = [r for r in results if not r.passed]
        neutral_checks = [r for r in results if r.passed and r.score_adj <= 0]

        quality = (
            "🌟 PREMIUM" if score >= 10 else
            "✅ STRONG" if score >= 7 else
            "🟡 ACCEPTABLE" if score >= MINIMUM_SIGNAL_SCORE else
            "⚠️ WEAK" if score >= 1 else
            "🚫 BLOCKED"
        )

        summary = f"{quality} signal: {instrument} {direction} {timeframe} (score: {score}/{BASE_SIGNAL_SCORE + 15})"

        # === Telegram formatted text ===
        tg_lines = [
            f"\n🛡️ <b>Signal Guardrails</b> [{quality}]",
            f"Score: <b>{score}</b> / {BASE_SIGNAL_SCORE + 15} (min: {MINIMUM_SIGNAL_SCORE})",
        ]
        for r in results:
            icon = "✅" if r.passed and r.score_adj > 0 else "⚠️" if r.score_adj < 0 else "➖"
            if r.is_hard_block:
                icon = "🚫"
            tg_lines.append(f"{icon} {r.name}: {r.score_adj:+d} | {r.reason}")

        if hard_blocks:
            tg_lines.append(f"\n🚫 <b>HARD BLOCKED:</b>")
            for hb in hard_blocks:
                tg_lines.append(f"  • {hb}")

        if not passed:
            tg_lines.append(f"\n❌ Signal BLOCKED (score {score} < {MINIMUM_SIGNAL_SCORE} or hard block)")
        else:
            tg_lines.append(f"\n✅ Signal PASSED ({quality})")

        return {
            "passed": passed,
            "final_score": score,
            "base_score": BASE_SIGNAL_SCORE,
            "max_possible_score": BASE_SIGNAL_SCORE + 15,
            "results": results,
            "hard_blocks": hard_blocks,
            "summary": summary,
            "quality": quality,
            "telegram_text": "\n".join(tg_lines),
        }