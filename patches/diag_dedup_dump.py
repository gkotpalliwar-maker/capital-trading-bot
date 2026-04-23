#!/usr/bin/env python3
"""Find and dump risk_manager.check_duplicate_signal + dedup TTL logic."""
import os, re, glob

BOT_DIR = os.path.join(os.getcwd(), "bot")
print("=" * 75)
print("  RISK MANAGER DEDUP ANALYSIS")
print("=" * 75)

# 1. Find risk_manager import in scanner.py
scanner = os.path.join(BOT_DIR, "scanner.py")
with open(scanner) as f:
    scanner_lines = f.readlines()
print("\n--- scanner.py: risk_manager references ---")
for i, l in enumerate(scanner_lines, 1):
    if 'risk_manager' in l.lower() or 'check_duplicate' in l.lower():
        print(f"  L{i:>3}: {l.rstrip()}")

# 2. Search ALL .py files for check_duplicate_signal definition
print("\n--- Searching all .py files for 'check_duplicate_signal' definition ---")
for root, dirs, files in os.walk(os.getcwd()):
    for fname in files:
        if not fname.endswith('.py'):
            continue
        fpath = os.path.join(root, fname)
        try:
            with open(fpath) as f:
                lines = f.readlines()
            for i, l in enumerate(lines, 1):
                if 'def check_duplicate_signal' in l or 'check_duplicate' in l.lower():
                    relpath = os.path.relpath(fpath, os.getcwd())
                    print(f"  {relpath}:L{i}: {l.rstrip()}")
        except:
            pass

# 3. Find risk_manager module (could be a class in db.py, or standalone file)
print("\n--- Searching for risk_manager module/class ---")
for fname in os.listdir(BOT_DIR):
    if fname.endswith('.py'):
        fpath = os.path.join(BOT_DIR, fname)
        with open(fpath) as f:
            content = f.read()
        if 'risk_manager' in content.lower() or 'class RiskManager' in content or 'check_duplicate_signal' in content:
            print(f"\n  === bot/{fname} ({len(content.split(chr(10)))} lines) ===")
            lines = content.split('\n')
            # Show class defs and function defs related to risk/dedup
            in_method = False
            method_indent = 0
            for i, l in enumerate(lines, 1):
                s = l.strip()
                # Show class definitions
                if 'class ' in l and ('Risk' in l or 'risk' in l or 'Manager' in l):
                    print(f"  L{i:>3}: {l.rstrip()}")
                # Show the full check_duplicate_signal method
                if 'def check_duplicate_signal' in l:
                    in_method = True
                    method_indent = len(l) - len(l.lstrip())
                    print(f"\n  --- check_duplicate_signal method ---")
                if in_method:
                    print(f"  L{i:>3}: {l.rstrip()}")
                    # End of method: next def at same or less indentation
                    if i > 1 and l.strip() and not l.strip().startswith('#'):
                        curr_indent = len(l) - len(l.lstrip())
                        if curr_indent <= method_indent and 'def ' in l and 'check_duplicate_signal' not in l:
                            in_method = False
                # Also show TTL/cooldown related lines
                if not in_method:
                    ll = l.lower()
                    if any(kw in ll for kw in ['ttl', 'cooldown', 'dedup', 'duplicate', 
                                                 'signal_ttl', 'hours', '3600', '7200',
                                                 '14400', '900', 'timedelta']):
                        if not s.startswith('#'):
                            print(f"  L{i:>3}: {l.rstrip()}")

# 4. Check data/ directory for any risk_manager or db module
print("\n--- Checking for standalone risk_manager or db module ---")
for fname in ['risk_manager.py', 'risk.py', 'db.py', 'database.py', 'persistence.py']:
    fpath = os.path.join(BOT_DIR, fname)
    if os.path.exists(fpath):
        print(f"  Found: bot/{fname}")
    # Also check root
    fpath2 = os.path.join(os.getcwd(), fname)
    if os.path.exists(fpath2):
        print(f"  Found: {fname}")

print("\n" + "=" * 75)
