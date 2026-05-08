#!/usr/bin/env python3
"""Import Capital.com trades v5 - hardcoded from TradingView order history.
Extracted from TradingView Trading Panel (5/4 - 5/8 2026).
No API calls needed - direct DB insert.
"""
import sys, sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("/opt/trading-bot/data/bot.db")

print("=" * 70)
print("  IMPORT TRADES -> bot.db (v5 - from TradingView history)")
print("=" * 70)

# All round-trip trades matched from TradingView order history
# Times converted from SGT (UTC+8) to UTC
# PnL in SGD (calculated from fills, rate ~1.33)
# (epic, direction, entry, exit, pnl_sgd, timestamp_utc, duration_desc)
TRADES = [
    # Trade 1: EUR/USD BUY - LOSS
    ("EURUSD", "BUY", 1.17329, 1.17185, -3.83,
     "2026-05-04T04:16:00+00:00", "3.5h"),
    # Trade 2: US100 SELL - WIN (closed at 27561.9, opened at 27792.3)
    ("US100", "SELL", 27792.3, 27561.9, 61.29,
     "2026-05-04T09:45:00+00:00", "22min"),
    # Trade 3: OIL_CRUDE BUY - LOSS
    ("OIL_CRUDE", "BUY", 102.27, 101.789, -19.19,
     "2026-05-05T06:59:00+00:00", "3min"),
    # Trade 4: GOLD BUY - WIN
    ("GOLD", "BUY", 4557.09, 4583.56, 28.17,
     "2026-05-05T11:33:00+00:00", "1.75h"),
    # Trade 5: EUR/USD BUY - LOSS
    ("EURUSD", "BUY", 1.17106, 1.17001, -8.38,
     "2026-05-05T15:53:00+00:00", "25min"),
    # Trade 6: GOLD BUY - WIN
    ("GOLD", "BUY", 4624.81, 4637.45, 13.45,
     "2026-05-06T01:39:00+00:00", "1.1h"),
    # Trade 7: EUR/USD BUY - WIN (TP at 1.17731)
    ("EURUSD", "BUY", 1.17391, 1.17731, 9.04,
     "2026-05-06T08:03:00+00:00", "1.6h"),
    # Trade 8: OIL_CRUDE BUY - LOSS
    ("OIL_CRUDE", "BUY", 96.661, 95.955, -28.17,
     "2026-05-06T08:17:00+00:00", "33min"),
    # Trade 9: GOLD BUY - LOSS (scaled entry, partial close)
    ("GOLD", "BUY", 4685.13, 4678.99, -8.17,
     "2026-05-06T12:27:00+00:00", "28min"),
    # Trade 10: OIL_CRUDE BUY - WIN
    ("OIL_CRUDE", "BUY", 93.061, 94.499, 57.38,
     "2026-05-06T12:43:00+00:00", "26min"),
    # Trade 11: CAD/CHF BUY - LOSS
    ("CADCHF", "BUY", 0.57284, 0.57217, -10.05,
     "2026-05-06T14:11:00+00:00", "30min"),
    # Trade 12: US100 SELL - LOSS
    ("US100", "SELL", 28553.2, 28612.0, -7.82,
     "2026-05-07T01:23:00+00:00", "4.3h"),
    # Trade 13: ETH/USD BUY - LOSS
    ("ETHUSD", "BUY", 2337.12, 2305.87, -37.41,
     "2026-05-07T08:35:00+00:00", "5.2h"),
    # Trade 14: OIL_CRUDE BUY - WIN
    ("OIL_CRUDE", "BUY", 89.387, 89.809, 16.84,
     "2026-05-07T14:36:00+00:00", "31min"),
    # Trade 15: OIL_CRUDE BUY - WIN
    ("OIL_CRUDE", "BUY", 90.351, 90.993, 25.62,
     "2026-05-07T15:46:00+00:00", "11min"),
    # Trade 16: GOLD SELL - WIN
    ("GOLD", "SELL", 4727.21, 4713.89, 17.72,
     "2026-05-07T16:03:00+00:00", "9min"),
    # Trade 17: GOLD BUY - LOSS (SL hit at 4706.89)
    ("GOLD", "BUY", 4720.22, 4706.89, -17.73,
     "2026-05-08T02:33:00+00:00", "5min"),
    # Trade 18: US500 BUY - WIN (TP hit at 7370)
    ("US500", "BUY", 7353.1, 7370.0, 20.23,
     "2026-05-08T04:40:00+00:00", "3.7h"),
]

# Process trades
trades = []
for epic, direction, entry, exit_p, pnl, ts_str, dur in TRADES:
    ts = datetime.fromisoformat(ts_str)
    hour = ts.hour
    day = ts.strftime("%A")
    sess = "asian" if hour < 7 else "london" if hour < 12 else "new_york" if hour < 21 else "late"

    if direction == "SELL":
        zone = "bearish+mss+sell"
        mss = "bearish_mss"
    else:
        zone = "bullish+mss+buy"
        mss = "bullish_mss"

    trades.append({
        "epic": epic, "direction": direction,
        "entry_price": entry, "pnl": pnl,
        "timestamp": ts_str, "session": sess,
        "zone_types": zone, "mss_type": mss,
        "timeframe": "H1", "confluence": 7,
    })

# Display
print(f"\n  Trades from TradingView: {len(trades)}")
print(f"  {'#':>3} {'Epic':<12} {'Dir':<5} {'Entry':>10} {'PnL':>9} {'Date':<20} {'Sess':<8} {'W/L'}")
print(f"  {'-'*3} {'-'*12} {'-'*5} {'-'*10} {'-'*9} {'-'*20} {'-'*8} {'-'*3}")
w, l = 0, 0
for i, t in enumerate(trades, 1):
    wl = "W" if t["pnl"] > 0 else "L"
    if t["pnl"] > 0: w += 1
    else: l += 1
    print(f"  {i:>3} {t['epic']:<12} {t['direction']:<5} {t['entry_price']:>10.4f} "
          f"{t['pnl']:>+8.2f} {t['timestamp'][:19]} {t['session']:<8} {wl}")

total_pnl = sum(t["pnl"] for t in trades)
print(f"\n  {w}W / {l}L ({w/(w+l)*100:.0f}% WR) | Total PnL: {total_pnl:+.2f} SGD")

# Confirm
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
                    dup = True; break
            except:
                pass
    if dup:
        skipped += 1; continue

    data = {
        "epic": t["epic"], "direction": t["direction"],
        "entry_price": t["entry_price"],
        "timestamp": t["timestamp"], "status": "closed",
        "pnl": t["pnl"], "timeframe": t["timeframe"],
        "zone_types": t["zone_types"], "mss_type": t["mss_type"],
        "confluence": t["confluence"], "session": t["session"],
    }
    valid = {k: v for k, v in data.items() if k in columns}
    try:
        cols = ", ".join(valid.keys())
        phs = ", ".join(["?"] * len(valid))
        cursor.execute(f"INSERT INTO trades ({cols}) VALUES ({phs})", list(valid.values()))
        inserted += 1
    except Exception as e:
        print(f"    ERR: {e}")

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
