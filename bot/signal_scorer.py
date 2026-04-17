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