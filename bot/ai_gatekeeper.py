"""XGBoost gatekeeper for final signal approval.

Offline-trained model that focuses on time/session decay and lightweight
market context. It is intentionally fail-open: model or dependency failures
must not stop the trading bot.
"""
from __future__ import annotations

import csv
import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("ai_gatekeeper")

try:
    import xgboost as xgb
except Exception as exc:  # pragma: no cover - depends on VPS dependency set
    xgb = None
    logger.warning("xgboost unavailable; AI gatekeeper will fail open: %s", exc)


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MODEL_PATH = DATA_DIR / "xgb_gatekeeper.json"
META_PATH = DATA_DIR / "xgb_gatekeeper_meta.json"
DRIFT_LOG_PATH = DATA_DIR / "xgb_gatekeeper_live_features.csv"
DEFAULT_THRESHOLD = float(os.getenv("AI_GATEKEEPER_THRESHOLD", "0.55"))

FEATURE_COLUMNS = [
    "time_sin",
    "time_cos",
    "session",
    "day_of_week",
    "timeframe",
    "h1_trend",
    "h4_trend",
    "atr_ratio",
]

TIMEFRAME_MAP = {"M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440, "D": 1440}
TREND_MAP = {
    "bearish": -1.0,
    "down": -1.0,
    "sell": -1.0,
    "short": -1.0,
    "-1": -1.0,
    "neutral": 0.0,
    "flat": 0.0,
    "range": 0.0,
    "ranging": 0.0,
    "0": 0.0,
    "bullish": 1.0,
    "up": 1.0,
    "buy": 1.0,
    "long": 1.0,
    "1": 1.0,
}


def _parse_timestamp(timestamp) -> datetime:
    if isinstance(timestamp, datetime):
        dt = timestamp
    else:
        raw = str(timestamp or "")
        if not raw:
            dt = datetime.now(timezone.utc)
        else:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _market_session(dt: datetime) -> int:
    """Return 1 Asia, 2 London, 3 New York, 0 dead zone by UTC hour."""
    hour = dt.hour
    if 0 <= hour < 7:
        return 1
    if 7 <= hour < 13:
        return 2
    if 13 <= hour < 21:
        return 3
    return 0


def _trend_value(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(max(-1.0, min(1.0, value)))
    return TREND_MAP.get(str(value).strip().lower(), 0.0)


def _timeframe_value(timeframe: str) -> float:
    minutes = TIMEFRAME_MAP.get(str(timeframe or "M15").upper(), 15)
    return minutes / 240.0


def prepare_features(timestamp, timeframe, h1_trend, h4_trend, atr_ratio) -> Dict[str, float]:
    """Prepare model-ready features from live or historical signal context."""
    dt = _parse_timestamp(timestamp)
    minute_of_day = dt.hour * 60 + dt.minute
    angle = 2 * math.pi * minute_of_day / 1440.0
    return {
        "time_sin": math.sin(angle),
        "time_cos": math.cos(angle),
        "session": float(_market_session(dt)),
        "day_of_week": float(dt.weekday()) / 6.0,
        "timeframe": _timeframe_value(timeframe),
        "h1_trend": _trend_value(h1_trend),
        "h4_trend": _trend_value(h4_trend),
        "atr_ratio": float(atr_ratio or 1.0),
    }


def _feature_frame(rows: Iterable[Dict[str, float]]) -> pd.DataFrame:
    return pd.DataFrame([{col: float(row.get(col, 0.0)) for col in FEATURE_COLUMNS} for row in rows])


def _label_from_row(row: Dict) -> Optional[int]:
    for key in ("label", "target", "win", "is_win"):
        if key in row and str(row.get(key, "")).strip() != "":
            return 1 if float(row[key]) > 0 else 0
    outcome = str(row.get("outcome", "")).strip().upper()
    if outcome == "TP":
        return 1
    if outcome == "SL":
        return 0
    for key in ("outcome_r", "pnl_r", "pnl"):
        if key in row and str(row.get(key, "")).strip() != "":
            return 1 if float(row[key]) > 0 else 0
    return None


def _row_features(row: Dict) -> Dict[str, float]:
    timestamp = row.get("timestamp") or row.get("time") or row.get("opened_at")
    timeframe = row.get("timeframe") or row.get("tf")
    return prepare_features(
        timestamp=timestamp,
        timeframe=timeframe,
        h1_trend=row.get("h1_trend") or row.get("h1_bias") or row.get("trend_h1"),
        h4_trend=row.get("h4_trend") or row.get("h4_bias") or row.get("trend_h4"),
        atr_ratio=row.get("atr_ratio") or row.get("atr") or 1.0,
    )


class XGBoostGatekeeper:
    """Offline-trained XGBoost approval gate."""

    def __init__(self, model_path: Path = MODEL_PATH):
        self.model_path = Path(model_path)
        self.model = None
        self.last_error = ""

    def load_model(self) -> bool:
        if xgb is None:
            self.last_error = "xgboost is not installed"
            return False
        if not self.model_path.exists():
            self.last_error = f"model not found: {self.model_path}"
            return False
        try:
            model = xgb.XGBClassifier()
            model.load_model(str(self.model_path))
            self.model = model
            self.last_error = ""
            return True
        except Exception as exc:
            self.last_error = str(exc)
            logger.exception("Failed to load XGBoost gatekeeper")
            return False

    def train_model(self, csv_path) -> Dict:
        if xgb is None:
            raise RuntimeError("xgboost is not installed")
        try:
            from sklearn.metrics import accuracy_score, roc_auc_score
            from sklearn.model_selection import train_test_split
        except Exception as exc:
            raise RuntimeError(f"scikit-learn is not installed: {exc}") from exc

        rows = list(csv.DictReader(open(csv_path, newline="", encoding="utf-8")))
        X_rows: List[Dict[str, float]] = []
        y_rows: List[int] = []
        for row in rows:
            label = _label_from_row(row)
            if label is None:
                continue
            X_rows.append(_row_features(row))
            y_rows.append(label)

        if len(y_rows) < 30:
            raise ValueError(f"Need at least 30 labelled rows, got {len(y_rows)}")
        if len(set(y_rows)) < 2:
            raise ValueError("Training data needs both winning and losing examples")

        X = _feature_frame(X_rows)
        y = np.array(y_rows, dtype=int)
        stratify = y if min(np.bincount(y)) >= 2 else None
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=stratify
        )

        model = xgb.XGBClassifier(
            max_depth=3,
            learning_rate=0.05,
            n_estimators=80,
            subsample=0.8,
            colsample_bytree=0.8,
            eval_metric="logloss",
            random_state=42,
        )
        model.fit(X_train, y_train)
        probabilities = model.predict_proba(X_test)[:, 1]
        predictions = (probabilities >= 0.5).astype(int)

        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        model.save_model(str(self.model_path))
        self.model = model

        meta = {
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "csv_path": str(csv_path),
            "rows": int(len(y)),
            "test_rows": int(len(y_test)),
            "test_accuracy": float(accuracy_score(y_test, predictions)),
            "test_auc": float(roc_auc_score(y_test, probabilities)) if len(set(y_test)) > 1 else None,
            "feature_columns": FEATURE_COLUMNS,
            "positive_rate": float(y.mean()),
        }
        META_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        logger.info("XGBoost gatekeeper trained: %s", meta)
        return meta

    def predict_score(self, features: Dict[str, float]) -> float:
        if self.model is None and not self.load_model():
            raise RuntimeError(self.last_error or "model unavailable")
        X = _feature_frame([features])
        return float(self.model.predict_proba(X)[0, 1])

    def predict_approval(self, features: Dict[str, float], threshold: float = DEFAULT_THRESHOLD) -> bool:
        return self.predict_score(features) >= threshold

    def safe_predict(self, features: Dict[str, float], threshold: float = DEFAULT_THRESHOLD) -> Tuple[bool, Optional[float], str]:
        """Fail-open prediction for live trading."""
        try:
            score = self.predict_score(features)
            return score >= threshold, score, "model"
        except Exception as exc:
            self.last_error = str(exc)
            logger.warning("AI gatekeeper fail-open: %s", exc)
            return True, None, "fail_open"


_gatekeeper = XGBoostGatekeeper()


def get_gatekeeper() -> XGBoostGatekeeper:
    return _gatekeeper


def build_live_features(signal_data: Dict, df=None, timestamp=None) -> Dict[str, float]:
    atr_ratio = signal_data.get("atr_ratio")
    if atr_ratio is None and df is not None and "atr" in df.columns and len(df) > 20:
        latest_atr = float(df["atr"].iloc[-1] or 0)
        avg_atr = float(df["atr"].tail(50).mean() or latest_atr or 1.0)
        atr_ratio = latest_atr / avg_atr if avg_atr else 1.0

    trend = signal_data.get("regime", "")
    h1_trend = signal_data.get("h1_trend", trend if signal_data.get("tf") == "H1" else "neutral")
    h4_trend = signal_data.get("h4_trend", trend if signal_data.get("tf") == "H4" else "neutral")
    return prepare_features(
        timestamp=timestamp or signal_data.get("timestamp") or datetime.now(timezone.utc),
        timeframe=signal_data.get("tf") or signal_data.get("timeframe"),
        h1_trend=h1_trend,
        h4_trend=h4_trend,
        atr_ratio=atr_ratio or 1.0,
    )


def log_feature_drift(signal_data: Dict, features: Dict[str, float], approved: bool, score: Optional[float], source: str):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    exists = DRIFT_LOG_PATH.exists()
    with DRIFT_LOG_PATH.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "timestamp", "instrument", "timeframe", "direction", "approved",
                "score", "source", *FEATURE_COLUMNS,
            ],
        )
        if not exists:
            writer.writeheader()
        writer.writerow({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "instrument": signal_data.get("inst_name") or signal_data.get("instrument"),
            "timeframe": signal_data.get("tf") or signal_data.get("timeframe"),
            "direction": signal_data.get("direction"),
            "approved": int(bool(approved)),
            "score": "" if score is None else round(float(score), 6),
            "source": source,
            **{col: features.get(col, 0.0) for col in FEATURE_COLUMNS},
        })


def evaluate_signal(signal_data: Dict, df=None, threshold: float = DEFAULT_THRESHOLD) -> Tuple[bool, Optional[float], str, Dict[str, float]]:
    features = build_live_features(signal_data, df=df)
    approved, score, source = get_gatekeeper().safe_predict(features, threshold=threshold)
    log_feature_drift(signal_data, features, approved, score, source)
    return approved, score, source, features
