#!/usr/bin/env python3
"""Import Capital.com trades v3 — uses /confirms/{dealId} for direction+levels."""
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
    print("ERROR: Missing credentials"); sys.exit(1)

print("=" * 70)
print("  IMPORT CAPITAL.COM TRADES -> bot.db (v3)")
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

# Step 1: Get activities (last 24h) to find dealIds
print("\n  Fetching activities (last 24h)...")
resp = session.get(f"{API_URL}/api/v1/history/activity", params={"lastPeriod": 86400})
if resp.status_code != 200:
    print(f"  Activities failed: {resp.status_code}"); sys.exit(1)
activities = resp.json().get("activities", [])
print(f"  Activities: {len(activities)}")

# Step 2: Get transactions (last 24h) for PnL
print("\n  Fetching transactions (PnL source)...")
tx_resp = session.get(f"{API_URL}/api/v1/history/transactions", params={
    "lastPeriod": 86400, "type": "TRADE"
})
tx_pnl = {}
if tx_resp.status_code == 200:
    for tx in tx_resp.json().get("transactions", []):
        deal_id = tx.get("dealId", "")
        pnl_str = str(tx.get("size", "0"))
        pnl = float(pnl_str.replace("SGD", "").replace(",", "").strip() or "0")
        tx_pnl[deal_id] = {"pnl": pnl, "date": tx.get("dateUtc", ""), "epic": tx.get("instrumentName", "")}
print(f"  Transactions with PnL: {len(tx_pnl)}")

# Step 3: For each POSITION dealId, get confirmation details
print("\n  Fetching deal confirmations (for direction + levels)...")
position_deals = [a for a in activities if a.get("type") == "POSITION"]
print(f"  Position activities: {len(position_deals)}")

trades = []
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

for act in position_deals:
    deal_id = act.get("dealId", "")
    epic_raw = act.get("epic", "")
    date_str = act.get("dateUTC", act.get("date", ""))
    
    # Try /confirms/{dealId}
    confirm_resp = session.get(f"{API_URL}/api/v1/confirms/{deal_id}")
    direction = ""
    open_level = 0
    close_level = 0
    
    if confirm_resp.status_code == 200:
        confirm = confirm_resp.json()
        direction = confirm.get("direction", "").upper()
        open_level = float(confirm.get("level", confirm.get("openLevel", 0)) or 0)
        close_level = float(confirm.get("closeLevel", 0) or 0)
        # If this is a close confirmation, profit may be here
        profit = float(confirm.get("profit", 0) or 0)
        if not epic_raw:
            epic_raw = confirm.get("epic", "")
        print(f"    {deal_id[:20]}... → {direction} {epic_raw} level={open_level} profit={profit}")
    else:
        print(f"    {deal_id[:20]}... → confirms {confirm_resp.status_code}")
    
    # Get PnL from transactions
    pnl = tx_pnl.get(deal_id, {}).get("pnl", 0)
    
    # Parse timestamp
    ts = datetime.now(timezone.utc)
    if date_str:
        try: ts = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except:
            try: ts = datetime.strptime(date_str[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            except: pass
    
    epic = norm_epic(epic_raw)
    hour = ts.hour
    sess = "asian" if hour < 7 else "london" if hour < 12 else "new_york" if hour < 21 else "late"
    
    if direction and (pnl != 0 or close_level != 0):
        trades.append({
            "epic": epic, "direction": direction,
            "entry_price": open_level, "close_price": close_level,
            "pnl": pnl, "timestamp": ts.isoformat(), "session": sess,
            "zone_types": "bearish+mss+sell" if direction == "SELL" else "bullish+mss+buy",
            "mss_type": "bearish_mss" if direction == "SELL" else "bullish_mss",
            "timeframe": "H1", "confluence": 7,
        })

# Display
print(f"\n  Parsed {len(trades)} valid trades:")
print(f"  {'#':>3} {'Epic':<12} {'Dir':<5} {'Entry':>10} {'PnL':>8} {'Date':<16} {'Sess'}")
print(f"  {'-'*3} {'-'*12} {'-'*5} {'-'*10} {'-'*8} {'-'*16} {'-'*7}")
w, l = 0, 0
for i, t in enumerate(trades, 1):
    if t["pnl"] > 0: w += 1
    elif t["pnl"] < 0: l += 1
    print(f"  {i:>3} {t['epic']:<12} {t['direction']:<5} {t['entry_price']:>10.4f} {t['pnl']:>+7.2f} {t['timestamp'][:16]} {t['session']}")
if w + l > 0:
    print(f"\n  {w}W / {l}L ({w/(w+l)*100:.0f}% WR) | PnL: {sum(t['pnl'] for t in trades):+.2f} SGD")

if not trades:
    print("\n  No trades with direction found.")
    print("  Try running with wider lastPeriod or check /api/v1/confirms endpoint.")
    sys.exit(0)

# DB insert
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
                if abs((edt - ndt).total_seconds()) < 300: dup = True; break
            except: pass
    if dup:
        skipped += 1; continue
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
print(f"  Inserted: {inserted} | Skipped: {skipped}")
print(f"  TOTALS: {total} trades | {total_wins} wins | WR: {total_wins/total*100:.1f}%")
conn.close()

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
    except Exception as e:
        print(f"  ML error: {e}")

print(f"\n{'='*70}\n  DONE\n{'='*70}")
