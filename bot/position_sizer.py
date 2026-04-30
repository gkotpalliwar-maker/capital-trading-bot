"""
Capital.com Trading Bot v2.2 - Position Sizer
Fixed percentage risk model: calculates lot size from account balance,
risk percentage, and SL distance.
"""
import logging
from typing import Dict
from config import (RISK_PER_TRADE_PCT, MIN_POSITION_SIZE, MAX_POSITION_SIZE,
                    DEFAULT_SIZE, PIP_SIZE, resolve_instrument)

logger = logging.getLogger(__name__)


def calculate_position_size(client, instrument: str, direction: str,
                            entry_price: float, stop_loss: float,
                            confidence_multiplier: float = 1.0) -> Dict:
    epic = resolve_instrument(instrument)

    try:
        accs = client.get_accounts()
        acc = accs.get("accounts", [{}])[0]
        balance = float(acc.get("balance", {}).get("balance", 0))
    except Exception as e:
        logger.warning("Cannot get balance for sizing, using default: %s", e)
        return _fallback_size(epic, confidence_multiplier)

    if balance <= 0:
        return _fallback_size(epic, confidence_multiplier)

    sl_distance = abs(entry_price - stop_loss)
    if sl_distance == 0:
        return _fallback_size(epic, confidence_multiplier)

    risk_pct = RISK_PER_TRADE_PCT * confidence_multiplier
    risk_pct = max(0.001, min(risk_pct, 0.05))
    risk_amount = balance * risk_pct

    pip = PIP_SIZE.get(epic, 0.0001)
    sl_pips = sl_distance / pip

    if epic in ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDJPY", "USDCAD", "USDCHF", "AUDCAD"):
        raw_size = risk_amount / (sl_pips * pip) if sl_pips > 0 else DEFAULT_SIZE.get(epic, 1000)
        raw_size = round(raw_size / 100) * 100
    elif epic == "GOLD":
        raw_size = risk_amount / sl_distance if sl_distance > 0 else DEFAULT_SIZE.get(epic, 0.01)
        raw_size = round(raw_size, 2)
    elif epic == "OIL_CRUDE":
        raw_size = risk_amount / sl_distance if sl_distance > 0 else DEFAULT_SIZE.get(epic, 0.1)
        raw_size = round(raw_size, 1)
    elif epic in ("BTCUSD", "ETHUSD"):
        raw_size = risk_amount / sl_distance if sl_distance > 0 else DEFAULT_SIZE.get(epic, 0.01)
        raw_size = round(raw_size, 4)
    elif epic in ("US100", "US500", "US30"):
        raw_size = risk_amount / sl_distance if sl_distance > 0 else DEFAULT_SIZE.get(epic, 0.1)
        raw_size = round(raw_size, 2)
    else:
        raw_size = risk_amount / sl_distance if sl_distance > 0 else DEFAULT_SIZE.get(epic, 1)

    min_size = MIN_POSITION_SIZE.get(epic, 0.01)
    max_size = MAX_POSITION_SIZE.get(epic, DEFAULT_SIZE.get(epic, 1) * 10)
    final_size = max(min_size, min(raw_size, max_size))

    logger.info("Sizing: %s bal=%.2f risk=%.2f%% (%.2f) SL_dist=%.5f -> size=%.4f",
                epic, balance, risk_pct * 100, risk_amount, sl_distance, final_size)

    return {
        "size": final_size, "risk_amount": round(risk_amount, 2),
        "risk_pct": round(risk_pct * 100, 2), "sl_distance": sl_distance,
        "sl_pips": round(sl_pips, 1), "balance": balance, "method": "risk_pct",
    }


def _fallback_size(epic: str, multiplier: float = 1.0) -> Dict:
    size = DEFAULT_SIZE.get(epic, 1) * multiplier
    min_size = MIN_POSITION_SIZE.get(epic, 0.01)
    size = max(min_size, size)
    return {"size": size, "risk_amount": 0, "risk_pct": 0,
            "sl_distance": 0, "sl_pips": 0, "balance": 0, "method": "fallback"}


def format_sizing_info(sizing: Dict) -> str:
    if sizing["method"] == "fallback":
        return f"Size: {sizing['size']} (default)"
    return (f"Size: {sizing['size']} | "
            f"Risk: {sizing['risk_pct']:.1f}% (${sizing['risk_amount']:.2f}) | "
            f"SL: {sizing['sl_pips']:.0f} pips")
