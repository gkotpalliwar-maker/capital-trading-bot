#!/usr/bin/env python3
"""Find DEDUP_HOURS value + get_pending_signal_count DB query."""
import os, re

BOT = os.path.join(os.getcwd(), "bot")
print("=" * 70)
print("  DEDUP_HOURS + DB QUERY ANALYSIS")
print("=" * 70)

# 1. Find DEDUP_HOURS definition
print("\n--- DEDUP_HOURS references across all files ---")
for fname in sorted(os.listdir(BOT)):
    if not fname.endswith('.py'):
        continue
    fpath = os.path.join(BOT, fname)
    with open(fpath) as f:
        lines = f.readlines()
    for i, l in enumerate(lines, 1):
        if 'DEDUP_HOURS' in l or 'dedup_hours' in l.lower():
            print(f"  bot/{fname}:L{i}: {l.rstrip()}")

# Also check .env
env_path = os.path.join(os.getcwd(), ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for l in f:
            if 'DEDUP' in l.upper():
                print(f"  .env: {l.rstrip()}")

# 2. Find get_pending_signal_count in persistence.py / db.py
print("\n--- get_pending_signal_count implementation ---")
for fname in ['persistence.py', 'db.py', 'database.py']:
    fpath = os.path.join(BOT, fname)
    if not os.path.exists(fpath):
        continue
    with open(fpath) as f:
        lines = f.readlines()
    in_func = False
    func_indent = 0
    for i, l in enumerate(lines, 1):
        if 'def get_pending_signal_count' in l:
            in_func = True
            func_indent = len(l) - len(l.lstrip())
            print(f"\n  bot/{fname}:")
        if in_func:
            print(f"  L{i:>3}: {l.rstrip()}")
            if i > 1 and l.strip() and not l.strip().startswith('#') and not l.strip().startswith('"""'):
                curr = len(l) - len(l.lstrip())
                if curr <= func_indent and 'def ' in l and 'get_pending_signal_count' not in l:
                    in_func = False

# 3. Also find config.py with DEDUP_HOURS
print("\n--- Config file with DEDUP_HOURS ---")
for fname in ['config.py', 'settings.py', 'constants.py']:
    fpath = os.path.join(BOT, fname)
    if not os.path.exists(fpath):
        continue
    with open(fpath) as f:
        lines = f.readlines()
    for i, l in enumerate(lines, 1):
        if 'DEDUP' in l or 'COOLDOWN' in l:
            print(f"  bot/{fname}:L{i}: {l.rstrip()}")

print("\n" + "=" * 70)
