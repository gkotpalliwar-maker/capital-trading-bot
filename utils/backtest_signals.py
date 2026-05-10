#!/usr/bin/env python3
"""Dry-run recent candle history through the current signal decision stack.

This is a signal backtest, not a broker execution simulator. It replays rolling
windows of Capital.com candles and records what the bot would have classified as
BLOCK/WATCH/ALERT/EXECUTABLE at each step.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT_DIR = ROOT / "bot"
sys.path.insert(0, str(BOT_DIR))

from capital_client import CapitalClient
from config import (
    CAPITAL_API_KEY,
    CAPITAL_API_URL,
    CAPITAL_EMAIL,
    CAPITAL_PASSWORD,
    DEFAULT_INSTRUMENTS,
    DEFAULT_TIMEFRAMES,
    WINNING_ZONE_COMBOS,
    resolve_instrument,
)
from data_fetcher import add_technical_indicators, fetch_candles
from retrace_entry import init_retrace_scanner, scan_retrace_entry
from signal_guardrails import SignalGuardrails
from strategies.smc_ict import SMCICTStrategy
import regime_filter
import signal_decision
import signal_scorer


class _Sig:
    def __init__(self, data):
        self.direction = data["direction"]
        self.entry_price = data["entry"]
        self.stop_loss = data["sl"]
        self.take_profit = data["tp"]
        self.metadata = data
        self._rr = data.get("rr_ratio", 0)

    def risk_reward_ratio(self):
        return self._rr


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


def _signal_to_dict(sig, instrument: str, epic: str, timeframe: str, df, regime: dict) -> dict:
    zone_types = sig.metadata.get("zone_types", "")
    rsi = df["rsi"].iloc[-1] if "rsi" in df.columns else 0
    return {
        "instrument": instrument,
        "inst_name": epic,
        "tf": timeframe,
        "direction": sig.direction,
        "entry": sig.entry_price,
        "sl": sig.stop_loss,
        "tp": sig.take_profit,
        "rr": sig.risk_reward_ratio(),
        "confluence": sig.metadata.get("smc_confluence", sig.metadata.get("confluence", 0)),
        "zone_types": zone_types,
        "mss_type": sig.metadata.get("mss_type", "none"),
        "rsi": float(rsi) if rsi == rsi else 0,
        "top5": zone_types in WINNING_ZONE_COMBOS,
        "risk_pct": abs(sig.entry_price - sig.stop_loss) / sig.entry_price * 100 if sig.entry_price else 0,
        "session": "backtest",
        "regime": regime.get("label", ""),
    }


def run_backtest(args) -> list[dict]:
    client = _client()
    strategy = SMCICTStrategy()
    retrace = init_retrace_scanner()
    guardrails = SignalGuardrails(market_intel=None)
    rows = []

    instruments = args.instruments or DEFAULT_INSTRUMENTS
    timeframes = args.timeframes or DEFAULT_TIMEFRAMES

    for instrument in instruments:
        epic = resolve_instrument(instrument)
        for timeframe in timeframes:
            raw = fetch_candles(client, instrument, timeframe, count=args.candles)
            if raw.empty or len(raw) < args.window:
                print(f"skip {instrument} {timeframe}: only {len(raw)} candles")
                continue

            print(f"replay {instrument} {timeframe}: {len(raw)} candles")
            start = max(args.window, 80)
            for end in range(start, len(raw) + 1, args.step):
                df = add_technical_indicators(raw.iloc[:end].copy())
                regime = regime_filter.detect_regime(df)
                signals = strategy.generate_signals(df, instrument, timeframe)

                if args.include_retrace:
                    try:
                        signals.extend(_Sig(s) for s in scan_retrace_entry(df, instrument, timeframe))
                    except Exception as exc:
                        print(f"warn retrace {instrument} {timeframe} @{end}: {exc}")

                for sig in signals:
                    sig_data = _signal_to_dict(sig, instrument, epic, timeframe, df, regime)
                    decision = signal_decision.evaluate_signal_candidate(
                        signal=sig_data,
                        df=df,
                        client=None,
                        instrument=epic,
                        timeframe=timeframe,
                        regime=regime,
                        guardrails=guardrails,
                        risk_manager=None,
                        news_filter_mod=None if args.technical_only else None,
                        ml_scorer_mod=signal_scorer if args.use_ml else None,
                        mtf_func=None,
                    )
                    memory = decision["modifiers"].get("market_memory") or {}
                    rows.append({
                        "time": str(df.index[-1]),
                        "instrument": instrument,
                        "epic": epic,
                        "timeframe": timeframe,
                        "direction": sig_data["direction"],
                        "status": decision["status"],
                        "score": decision["score"],
                        "quality": decision["quality"],
                        "zone_types": sig_data["zone_types"],
                        "mss_type": sig_data["mss_type"],
                        "rr": round(float(sig_data.get("rr") or 0), 2),
                        "regime": sig_data["regime"],
                        "memory_bias": memory.get("bias", "neutral"),
                        "memory_adj": memory.get("score_adj", 0),
                        "blocks": " | ".join(decision["blocks"][:3]),
                        "warnings": " | ".join(decision["warnings"][:3]),
                    })
    return rows


def print_summary(rows: list[dict]) -> None:
    print("\n=== Summary ===")
    print(f"Total candidates: {len(rows)}")
    by_status = Counter(r["status"] for r in rows)
    print("By status:", dict(by_status))
    by_pair = Counter((r["epic"], r["timeframe"], r["status"]) for r in rows)
    print("\nTop instrument/timeframe/status:")
    for (epic, tf, status), count in by_pair.most_common(20):
        print(f"  {epic:<10} {tf:<3} {status:<10} {count}")

    actionable = [r for r in rows if r["status"] in ("ALERT", "EXECUTABLE")]
    print(f"\nActionable alerts/executables: {len(actionable)}")
    for r in actionable[-30:]:
        print(
            f"  {r['time']} {r['epic']} {r['timeframe']} {r['direction']} "
            f"{r['status']} score={r['score']} rr={r['rr']} "
            f"zones={r['zone_types']} memory={r['memory_bias']}({r['memory_adj']:+})"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest recent signals through the decision engine.")
    parser.add_argument("--instruments", nargs="*", default=None, help="Instrument keys, e.g. gold eurusd btcusd")
    parser.add_argument("--timeframes", nargs="*", default=None, help="Timeframes, e.g. M15 H1 H4")
    parser.add_argument("--candles", type=int, default=500, help="Candles to fetch per instrument/timeframe")
    parser.add_argument("--window", type=int, default=160, help="Minimum rolling candles before evaluating")
    parser.add_argument("--step", type=int, default=5, help="Replay step in candles")
    parser.add_argument("--include-retrace", action="store_true", help="Include retrace-entry candidates")
    parser.add_argument("--use-ml", action="store_true", help="Include current ML scorer")
    parser.add_argument("--technical-only", action="store_true", default=True, help="Skip live news/MTF/dedup context")
    parser.add_argument("--csv", default="data/backtest_signals.csv", help="CSV output path")
    args = parser.parse_args()

    rows = run_backtest(args)
    print_summary(rows)

    out = ROOT / args.csv
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["time"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
