"""Flexible Telegram trading-signal parser."""
from __future__ import annotations

import re
from typing import Dict, Optional

from config import resolve_instrument


INSTRUMENT_RE = re.compile(r"\b([A-Z]{3,6}(?:USD|USDT)?|GOLD|XAUUSD|CRUDE|WTI|BTCUSD|BTCUSDT|ETHUSD|ETHUSDT)\b", re.I)
TIMEFRAME_RE = re.compile(r"\b(M15|M30|H1|H4|D1)\b", re.I)
DIRECTION_RE = re.compile(r"\b(BUY|SELL|LONG|SHORT)\b", re.I)
ENTRY_RE = re.compile(r"\b(?:ENTRY|ENTER|PRICE|@)\s*[:@]?\s*([0-9]+(?:\.[0-9]+)?)\b", re.I)
SL_RE = re.compile(r"\b(?:SL|STOP|STOP\s*LOSS)\s*[:@]?\s*([0-9]+(?:\.[0-9]+)?)\b", re.I)
TP_RE = re.compile(r"\b(?:TP|TAKE\s*PROFIT|TARGET)\s*[0-9]*\s*[:@]?\s*([0-9]+(?:\.[0-9]+)?)\b", re.I)


def parse_signal_text(text: str) -> Optional[Dict]:
    """Parse common channel signal formats.

    Examples accepted:
    - "EURUSD BUY M15 Entry 1.0830 SL 1.0800 TP 1.0900"
    - "BTCUSDT short H1 @ 65000 stop 66000 target 62500"
    """
    raw = " ".join((text or "").replace("\n", " ").split())
    instrument_match = INSTRUMENT_RE.search(raw)
    direction_match = DIRECTION_RE.search(raw)
    timeframe_match = TIMEFRAME_RE.search(raw)
    entry_match = ENTRY_RE.search(raw)
    sl_match = SL_RE.search(raw)
    tp_match = TP_RE.search(raw)

    if not (instrument_match and direction_match and timeframe_match and sl_match):
        return None

    instrument = instrument_match.group(1).upper()
    direction = direction_match.group(1).upper()
    if direction == "LONG":
        direction = "BUY"
    elif direction == "SHORT":
        direction = "SELL"

    entry = float(entry_match.group(1)) if entry_match else 0.0
    sl = float(sl_match.group(1))
    tp = float(tp_match.group(1)) if tp_match else 0.0
    rr = 0.0
    if entry > 0 and sl > 0 and tp > 0:
        risk = abs(entry - sl)
        reward = abs(tp - entry)
        rr = reward / risk if risk > 0 else 0.0

    return {
        "instrument": instrument.lower().replace("xauusd", "gold").replace("wti", "crude"),
        "inst_name": resolve_instrument(instrument),
        "tf": timeframe_match.group(1).upper(),
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "confluence": 0,
        "zone_types": "telegram_signal",
        "mss_type": "telegram",
        "rsi": 0,
        "top5": False,
        "risk_pct": abs(entry - sl) / entry * 100 if entry else 0,
        "session": "telegram",
        "regime": "",
    }
