#!/usr/bin/env python3
"""Import Capital.com manual trades into bot.db for ML training."""
import os, sys, sqlite3, requests
from datetime import datetime, timezone, timedelta
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
    print("ERROR: Missing credentials in .env"); sys.exit(1)

print("=" * 70)
print("  IMPORT CAPITAL.COM TRADES -> bot.db")
print("=" * 70)

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

# Fetch in 3-day chunks (Capital.com lastPeriod max seems ~7 days)
print("\n  Fetching transactions (last 10 days in chunks)...")
all_transactions = []
for days_ago_start in range(0, 10, 3):
    period_seconds = min(3 * 86400, (10 - days_ago_start) * 86400)
    # Use from/to dates
    dt_to = datetime.now(timezone.utc) - timedelta(days=days_ago_start)
    dt_from = dt_to - timedelta(seconds=period_seconds)
    
    params = {
        "from": dt_from.strftime("%Y-%m-%dT%H:%M:%S"),
        "to": dt_to.strftime("%Y-%m-%dT%H:%M:%S"),
        "type": "TRADE",
    }
    resp = session.get(f"{API_URL}/api/v1/history/transactions", params=params)
    if resp.status_code == 200:
        txs = resp.json().get("transactions", [])
        all_transactions.extend(txs)
        print(f"    {dt_from.strftime('%m-%d')} to {dt_to.strftime('%m-%d')}: {len(txs)} transactions")
    elif resp.status_code == 400:
        # Try lastPeriod with smaller value
        resp2 = session.get(f"{API_URL}/api/v1/history/transactions", params={
            "lastPeriod": period_seconds, "type": "TRADE"
        })
        if resp2.status_code == 200:
            txs = resp2.json().get("transactions", [])
            all_transactions.extend(txs)
            print(f"    lastPeriod={period_seconds}s: {len(txs)} transactions")
            break  # lastPeriod gets all from that period, no need to chunk further
        else:
            print(f"    Chunk {days_ago_start}: both methods failed ({resp.status_code}, {resp2.status_code})")
    else:
        print(f"    Chunk {days_ago_start}: {resp.status_code}")

# Deduplicate by reference
seen_refs = set()
transactions = []
for tx in all_transactions:
    ref = tx.get("reference", tx.get("dealId", str(len(transactions))))
    if ref not in seen_refs:
        seen_refs.add(ref)
        transactions.append(tx)

print(f"  Total unique transactions: {len(transactions)}")

if not transactions:
    print("\n  No transactions found. Check API dates or account activity.")
    sys.exit(0)

# Show raw structure of first transaction for debugging
print(f"\n  Sample transaction keys: {list(transactions[0].keys())}")
print(f"  Sample: {transactions[0]}")

EPIC_MAP = {
    "EURUSD": "EURUSD", "GBPUSD": "GBPUSD", "USDJPY": "USDJPY",
    "GOLD": "GOLD", "XAUUSD": "GOLD", "US100": "US100",
    "USTECH100": "US100", "US500": "US500", "SPX500": "US500",
    "OIL_CRUDE": "OIL_CRUDE", "OILCRUDE": "OIL_CRUDE",
    "BTCUSD": "BTCUSD", "ETHUSD": "ETHUSD",
}

def norm_epic(raw):
    raw = raw.upper().replace(" ", "").replace("/", "").replace("_", "")
    for k, v in EPIC_MAP.items():
        if k.replace("_", "") in raw:
            return v
    return raw

