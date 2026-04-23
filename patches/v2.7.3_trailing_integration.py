#!/usr/bin/env python3
"""v2.7.3: Integrate bot-side trailing (TrailingManager) into scanner.py.

Patches scanner.py only. bot_trailing.py already exists on VPS.
Run from /opt/trading-bot:
  python3 /tmp/v273/patches/v2.7.3_trailing_integration.py
"""
import os
import shutil
from datetime import datetime

BOT_DIR = os.path.join(os.getcwd(), "bot")
SCANNER = os.path.join(BOT_DIR, "scanner.py")
BACKUP_DIR = os.path.join(os.getcwd(), "backups", datetime.now().strftime("%Y%m%d_%H%M%S"))
os.makedirs(BACKUP_DIR, exist_ok=True)

print("=" * 65)
print("  v2.7.3: Integrate Bot-Side Trailing into Scanner")
print("=" * 65)

# Verify bot_trailing.py exists
bt_path = os.path.join(BOT_DIR, "bot_trailing.py")
if not os.path.exists(bt_path):
    print(f"\n  \u274c bot_trailing.py not found at {bt_path}")
    print(f"  Run: cp /tmp/v273/bot/bot_trailing.py bot/bot_trailing.py")
    exit(1)
print(f"\n  \u2705 bot_trailing.py found")

# Backup scanner.py
shutil.copy(SCANNER, os.path.join(BACKUP_DIR, "scanner.py.bak"))
print(f"  \u2705 Backed up scanner.py")

with open(SCANNER) as f:
    code = f.read()

changes = []

# ============================================================
# PATCH 1: Add import (after market_hours import, ~L32)
# ============================================================
IMPORT_ANCHOR = "from market_hours import is_market_open, get_scannable_instruments"
IMPORT_LINE = "from bot_trailing import TrailingManager"

if IMPORT_LINE in code:
    print(f"  PATCH 1: import already present, skipping")
else:
    if IMPORT_ANCHOR in code:
        code = code.replace(
            IMPORT_ANCHOR,
            IMPORT_ANCHOR + "\n" + IMPORT_LINE
        )
        changes.append("Added TrailingManager import")
    else:
        print(f"  \u26a0\ufe0f Could not find import anchor line")
        exit(1)

# ============================================================
# PATCH 2: Init TrailingManager after sync_positions (~L186)
# ============================================================
INIT_ANCHOR = '    logger.info("  Restart recovery: %d open positions at broker", len(broker_positions))'
INIT_BLOCK = """    logger.info("  Restart recovery: %d open positions at broker", len(broker_positions))

    # v2.7.3: Initialize bot-side trailing manager
    trailing_manager = TrailingManager(client)
    logger.info("  Trailing manager initialized (enabled=%s, breakeven=%.1fR, trail_start=%.1fR)",
                trailing_manager.state is not None, 1.0, 1.5)"""

if "trailing_manager = TrailingManager" in code:
    print(f"  PATCH 2: TrailingManager init already present, skipping")
else:
    if INIT_ANCHOR in code:
        code = code.replace(INIT_ANCHOR, INIT_BLOCK)
        changes.append("Init TrailingManager after client auth")
    else:
        print(f"  \u26a0\ufe0f Could not find init anchor line")
        exit(1)

# ============================================================
# PATCH 3: Replace main loop trailing block (L275-286)
# The old block:
#     # Trailing SL on open positions
#     positions_count = trail_updates = 0
#     try:
#         positions = get_open_positions(client)
#         positions_count = len(positions)
#         for pos in positions:
#             if _apply_trailing_sl(client, pos):
#                 trail_updates += 1
#         logger.info("  Positions: %d | Trail updates: %d", positions_count, trail_updates)
#     except Exception as e:
#         logger.error("Position error: %s", e)
#         db.log_error("position", "Position check failed", traceback.format_exc())
# ============================================================
OLD_TRAIL_BLOCK = """        # Trailing SL on open positions
        positions_count = trail_updates = 0
        try:
            positions = get_open_positions(client)
            positions_count = len(positions)
            for pos in positions:
                if _apply_trailing_sl(client, pos):
                    trail_updates += 1
            logger.info("  Positions: %d | Trail updates: %d", positions_count, trail_updates)
        except Exception as e:
            logger.error("Position error: %s", e)
            db.log_error("position", "Position check failed", traceback.format_exc())"""

NEW_TRAIL_BLOCK = """        # v2.7.3: Bot-side trailing (breakeven + ratcheting SL)
        positions_count = trail_updates = 0
        try:
            positions = get_open_positions(client)
            positions_count = len(positions)
            # Run TrailingManager (handles breakeven at 1R, trail after 1.5R)
            updates = trailing_manager.update_all()
            trail_updates = len(updates)
            for u in updates:
                logger.info("  TRAIL: %s SL->%.5f (%s)", u['deal_id'], u['new_sl'], u['reason'])
            # Cleanup state for closed positions
            open_deal_ids = {pos.get('position', {}).get('dealId') for pos in positions}
            trailing_manager.cleanup_closed(open_deal_ids)
            logger.info("  Positions: %d | Trail updates: %d", positions_count, trail_updates)
        except Exception as e:
            logger.error("Position/trailing error: %s", e)
            db.log_error("trailing", "Bot-side trailing failed", traceback.format_exc())"""

if "trailing_manager.update_all()" in code and "cleanup_closed" in code:
    print(f"  PATCH 3: main loop trailing already patched, skipping")
