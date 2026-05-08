#!/usr/bin/env python3
"""Remove 19 phantom trades (ids 10-28) with PnL=0 and retrain ML."""
import sys, sqlite3
from pathlib import Path

DB_PATH = Path("/opt/trading-bot/data/bot.db")
conn = sqlite3.connect(str(DB_PATH))
cursor = conn.cursor()

print("=" * 70)
print("  REMOVE PHANTOM TRADES (ids 10-28) + RETRAIN ML")
print("=" * 70)

# Show what we're removing
cursor.execute("SELECT id, epic, direction, pnl, timestamp FROM trades WHERE id BETWEEN 10 AND 28")
rows = cursor.fetchall()
print(f"\n  Phantom trades to remove: {len(rows)}")
print(f"  {'ID':>4} {'Epic':<12} {'Dir':<5} {'PnL':>8} {'Timestamp'}")
print(f"  {'-'*4} {'-'*12} {'-'*5} {'-'*8} {'-'*25}")
for r in rows:
    print(f"  {r[0]:>4} {r[1] or '?':<12} {r[2] or '?':<5} {r[3] or 0:>+7.2f} {str(r[4])[:25]}")

# Confirm all have pnl=0
non_zero = [r for r in rows if r[3] and r[3] != 0]
if non_zero:
    print(f"\n  WARNING: {len(non_zero)} trades have non-zero PnL!")
    for r in non_zero:
        print(f"    id={r[0]} pnl={r[3]}")
    print("  Aborting to be safe.")
    sys.exit(1)

confirm = input(f"\n  Delete {len(rows)} phantom trades? (y/N): ").strip().lower()
if confirm != "y":
    print("  Aborted."); sys.exit(0)

# Delete
cursor.execute("DELETE FROM trades WHERE id BETWEEN 10 AND 28 AND (pnl = 0 OR pnl IS NULL)")
deleted = cursor.rowcount
conn.commit()
print(f"\n  Deleted: {deleted} trades")

# Show remaining
cursor.execute("SELECT COUNT(*) FROM trades WHERE status='closed'")
total = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM trades WHERE status='closed' AND pnl > 0")
wins = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM trades WHERE status='closed' AND pnl < 0")
losses = cursor.fetchone()[0]
print(f"  Remaining: {total} trades | {wins}W / {losses}L | WR: {wins/(wins+losses)*100:.1f}%")

# Retrain ML
print(f"\n  Forcing ML retrain...")
sys.path.insert(0, "/opt/trading-bot/bot")
try:
    from signal_scorer import train_model
    ok, meta = train_model(force=True)
    if ok:
        print(f"  ML: {meta['n_trades']} trades | CV: {meta['cv_accuracy']:.1%} | WR: {meta['win_rate']:.1%}")
        imp = meta.get("feature_importance", {})
        top = sorted(imp.items(), key=lambda x: -x[1])[:5]
        print(f"  Top features: {', '.join(f'{k}({v:.0%})' for k,v in top)}")
    else:
        print(f"  ML: training failed or insufficient data")
except Exception as e:
    print(f"  ML retrain error: {e}")

conn.close()
print(f"\n{'=' * 70}")
print(f"  DONE")
print(f"{'=' * 70}")
