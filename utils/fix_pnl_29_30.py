#!/usr/bin/env python3
"""Fix PnL for bot.db entries 29 and 30 (retrace signals with 0.00 PnL)."""
import sqlite3
from pathlib import Path

DB_PATH = Path("/opt/trading-bot/data/bot.db")
conn = sqlite3.connect(str(DB_PATH))
cursor = conn.cursor()

# Verify current state
cursor.execute("SELECT id, epic, direction, pnl, zone_types FROM trades WHERE id IN (29, 30)")
rows = cursor.fetchall()
print("Before:")
for r in rows:
    print(f"  id={r[0]} {r[1]} {r[2]} pnl={r[3]} ({r[4]})")

# Update with actual PnL from TradingView fills
# id=29: EURUSD BUY @ 1.17329 -> closed @ 1.17185, qty=2000 -> -2.88 USD * 1.33 = -3.83 SGD
# id=30: US100 SELL @ 27792.3 -> closed @ 27561.9, qty=0.2 -> +46.08 USD * 1.33 = +61.29 SGD
cursor.execute("UPDATE trades SET pnl = -3.83, status = 'closed' WHERE id = 29")
cursor.execute("UPDATE trades SET pnl = 61.29, status = 'closed' WHERE id = 30")
conn.commit()

# Verify
cursor.execute("SELECT id, epic, direction, pnl, status FROM trades WHERE id IN (29, 30)")
rows = cursor.fetchall()
print("\nAfter:")
for r in rows:
    print(f"  id={r[0]} {r[1]} {r[2]} pnl={r[3]} status={r[4]}")

# Updated totals
cursor.execute("SELECT COUNT(*) FROM trades WHERE status='closed' AND pnl IS NOT NULL AND pnl != 0")
total = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM trades WHERE status='closed' AND pnl > 0")
wins = cursor.fetchone()[0]
print(f"\nDB (non-zero PnL): {total} trades | {wins} wins | WR: {wins/total*100:.1f}%")
conn.close()
print("Done.")
