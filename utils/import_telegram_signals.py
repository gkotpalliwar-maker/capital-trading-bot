#!/usr/bin/env python3
"""Import Telegram Desktop JSON signal history and label outcomes.

Usage:
    python utils/import_telegram_signals.py result.json --out data/telegram_signals_outcomes.csv

The utility:
1. Reads Telegram Desktop JSON export messages.
2. Parses flexible signal text into instrument/timeframe/direction/entry/SL/TP.
3. Replays Capital.com candles after each signal timestamp.
4. Writes raw and outcome-labelled CSVs for XGBoost training.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[1]
BOT_DIR = ROOT / "bot"
sys.path.insert(0, str(BOT_DIR))

from capital_client import CapitalClient
from config import CAPITAL_API_KEY, CAPITAL_API_URL, CAPITAL_EMAIL, CAPITAL_PASSWORD, resolve_instrument
from data_fetcher import add_technical_indicators, fetch_candles
from telegram_signal_parser import parse_signal_text


TIMEFRAME_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440, "D": 1440}


def _client() -> CapitalClient:
    missing = [
        name for name, value in {
            "CAPITAL_API_KEY": CAPITAL_API_KEY,
            "CAPITAL_EMAIL": CAPITAL_EMAIL,
            "CAPITAL_PASSWORD": CAPITAL_PASSWORD,
        }.items()
        if not value
    ]
    if missing:
        raise SystemExit(f"Missing required env values: {', '.join(missing)}")
    return CapitalClient(CAPITAL_API_URL, CAPITAL_API_KEY, CAPITAL_EMAIL, CAPITAL_PASSWORD)


def _flatten_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text", "")))
        return "".join(parts)
    return str(value or "")


def _parse_dt(raw: str) -> datetime:
    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _read_telegram_messages(path: Path) -> Iterable[Dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    messages = payload.get("messages", payload if isinstance(payload, list) else [])
    for msg in messages:
        if msg.get("type") not in (None, "message"):
            continue
        text = _flatten_text(msg.get("text", ""))
        if not text.strip():
            continue
        raw_date = msg.get("date") or msg.get("date_unixtime")
        if not raw_date:
            continue
        if msg.get("date_unixtime"):
            timestamp = datetime.fromtimestamp(int(msg["date_unixtime"]), tz=timezone.utc)
        else:
            timestamp = _parse_dt(raw_date)
        yield {"timestamp": timestamp, "text": text, "message_id": msg.get("id", "")}


def _format_capital_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _simulate_outcome(df, signal: Dict, max_bars: int) -> Dict:
    if df.empty:
        return {"outcome": "NO_DATA", "outcome_bars": 0, "outcome_r": 0.0, "outcome_time": ""}

    entry = float(signal.get("entry") or 0)
    sl = float(signal.get("sl") or 0)
    tp = float(signal.get("tp") or 0)
    direction = signal.get("direction", "")
    if not entry or not sl or not tp:
        return {"outcome": "MISSING_LEVELS", "outcome_bars": 0, "outcome_r": 0.0, "outcome_time": ""}

    risk = abs(entry - sl)
    if risk <= 0:
        return {"outcome": "INVALID_RISK", "outcome_bars": 0, "outcome_r": 0.0, "outcome_time": ""}

    # Start with first candle after the message timestamp. If both are hit in
    # one candle, count SL first as the conservative assumption.
    future = df.iloc[:max_bars]
    for offset, (ts, row) in enumerate(future.iterrows(), start=1):
        high = float(row["high"])
        low = float(row["low"])
        if direction == "BUY":
            hit_sl = low <= sl
            hit_tp = high >= tp
        else:
            hit_sl = high >= sl
            hit_tp = low <= tp

        if hit_sl:
            return {"outcome": "SL", "outcome_bars": offset, "outcome_r": -1.0, "outcome_time": str(ts)}
        if hit_tp:
            return {"outcome": "TP", "outcome_bars": offset, "outcome_r": round(abs(tp - entry) / risk, 2), "outcome_time": str(ts)}

    if future.empty:
        return {"outcome": "NO_DATA", "outcome_bars": 0, "outcome_r": 0.0, "outcome_time": ""}

    last_close = float(future["close"].iloc[-1])
    open_r = (last_close - entry) / risk if direction == "BUY" else (entry - last_close) / risk
    return {"outcome": "OPEN", "outcome_bars": len(future), "outcome_r": round(open_r, 2), "outcome_time": str(future.index[-1])}


def _trend_from_df(df) -> str:
    if df.empty or len(df) < 30:
        return "neutral"
    ind = add_technical_indicators(df)
    last = ind.iloc[-1]
    if last.get("ema_short", 0) > last.get("ema_long", 0):
        return "bullish"
    if last.get("ema_short", 0) < last.get("ema_long", 0):
        return "bearish"
    return "neutral"


def _atr_ratio(df) -> float:
    if df.empty or len(df) < 30:
        return 1.0
    ind = add_technical_indicators(df)
    latest = float(ind["atr"].iloc[-1] or 0)
    avg = float(ind["atr"].tail(50).mean() or latest or 1.0)
    return round(latest / avg, 4) if avg else 1.0


def _fetch_window(client, instrument: str, timeframe: str, timestamp: datetime, bars_before: int, bars_after: int):
    minutes = TIMEFRAME_MINUTES.get(timeframe.upper(), 15)
    start = timestamp - timedelta(minutes=minutes * bars_before)
    end = timestamp + timedelta(minutes=minutes * bars_after)
    count = min(1000, bars_before + bars_after + 20)
    df = fetch_candles(
        client,
        instrument,
        timeframe,
        count=count,
        from_time=_format_capital_dt(start),
        to_time=_format_capital_dt(end),
    )
    if df.empty:
        return df
    ts_naive = timestamp.replace(tzinfo=None)
    return df[df.index >= ts_naive].copy()


def import_signals(args) -> List[Dict]:
    client = _client()
    rows = []
    parsed_count = 0
    for msg in _read_telegram_messages(Path(args.telegram_json)):
        parsed = parse_signal_text(msg["text"])
        if not parsed:
            continue
        parsed_count += 1
        timestamp = msg["timestamp"]
        instrument = parsed["instrument"]
        timeframe = parsed["tf"]
        epic = resolve_instrument(instrument)

        df_after = _fetch_window(client, instrument, timeframe, timestamp, args.bars_before, args.outcome_bars)
        outcome = _simulate_outcome(df_after, parsed, args.outcome_bars)

        # Light context for the XGBoost feature set.
        context_start = timestamp - timedelta(hours=24)
        context_end = timestamp + timedelta(minutes=5)
        h1_df = fetch_candles(client, instrument, "H1", count=200, from_time=_format_capital_dt(context_start), to_time=_format_capital_dt(context_end))
        h4_df = fetch_candles(client, instrument, "H4", count=200, from_time=_format_capital_dt(context_start - timedelta(days=5)), to_time=_format_capital_dt(context_end))

        row = {
            "time": timestamp.isoformat(),
            "message_id": msg["message_id"],
            "instrument": instrument,
            "epic": epic,
            "timeframe": timeframe,
            "direction": parsed["direction"],
            "entry": parsed["entry"],
            "sl": parsed["sl"],
            "tp": parsed["tp"],
            "rr": round(float(parsed.get("rr") or 0), 2),
            "h1_trend": _trend_from_df(h1_df),
            "h4_trend": _trend_from_df(h4_df),
            "atr_ratio": _atr_ratio(df_after),
            "outcome": outcome["outcome"],
            "outcome_bars": outcome["outcome_bars"],
            "outcome_r": outcome["outcome_r"],
            "outcome_time": outcome["outcome_time"],
            "raw_text": msg["text"].replace("\n", " ")[:500],
        }
        rows.append(row)
        if args.limit and len(rows) >= args.limit:
            break
        if args.progress_every and len(rows) % args.progress_every == 0:
            print(f"labelled {len(rows)} signals (parsed {parsed_count})", flush=True)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Import Telegram exported signals and label outcomes")
    parser.add_argument("telegram_json", help="Telegram Desktop JSON export, usually result.json")
    parser.add_argument("--out", default="data/telegram_signals_outcomes.csv", help="Labelled output CSV")
    parser.add_argument("--raw-out", default="data/telegram_signals_raw.csv", help="Raw parsed output CSV")
    parser.add_argument("--outcome-bars", type=int, default=48, help="Future candles to inspect for TP/SL")
    parser.add_argument("--bars-before", type=int, default=10, help="Candle buffer before message timestamp")
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--limit", type=int, default=0, help="Limit labelled signals for test runs")
    args = parser.parse_args()

    rows = import_signals(args)
    out = ROOT / args.out
    raw_out = ROOT / args.raw_out
    out.parent.mkdir(parents=True, exist_ok=True)
    raw_out.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "time", "message_id", "instrument", "epic", "timeframe", "direction",
        "entry", "sl", "tp", "rr", "h1_trend", "h4_trend", "atr_ratio",
        "outcome", "outcome_bars", "outcome_r", "outcome_time", "raw_text",
    ]
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    with raw_out.open("w", newline="", encoding="utf-8") as fh:
        raw_fields = ["time", "message_id", "instrument", "epic", "timeframe", "direction", "entry", "sl", "tp", "rr", "raw_text"]
        writer = csv.DictWriter(fh, fieldnames=raw_fields)
        writer.writeheader()
        writer.writerows([{k: row.get(k, "") for k in raw_fields} for row in rows])

    print(f"Parsed and labelled {len(rows)} signals")
    print(f"Wrote {out}")
    print(f"Wrote {raw_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
