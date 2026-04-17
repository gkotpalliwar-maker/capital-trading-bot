#!/bin/bash
set -e
cd /root/trading-bot
echo "==========================================================="
echo "  Trading Bot v2.3.0 — FULLY AUTOMATED"
echo "  ML Signal Scoring + Breakeven & Partial TP"
echo "==========================================================="

# ── BACKUP ──────────────────────────────────────────────────────
BACKUP="backups/v2.2.9_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP"
for f in bot/config.py bot/telegram_bot.py bot/scanner.py bot/execution.py; do
    [ -f "$f" ] && cp "$f" "$BACKUP/"
done
echo "✅ Backed up to $BACKUP"

# ════════════════════════════════════════════════════════════════
# MODULE 1: signal_scorer.py — ML Classifier for Signal Quality
# ════════════════════════════════════════════════════════════════
cat > bot/signal_scorer.py << 'PYEOF'
import os, json, pickle, logging, sqlite3
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from threading import Lock
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import LabelEncoder
from sklearn.model_selection import cross_val_score

logger = logging.getLogger("signal_scorer")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "trading.db"
MODEL_PATH = DATA_DIR / "signal_model.pkl"
ENCODERS_PATH = DATA_DIR / "signal_encoders.pkl"
META_PATH = DATA_DIR / "signal_model_meta.json"
_model_lock = Lock()
_model = _encoders = _model_meta = _last_train_time = None
_trades_at_train = 0
ML_CONFIDENCE_THRESHOLD = float(os.getenv("ML_CONFIDENCE_THRESHOLD", "0.35"))
ML_RETRAIN_HOURS = int(os.getenv("ML_RETRAIN_HOURS", "24"))
ML_MIN_TRADES = int(os.getenv("ML_MIN_TRADES", "30"))
CATEGORICAL_FEATURES = ["combo", "mss_type", "regime_trend", "regime_vol", "session", "timeframe", "instrument_category"]
NUMERIC_FEATURES = ["confluence", "rsi", "adx", "atr_ratio", "hour", "day_of_week"]

def _get_instrument_category(epic):
    if epic in ("EURUSD","GBPUSD","USDJPY","AUDUSD","NZDUSD","USDCAD","USDCHF"): return "forex"
    if epic in ("GOLD","SILVER","OIL_CRUDE"): return "commodity"
    if epic in ("US100","US500","US30"): return "index"
    if epic in ("BTCUSD","ETHUSD"): return "crypto"
    return "other"

def _parse_regime(regime_str):
    if not regime_str: return "unknown", "normal"
    parts = regime_str.lower().split("+") if "+" in regime_str else regime_str.lower().split("_")
    return (parts[0] if parts else "unknown"), (parts[1] if len(parts) > 1 else "normal")

def _extract_features(row):
    regime_trend, regime_vol = _parse_regime(row.get("regime", ""))
    ts = row.get("opened_at") or row.get("timestamp") or ""
    hour, dow = 12, 3
    if ts:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00")) if isinstance(ts, str) else ts
            hour, dow = dt.hour, dt.weekday()
        except: pass
    return {"combo": str(row.get("combo","unknown")).lower(), "mss_type": str(row.get("mss_type","none")).lower(),
            "regime_trend": regime_trend, "regime_vol": regime_vol, "session": str(row.get("session","unknown")).lower(),
            "timeframe": str(row.get("timeframe","M15")).upper(), "instrument_category": _get_instrument_category(row.get("epic","")),
            "confluence": float(row.get("confluence",0)), "rsi": float(row.get("rsi",50)), "adx": float(row.get("adx",25)),
            "atr_ratio": float(row.get("atr_ratio",1.0)), "hour": hour, "day_of_week": dow}

def _load_closed_trades():
    if not DB_PATH.exists(): return []
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    trades = [dict(r) for r in conn.cursor().execute("SELECT * FROM trades WHERE status='closed' AND pnl IS NOT NULL").fetchall()]
    conn.close()
    return trades

def _build_training_data(trades):
    X_raw, y = [], []
    for t in trades:
        X_raw.append(_extract_features(t))
        y.append(1 if float(t.get("pnl",0)) > 0 else 0)
    return X_raw, np.array(y)