else:
    if OLD_TRAIL_BLOCK in code:
        code = code.replace(OLD_TRAIL_BLOCK, NEW_TRAIL_BLOCK)
        changes.append("Replaced main loop trailing with TrailingManager")
    else:
        print(f"  \u26a0\ufe0f Could not find old trailing block in main loop")
        # Show what's around line 275
        lines = code.split('\n')
        for i in range(270, min(290, len(lines))):
            print(f"  L{i+1}: {lines[i].rstrip()[:90]}")
        exit(1)

# ============================================================
# PATCH 4: Replace paused-mode trailing (L246-253)
# Old:
#     try:
#         positions = get_open_positions(client)
#         if positions:
#             for pos in positions:
#                 _apply_trailing_sl(client, pos)
#     except Exception as e:
#         db.log_error("trailing", "Trailing SL error while paused", str(e))
# ============================================================
OLD_PAUSED = """            try:
                positions = get_open_positions(client)
                if positions:
                    for pos in positions:
                        _apply_trailing_sl(client, pos)
            except Exception as e:
                db.log_error("trailing", "Trailing SL error while paused", str(e))"""

NEW_PAUSED = """            # v2.7.3: Bot-side trailing even when scanner paused
            try:
                updates = trailing_manager.update_all()
                if updates:
                    logger.info("  [PAUSED] Trail updates: %d", len(updates))
            except Exception as e:
                db.log_error("trailing", "Trailing SL error while paused", str(e))"""

if "trailing_manager.update_all()" in code.split("# Regular scheduled scan")[0] if "# Regular scheduled scan" in code else "":
    print(f"  PATCH 4: paused trailing already patched, skipping")
else:
    if OLD_PAUSED in code:
        code = code.replace(OLD_PAUSED, NEW_PAUSED)
        changes.append("Replaced paused-mode trailing with TrailingManager")
    else:
        # Try with different indentation
        print(f"  \u26a0\ufe0f Could not find exact paused trailing block, trying flexible match...")
        # Look for the key pattern
        if "_apply_trailing_sl(client, pos)" in code and "Trailing SL error while paused" in code:
            # Find and replace the block line by line
            lines = code.split('\n')
            new_lines = []
            skip_until_except_end = False
            paused_replaced = False
            i = 0
            while i < len(lines):
                line = lines[i]
                # Detect start of paused trailing block
                if (not paused_replaced and "# Still check positions" in line.lower() or
                    (not paused_replaced and "get_open_positions" in line and
                     i + 3 < len(lines) and "_apply_trailing_sl" in lines[i + 2])):
                    # Check if this is the paused section (before "Sleep 15 seconds")
                    context = '\n'.join(lines[max(0, i-3):i+8])
                    if "paused" in context.lower() or "scanner_active" in context.lower():
                        # Find the try block start
                        while i < len(lines) and 'try:' not in lines[i]:
                            new_lines.append(lines[i])
                            i += 1
                        # Insert new block
                        indent = '            '  # 12 spaces
                        new_lines.append(f"{indent}# v2.7.3: Bot-side trailing even when scanner paused")
                        new_lines.append(f"{indent}try:")
                        new_lines.append(f"{indent}    updates = trailing_manager.update_all()")
                        new_lines.append(f"{indent}    if updates:")
                        new_lines.append(f'{indent}        logger.info("  [PAUSED] Trail updates: %d", len(updates))')
                        new_lines.append(f"{indent}except Exception as e:")
                        new_lines.append(f'{indent}    db.log_error("trailing", "Trailing SL error while paused", str(e))')
                        # Skip old block
                        while i < len(lines):
                            if 'db.log_error' in lines[i] and 'paused' in lines[i].lower():
                                i += 1
                                break
                            i += 1
                        paused_replaced = True
                        changes.append("Replaced paused-mode trailing (flexible match)")
                        continue
                new_lines.append(lines[i])
                i += 1
            code = '\n'.join(new_lines)
        else:
            print(f"  \u26a0\ufe0f Paused trailing block not found, skipping (non-critical)")

# ============================================================
# Verify compilation
# ============================================================
try:
    compile(code, SCANNER, "exec")
    print(f"\n  \u2705 Compilation: OK")
except SyntaxError as e:
    print(f"\n  \u274c SYNTAX ERROR: {e}")
    shutil.copy(os.path.join(BACKUP_DIR, "scanner.py.bak"), SCANNER)
    print(f"  Restored from backup")
    exit(1)

# Write patched file
with open(SCANNER, 'w') as f:
    f.write(code)

new_lines = len(code.split('\n'))
print(f"  \u2705 Written: {new_lines} lines")

# ============================================================
# SUMMARY
# ============================================================
print(f"\n{'=' * 65}")
print(f"  CHANGES APPLIED ({len(changes)}):")
for c in changes:
    print(f"    \u2022 {c}")
print(f"""
  How it works:
    1. TrailingManager polls open positions via Capital.com API
    2. At 1.0R profit: moves SL to breakeven (entry + buffer)
    3. At 1.5R profit: starts trailing (SL = highest/lowest - 1*risk)
    4. State persisted to data/trailing_state.json (survives restarts)
    5. Closed positions auto-cleaned from state

  Config (.env):
    TRAILING_STOP_ENABLED=true    (master switch)
    BREAKEVEN_TRIGGER_R=1.0       (move SL to entry at 1R)
    TRAIL_START_R=1.5             (start trailing at 1.5R)
    TRAIL_DISTANCE_ATR=1.0        (trail 1x risk behind peak)

  Verify: check journalctl for "TRAIL:" log lines
{'=' * 65}
""")
