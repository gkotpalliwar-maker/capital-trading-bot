#!/usr/bin/env python3
"""Import Capital.com manual trades into bot.db for ML training."""
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
    print("ERROR: Missing credentials in .env"); sys.exit(1)

print("=" * 70)
print("  IMPORT CAPITAL.COM TRADES -> bot.db")
print("=" * 70)

# Auth
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

# Fetch history
print("\n  Fetching transactions (last 10 days)...")
resp = session.get(f"{API_URL}/api/v1/history/transactions", params={
    "lastPeriod": 10 * 86400, "type": "TRADE"
})
if resp.status_code != 200:
    print(f"  ERROR: {resp.status_code}: {resp.text}"); sys.exit(1)

transactions = resp.json().get("transactions", [])
print(f"  Raw transactions: {len(transactions)}")

# Epic mapping
EPIC_MAP = {
    "EURUSD": "EURUSD", "EUR/USD": "EURUSD",
    "GBPUSD": "GBPUSD", "GBP/USD": "GBPUSD",
    "USDJPY": "USDJPY", "USD/JPY": "USDJPY",
    "GOLD": "GOLD", "XAUUSD": "GOLD",
    "US100": "US100", "USTECH100": "US100",
    "US500": "US500", "SPX500": "US500",
    "OIL_CRUDE": "OIL_CRUDE", "OILCRUDE": "OIL_CRUDE",
    "BTCUSD": "BTCUSD", "ETHUSD": "ETHUSD",
}

def normalize_epic(raw):
    raw = raw.upper().replace(" ", "").replace("_", "")
    for k, v in EPIC_MAP.items():
        if k.replace("_", "").replace("/", "") in raw.replace("/", ""):
            return v
    return raw

# Parse trades
trades = []
seen = set()
for tx in transactions:
    ref = tx.get("reference", "")
    if ref in seen:
        continue
    seen.add(ref)

    pnl_str = str(tx.get("profitAndLoss", "0"))
    pnl = float(pnl_str.replace("SGD", "").replace(",", "").strip() or "0")
    direction = str(tx.get("direction", "")).upper()
    open_level = float(tx.get("openLevel", 0) or 0)
    close_level = float(tx.get("closeLevel", 0) or 0)
    epic_raw = str(tx.get("instrumentName", "") or tx.get("epic", ""))
    date_str = tx.get("dateUtc", "") or tx.get("date", "")

    if not direction or (pnl == 0 and close_level == 0):
        continue

    ts = datetime.now(timezone.utc)
    if date_str:
        try:
            ts = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except:
            try:
                ts = datetime.strptime(date_str[:19], "%Y/%m/%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except:
                pass

    epic = normalize_epic(epic_raw)
    hour = ts.hour
    sess = "asian" if hour < 7 else "london" if hour < 12 else "new_york" if hour < 21 else "late"

    trades.append({
        "epic": epic, "direction": direction,
        "entry_price": open_level, "close_price": close_level,
        "pnl": pnl, "timestamp": ts.isoformat(),
        "session": sess,
        "zone_types": f"bearish+mss+sell" if direction == "SELL" else "bullish+mss+buy",
        "mss_type": f"bearish_mss" if direction == "SELL" else "bullish_mss",
        "timeframe": "H1", "confluence": 7,
    })

# Display
print(f"\n  Parsed {len(trades)} closed trades:")
print(f"  {'#':>3} {'Epic':<12} {'Dir':<5} {'Entry':>10} {'Close':>10} {'PnL':>8} {'Date':<16} {'Sess'}")
print(f"  {'-'*3} {'-'*12} {'-'*5} {'-'*10} {'-'*10} {'-'*8} {'-'*16} {'-'*7}")

wins, loss = 0, 0
for i, t in enumerate(trades, 1):
    m = "W" if t["pnl"] > 0 else "L"
    if t["pnl"] > 0: wins += 1
    else: loss += 1
    print(f"  {i:>3} {t['epic']:<12} {t['direction']:<5} {t['entry_price']:>10.4f} "
          f"{t['close_price']:>10.4f} {t['pnl']:>+7.2f} {t['timestamp'][:16]} {t['session']} {m}")

if wins + loss > 0:
    print(f"\n  Summary: {wins}W / {loss}L ({wins/(wins+loss)*100:.0f}% WR) | PnL: {sum(t['pnl'] for t in trades):+.2f} SGD")

# DB insert
print(f"\n  Checking bot.db...")
conn = sqlite3.connect(str(DB_PATH))
cursor = conn.cursor()
cursor.execute("PRAGMA table_info(trades)")
columns = {row[1]: row[2] for row in cursor.fetchall()}
print(f"  Columns: {list(columns.keys())}")

cursor.execute("SELECT COUNT(*) FROM trades WHERE status='closed' AND pnl IS NOT NULL")
existing = cursor.fetchone()[0]
print(f"  Existing closed: {existing}")

# Dedup
cursor.execute("SELECT timestamp, epic, direction FROM trades WHERE status='closed'")
existing_set = [(r[0], r[1], r[2]) for r in cursor.fetchall()]

new_trades = []
for t in trades:
    dup = False
    for et, ee, ed in existing_set:
        if ee == t["epic"] and ed == t["direction"]:
            try:
                edt = datetime.fromisoformat(str(et).replace("Z", "+00:00"))
                ndt = datetime.fromisoformat(t["timestamp"])
                if abs((edt - ndt).total_seconds()) < 300:
                    dup = True; break
            except:
                pass
    if not dup:
        new_trades.append(t)

print(f"  Non-duplicate: {len(new_trades)}")

if not new_trades:
    print("\n  All trades already in DB. Nothing to insert.")
    conn.close(); sys.exit(0)

# Insert
print(f"\n  Inserting {len(new_trades)} trades...")
inserted = 0
for t in new_trades:
    data = {
        "epic": t["epic"], "direction": t["direction"],
        "entry_price": t["entry_price"], "timestamp": t["timestamp"],
        "status": "closed", "pnl": t["pnl"],
        "timeframe": t["timeframe"], "zone_types": t["zone_types"],
        "mss_type": t["mss_type"], "confluence": t["confluence"],
        "session": t["session"],
    }
    valid = {k: v for k, v in data.items() if k in columns}
    try:
        cols = ", ".join(valid.keys())
        phs = ", ".join(["?"] * len(valid))
        cursor.execute(f"INSERT INTO trades ({cols}) VALUES ({phs})", list(valid.values()))
        inserted += 1
    except Exception as e:
        print(f"    ERR: {t['epic']} {t['direction']}: {e}")

conn.commit()
cursor.execute("SELECT COUNT(*) FROM trades WHERE status='closed' AND pnl IS NOT NULL")
total = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM trades WHERE status='closed' AND pnl > 0")
total_wins = cursor.fetchone()[0]
print(f"  Inserted: {inserted}")
print(f"\n  NEW TOTALS: {total} trades | {total_wins} wins | WR: {total_wins/total*100:.1f}%")
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
        print(f"  Top features: {', '.join(f'{k}({v:.0%})' for k,v in top)}")
    else:
        print(f"  Note: {meta}")
except Exception as e:
    print(f"  ML error: {e} (will retrain on next scan)")

print(f"\n{'='*70}\n  DONE\n{'='*70}")
