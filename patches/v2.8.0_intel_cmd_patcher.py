#!/usr/bin/env python3
"""v2.8.0 Patcher: Register /intel command in telegram_bot.py"""
import os, sys

print("v2.8.0 /intel Command Registration")
print("=" * 55)

path = os.path.join(os.getcwd(), "bot", "telegram_bot.py")
if not os.path.exists(path):
    print(f"  ERROR: {path} not found")
    sys.exit(1)

with open(path) as f:
    code = f.read()

orig = code
changes = []

# 1) Add import after existing try/except imports (near top)
#    Match: after "from news_filter import" block or similar
IMPORT_ANCHOR = "from telegram.ext import ("
IMPORT_ADDITION = """from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes

# v2.8.0: Market intelligence command
try:
    from intel_commands import intel_cmd
    HAS_INTEL = True
except ImportError:
    HAS_INTEL = False"""

# Simpler: just add import before def setup_telegram_app
SETUP_ANCHOR = "def setup_telegram_app():"
IMPORT_BLOCK = """# v2.8.0: Market intelligence command
try:
    from intel_commands import intel_cmd
    HAS_INTEL = True
except ImportError:
    HAS_INTEL = False

def setup_telegram_app():"""

if "intel_cmd" not in code:
    if SETUP_ANCHOR in code:
        code = code.replace(SETUP_ANCHOR, IMPORT_BLOCK, 1)
        changes.append("Added intel_cmd import before setup_telegram_app()")
    else:
        changes.append("WARNING: Could not find setup_telegram_app anchor")
else:
    changes.append("intel_cmd import already present (skipped)")

# 2) Add handler registration after trailing command (line 845)
HANDLER_ANCHOR = '    app.add_handler(CommandHandler("trailing", trailing_cmd))'
HANDLER_REPLACEMENT = """    app.add_handler(CommandHandler("trailing", trailing_cmd))

    # v2.8.0: Market intelligence
    if HAS_INTEL:
        app.add_handler(CommandHandler("intel", intel_cmd))"""

if "intel" not in code or 'CommandHandler("intel"' not in code:
    if HANDLER_ANCHOR in code:
        code = code.replace(HANDLER_ANCHOR, HANDLER_REPLACEMENT, 1)
        changes.append("Added /intel handler after /trailing")
    else:
        changes.append("WARNING: Could not find trailing handler anchor")
else:
    changes.append("/intel handler already present (skipped)")

# Verify
try:
    compile(code, path, "exec")
    print("  \u2705 telegram_bot.py compiles after patching")
except SyntaxError as e:
    print(f"  \u274c Syntax error: {e}")
    print("  Reverting...")
    code = orig
    changes = ["REVERTED due to syntax error"]

if code != orig:
    with open(path + ".v2.7.2.bak", "w") as f:
        f.write(orig)
    print("  Backed up to telegram_bot.py.v2.7.2.bak")
    with open(path, "w") as f:
        f.write(code)
    print(f"  telegram_bot.py updated ({len(changes)} changes):")
    for c in changes:
        print(f"    - {c}")
else:
    for c in changes:
        print(f"    {c}")

print()
print("=" * 55)
print("Done. Run: sudo systemctl restart trading-bot")
