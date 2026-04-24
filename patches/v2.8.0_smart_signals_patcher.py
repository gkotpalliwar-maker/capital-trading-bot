#!/usr/bin/env python3
"""v2.8.0 Patcher: Integrate signal guardrails into scanner.py

This patcher:
1. Adds import for MarketIntelligence and SignalGuardrails
2. Initializes intel + guardrails after client setup
3. Wraps signal generation with guardrail evaluation
4. Installs tradingview-ta if missing
"""
import os
import sys
import subprocess

print("v2.8.0 Smart Signals Patcher")
print("=" * 55)

# Install tradingview-ta if missing
try:
    import tradingview_ta
    print(f"  tradingview-ta: already installed (v{tradingview_ta.__version__})")
except ImportError:
    print("  Installing tradingview-ta...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "tradingview-ta", "-q"])
    print("  tradingview-ta installed")

# Patch scanner.py
scanner_path = os.path.join(os.getcwd(), "bot", "scanner.py")
if not os.path.exists(scanner_path):
    print(f"  ERROR: {scanner_path} not found")
    sys.exit(1)

with open(scanner_path) as f:
    lines = f.readlines()

orig_code = "".join(lines)
changes = []

# 1) Find last "from bot." import line and add guardrails import after it
if "signal_guardrails" not in orig_code:
    last_bot_import_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("from bot.") or stripped.startswith("import bot."):
            last_bot_import_idx = i

    import_block = [
        "\n",
        "# v2.8.0: Smart signal intelligence\n",
        "try:\n",
        "    from bot.market_intelligence import MarketIntelligence\n",
        "    from bot.signal_guardrails import SignalGuardrails\n",
        "    HAS_GUARDRAILS = True\n",
        "except ImportError as e:\n",
        "    logger.warning(f\"Guardrails not available: {e}\")\n",
        "    HAS_GUARDRAILS = False\n",
        "\n",
    ]

    if last_bot_import_idx >= 0:
        for j, imp_line in enumerate(import_block):
            lines.insert(last_bot_import_idx + 1 + j, imp_line)
        changes.append(f"Added guardrails imports after line {last_bot_import_idx + 1}")
    else:
        # Fallback: add after "import logging"
        for i, line in enumerate(lines):
            if "import logging" in line:
                for j, imp_line in enumerate(import_block):
                    lines.insert(i + 1 + j, imp_line)
                changes.append("Added guardrails imports after import logging")
                break

# 2) Find scanning loop and add guardrails initialization before it
code_so_far = "".join(lines)
if "_guardrails" not in code_so_far:
    init_block = [
        "\n",
        "    # v2.8.0: Initialize smart signal guardrails\n",
        "    if HAS_GUARDRAILS:\n",
        "        _intel = MarketIntelligence()\n",
        "        _guardrails = SignalGuardrails(market_intel=_intel)\n",
        "        logger.info(\"Smart signal guardrails initialized\")\n",
        "    else:\n",
        "        _intel = None\n",
        "        _guardrails = None\n",
        "\n",
    ]

    inserted = False
    for i, line in enumerate(lines):
        if "while True:" in line and not line.strip().startswith("#"):
            for j, init_line in enumerate(init_block):
                lines.insert(i + j, init_line)
            changes.append(f"Added guardrails initialization before line {i + 1}")
            inserted = True
            break
    if not inserted:
        changes.append("WARNING: Could not find scanner loop — manual init needed")

# 3) Find signal notification section and add guardrail check
code_so_far = "".join(lines)
if "guardrail_score" not in code_so_far:
    # Look for the line where signals are iterated
    guard_block = [
        "\n",
        "                # v2.8.0: Guardrail evaluation\n",
        "                if HAS_GUARDRAILS and _guardrails is not None:\n",
        "                    try:\n",
        "                        _eval = _guardrails.evaluate_signal(\n",
        "                            df=df, instrument=inst_key, direction=sig.direction,\n",
        "                            timeframe=tf\n",
        "                        )\n",
        "                        sig.metadata[\"guardrail_score\"] = _eval[\"final_score\"]\n",
        "                        sig.metadata[\"guardrail_quality\"] = _eval[\"quality\"]\n",
        "                        sig.metadata[\"guardrail_text\"] = _eval[\"telegram_text\"]\n",
        "                        if not _eval[\"passed\"]:\n",
        "                            logger.info(f\"Signal BLOCKED: {inst_key} {tf} {sig.direction} \"\n",
        "                                       f\"(score:{_eval['final_score']}, blocks:{_eval['hard_blocks']})\")\n",
        "                            continue\n",
        "                        else:\n",
        "                            logger.info(f\"Signal PASSED: {inst_key} {tf} {sig.direction} \"\n",
        "                                       f\"(score:{_eval['final_score']}, quality:{_eval['quality']})\")\n",
        "                    except Exception as e:\n",
        "                        logger.error(f\"Guardrail error: {e}\")\n",
        "\n",
    ]

    inserted = False
    for i, line in enumerate(lines):
        # Look for "for sig in" pattern inside signal processing
        if "for sig in" in line and ("signals" in line or "smc_signal" in line):
            for j, g_line in enumerate(guard_block):
                lines.insert(i + 1 + j, g_line)
            changes.append(f"Added guardrail check after line {i + 1}")
            inserted = True
            break
    if not inserted:
        changes.append("WARNING: Could not find signal loop — manual guardrail wiring needed")

# Verify syntax
new_code = "".join(lines)
try:
    compile(new_code, scanner_path, "exec")
    print("  \u2705 scanner.py compiles after patching")
except SyntaxError as e:
    print(f"  \u274c Syntax error after patching: {e}")
    print(f"  Reverting to original...")
    new_code = orig_code
    changes = ["REVERTED — syntax error"]

# Write
if new_code != orig_code:
    with open(scanner_path + ".v2.7.2.bak", "w") as f:
        f.write(orig_code)
    print(f"  Backed up original to scanner.py.v2.7.2.bak")

    with open(scanner_path, "w") as f:
        f.write(new_code)
    print(f"  scanner.py updated with {len(changes)} changes:")
    for c in changes:
        print(f"    - {c}")
else:
    print("  No changes needed (already patched or reverted)")

print()
print("=" * 55)
print("v2.8.0 patcher complete.")
print("Run: sudo systemctl restart trading-bot")