def _encode_features(X_raw, fit=False):
    global _encoders
    if fit or _encoders is None:
        _encoders = {cat: LabelEncoder() for cat in CATEGORICAL_FEATURES}
    X = []
    for row in X_raw:
        enc_row = []
        for cat in CATEGORICAL_FEATURES:
            val, enc = row.get(cat, "unknown"), _encoders[cat]
            if fit:
                if not hasattr(enc, 'classes_') or len(enc.classes_) == 0: enc.fit([val, "unknown"])
                elif val not in enc.classes_: enc.classes_ = np.append(enc.classes_, val)
            try: enc_row.append(enc.transform([val])[0])
            except: enc_row.append(0)
        for num in NUMERIC_FEATURES: enc_row.append(row.get(num, 0))
        X.append(enc_row)
    return np.array(X)

def train_model(force=False):
    global _model, _model_meta, _last_train_time, _trades_at_train
    trades = _load_closed_trades()
    n = len(trades)
    if n < ML_MIN_TRADES:
        logger.info(f"Only {n} trades, need {ML_MIN_TRADES}. ML bypassed.")
        return False, f"Need {ML_MIN_TRADES} trades (have {n})"
    with _model_lock:
        X_raw, y = _build_training_data(trades)
        X = _encode_features(X_raw, fit=True)
        clf = RandomForestClassifier(n_estimators=100, max_depth=8, min_samples_split=5, class_weight="balanced", random_state=42, n_jobs=-1)
        cv = cross_val_score(clf, X, y, cv=min(5, n//5), scoring="accuracy")
        clf.fit(X, y)
        names = CATEGORICAL_FEATURES + NUMERIC_FEATURES
        imp = dict(zip(names, clf.feature_importances_))
        with open(MODEL_PATH,"wb") as f: pickle.dump(clf, f)
        with open(ENCODERS_PATH,"wb") as f: pickle.dump(_encoders, f)
        _model, _last_train_time, _trades_at_train = clf, datetime.utcnow(), n
        _model_meta = {"trained_at": _last_train_time.isoformat(), "n_trades": n, "cv_accuracy": float(np.mean(cv)),
                       "cv_std": float(np.std(cv)), "feature_importance": imp, "win_rate": float(y.mean())}
        with open(META_PATH,"w") as f: json.dump(_model_meta, f, indent=2)
        logger.info(f"Model trained: {n} trades, CV {np.mean(cv):.1%}")
        return True, _model_meta

def _load_model():
    global _model, _encoders, _model_meta
    if MODEL_PATH.exists() and ENCODERS_PATH.exists():
        with open(MODEL_PATH,"rb") as f: _model = pickle.load(f)
        with open(ENCODERS_PATH,"rb") as f: _encoders = pickle.load(f)
        if META_PATH.exists():
            with open(META_PATH) as f: _model_meta = json.load(f)
        return True
    return False

def _should_retrain():
    if _model is None: return True
    if _last_train_time and (datetime.utcnow() - _last_train_time).total_seconds()/3600 >= ML_RETRAIN_HOURS: return True
    if len(_load_closed_trades()) >= _trades_at_train + 10: return True
    return False

def score_signal(data: dict) -> float:
    global _model, _encoders
    if _model is None and not _load_model():
        if not train_model()[0]: return 1.0
    if _should_retrain(): train_model()
    with _model_lock:
        if _model is None: return 1.0
        try:
            X = _encode_features([_extract_features(data)], fit=False)
            p = _model.predict_proba(X)[0]
            return float(p[1] if len(p) > 1 else p[0])
        except Exception as e:
            logger.error(f"Score error: {e}")
            return 1.0

def should_take_signal(data: dict, threshold: float = None) -> tuple:
    th = threshold or ML_CONFIDENCE_THRESHOLD
    s = score_signal(data)
    return (True, s, f"ML {s:.1%} >= {th:.1%}") if s >= th else (False, s, f"ML {s:.1%} < {th:.1%}")

def get_model_stats() -> dict:
    if _model_meta: return _model_meta
    if META_PATH.exists():
        with open(META_PATH) as f: return json.load(f)
    return {"status": "No model trained yet"}

def set_threshold(v: float):
    global ML_CONFIDENCE_THRESHOLD
    ML_CONFIDENCE_THRESHOLD = max(0.0, min(1.0, v))
    return ML_CONFIDENCE_THRESHOLD

def get_threshold() -> float: return ML_CONFIDENCE_THRESHOLD
PYEOF
echo "✅ signal_scorer.py"

# ════════════════════════════════════════════════════════════════
# MODULE 2: signal_scorer_commands.py
# ════════════════════════════════════════════════════════════════
cat > bot/signal_scorer_commands.py << 'PYEOF'
import logging
from signal_scorer import get_model_stats, train_model, set_threshold, get_threshold
logger = logging.getLogger("telegram")

async def mlstats_cmd(update, context):
    try:
        stats = get_model_stats()
        if "status" in stats:
            await update.message.reply_text(f"ℹ️ {stats['status']}")
            return
        fi = stats.get("feature_importance", {})
        fi_text = "\n".join([f"  {k}: {v:.1%}" for k, v in sorted(fi.items(), key=lambda x: x[1], reverse=True)[:5]])
        text = (f"🤖 <b>ML Signal Scorer</b>\n\n<b>Model:</b>\n  Acc: {stats.get('cv_accuracy',0):.1%} ± {stats.get('cv_std',0):.1%}\n"
                f"  Trades: {stats.get('n_trades',0)}\n  WR: {stats.get('win_rate',0):.1%}\n  At: {stats.get('trained_at','N/A')[:16]}\n\n"
                f"<b>Top Features:</b>\n{fi_text}\n\n<b>Threshold:</b> {get_threshold():.1%}")
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

async def retrain_cmd(update, context):
    try:
        await update.message.reply_text("🔄 Retraining...")
        ok, res = train_model(force=True)
        if ok:
            await update.message.reply_text(f"✅ <b>Retrained</b>\nTrades: {res.get('n_trades',0)}\nAcc: {res.get('cv_accuracy',0):.1%}\nWR: {res.get('win_rate',0):.1%}", parse_mode="HTML")
        else:
            await update.message.reply_text(f"⚠️ {res}")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

async def mlthreshold_cmd(update, context):
    args = context.args
    if not args:
        await update.message.reply_text(f"ℹ️ <b>ML Threshold</b>\nCurrent: {get_threshold():.1%}\n\n/mlthreshold 0.4", parse_mode="HTML")
        return
    try:
        r = set_threshold(float(args[0]))
        await update.message.reply_text(f"✅ Threshold set to {r:.1%}")
    except:
        await update.message.reply_text("❌ Must be 0-1")
PYEOF
echo "✅ signal_scorer_commands.py"

# ════════════════════════════════════════════════════════════════
# MODULE 3: trade_manager.py
# ════════════════════════════════════════════════════════════════
cat > bot/trade_manager.py << 'PYEOF'
import os, logging, sqlite3
from pathlib import Path
logger = logging.getLogger("trade_manager")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "trading.db"
BREAKEVEN_TRIGGER_R = float(os.getenv("BREAKEVEN_TRIGGER_R", "1.0"))
PARTIAL_TP_ENABLED = os.getenv("PARTIAL_TP_ENABLED", "true").lower() == "true"
PARTIAL_TP_TARGET_R = float(os.getenv("PARTIAL_TP_TARGET_R", "1.5"))
PARTIAL_TP_RATIO = float(os.getenv("PARTIAL_TP_RATIO", "0.5"))
TRAILING_AFTER_PARTIAL_ATR = float(os.getenv("TRAILING_AFTER_PARTIAL_ATR", "1.5"))

def init_trade_manager_schema():
    if not DB_PATH.exists(): return
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("PRAGMA table_info(trades)")
    cols = [r[1] for r in c.fetchall()]
    for col, td in {"breakeven_hit":"INTEGER DEFAULT 0","partial_tp_hit":"INTEGER DEFAULT 0","partial_close_deal_id":"TEXT","ml_score":"REAL","original_size":"REAL"}.items():
        if col not in cols:
            c.execute(f"ALTER TABLE trades ADD COLUMN {col} {td}")
            logger.info(f"Added: {col}")
    conn.commit()
    conn.close()

def get_open_trades_for_management():
    if not DB_PATH.exists(): return []
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    trades = [dict(r) for r in conn.cursor().execute("SELECT * FROM trades WHERE status='open'").fetchall()]
    conn.close()
    return trades

def _calc_r(t, price):
    entry, sl, d = float(t.get("entry",0)), float(t.get("stop_loss",0)), t.get("direction","BUY")
    if entry == 0 or sl == 0: return 0
    risk = abs(entry - sl)
    if risk == 0: return 0
    return ((price - entry) if d == "BUY" else (entry - price)) / risk

def check_breakeven_triggers(trades, prices, update_sl_fn, notify_fn=None):
    triggered = []
    for t in trades:
        if t.get("breakeven_hit"): continue
        did, epic, entry = t.get("deal_id"), t.get("epic"), float(t.get("entry",0))
        if epic not in prices: continue
        r = _calc_r(t, prices[epic])
        if r >= BREAKEVEN_TRIGGER_R:
            logger.info(f"BE: {did} at {r:.2f}R")
            try:
                update_sl_fn(did, entry)
                conn = sqlite3.connect(str(DB_PATH))
                conn.cursor().execute("UPDATE trades SET breakeven_hit=1 WHERE deal_id=?",(did,))
                conn.commit()
                conn.close()
                triggered.append({"deal_id":did,"epic":epic,"r":r,"sl":entry})
                if notify_fn: notify_fn(f"🛡️ <b>BREAKEVEN</b>\n<code>{did[:8]}...</code> {epic}\nR: {r:.2f}\nSL→entry")
            except Exception as e: logger.error(f"BE fail: {e}")
    return triggered

def check_partial_tp_triggers(trades, prices, partial_fn, update_sl_fn, atr_fn, notify_fn=None):
    if not PARTIAL_TP_ENABLED: return []
    triggered = []
    for t in trades:
        if t.get("partial_tp_hit"): continue
        did, epic, entry, d = t.get("deal_id"), t.get("epic"), float(t.get("entry",0)), t.get("direction")
        size = float(t.get("size",0)) or float(t.get("original_size",0))
        if epic not in prices or size == 0: continue
        cur = prices[epic]
        r = _calc_r(t, cur)
        if r >= PARTIAL_TP_TARGET_R:
            logger.info(f"PTP: {did} at {r:.2f}R")
            try:
                ps = size * PARTIAL_TP_RATIO
                pdid = partial_fn(did, ps)
                atr = atr_fn(epic) or abs(entry - float(t.get("stop_loss",entry)))
                td = atr * TRAILING_AFTER_PARTIAL_ATR
                new_sl = (cur - td) if d == "BUY" else (cur + td)
                update_sl_fn(did, new_sl)
                conn = sqlite3.connect(str(DB_PATH))
                conn.cursor().execute("UPDATE trades SET partial_tp_hit=1, partial_close_deal_id=? WHERE deal_id=?",(pdid,did))
                conn.commit()
                conn.close()
                triggered.append({"deal_id":did,"epic":epic,"r":r,"closed":ps,"sl":new_sl})
                if notify_fn: notify_fn(f"💰 <b>PARTIAL TP</b>\n<code>{did[:8]}...</code> {epic}\nR: {r:.2f}\nClosed: {PARTIAL_TP_RATIO:.0%}\nTrailing SL: {new_sl:.5f}")
            except Exception as e: logger.error(f"PTP fail: {e}")
    return triggered

def manage_trades(trades, prices, update_sl_fn, partial_fn, atr_fn, notify_fn=None):
    return {"breakeven": check_breakeven_triggers(trades, prices, update_sl_fn, notify_fn),
            "partial_tp": check_partial_tp_triggers(trades, prices, partial_fn, update_sl_fn, atr_fn, notify_fn)}

def get_trade_status(did=None):
    trades = get_open_trades_for_management()
    if did: trades = [t for t in trades if t.get("deal_id","").startswith(did)]
    return [{"deal_id":t.get("deal_id"),"epic":t.get("epic"),"direction":t.get("direction"),"entry":t.get("entry"),
             "stop_loss":t.get("stop_loss"),"breakeven_hit":bool(t.get("breakeven_hit")),"partial_tp_hit":bool(t.get("partial_tp_hit")),
             "ml_score":t.get("ml_score")} for t in trades]

def toggle_partial_tp(e=None):
    global PARTIAL_TP_ENABLED
    PARTIAL_TP_ENABLED = (not PARTIAL_TP_ENABLED) if e is None else e
    return PARTIAL_TP_ENABLED

def get_settings():
    return {"breakeven_trigger_r":BREAKEVEN_TRIGGER_R,"partial_tp_enabled":PARTIAL_TP_ENABLED,"partial_tp_target_r":PARTIAL_TP_TARGET_R,"partial_tp_ratio":PARTIAL_TP_RATIO,"trailing_after_partial_atr":TRAILING_AFTER_PARTIAL_ATR}
PYEOF
echo "✅ trade_manager.py"

# ════════════════════════════════════════════════════════════════
# MODULE 4: trade_manager_commands.py
# ════════════════════════════════════════════════════════════════
cat > bot/trade_manager_commands.py << 'PYEOF'
import logging
from trade_manager import get_trade_status, get_settings, toggle_partial_tp
logger = logging.getLogger("telegram")

async def breakeven_cmd(update, context):
    try:
        st = get_trade_status()
        if not st: await update.message.reply_text("ℹ️ No open trades."); return
        lines = ["🛡️ <b>Breakeven Status</b>\n"]
        for t in st:
            lines.append(f"{'✅' if t['breakeven_hit'] else '⏳'} <code>{t['deal_id'][:8]}...</code> {t['epic']} | SL: {t['stop_loss']}")
        lines.append(f"\nTrigger: {get_settings()['breakeven_trigger_r']}R")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e: await update.message.reply_text(f"❌ {e}")

async def partialtp_cmd(update, context):
    try:
        args = context.args
        s = get_settings()
        if args and args[0].lower() in ("on","off"):
            toggle_partial_tp(args[0].lower() == "on")
            await update.message.reply_text(f"✅ Partial TP {'on' if args[0].lower()=='on' else 'off'}")
            return
        st = get_trade_status()
        ph = sum(1 for t in st if t.get("partial_tp_hit"))
        text = (f"💰 <b>Partial TP</b>\n\nStatus: {'✅' if s['partial_tp_enabled'] else '❌'}\nTarget: {s['partial_tp_target_r']}R\n"
                f"Ratio: {s['partial_tp_ratio']:.0%}\nTrail: {s['trailing_after_partial_atr']} ATR\n\nOpen: {len(st)} | Partial: {ph}\n\n/partialtp on|off")
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e: await update.message.reply_text(f"❌ {e}")

async def trademanage_cmd(update, context):
    try:
        args = context.args
        if not args: await update.message.reply_text("ℹ️ /trademanage <deal_id>"); return
        st = get_trade_status(args[0])
        if not st: await update.message.reply_text(f"❌ Not found: {args[0]}"); return
        t, s = st[0], get_settings()
        be = "✅" if t["breakeven_hit"] else f"⏳ at {s['breakeven_trigger_r']}R"
        pt = "✅" if t["partial_tp_hit"] else f"⏳ at {s['partial_tp_target_r']}R"
        text = (f"📊 <b>Trade</b>\n<code>{t['deal_id']}</code>\n{t['epic']} {t['direction']}\n\n"
                f"Entry: {t['entry']}\nSL: {t['stop_loss']}\nML: {t.get('ml_score','N/A')}\n\nBE: {be}\nPTP: {pt}")
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e: await update.message.reply_text(f"❌ {e}")
PYEOF
echo "✅ trade_manager_commands.py"
echo ""

# ════════════════════════════════════════════════════════════════
# PYTHON PATCHER
# ════════════════════════════════════════════════════════════════
echo "🔧 Patching..."
python3 << 'PATCHEOF'
import re, os
def patch(path, patches, lbl):
    if not os.path.exists(path): print(f"  ⚠️ {lbl}: not found"); return
    with open(path) as f: code = f.read()
    orig = code
    for n, fn in patches:
        r = fn(code)
        if r != code: code = r; print(f"  ✅ {lbl}: {n}")
        else: print(f"  ⏭️ {lbl}: {n} (done)")
    if code != orig:
        with open(path,"w") as f: f.write(code)

# scanner.py
def add_ml_imp(c):
    if "signal_scorer" in c: return c
    i = "\nfrom signal_scorer import should_take_signal, score_signal\nfrom trade_manager import init_trade_manager_schema, manage_trades, get_open_trades_for_management\n"
    for m in ["from trade_validator import", "from instrument_manager import"]:
        if m in c:
            idx = c.index(m); end = c.index("\n", idx)
            return c[:end+1] + i + c[end+1:]
    return i + c

def add_init_tm(c):
    if "init_trade_manager_schema()" in c: return c
    if "init_validation_schema()" in c:
        idx = c.index("init_validation_schema()"); end = c.index("\n", idx)
        return c[:end+1] + "    init_trade_manager_schema()\n" + c[end+1:]
    return c

def add_ml_filter(c):
    if "should_take_signal(sig_data)" in c: return c
    for p in [r'([ \t]+)(notify_signal\(sig_data\))', r'([ \t]+)(send_signal_alert\(sig_data\))', r'([ \t]+)(await.*notify.*signal)']:
        m = re.search(p, c, re.I)
        if m:
            ind = m.group(1)
            ml = (f"{ind}# v2.3.0: ML\n{ind}ml_ok, ml_sc, ml_r = should_take_signal(sig_data)\n"
                  f"{ind}sig_data['ml_score'] = ml_sc\n{ind}if not ml_ok:\n"
                  f'{ind}    logger.info(f"ML BLOCKED: {{sig_data.get(\'instrument\')}} - {{ml_r}}")\n{ind}    continue\n\n')
            return c[:m.start()] + ml + c[m.start():]
    return c

def add_tm_call(c):
    if "manage_trades(" in c: return c
    for p in [r'([ \t]+)(validate_all_open_trades\([^)]+\))', r'([ \t]+)(trailing_stop_monitor\([^)]+\))', r'([ \t]+)(logger\.info.*[Ss]can.*[Cc]omplete.*)']:
        m = re.search(p, c, re.I)
        if m:
            ind = m.group(1)
            tm = (f"\n\n{ind}# v2.3.0: Trade Mgmt\n{ind}try:\n{ind}    otr = get_open_trades_for_management()\n"
                  f"{ind}    if otr:\n{ind}        cpr = {{t['epic']: get_current_price(t['epic']) for t in otr}}\n"
                  f"{ind}        manage_trades(otr, cpr, update_position_sl, partial_close_position, get_instrument_atr, send_telegram_message)\n"
                  f'{ind}except Exception as e: logger.error(f"TM error: {{e}}")\n')
            return c[:m.end()] + tm + c[m.end():]
    return c

patch("bot/scanner.py", [("ML imports", add_ml_imp), ("init_tm", add_init_tm), ("ML filter", add_ml_filter), ("TM call", add_tm_call)], "scanner")

# telegram_bot.py
def add_tg_imp(c):
    if "signal_scorer_commands" in c: return c
    i = "\nfrom signal_scorer_commands import mlstats_cmd, retrain_cmd, mlthreshold_cmd\nfrom trade_manager_commands import breakeven_cmd, partialtp_cmd, trademanage_cmd\n"
    for m in ["from trade_validator_commands import", "from instrument_commands import", "import logging"]:
        if m in c:
            idx = c.index(m); end = c.index("\n", idx)
            return c[:end+1] + i + c[end+1:]
    return i + c

def add_tg_handlers(c):
    if 'CommandHandler("mlstats"' in c: return c
    for p in [r'(app\.add_handler\(CommandHandler\("validity"[^)]+\)\))', r'(app\.add_handler\(CommandHandler\("help"[^)]+\)\))']:
        m = re.search(p, c)
        if m:
            h = ('\n    # v2.3.0\n    app.add_handler(CommandHandler("mlstats", mlstats_cmd))\n'
                 '    app.add_handler(CommandHandler("retrain", retrain_cmd))\n'
                 '    app.add_handler(CommandHandler("mlthreshold", mlthreshold_cmd))\n'
                 '    app.add_handler(CommandHandler("breakeven", breakeven_cmd))\n'
                 '    app.add_handler(CommandHandler("partialtp", partialtp_cmd))\n'
                 '    app.add_handler(CommandHandler("trademanage", trademanage_cmd))\n')
            return c[:m.end()] + h + c[m.end():]
    return c

patch("bot/telegram_bot.py", [("TG imports", add_tg_imp), ("TG handlers", add_tg_handlers)], "telegram_bot")

# execution.py
def add_exec_funcs(c):
    if "def partial_close_position" in c: return c
    f = """

def partial_close_position(deal_id: str, close_size: float) -> str:
    import logging; logger = logging.getLogger("execution")
    try:
        positions = client.get("/api/v1/positions").get("positions", [])
        pos = next((p for p in positions if p.get("position", {}).get("dealId") == deal_id), None)
        if not pos: logger.error(f"Not found: {deal_id}"); return None
        p, m = pos["position"], pos["market"]
        orig_sz, rem_sz = abs(float(p.get("size", 0))), abs(float(p.get("size", 0))) - close_size
        if rem_sz <= 0: client.delete(f"/api/v1/positions/{deal_id}"); return None
        d, e, sl, tp = p.get("direction","BUY"), m.get("epic"), p.get("stopLevel"), p.get("profitLevel")
        client.delete(f"/api/v1/positions/{deal_id}")
        logger.info(f"Closed {deal_id} for PTP")
        od = {"epic": e, "direction": d, "size": rem_sz}
        if sl: od["stopLevel"] = sl
        if tp: od["profitLevel"] = tp
        resp = client.post("/api/v1/positions", od)
        nid = resp.get("dealReference") or resp.get("dealId")
        logger.info(f"Reopened: {nid} size={rem_sz}")
        return nid
    except Exception as e: logger.error(f"PTP fail: {e}"); raise

def update_position_sl(deal_id: str, new_sl: float) -> bool:
    import logging; logger = logging.getLogger("execution")
    try:
        positions = client.get("/api/v1/positions").get("positions", [])
        pos = next((p for p in positions if p.get("position", {}).get("dealId") == deal_id), None)
        if not pos: logger.error(f"Not found: {deal_id}"); return False
        tp = pos["position"].get("profitLevel")
        ud = {"stopLevel": new_sl}
        if tp: ud["profitLevel"] = tp
        client.put(f"/api/v1/positions/{deal_id}", ud)
        logger.info(f"SL: {deal_id} -> {new_sl}")
        return True
    except Exception as e: logger.error(f"SL fail: {e}"); return False

def get_current_price(epic: str) -> float:
    try:
        r = client.get(f"/api/v1/markets/{epic}")
        s = r.get("snapshot", {})
        return (float(s.get("bid",0)) + float(s.get("offer",0))) / 2
    except: return 0.0

def get_instrument_atr(epic: str, period: int = 14) -> float:
    try:
        r = client.get(f"/api/v1/prices/{epic}", {"resolution": "HOUR", "max": period + 1})
        prices = r.get("prices", [])
        if len(prices) < 2: return 0.0
        trs = []
        for i in range(1, len(prices)):
            h = float(prices[i].get("highPrice", {}).get("mid", 0))
            l = float(prices[i].get("lowPrice", {}).get("mid", 0))
            pc = float(prices[i-1].get("closePrice", {}).get("mid", 0))
            trs.append(max(h-l, abs(h-pc), abs(l-pc)))
        return sum(trs)/len(trs) if trs else 0.0
    except: return 0.0
"""
    return c.rstrip() + "\n" + f

patch("bot/execution.py", [("exec funcs", add_exec_funcs)], "execution")
print("\n✅ Patches done.")
PATCHEOF

sed -i 's/v2\.2\.[0-9]*/v2.3.0/g' bot/scanner.py 2>/dev/null || true
echo ""
echo "✅ v2.3.0 deployment complete!"
echo "New commands: /mlstats /retrain /mlthreshold /breakeven /partialtp /trademanage"
echo "Restart: systemctl restart trading-bot"
