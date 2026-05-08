#!/usr/bin/env python3
"""Import Capital.com trades v4 — interactive direction input.
Capital.com API doesn't expose direction in activity/confirms/transactions.
This script fetches PnL from transactions, then asks user for direction.
"""
import os, sys, sqlite3, requests
from datetime import datetime, timezone
from pathlib import Path

env_path = Path("/opt/trading-bot/.env")
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

API_KEY = os.getenv("CAPITAL_API_KEY")
API_URL = os.getenv("CAPITAL_API_URL", "https://api-capital.backend-capital.com")
EMAIL = os.getenv("CAPITAL_EMAIL")
PASSWORD = os.getenv("CAPITAL_PASSWORD")
DB_PATH = Path("/opt/trading-bot/data/bot.db")

if not all([API_KEY, EMAIL, PASSWORD]):
    print("ERROR: Missing credentials"); sys.exit(1)

print("=" * 70)
print("  IMPORT CAPITAL.COM TRADES -> bot.db (v4 — interactive)")
print("=" * 70)

# ── Auth ──
print("\n  Authenticating...")
session = requests.Session()
resp = session.post(f"{API_URL}/api/v1/session", json={
    "identifier": EMAIL, "password": PASSWORD
}, headers={"X-CAP-API-KEY": API_KEY})
if resp.status_code != 200:
    print(f"  ERROR: {resp.status_code}: {resp.text}"); sys.exit(1)
session.headers.update({
    "CST": resp.headers.get("CST"),
    "X-SECURITY-TOKEN": resp.headers.get("X-SECURITY-TOKEN"),
    "X-CAP-API-KEY": API_KEY
})
print("  OK")

# ── Fetch transactions (reliable PnL source) ──
print("\n  Fetching transactions (last 24h)...")
tx_resp = session.get(f"{API_URL}/api/v1/history/transactions", params={
    "lastPeriod": 86400, "type": "TRADE"
})
if tx_resp.status_code != 200:
    print(f"  Transactions failed: {tx_resp.status_code}")
    # Try without type filter
    tx_resp = session.get(f"{API_URL}/api/v1/history/transactions", params={"lastPeriod": 86400})

transactions = tx_resp.json().get("transactions", [])
print(f"  Raw transactions: {len(transactions)}")

# Filter to actual trades (non-zero PnL)
EPIC_MAP = {
    "EURUSD": "EURUSD", "GBPUSD": "GBPUSD", "USDJPY": "USDJPY",
    "Gold": "GOLD", "GOLD": "GOLD", "US Tech 100": "US100",
    "US 500": "US500", "US500": "US500", "Oil - Crude": "OIL_CRUDE",
    "OIL_CRUDE": "OIL_CRUDE", "Bitcoin": "BTCUSD", "BTCUSD": "BTCUSD",
    "Ethereum": "ETHUSD", "ETHUSD": "ETHUSD",
}
def norm_epic(raw):
    if raw in EPIC_MAP:
        return EPIC_MAP[raw]
    for k, v in EPIC_MAP.items():
        if k.lower() in raw.lower():
            return v
    return raw.upper().replace(" ", "")

trades_raw = []
for tx in transactions:
    pnl_str = str(tx.get("size", "0"))
    pnl = float(pnl_str.replace("SGD", "").replace(",", "").strip() or "0")
    if pnl == 0:
        continue  # Skip zero-PnL (fees, adjustments)
    epic_raw = tx.get("instrumentName", "")
    date_str = tx.get("dateUtc", tx.get("date", ""))
    trades_raw.append({
        "epic_raw": epic_raw,
        "epic": norm_epic(epic_raw),
        "pnl": pnl,
        "date": date_str,
        "dealId": tx.get("dealId", ""),
    })

if not trades_raw:
    print("\n  No trades with PnL found in last 24h.")
    print("  Try running during market hours or after recent trades close.")
    sys.exit(0)

# ── Display and ask for directions ──
print(f"\n  Found {len(trades_raw)} trades with PnL:")
print(f"  {'#':>3} {'Epic':<12} {'PnL':>8} {'Date':<20}")
print(f"  {'-'*3} {'-'*12} {'-'*8} {'-'*20}")
for i, t in enumerate(trades_raw, 1):
    print(f"  {i:>3} {t['epic']:<12} {t['pnl']:>+7.2f} {t['date'][:19]}")

print(f"\n  Enter direction for each trade (B=BUY, S=SELL, X=skip):")
print(f"  You can also type all at once, e.g. 'SBSBSX' for 6 trades.")
print()