trades = []
for tx in transactions:
    pnl_str = str(tx.get("profitAndLoss", tx.get("pl", tx.get("size", "0"))))
    pnl = float(pnl_str.replace("SGD", "").replace("USD", "").replace(",", "").strip() or "0")
    direction = str(tx.get("direction", "")).upper()
    open_level = float(tx.get("openLevel", tx.get("level", 0)) or 0)
    close_level = float(tx.get("closeLevel", 0) or 0)
    epic_raw = str(tx.get("instrumentName", tx.get("epic", "")))
    date_str = tx.get("dateUtc", tx.get("date", tx.get("openDateUtc", "")))

    if not direction or (pnl == 0 and close_level == 0):
        continue

    ts = datetime.now(timezone.utc)
    if date_str:
        try: ts = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except:
            try: ts = datetime.strptime(date_str[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            except:
                try: ts = datetime.strptime(date_str[:19], "%Y/%m/%d %H:%M:%S").replace(tzinfo=timezone.utc)
                except: pass

    epic = norm_epic(epic_raw)
    hour = ts.hour
    sess = "asian" if hour < 7 else "london" if hour < 12 else "new_york" if hour < 21 else "late"

    trades.append({
        "epic": epic, "direction": direction,
        "entry_price": open_level, "pnl": pnl,
        "timestamp": ts.isoformat(), "session": sess,
        "zone_types": "bearish+mss+sell" if direction == "SELL" else "bullish+mss+buy",
        "mss_type": "bearish_mss" if direction == "SELL" else "bullish_mss",
        "timeframe": "H1", "confluence": 7,
    })

print(f"\n  Parsed {len(trades)} closed trades:")
print(f"  {'#':>3} {'Epic':<12} {'Dir':<5} {'Entry':>10} {'PnL':>8} {'Date':<16} {'Sess'}")
print(f"  {'-'*3} {'-'*12} {'-'*5} {'-'*10} {'-'*8} {'-'*16} {'-'*7}")
w, l = 0, 0
for i, t in enumerate(trades, 1):
    if t["pnl"] > 0: w += 1
    else: l += 1
    print(f"  {i:>3} {t['epic']:<12} {t['direction']:<5} {t['entry_price']:>10.4f} {t['pnl']:>+7.2f} {t['timestamp'][:16]} {t['session']}")
if w + l > 0:
    print(f"\n  {w}W / {l}L ({w/(w+l)*100:.0f}% WR) | PnL: {sum(t['pnl'] for t in trades):+.2f} SGD")

# DB insert
print(f"\n  Checking bot.db...")
conn = sqlite3.connect(str(DB_PATH))
cursor = conn.cursor()
cursor.execute("PRAGMA table_info(trades)")
columns = {row[1] for row in cursor.fetchall()}
print(f"  Columns: {sorted(columns)}")

cursor.execute("SELECT COUNT(*) FROM trades WHERE status='closed' AND pnl IS NOT NULL")
existing_count = cursor.fetchone()[0]
print(f"  Existing closed: {existing_count}")

cursor.execute("SELECT timestamp, epic, direction FROM trades WHERE status='closed'")
existing_set = [(r[0], r[1], r[2]) for r in cursor.fetchall()]

inserted = 0
skipped = 0
for t in trades:
    dup = False
    for et, ee, ed in existing_set:
        if ee == t["epic"] and ed == t["direction"]:
            try:
                edt = datetime.fromisoformat(str(et).replace("Z", "+00:00"))
                ndt = datetime.fromisoformat(t["timestamp"])
                if abs((edt - ndt).total_seconds()) < 300:
                    dup = True; break
            except: pass
    if dup:
        skipped += 1
        continue
    data = {"epic": t["epic"], "direction": t["direction"], "entry_price": t["entry_price"],
            "timestamp": t["timestamp"], "status": "closed", "pnl": t["pnl"],
            "timeframe": t["timeframe"], "zone_types": t["zone_types"],
            "mss_type": t["mss_type"], "confluence": t["confluence"], "session": t["session"]}
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
print(f"\n  Inserted: {inserted} | Skipped (dup): {skipped}")
print(f"  TOTALS: {total} trades | {total_wins} wins | WR: {total_wins/total*100:.1f}%")
conn.close()

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
        print(f"  Top: {', '.join(f'{k}({v:.0%})' for k,v in top)}")
    else:
        print(f"  Note: {meta}")
except Exception as e:
    print(f"  ML error: {e} (will retrain next scan)")

print(f"\n{'='*70}\n  DONE\n{'='*70}")
