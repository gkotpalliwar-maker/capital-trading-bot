#!/usr/bin/env python3
"""Check overlap between v5 import trades and existing bot.db entries."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("/opt/trading-bot/data/bot.db")

# The 18 trades we want to import
IMPORT_TRADES = [
    ("EURUSD", "BUY", "2026-05-04T04:16:00+00:00"),
    ("US100", "SELL", "2026-05-04T09:45:00+00:00"),
    ("OIL_CRUDE", "BUY", "2026-05-05T06:59:00+00:00"),
    ("GOLD", "BUY", "2026-05-05T11:33:00+00:00"),
    ("EURUSD", "BUY", "2026-05-05T15:53:00+00:00"),
    ("GOLD", "BUY", "2026-05-06T01:39:00+00:00"),
    ("EURUSD", "BUY", "2026-05-06T08:03:00+00:00"),
    ("OIL_CRUDE", "BUY", "2026-05-06T08:17:00+00:00"),
    ("GOLD", "BUY", "2026-05-06T12:27:00+00:00"),
    ("OIL_CRUDE", "BUY", "2026-05-06T12:43:00+00:00"),
    ("CADCHF", "BUY", "2026-05-06T14:11:00+00:00"),
    ("US100", "SELL", "2026-05-07T01:23:00+00:00"),
    ("ETHUSD", "BUY", "2026-05-07T08:35:00+00:00"),
    ("OIL_CRUDE", "BUY", "2026-05-07T14:36:00+00:00"),
    ("OIL_CRUDE", "BUY", "2026-05-07T15:46:00+00:00"),
    ("GOLD", "SELL", "2026-05-07T16:03:00+00:00"),
    ("GOLD", "BUY", "2026-05-08T02:33:00+00:00"),
    ("US500", "BUY", "2026-05-08T04:40:00+00:00"),
]

conn = sqlite3.connect(str(DB_PATH))
cursor = conn.cursor()

# Get all existing closed trades
cursor.execute("SELECT id, epic, direction, timestamp, pnl, zone_types FROM trades WHERE status='closed' ORDER BY timestamp")
existing = cursor.fetchall()

print("=" * 70)
print("  OVERLAP CHECK: v5 import vs existing bot.db")
print("=" * 70)
print(f"\n  Existing trades in DB: {len(existing)}")
print(f"  Trades to import: {len(IMPORT_TRADES)}")

print(f"\n  Existing trades (for reference):")
print(f"  {'ID':>4} {'Epic':<12} {'Dir':<5} {'Timestamp':<26} {'PnL':>8} {'Zones'}")
print(f"  {'-'*4} {'-'*12} {'-'*5} {'-'*26} {'-'*8} {'-'*20}")
for row in existing:
    tid, epic, direction, ts, pnl, zones = row
    print(f"  {tid:>4} {epic or '?':<12} {direction or '?':<5} {str(ts)[:25]:<26} {pnl or 0:>+7.2f} {zones or '?'}")

# Check overlaps (within 5 min window)
print(f"\n\n  OVERLAP ANALYSIS (5-min window):")
print(f"  {'-'*60}")
overlaps = []
for i, (epic, direction, ts_str) in enumerate(IMPORT_TRADES, 1):
    new_ts = datetime.fromisoformat(ts_str)
    matched = False
    for row in existing:
        tid, e_epic, e_dir, e_ts, e_pnl, e_zones = row
        if e_epic == epic and e_dir == direction:
            try:
                existing_ts = datetime.fromisoformat(str(e_ts).replace("Z", "+00:00"))
                diff = abs((existing_ts - new_ts).total_seconds())
                if diff < 300:
                    print(f"  #{i:>2} {epic:<12} {direction:<5} {ts_str[:19]} -> DUPLICATE (DB id={tid}, diff={diff:.0f}s)")
                    overlaps.append(i)
                    matched = True
                    break
            except:
                pass
    if not matched:
        print(f"  #{i:>2} {epic:<12} {direction:<5} {ts_str[:19]} -> NEW (will be inserted)")

print(f"\n  Summary: {len(overlaps)} duplicates will be skipped, {len(IMPORT_TRADES) - len(overlaps)} new trades will be inserted")
conn.close()
