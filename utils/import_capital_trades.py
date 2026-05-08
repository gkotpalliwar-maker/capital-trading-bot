#!/usr/bin/env python3
"""Import Capital.com trades v7 - handles all NOT NULL constraints."""
import sys, sqlite3, uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("/opt/trading-bot/data/bot.db")

print("=" * 70)
print("  IMPORT TRADES -> bot.db (v7 - from TradingView history)")
print("=" * 70)

# First: dump table schema to show all constraints
conn = sqlite3.connect(str(DB_PATH))
cursor = conn.cursor()
cursor.execute("PRAGMA table_info(trades)")
schema = cursor.fetchall()
print(f"\n  TABLE SCHEMA (trades):")
print(f"  {'Col':<20} {'Type':<12} {'NotNull':<8} {'Default':<10} {'PK'}")
print(f"  {'-'*20} {'-'*12} {'-'*8} {'-'*10} {'-'*3}")
not_null_cols = []
all_cols = []
for row in schema:
    cid, name, ctype, notnull, default, pk = row
    print(f"  {name:<20} {ctype:<12} {'YES' if notnull else ''::<8} {str(default or ''):<10} {'PK' if pk else ''}")
    all_cols.append(name)
    if notnull and not pk:
        not_null_cols.append(name)

print(f"\n  NOT NULL columns (must provide): {not_null_cols}")
print(f"  All columns: {all_cols}")

# Get a sample existing trade to see what fields are populated
cursor.execute("SELECT * FROM trades WHERE id=1")
sample = cursor.fetchone()
if sample:
    print(f"\n  Sample trade (id=1):")
    for col, val in zip(all_cols, sample):
        if val is not None:
            print(f"    {col}: {val}")

conn.close()

SGD_RATE = 1.33

# (epic, direction, entry, exit, qty, open_time_utc)
RAW_TRADES = [
    ("EURUSD", "BUY", 1.17329, 1.17185, 2000, "2026-05-04T04:16:00+00:00"),
    ("US100", "SELL", 27792.3, 27561.9, 0.2, "2026-05-04T09:45:00+00:00"),
    ("OIL_CRUDE", "BUY", 102.27, 101.789, 30, "2026-05-05T06:59:00+00:00"),
    ("GOLD", "BUY", 4557.09, 4583.56, 0.8, "2026-05-05T11:33:00+00:00"),
    ("EURUSD", "BUY", 1.17106, 1.17001, 6000, "2026-05-05T15:53:00+00:00"),
    ("GOLD", "BUY", 4624.81, 4637.45, 0.8, "2026-05-06T01:39:00+00:00"),
    ("EURUSD", "BUY", 1.17391, 1.17731, 2000, "2026-05-06T08:03:00+00:00"),
    ("OIL_CRUDE", "BUY", 96.661, 95.955, 30, "2026-05-06T08:17:00+00:00"),
    ("GOLD", "BUY", 4685.13, 4678.99, 1, "2026-05-06T12:27:00+00:00"),
    ("OIL_CRUDE", "BUY", 93.061, 94.499, 30, "2026-05-06T12:43:00+00:00"),
    ("CADCHF", "BUY", 0.57284, 0.57217, 10000, "2026-05-06T14:11:00+00:00"),
    ("US100", "SELL", 28553.2, 28612.0, 0.1, "2026-05-07T01:23:00+00:00"),
    ("ETHUSD", "BUY", 2337.12, 2305.87, 0.9, "2026-05-07T08:35:00+00:00"),
    ("OIL_CRUDE", "BUY", 89.387, 89.809, 30, "2026-05-07T14:36:00+00:00"),
    ("OIL_CRUDE", "BUY", 90.351, 90.993, 30, "2026-05-07T15:46:00+00:00"),
    ("GOLD", "SELL", 4727.21, 4713.89, 1, "2026-05-07T16:03:00+00:00"),
    ("GOLD", "BUY", 4720.22, 4706.89, 1, "2026-05-08T02:33:00+00:00"),
    ("US500", "BUY", 7353.1, 7370.0, 0.9, "2026-05-08T04:40:00+00:00"),
]

