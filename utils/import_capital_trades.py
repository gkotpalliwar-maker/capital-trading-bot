#!/usr/bin/env python3
"""Import Capital.com trades into bot.db — v2 (fixed for transaction format)."""
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
print("  IMPORT CAPITAL.COM TRADES -> bot.db (v2)")
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

# ── Use ACTIVITY endpoint (has direction + levels) ──
print("\n  Fetching activity history (last 10 days)...")
dt_from = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S")
dt_to = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

resp = session.get(f"{API_URL}/api/v1/history/activity", params={
    "from": dt_from, "to": dt_to
})

activities = []
if resp.status_code == 200:
    data = resp.json()
    activities = data.get("activities", [])
    print(f"  Activities endpoint: {len(activities)} items")
    if activities:
        print(f"  Sample keys: {list(activities[0].keys())}")
        print(f"  Sample: {activities[0]}")
else:
    print(f"  Activities failed: {resp.status_code}")
    # Try lastPeriod with smaller value
    for period in [604800, 259200, 86400]:
        resp2 = session.get(f"{API_URL}/api/v1/history/activity", params={
            "lastPeriod": period
        })
        if resp2.status_code == 200:
            activities = resp2.json().get("activities", [])
            print(f"  lastPeriod={period}: {len(activities)} items")
            if activities:
                print(f"  Sample: {activities[0]}")
            break
        else:
            print(f"  lastPeriod={period}: {resp2.status_code}")

# Also fetch transactions for PnL mapping
print("\n  Fetching transactions (for PnL)...")
tx_resp = session.get(f"{API_URL}/api/v1/history/transactions", params={
    "from": dt_from, "to": dt_to, "type": "TRADE"
})
transactions = {}
if tx_resp.status_code == 200:
    for tx in tx_resp.json().get("transactions", []):
        deal_id = tx.get("dealId", "")
        pnl_str = str(tx.get("size", "0"))
        pnl = float(pnl_str.replace("SGD", "").replace(",", "").strip() or "0")
        transactions[deal_id] = {"pnl": pnl, "date": tx.get("dateUtc", "")}
    print(f"  Transactions: {len(transactions)} (PnL lookup)")
else:
    print(f"  Transactions: {tx_resp.status_code}")

# ── Parse activities into trades ──
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
seen_deals = set()

for act in activities:
    # Look for position close activities
    act_type = act.get("type", "").upper()
    status = act.get("status", "").upper()
    
    # Different activity formats Capital.com might return
    deal_id = act.get("dealId", act.get("dealReference", ""))
    epic_raw = act.get("epic", act.get("instrumentName", act.get("market", "")))
    direction = act.get("direction", act.get("dealDirection", "")).upper()
    
    # Get details from nested objects if present
    details = act.get("details", {}) or {}
    if not direction:
        direction = details.get("direction", "").upper()
    if not epic_raw:
        epic_raw = details.get("epic", details.get("instrumentName", ""))
    
    open_level = float(act.get("openLevel", details.get("openLevel", 0)) or 0)
    close_level = float(act.get("closeLevel", details.get("closeLevel", act.get("level", 0))) or 0)
    size = float(act.get("size", details.get("size", 0)) or 0)
    
    # Get PnL from transaction lookup
    pnl = float(act.get("profit", act.get("profitAndLoss", 0)) or 0)
    if pnl == 0 and deal_id in transactions:
        pnl = transactions[deal_id]["pnl"]
    
    # Parse date
    date_str = act.get("date", act.get("dateUtc", ""))
    ts = datetime.now(timezone.utc)
    if date_str:
        try: ts = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except:
            try: ts = datetime.strptime(date_str[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            except: pass
    
    # Skip if no useful data
    if not epic_raw:
        continue
    if deal_id in seen_deals:
        continue
    seen_deals.add(deal_id)
    
    epic = norm_epic(epic_raw)
    hour = ts.hour
    sess = "asian" if hour < 7 else "london" if hour < 12 else "new_york" if hour < 21 else "late"
    
    # If no direction from activity, try to infer from PnL + note
    if not direction:
        note = str(act.get("note", act.get("description", ""))).lower()
        if "buy" in note:
            direction = "BUY"
        elif "sell" in note:
            direction = "SELL"
    
    trades.append({
        "epic": epic, "direction": direction or "UNKNOWN",
        "entry_price": open_level, "close_price": close_level,
        "pnl": pnl, "timestamp": ts.isoformat(), "session": sess,
        "zone_types": ("bearish+mss+sell" if direction == "SELL" else "bullish+mss+buy") if direction else "",
        "mss_type": ("bearish_mss" if direction == "SELL" else "bullish_mss") if direction else "",
        "timeframe": "H1", "confluence": 7,
        "deal_id": deal_id, "act_type": act_type, "status": status,
        "raw_keys": list(act.keys()),
    })

# Display ALL parsed data
print(f"\n  Parsed {len(trades)} items from activities:")
print(f"  {'#':>3} {'Epic':<12} {'Dir':<7} {'Entry':>10} {'Close':>10} {'PnL':>8} {'Date':<16} {'Type'}")
print(f"  {'-'*3} {'-'*12} {'-'*7} {'-'*10} {'-'*10} {'-'*8} {'-'*16} {'-'*10}")
w, l = 0, 0
for i, t in enumerate(trades, 1):
    if t["pnl"] > 0: w += 1
    elif t["pnl"] < 0: l += 1
    print(f"  {i:>3} {t['epic']:<12} {t['direction']:<7} {t['entry_price']:>10.4f} "
          f"{t['close_price']:>10.4f} {t['pnl']:>+7.2f} {t['timestamp'][:16]} {t['act_type']}")

if w + l > 0:
    print(f"\n  {w}W / {l}L ({w/(w+l)*100:.0f}% WR) | PnL: {sum(t['pnl'] for t in trades):+.2f} SGD")

# Filter to only trades with direction and PnL
valid_trades = [t for t in trades if t["direction"] in ("BUY", "SELL") and t["pnl"] != 0]
print(f"\n  Valid for ML (have direction + PnL): {len(valid_trades)}")

if not valid_trades:
    print("\n  ⚠️  No valid trades found.")
    print("  The activity endpoint may not return direction for your account type.")
    print("  Showing raw activity structure for debugging:")
    for act in activities[:3]:
        print(f"    {act}")
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
for t in valid_trades:
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
