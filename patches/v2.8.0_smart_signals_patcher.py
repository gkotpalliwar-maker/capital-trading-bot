#!/usr/bin/env python3
"""v2.8.0 FINAL Patcher: Exact string replacement on scanner.py"""
import os, sys

print("v2.8.0 Smart Signals Patcher (FINAL)")
print("=" * 55)

scanner_path = os.path.join(os.getcwd(), "bot", "scanner.py")
if not os.path.exists(scanner_path):
    print(f"  ERROR: {scanner_path} not found")
    sys.exit(1)

with open(scanner_path) as f:
    code = f.read()

orig = code
changes = []

# ================================================================
# 1) Add import + init AFTER "    check_news_risk = None"
#    This is the last import fallback at module level (~line 38)
#    Uses DIRECT imports (not from bot.xxx) matching scanner.py style
# ================================================================

IMPORT_ANCHOR = "    check_news_risk = None"

IMPORT_REPLACEMENT = """    check_news_risk = None

# v2.8.0: Smart signal intelligence
try:
    from market_intelligence import MarketIntelligence
    from signal_guardrails import SignalGuardrails
    _intel = MarketIntelligence()
    _guardrails = SignalGuardrails(market_intel=_intel)
    HAS_GUARDRAILS = True
    logger.info("Smart signal guardrails initialized")
except ImportError as e:
    logger.warning(f"Guardrails not available: {e}")
    HAS_GUARDRAILS = False
    _guardrails = None"""

if IMPORT_ANCHOR in code and "signal_guardrails" not in code:
    code = code.replace(IMPORT_ANCHOR, IMPORT_REPLACEMENT, 1)
    changes.append("Added guardrails import + init after news_filter fallback")
elif "signal_guardrails" in code:
    changes.append("Guardrails import already present (skipped)")
else:
    changes.append("WARNING: Could not find import anchor")

# ================================================================
# 2) Add guardrail check inside signal loop
#    AFTER "for sig in signals:" (16 spaces indent)
#    BEFORE "zt = sig.metadata.get(...)" (20 spaces indent)
# ================================================================

LOOP_OLD = '                for sig in signals:\n                    zt = sig.metadata.get("zone_types", "")'

LOOP_NEW = """                for sig in signals:
                    # v2.8.0: Guardrail evaluation
                    if HAS_GUARDRAILS and _guardrails is not None:
                        try:
                            _eval = _guardrails.evaluate_signal(
                                df=df, instrument=inst, direction=sig.direction,
                                timeframe=tf
                            )
                            sig.metadata["guardrail_score"] = _eval["final_score"]
                            sig.metadata["guardrail_quality"] = _eval["quality"]
                            sig.metadata["guardrail_text"] = _eval["telegram_text"]
                            if not _eval["passed"]:
                                logger.info("Signal BLOCKED: %s %s %s (score:%s)"
                                           % (inst, tf, sig.direction, _eval["final_score"]))
                                continue
                            else:
                                logger.info("Signal PASSED: %s %s %s (score:%s, quality:%s)"
                                           % (inst, tf, sig.direction, _eval["final_score"], _eval["quality"]))
                        except Exception as _ge:
                            logger.error("Guardrail error: %s" % _ge)

                    zt = sig.metadata.get("zone_types", "")"""

if LOOP_OLD in code and "guardrail_score" not in code:
    code = code.replace(LOOP_OLD, LOOP_NEW, 1)
    changes.append("Added guardrail check in signal loop")
elif "guardrail_score" in code:
    changes.append("Guardrail check already present (skipped)")
else:
    changes.append("WARNING: Could not find signal loop anchor")

# ================================================================
# 3) Add guardrail data to sig_data dict
#    AFTER: sig_data["regime"] = regime.get("label", "")
# ================================================================

SIGDATA_OLD = '                    sig_data["regime"] = regime.get("label", "")'

SIGDATA_NEW = """                    sig_data["regime"] = regime.get("label", "")

                    # v2.8.0: Attach guardrail data
                    if sig.metadata.get("guardrail_score") is not None:
                        sig_data["guardrail_score"] = sig.metadata["guardrail_score"]
                        sig_data["guardrail_quality"] = sig.metadata["guardrail_quality"]
                        sig_data["guardrail_text"] = sig.metadata["guardrail_text"]"""

if SIGDATA_OLD in code and "guardrail_quality" not in code:
    code = code.replace(SIGDATA_OLD, SIGDATA_NEW, 1)
    changes.append("Added guardrail data to sig_data dict")
elif "guardrail_quality" in code:
    changes.append("Guardrail sig_data already present (skipped)")

# ================================================================
# VERIFY + WRITE
# ================================================================

try:
    compile(code, scanner_path, "exec")
    print("  \u2705 scanner.py compiles after patching")
except SyntaxError as e:
    print(f"  \u274c Syntax error: {e}")
    print("  Reverting...")
    code = orig
    changes = ["REVERTED due to syntax error"]

if code != orig:
    with open(scanner_path + ".v2.7.2.bak", "w") as f:
        f.write(orig)
    print("  Backed up to scanner.py.v2.7.2.bak")
    with open(scanner_path, "w") as f:
        f.write(code)
    print(f"  scanner.py updated ({len(changes)} changes):")
    for c in changes:
        print(f"    - {c}")
else:
    if changes:
        for c in changes:
            print(f"    {c}")
    else:
        print("  No changes needed")

print()
print("=" * 55)
print("Done. Run: sudo systemctl restart trading-bot")
