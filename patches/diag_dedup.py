#!/usr/bin/env python3
"""Diagnose dedup cache TTL in scanner.py.
Run from /opt/trading-bot: python3 /tmp/diag_dedup.py
"""
import re
import os
import sys

SCANNER = os.path.join(os.getcwd(), "bot", "scanner.py")

if not os.path.exists(SCANNER):
    print(f"ERROR: {SCANNER} not found")
    sys.exit(1)

with open(SCANNER) as f:
    content = f.read()
    lines = content.split('\n')

print("=" * 70)
print("  DEDUP / CACHE / TTL DIAGNOSTIC — scanner.py")
print(f"  File: {SCANNER} ({len(lines)} lines, {len(content)} chars)")
print("=" * 70)

# Search for dedup-related patterns
keywords = [
    'dedup', 'sent_signal', 'signal_cache', 'signal_key', 'signal_hash',
    'recent_signal', 'last_signal', 'already_sent', 'already_notified',
    'processed_signal', 'seen_signal', 'cooldown', 'ttl',
    'timedelta', '3600', '7200', '14400', '900', '1800',
    'signal_log', 'sent_', '_sent', '_cache', 'cache_',
    'time.time', 'time()', 'datetime.now',
    'expire', 'expiry', 'stale', 'evict', 'cleanup',
    'skip_duplicate', 'duplicate_check',
]

print("\n--- Keyword matches ---")
matches = []
for i, line in enumerate(lines, 1):
    ll = line.lower().strip()
    if not ll or ll.startswith('#'):
        continue
    for kw in keywords:
        if kw in ll:
            matches.append((i, line.rstrip(), kw))
            break

if matches:
    for lineno, line, kw in matches:
        print(f"  L{lineno:>4} [{kw:>20}]: {line}")
else:
    print("  No direct keyword matches found.")

# Search for dict/set assignments that could be caches
print("\n--- Dict/Set assignments (potential caches) ---")
for i, line in enumerate(lines, 1):
    s = line.strip()
    if '= {}' in s or '= set()' in s or '= dict()' in s or '= defaultdict(' in s:
        if not s.startswith('#'):
            print(f"  L{i:>4}: {s}")

# Search for conditional "in" checks (dedup pattern: if key in cache)
print("\n--- Dedup patterns (if X in Y / not in Y) ---")
for i, line in enumerate(lines, 1):
    s = line.strip()
    if re.search(r'if .+ (?:not )?in \w+(_cache|_sent|_signals|_seen|_log|_recent)', s, re.I):
        print(f"  L{i:>4}: {s}")

# Search for time-based comparisons (TTL checks)
print("\n--- Time-based comparisons (TTL checks) ---")
for i, line in enumerate(lines, 1):
    s = line.strip()
    if re.search(r'(time\.time|datetime|timedelta|now\(\)).*[<>]', s):
        if not s.startswith('#'):
            print(f"  L{i:>4}: {s}")

# Show all global-scope variable assignments at module level
print("\n--- Module-level assignments (lines with no indentation) ---")
for i, line in enumerate(lines, 1):
    s = line
    if s and not s[0].isspace() and '=' in s and not s.startswith('#') and not s.startswith('def ') and not s.startswith('class '):
        # Skip imports
        if not s.startswith('import ') and not s.startswith('from '):
            print(f"  L{i:>4}: {s.rstrip()[:100]}")

print("\n" + "=" * 70)
print("  If no dedup cache found above, the bot may send duplicate signals.")
print("  Common patterns to look for:")
print("    sent_signals = {}      # key: (inst, tf, dir), value: timestamp")
print("    SIGNAL_TTL = 3600      # seconds before re-sending same signal")
print("    if sig_key in sent_signals: continue")
print("=" * 70)
