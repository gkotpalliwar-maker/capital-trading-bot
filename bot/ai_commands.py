"""Telegram commands for the XGBoost gatekeeper."""
from __future__ import annotations

import asyncio
import glob
import math
import os
from pathlib import Path
from typing import Dict, List

import pandas as pd
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

from ai_gatekeeper import _label_from_row, _row_features, get_gatekeeper
from config import TELEGRAM_CHAT_ID, resolve_instrument

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID", TELEGRAM_CHAT_ID)


def _is_admin(update: Update) -> bool:
    allowed = str(ADMIN_ID or "").strip()
    if not allowed:
        return False
    chat_id = str(update.effective_chat.id) if update.effective_chat else ""
    user_id = str(update.effective_user.id) if update.effective_user else ""
    return allowed in {chat_id, user_id}


def _load_history_rows(instrument: str, timeframe: str) -> List[Dict]:
    files = []
    for pattern in ("backtest*.csv", "trade_backtest*.csv", "xgb_training*.csv"):
        files.extend(glob.glob(str(DATA_DIR / pattern)))

    target = resolve_instrument(instrument).upper()
    tf = timeframe.upper()
    rows: List[Dict] = []
    for path in sorted(set(files)):
        try:
            df = pd.read_csv(path)
        except Exception:
            continue
        if df.empty:
            continue
        for _, row in df.iterrows():
            item = row.to_dict()
            row_inst = str(item.get("epic") or item.get("instrument") or "").upper()
            row_tf = str(item.get("timeframe") or item.get("tf") or "").upper()
            if row_tf != tf:
                continue
            if row_inst not in {target, instrument.upper()}:
                continue
            if _label_from_row(item) is not None:
                rows.append(item)
    return rows


def _metrics(rows: List[Dict]) -> Dict:
    closed = []
    for row in rows:
        label = _label_from_row(row)
        if label is None:
            continue
        r_val = row.get("outcome_r") or row.get("pnl_r") or row.get("pnl") or (1 if label else -1)
        try:
            r_float = float(r_val)
        except Exception:
            r_float = 1.0 if label else -1.0
        closed.append((label, r_float))

    total = len(closed)
    wins = sum(1 for label, _ in closed if label == 1)
    gross_win = sum(max(0.0, r) for _, r in closed)
    gross_loss = abs(sum(min(0.0, r) for _, r in closed))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else math.inf if gross_win > 0 else 0.0
    return {
        "total": total,
        "wins": wins,
        "win_rate": wins / total if total else 0.0,
        "profit_factor": profit_factor,
    }


def _run_retest(instrument: str, timeframe: str, threshold: float = 0.55) -> Dict:
    rows = _load_history_rows(instrument, timeframe)
    gatekeeper = get_gatekeeper()
    model_loaded = gatekeeper.load_model()

    if not rows:
        return {"error": f"No labelled history found for {instrument.upper()} {timeframe.upper()}."}

    baseline = _metrics(rows)
    filtered_rows = []
    blocked = 0
    for row in rows:
        features = _row_features(row)
        if model_loaded:
            try:
                approved = gatekeeper.predict_approval(features, threshold=threshold)
            except Exception:
                approved = True
        else:
            approved = True
        if approved:
            filtered_rows.append(row)
        else:
            blocked += 1

    return {
        "instrument": instrument.upper(),
        "timeframe": timeframe.upper(),
        "model_loaded": model_loaded,
        "model_error": gatekeeper.last_error,
        "baseline": baseline,
        "filtered": _metrics(filtered_rows),
        "blocked": blocked,
        "history_rows": len(rows),
    }


def _fmt_pf(value: float) -> str:
    if value == math.inf:
        return "inf"
    return f"{value:.2f}"


async def retest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        await update.message.reply_text("Unauthorized.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /retest EURUSD M15")
        return

    instrument = context.args[0]
    timeframe = context.args[1].upper()
    await update.message.reply_text(f"Running retest for {instrument.upper()} {timeframe}...")
    result = await asyncio.to_thread(_run_retest, instrument, timeframe)

    if result.get("error"):
        await update.message.reply_text(result["error"])
        return

    base = result["baseline"]
    ai = result["filtered"]
    model_line = "loaded" if result["model_loaded"] else f"not loaded ({result['model_error']})"
    text = (
        f"Retest Results for {result['instrument']} {result['timeframe']}\n"
        f"Model: {model_line}\n\n"
        f"Without AI: {base['win_rate']:.1%} WR | {base['total']} trades | PF {_fmt_pf(base['profit_factor'])}\n"
        f"With AI: {ai['win_rate']:.1%} WR | {ai['total']} trades | PF {_fmt_pf(ai['profit_factor'])}\n"
        f"Blocked Trades: {result['blocked']}"
    )
    await update.message.reply_text(text)


def register_ai_handlers(app):
    app.add_handler(CommandHandler("retest", retest_cmd))