trades = []
for epic, direction, entry, exit_p, qty, ts_str in RAW_TRADES:
    if direction == "BUY":
        pnl_usd = (exit_p - entry) * qty
    else:
        pnl_usd = (entry - exit_p) * qty
    pnl_sgd = round(pnl_usd * SGD_RATE, 2)

    ts = datetime.fromisoformat(ts_str)
    hour = ts.hour
    sess = "asian" if hour < 7 else "london" if hour < 12 else "new_york" if hour < 21 else "late"

    if direction == "SELL":
        zone = "bearish+mss+sell"
        mss = "bearish_mss"
    else:
        zone = "bullish+mss+buy"
        mss = "bullish_mss"

    trades.append({
        "epic": epic,
        "instrument": epic,  # NOT NULL
        "direction": direction,
        "entry_price": entry,
        "pnl": pnl_sgd,
        "timestamp": ts_str,
        "session": sess,
        "zone_types": zone,
        "mss_type": mss,
        "timeframe": "H1",
        "confluence": 7,
        "deal_id": f"manual-{uuid.uuid4().hex[:16]}",
        "status": "closed",
        # Fill other likely NOT NULL fields with defaults
        "size": qty,
        "sl_price": 0,
        "tp_price": 0,
        "ml_score": 0,
    })

# Display
print(f"\n  Trades from TradingView: {len(trades)}")
print(f"  {'#':>3} {'Epic':<12} {'Dir':<5} {'Entry':>10} {'PnL':>9} {'Date':<20} {'Sess':<8} {'W/L'}")
print(f"  {'-'*3} {'-'*12} {'-'*5} {'-'*10} {'-'*9} {'-'*20} {'-'*8} {'-'*3}")
w, l = 0, 0
for i, t in enumerate(trades, 1):
    wl = "W" if t["pnl"] > 0 else "L"
    if t["pnl"] > 0:
        w += 1
    else:
        l += 1
    print(f"  {i:>3} {t['epic']:<12} {t['direction']:<5} {t['entry_price']:>10.4f} "
          f"{t['pnl']:>+8.2f} {t['timestamp'][:19]} {t['session']:<8} {wl}")

total_pnl = sum(t["pnl"] for t in trades)
print(f"\n  {w}W / {l}L ({w/(w+l)*100:.0f}% WR) | Total PnL: {total_pnl:+.2f} SGD")

confirm = input(f"\n  Insert {len(trades)} trades into bot.db? (y/N): ").strip().lower()
if confirm != "y":
    print("  Aborted."); sys.exit(0)

# DB insert with dedup
print(f"\n  Inserting into bot.db...")
conn = sqlite3.connect(str(DB_PATH))
cursor = conn.cursor()
cursor.execute("PRAGMA table_info(trades)")
columns = {row[1] for row in cursor.fetchall()}

cursor.execute("SELECT timestamp, epic, direction FROM trades WHERE status='closed'")
existing = [(r[0], r[1], r[2]) for r in cursor.fetchall()]

inserted, skipped = 0, 0
for t in trades:
    dup = False
    for et, ee, ed in existing:
        if ee == t["epic"] and ed == t["direction"]:
            try:
                edt = datetime.fromisoformat(str(et).replace("Z", "+00:00"))
                ndt = datetime.fromisoformat(t["timestamp"])
                if abs((edt - ndt).total_seconds()) < 300:
                    dup = True
                    break
            except:
                pass
    if dup:
        skipped += 1
        continue

    # Only include fields that exist in the table
    valid = {k: v for k, v in t.items() if k in columns}
    try:
        cols = ", ".join(valid.keys())
        phs = ", ".join(["?"] * len(valid))
        cursor.execute(f"INSERT INTO trades ({cols}) VALUES ({phs})", list(valid.values()))
        inserted += 1
    except Exception as e:
        print(f"    ERR: {e}")
        # On first error, show what we tried to insert
        if inserted == 0 and skipped <= 2:
            print(f"    Attempted cols: {list(valid.keys())}")
            break

conn.commit()
cursor.execute("SELECT COUNT(*) FROM trades WHERE status='closed' AND pnl IS NOT NULL")
total = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM trades WHERE status='closed' AND pnl > 0")
total_wins = cursor.fetchone()[0]
print(f"  Inserted: {inserted} | Skipped (dup): {skipped}")
print(f"  DB TOTALS: {total} trades | {total_wins} wins | WR: {total_wins/total*100:.1f}%")
conn.close()

# ML retrain
if inserted > 0:
    print(f"\n  Forcing ML retrain...")
    sys.path.insert(0, "/opt/trading-bot/bot")
    try:
        from signal_scorer import train_model
        ok, meta = train_model(force=True)
        if ok:
            print(f"  ML: {meta['n_trades']} trades | CV: {meta['cv_accuracy']:.1%} | WR: {meta['win_rate']:.1%}")
            imp = meta.get("feature_importance", {})
            top = sorted(imp.items(), key=lambda x: -x[1])[:5]
            print(f"  Top: {', '.join(f'{k}({v:.0%})' for k,v in top)}")
        else:
            print(f"  ML: training failed or insufficient data")
    except Exception as e:
        print(f"  ML retrain error: {e}")

print(f"\n{'=' * 70}")
print(f"  DONE")
print(f"{'=' * 70}")