# Get input
user_input = input("  Directions: ").strip().upper()
if len(user_input) == len(trades_raw):
    # Single string like "BSSBBX"
    directions = list(user_input)
else:
    # Space-separated or comma-separated
    directions = [d.strip() for d in user_input.replace(",", " ").split()]

if len(directions) != len(trades_raw):
    print(f"\n  ERROR: Expected {len(trades_raw)} directions, got {len(directions)}")
    print(f"  Please enter exactly {len(trades_raw)} characters (B/S/X)")
    sys.exit(1)

# ── Build valid trades ──
trades = []
for t, d in zip(trades_raw, directions):
    if d == "X":
        continue
    direction = "BUY" if d == "B" else "SELL" if d == "S" else None
    if not direction:
        print(f"  WARNING: Unknown direction '{d}' for {t['epic']}, skipping")
        continue
    
    ts = datetime.now(timezone.utc)
    if t["date"]:
        try: ts = datetime.fromisoformat(t["date"].replace("Z", "+00:00"))
        except:
            try: ts = datetime.strptime(t["date"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            except: pass
    
    hour = ts.hour
    sess = "asian" if hour < 7 else "london" if hour < 12 else "new_york" if hour < 21 else "late"
    day = ts.strftime("%A")
    
    trades.append({
        "epic": t["epic"], "direction": direction,
        "entry_price": 0,  # Not available from API
        "pnl": t["pnl"], "timestamp": ts.isoformat(),
        "session": sess, "day": day,
        "zone_types": f"{'bearish' if direction == 'SELL' else 'bullish'}+mss+{direction.lower()}",
        "mss_type": f"{'bearish' if direction == 'SELL' else 'bullish'}_mss",
        "timeframe": "H1", "confluence": 7,
    })

# ── Summary ──
print(f"\n  Valid trades to import: {len(trades)}")
print(f"  {'#':>3} {'Epic':<12} {'Dir':<5} {'PnL':>8} {'Date':<16} {'Session':<8} {'Day'}")
print(f"  {'-'*3} {'-'*12} {'-'*5} {'-'*8} {'-'*16} {'-'*8} {'-'*9}")
w, l = 0, 0
for i, t in enumerate(trades, 1):
    if t["pnl"] > 0: w += 1
    else: l += 1
    print(f"  {i:>3} {t['epic']:<12} {t['direction']:<5} {t['pnl']:>+7.2f} {t['timestamp'][:16]} {t['session']:<8} {t['day']}")
total_pnl = sum(t["pnl"] for t in trades)
print(f"\n  {w}W / {l}L ({w/(w+l)*100:.0f}% WR) | PnL: {total_pnl:+.2f} SGD")

if not trades:
    print("\n  No trades to import."); sys.exit(0)

# Confirm
confirm = input(f"\n  Insert {len(trades)} trades into bot.db? (y/N): ").strip().lower()
if confirm != "y":
    print("  Aborted."); sys.exit(0)

# ── DB insert ──
print(f"\n  Inserting into bot.db...")
conn = sqlite3.connect(str(DB_PATH))
cursor = conn.cursor()
cursor.execute("PRAGMA table_info(trades)")
columns = {row[1] for row in cursor.fetchall()}

cursor.execute("SELECT timestamp, epic, direction FROM trades WHERE status='closed'")
existing = [(r[0], r[1], r[2]) for r in cursor.fetchall()]

inserted, skipped = 0, 0
for t in trades:
    # Dedup check (within 5 min)
    dup = False
    for et, ee, ed in existing:
        if ee == t["epic"] and ed == t["direction"]:
            try:
                edt = datetime.fromisoformat(str(et).replace("Z", "+00:00"))
                ndt = datetime.fromisoformat(t["timestamp"])
                if abs((edt - ndt).total_seconds()) < 300: dup = True; break
            except: pass
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

# ── ML retrain ──
if inserted > 0:
    print(f"\n  Forcing ML retrain...")
    sys.path.insert(0, "/opt/trading-bot/bot")
    try:
        from signal_scorer import train_model
        ok, meta = train_model(force=True)
        if ok:
            print(f"  ML retrained: {meta['n_trades']} trades | CV: {meta['cv_accuracy']:.1%} | WR: {meta['win_rate']:.1%}")
            imp = meta.get("feature_importance", {})
            top = sorted(imp.items(), key=lambda x: -x[1])[:5]
            print(f"  Top features: {', '.join(f'{k}({v:.0%})' for k,v in top)}")
        else:
            print(f"  ML: not enough data yet")
    except Exception as e:
        print(f"  ML retrain error: {e}")

print(f"\n{\'=\' * 70}")
print(f"  DONE")
print(f"{\'=\' * 70}")
