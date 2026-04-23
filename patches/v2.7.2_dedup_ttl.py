#!/usr/bin/env python3
"""v2.7.2: Per-Timeframe Dedup TTL patcher.

Patches:
  bot/config.py    — adds DEDUP_HOURS_MAP dict
  bot/risk_manager.py — uses timeframe-specific TTL

Run from /opt/trading-bot:
  python3 /tmp/v272/patches/v2.7.2_dedup_ttl.py
"""
import os
import shutil
from datetime import datetime

BOT_DIR = os.path.join(os.getcwd(), "bot")
BACKUP_DIR = os.path.join(os.getcwd(), "backups", datetime.now().strftime("%Y%m%d_%H%M%S"))
os.makedirs(BACKUP_DIR, exist_ok=True)

print("=" * 65)
print("  v2.7.2: Per-Timeframe Dedup TTL")
print("=" * 65)
changes = []

# ============================================================
# PATCH 1: config.py — add DEDUP_HOURS_MAP
# ============================================================
config_path = os.path.join(BOT_DIR, "config.py")
shutil.copy(config_path, os.path.join(BACKUP_DIR, "config.py.bak"))
print(f"\n  Backed up config.py")

with open(config_path) as f:
    config = f.read()

# Check if already patched
if "DEDUP_HOURS_MAP" in config:
    print("  config.py: DEDUP_HOURS_MAP already exists, skipping")
else:
    # Insert DEDUP_HOURS_MAP right after the DEDUP_HOURS line
    old_line = 'DEDUP_HOURS = float(os.getenv("DEDUP_HOURS", "2.0"))                 # Suppress duplicate signals within N hours'
    new_block = """DEDUP_HOURS = float(os.getenv("DEDUP_HOURS", "2.0"))                 # Suppress duplicate signals within N hours

# v2.7.2: Per-timeframe dedup TTL (~2 candles per TF)
# Override via env: DEDUP_HOURS_M15, DEDUP_HOURS_H1, DEDUP_HOURS_H4
DEDUP_HOURS_MAP = {
    "M15": float(os.getenv("DEDUP_HOURS_M15", "0.5")),   # 30 min (2 x M15 candles)
    "H1":  float(os.getenv("DEDUP_HOURS_H1",  "2.0")),   # 2 hours (2 x H1 candles)
    "H4":  float(os.getenv("DEDUP_HOURS_H4",  "8.0")),   # 8 hours (2 x H4 candles)
}"""
    if old_line in config:
        config = config.replace(old_line, new_block)
        changes.append("config.py: added DEDUP_HOURS_MAP")
    else:
        # Fallback: append at end
        config += "\n\n" + new_block.split("\n", 1)[1] + "\n"
        changes.append("config.py: appended DEDUP_HOURS_MAP (exact line not found)")

    with open(config_path, "w") as f:
        f.write(config)

    # Verify compilation
    try:
        compile(config, config_path, "exec")
        print(f"  config.py: \u2705 patched + compiles")
    except SyntaxError as e:
        print(f"  config.py: \u274c SYNTAX ERROR: {e}")
        # Restore backup
        shutil.copy(os.path.join(BACKUP_DIR, "config.py.bak"), config_path)
        print(f"  config.py: restored from backup")
        exit(1)

# ============================================================
# PATCH 2: risk_manager.py — use per-TF TTL
# ============================================================
rm_path = os.path.join(BOT_DIR, "risk_manager.py")
shutil.copy(rm_path, os.path.join(BACKUP_DIR, "risk_manager.py.bak"))
print(f"\n  Backed up risk_manager.py")

with open(rm_path) as f:
    rm = f.read()

# Fix 1: Update import to include DEDUP_HOURS_MAP
if "DEDUP_HOURS_MAP" in rm:
    print("  risk_manager.py: DEDUP_HOURS_MAP already imported, skipping import")
else:
    old_import = "COOLDOWN_AFTER_LOSSES, COOLDOWN_MINUTES, DEDUP_HOURS)"
    new_import = "COOLDOWN_AFTER_LOSSES, COOLDOWN_MINUTES, DEDUP_HOURS,\n                     DEDUP_HOURS_MAP)"
    if old_import in rm:
        rm = rm.replace(old_import, new_import)
        changes.append("risk_manager.py: added DEDUP_HOURS_MAP to imports")
    else:
        print(f"  \u26a0\ufe0f Could not find import line, trying broader match...")
        # Try without the closing paren
        if "DEDUP_HOURS)" in rm:
            rm = rm.replace("DEDUP_HOURS)", "DEDUP_HOURS,\n                     DEDUP_HOURS_MAP)")
            changes.append("risk_manager.py: added DEDUP_HOURS_MAP to imports (broad match)")

# Fix 2: Update check_duplicate_signal to use per-TF TTL
old_dedup = '    count = db.get_pending_signal_count(instrument, direction, timeframe, DEDUP_HOURS)'
new_dedup = '    # v2.7.2: Per-timeframe dedup TTL\n    dedup_ttl = DEDUP_HOURS_MAP.get(timeframe, DEDUP_HOURS)\n    count = db.get_pending_signal_count(instrument, direction, timeframe, dedup_ttl)'

if 'DEDUP_HOURS_MAP.get' in rm:
    print("  risk_manager.py: per-TF lookup already present, skipping")
else:
    if old_dedup in rm:
        rm = rm.replace(old_dedup, new_dedup)
        changes.append("risk_manager.py: check_duplicate_signal uses per-TF TTL")
    else:
        print(f"  \u26a0\ufe0f Could not find exact dedup line")
        # Show what's actually there
        for i, line in enumerate(rm.split('\n'), 1):
            if 'get_pending_signal_count' in line:
                print(f"  Found L{i}: {line.rstrip()}")

with open(rm_path, "w") as f:
    f.write(rm)

# Verify compilation
try:
    compile(rm, rm_path, "exec")
    print(f"  risk_manager.py: \u2705 patched + compiles")
except SyntaxError as e:
    print(f"  risk_manager.py: \u274c SYNTAX ERROR: {e}")
    shutil.copy(os.path.join(BACKUP_DIR, "risk_manager.py.bak"), rm_path)
    print(f"  risk_manager.py: restored from backup")
    exit(1)

# ============================================================
# SUMMARY
# ============================================================
print(f"\n{'=' * 65}")
print(f"  CHANGES APPLIED ({len(changes)}):")
for c in changes:
    print(f"    \u2022 {c}")
print(f"\n  Dedup TTL is now:")
print(f"    M15: 0.5h (was 2.0h) \u2014 \u22124x faster re-entry")
print(f"    H1:  2.0h (unchanged)")
print(f"    H4:  8.0h (was 2.0h) \u2014 \u00d74x spam protection")
print(f"\n  Override via .env:")
print(f"    DEDUP_HOURS_M15=0.5")
print(f"    DEDUP_HOURS_H1=2.0")
print(f"    DEDUP_HOURS_H4=8.0")
print(f"{'=' * 65}")
