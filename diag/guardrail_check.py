#!/usr/bin/env python3
"""
Diagnostic: Show full guardrail evaluation for Gold and Crude.
Usage: cd /opt/trading-bot && venv/bin/python3 diag/guardrail_check.py
"""
import sys, os
sys.path.insert(0, "/opt/trading-bot")
os.chdir("/opt/trading-bot")

import json
from pathlib import Path

# Load bot config
config = json.loads(Path("config.json").read_text())

# Initialize Capital.com client
from bot.capital_client import CapitalClient
client = CapitalClient(config["api_key"], config["identifier"], config["password"],
                      demo=config.get("demo", True))
client.create_session()

# Import scanner's candle fetcher
from bot.candle_fetcher import fetch_candles_for_scanner
from bot.signal_guardrails import SignalGuardrails
import pandas as pd

guardrails = SignalGuardrails()

print("=" * 70)
print("  GUARDRAIL DIAGNOSTIC: Gold & Crude Signal Evaluation")
print("=" * 70)

instruments = [
    ("gold", "BUY"), ("gold", "SELL"),
    ("crude", "BUY"), ("crude", "SELL"),
]

for instrument, direction in instruments:
    for tf in ["H1", "H4"]:
        print(f"\n{'─'*60}")
        print(f"  {instrument.upper()} {tf} {direction}")
        print(f"{'─'*60}")

        try:
            df = fetch_candles_for_scanner(client, instrument, tf, count=200)
            close = df["close"].iloc[-1]
            low5 = df["low"].iloc[-5:].min()
            high5 = df["high"].iloc[-5:].max()

            # Simulate signal metadata
            if direction == "BUY":
                sl = low5
                tp = close + (close - low5) * 1.5
            else:
                sl = high5
                tp = close - (high5 - close) * 1.5

            signal_metadata = {
                "entry_price": str(close),
                "sl_price": str(sl),
                "tp_price": str(tp),
            }

            result = guardrails.evaluate(
                df=df,
                instrument=instrument,
                direction=direction,
                timeframe=tf,
                signal_metadata=signal_metadata
            )

            print(f"  Entry: {close:.5f} | SL: {sl:.5f} | TP: {tp:.5f}")
            print(f"  Score: {result['final_score']}/20 (min: 3) | Passed: {result['passed']}")
            if result['hard_blocks']:
                print(f"  🚫 HARD BLOCKS: {result['hard_blocks']}")
            print(f"  Checks:")
            for r in result['results']:
                icon = "✅" if r.passed and r.score_adj > 0 else "⚠️" if r.score_adj < 0 else "➖"
                if r.is_hard_block:
                    icon = "🚫"
                print(f"    {icon} {r.name:20s} {r.score_adj:+d}  {r.reason}")

        except Exception as e:
            print(f"  ❌ Error: {e}")

client.close_session()
print(f"\n{'='*70}")
print("  DONE")
print(f"{'='*70}")
