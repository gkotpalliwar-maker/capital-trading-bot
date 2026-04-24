#!/usr/bin/env python3
"""v2.8.0 Patcher: Integrate signal guardrails into scanner.py

This patcher:
1. Adds import for MarketIntelligence and SignalGuardrails
2. Initializes intel + guardrails after client setup
3. Wraps signal generation with guardrail evaluation
4. Adds guardrail summary to Telegram notifications
5. Installs tradingview-ta if missing
"""
import os
import re
import sys
import subprocess

print("v2.8.0 Smart Signals Patcher")
print("=" * 55)

# ── Install tradingview-ta if missing ──
try:
    import tradingview_ta
    print(f"  tradingview-ta: already installed (v{tradingview_ta.__version__})")
except ImportError:
    print("  Installing tradingview-ta...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "tradingview-ta", "-q"])
    print("  tradingview-ta installed")

# ── Patch scanner.py ──
scanner_path = os.path.join(os.getcwd(), "bot", "scanner.py")
if not os.path.exists(scanner_path):
    print(f"  ERROR: {scanner_path} not found")
    sys.exit(1)

with open(scanner_path) as f:
    code = f.read()

orig = code
changes = []

# 1) Add imports after existing bot imports
import_block = """
# v2.8.0: Smart signal intelligence
try:
    from bot.market_intelligence import MarketIntelligence
    from bot.signal_guardrails import SignalGuardrails
    HAS_GUARDRAILS = True
except ImportError as e:
    logger.warning(f"Guardrails not available: {e}")
    HAS_GUARDRAILS = False
"""

if "signal_guardrails" not in code:
    # Insert after the last "from bot." import line
    # Find all "from bot." import lines
    last_bot_import = None
    for m in re.finditer(r"^(?:from bot\.|import bot\.)[^
]+$", code, re.MULTILINE):
        last_bot_import = m

    if last_bot_import:
        insert_pos = last_bot_import.end()
        code = code[:insert_pos] + import_block + code[insert_pos:]
        changes.append("Added guardrails imports")
    else:
        # Fallback: add after "import logging"
        code = code.replace("import logging", "import logging" + import_block, 1)
        changes.append("Added guardrails imports (after logging)")

# 2) Initialize intel + guardrails
# Look for where client is initialized (after Capital.com login)
init_block = """
    # v2.8.0: Initialize smart signal guardrails
    if HAS_GUARDRAILS:
        _intel = MarketIntelligence()
        _guardrails = SignalGuardrails(market_intel=_intel)
        logger.info("Smart signal guardrails initialized")
    else:
        _intel = None
        _guardrails = None
"""

if "_guardrails" not in code:
    # Insert after "client = " or scanner init
    # Look for where scanning loop starts
    scanner_init_patterns = [
        r"(\s+logger\.info\([^)]*[Ss]canning[^)]*\))",
        r"(\s+logger\.info\([^)]*[Ss]tarting[^)]*\))",
        r"(\s+while\s+True:)",
    ]
    inserted = False
    for pattern in scanner_init_patterns:
        m = re.search(pattern, code)
        if m:
            code = code[:m.start()] + init_block + code[m.start():]
            changes.append("Added guardrails initialization")
            inserted = True
            break
    if not inserted:
        changes.append("WARNING: Could not find scanner init point — manual init needed")

# 3) Add guardrail check in signal flow
# This wraps the signal notification/execution
guardrail_check = """
            # v2.8.0: Guardrail evaluation
            if HAS_GUARDRAILS and _guardrails is not None:
                try:
                    _eval = _guardrails.evaluate_signal(
                        df=df, instrument=inst_key, direction=sig.direction,
                        timeframe=tf
                    )
                    sig.metadata["guardrail_score"] = _eval["final_score"]
                    sig.metadata["guardrail_quality"] = _eval["quality"]
                    sig.metadata["guardrail_passed"] = _eval["passed"]

                    # Attach intel report if available
                    if _intel is not None:
                        _report = _intel.get_full_report(inst_key, tf, df=df)
                        sig.metadata["intel_report"] = _intel.format_telegram(_report, sig.direction)

                    if not _eval["passed"]:
                        logger.info(f"Signal BLOCKED by guardrails: {inst_key} {tf} {sig.direction} "
                                   f"(score: {_eval['final_score']}, blocks: {_eval['hard_blocks']})")
                        # Add guardrail text to the signal for transparency
                        sig.metadata["guardrail_text"] = _eval["telegram_text"]
                        # Skip execution but still notify (with BLOCKED label)
                        continue
                    else:
                        sig.metadata["guardrail_text"] = _eval["telegram_text"]
                        logger.info(f"Signal PASSED guardrails: {inst_key} {tf} {sig.direction} "
                                   f"(score: {_eval['final_score']}, quality: {_eval['quality']})")
                except Exception as e:
                    logger.error(f"Guardrail evaluation error: {e}")
                    # On error, let signal through (fail-open)
"""

if "guardrail_score" not in code:
    # Find the signal processing section — look for where signals are iterated
    signal_patterns = [
        r"(\s+for sig in (?:signals|smc_signals|all_signals)[^:]*:)",
        r"(\s+if sig\.direction)",
    ]
    for pattern in signal_patterns:
        m = re.search(pattern, code)
        if m:
            # Insert guardrail check right after the for loop starts
            insert_at = m.end()
            code = code[:insert_at] + guardrail_check + code[insert_at:]
            changes.append("Added guardrail evaluation in signal loop")
            break

# 4) Verify syntax
try:
    compile(code, scanner_path, "exec")
    print("  ✅ scanner.py compiles after patching")
except SyntaxError as e:
    print(f"  ❌ Syntax error after patching: {e}")
    print(f"  Reverting to original...")
    code = orig
    changes = ["REVERTED — syntax error"]

# ── Write ──
if code != orig:
    # Backup
    with open(scanner_path + ".v2.7.2.bak", "w") as f:
        f.write(orig)
    print(f"  Backed up original to scanner.py.v2.7.2.bak")

    with open(scanner_path, "w") as f:
        f.write(code)
    print(f"  scanner.py updated with {len(changes)} changes:")
    for c in changes:
        print(f"    • {c}")
else:
    print("  No changes needed (already patched or no match)")

print("
" + "=" * 55)
print("v2.8.0 patcher complete.")
print("Run: sudo systemctl restart trading-bot")
