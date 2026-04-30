"""Trade Execution & Management v2.1 - with persistence and validation"""
from __future__ import annotations
import os
import os
import logging
from typing import Dict, List
from config import resolve_instrument, DEFAULT_SIZE, PIP_SIZE, get_current_session
import persistence as db

# v2.5.3: Minimum SL distance per instrument (in price points)
MIN_SL_DISTANCE = {
    "EURUSD": 0.0015, "GBPUSD": 0.0020, "USDJPY": 0.150,
    "AUDUSD": 0.0015, "NZDUSD": 0.0015, "USDCAD": 0.0020, "USDCHF": 0.0015,
    "GOLD": 2.0, "SILVER": 0.15, "OIL_CRUDE": 0.50,
    "US100": 30.0, "US500": 8.0, "US30": 50.0,
    "BTCUSD": 200.0, "ETHUSD": 15.0}

logger = logging.getLogger(__name__)

def open_trade(client, instrument, direction, entry_price=None, stop_loss=None,
               take_profit=None, size=None, trailing_sl_pct=None,
               trailing_sl_distance=None, signal_id=None, signal_data=None):
    from data_fetcher import get_current_price
    epic = resolve_instrument(instrument)
    if size is None:
        from position_sizer import calculate_position_size
        if entry_price and stop_loss and entry_price != stop_loss:
            _cm = signal_data.get("_confidence_mult", 1.0) if signal_data else 1.0
            sizing = calculate_position_size(client, instrument, direction, entry_price, stop_loss, _cm)
            size = sizing["size"]
        else:
            size = DEFAULT_SIZE.get(epic, 1)
    price_info = get_current_price(client, instrument)
    current_price = price_info["ask"] if direction == "BUY" else price_info["bid"]
    spread = price_info["spread"]

    order = {"epic": epic, "direction": direction.upper(), "size": size}
    if stop_loss is not None:
        order["stopLevel"] = stop_loss
    # v2.5.3: Enforce minimum SL distance
    min_dist = MIN_SL_DISTANCE.get(epic, 0)
    if min_dist > 0 and stop_loss is not None and current_price > 0:
        actual_dist = abs(current_price - stop_loss)
        if actual_dist < min_dist:
            if direction.upper() == "BUY":
                stop_loss = current_price - min_dist
            else:
                stop_loss = current_price + min_dist
            order["stopLevel"] = round(stop_loss, 5)
            logger.warning(f"SL too tight ({actual_dist:.5f}), enforced min {min_dist:.5f} -> SL={stop_loss:.5f}")
    if take_profit is not None:
        order["profitLevel"] = take_profit

    trail_config = {}
    if trailing_sl_pct:
        trail_config = {"type": "pct", "pct": trailing_sl_pct,
                        "distance": current_price * trailing_sl_pct}
    elif trailing_sl_distance:
        trail_config = {"type": "fixed", "distance": trailing_sl_distance}

    logger.info("Opening %s on %s, size=%s, SL=%s, TP=%s", direction, epic, size, stop_loss, take_profit)

    try:

        resp = client.post("/api/v1/positions", data=order)
        deal_ref = resp.get("dealReference", "")
        confirm = client.get(f"/api/v1/confirms/{deal_ref}")
        deal_id = confirm.get("dealId", deal_ref)
        status = confirm.get("dealStatus", "UNKNOWN")
        fill_price = confirm.get("level", current_price)

        # Persist trailing config
        if trail_config and deal_id:
            trail_config["direction"] = direction.upper()
            if direction == "BUY":
                trail_config["highest"] = fill_price
            else:
                trail_config["lowest"] = fill_price
            db.save_trailing_config(deal_id, trail_config)

        # Persist trade record
        sessions = get_current_session()
        session_str = ", ".join(s.value for s in sessions)
        trade_record = {
            "deal_id": deal_id, "deal_ref": deal_ref,
            "signal_id": signal_id,
            "instrument": instrument, "epic": epic,
            "direction": direction.upper(), "size": size,
            "entry_price": fill_price,
            "stop_loss": stop_loss, "take_profit": take_profit,
            "session": session_str,
            "spread_at_entry": spread}
        if signal_data:
            trade_record["zone_types"] = signal_data.get("zone_types", "")
            trade_record["mss_type"] = signal_data.get("mss_type", "")
            trade_record["confluence"] = signal_data.get("confluence", 0)
            trade_record["timeframe"] = signal_data.get("tf", "")
            trade_record["regime"] = signal_data.get("regime", "")
        db.save_trade(trade_record)

        logger.info("Trade opened: %s @ %s (%s)", deal_id, fill_price, status)
        return {"deal_id": deal_id, "deal_ref": deal_ref,
                "entry_price": fill_price, "status": status}
    except Exception as e:
        logger.error("Trade failed: %s", e)
        db.log_error("order", f"Trade open failed: {epic} {direction}", str(e))
        return {"error": str(e)}

def close_trade(client, deal_id):
    try:
        resp = client.delete(f"/api/v1/positions/{deal_id}")
        logger.info("Closed %s", deal_id)
        # Update trade record
        db.close_trade_record(deal_id, reason="manual")
        db.delete_trailing_config(deal_id)
        return resp
    except Exception as e:
        logger.error("Close failed: %s", e)
        db.log_error("order", f"Trade close failed: {deal_id}", str(e))
        return {"error": str(e)}

def update_stop_loss(client, deal_id, new_sl, new_tp=None):
    try:
        payload = {"stopLevel": new_sl}
        if new_tp is not None:
            payload["profitLevel"] = new_tp
        client.put(f"/api/v1/positions/{deal_id}", data=payload)
        return True
    except Exception as e:
        logger.error("SL update failed %s: %s", deal_id, e)
        db.log_error("trailing", f"SL update failed: {deal_id}", str(e))
        return False

def get_open_positions(client):
    try:
        resp = client.get("/api/v1/positions")
        positions = []
        for p in resp.get("positions", []):
            ps = p["position"]
            mk = p["market"]
            positions.append({
                "deal_id": ps["dealId"], "epic": mk.get("epic", ""),
                "instrument_name": mk.get("instrumentName", ""),
                "direction": ps["direction"], "size": ps["size"],
                "entry_price": ps.get("level"),
                "stop_loss": ps.get("stopLevel"),
                "take_profit": ps.get("profitLevel"),
                "upl": ps.get("upl", 0),
                "currency": ps.get("currency", "SGD"),
                "current_bid": mk.get("bid"),
                "current_ask": mk.get("offer")})
        return positions
    except Exception as e:
        logger.error("Position fetch error: %s", e)
        db.log_error("position", "Position fetch failed", str(e))
        return []

def _apply_trailing_sl(client, position):
    """Apply trailing stop loss using persisted configs."""
    deal_id = position["deal_id"]

    # Load from DB (survives restart)
    all_configs = db.get_trailing_configs()
    if deal_id not in all_configs:
        return False

    tc = all_configs[deal_id]
    direction = tc["direction"]
    distance = tc["distance"]
    current_bid = float(position.get("current_bid") or 0)
    current_ask = float(position.get("current_ask") or 0)
    current_sl = float(position.get("stop_loss") or 0)
    current_tp = position.get("take_profit")

    if direction == "BUY":
        if current_bid > (tc.get("highest") or 0):
            db.update_trailing_config(deal_id, highest=current_bid)
            tc["highest"] = current_bid
        new_sl = tc["highest"] - distance
        if new_sl > current_sl:
            tp_val = float(current_tp) if current_tp else None
            if update_stop_loss(client, deal_id, new_sl, tp_val):
                logger.info("Trail SL %s: %.5f->%.5f", deal_id, current_sl, new_sl)
                return True
    else:
        lowest = tc.get("lowest")
        if lowest is None or current_ask < lowest:
            db.update_trailing_config(deal_id, lowest=current_ask)
            tc["lowest"] = current_ask
        new_sl = tc["lowest"] + distance
        if new_sl < current_sl or current_sl == 0:
            tp_val = float(current_tp) if current_tp else None
            if update_stop_loss(client, deal_id, new_sl, tp_val):
                logger.info("Trail SL %s: %.5f->%.5f", deal_id, current_sl, new_sl)
                return True
    return False

def _fetch_close_details(client, deal_id, trade_info):
    """Fetch actual close price & P&L for a broker-closed position.
    
    Capital.com API patterns:
    - Transaction API uses 'lastPeriod' param (seconds), NOT from/to dates
    - Close transaction dealId = open dealId with last hex segment + 1
    - 'size' field = P&L in account currency (SGD)
    """
    import logging
    from datetime import datetime, timedelta, timezone
    logger = logging.getLogger("execution")
    entry = float(trade_info.get("entry_price", 0) or 0)
    direction = trade_info.get("direction", "BUY")
    size = float(trade_info.get("size", 0) or 0)
    sl = float(trade_info.get("stop_loss", 0) or 0)

    # Calculate expected close deal_id (last hex segment + 1)
    close_deal_id = None
    try:
        parts = deal_id.rsplit("-", 1)
        if len(parts) == 2:
            last_hex = int(parts[1], 16)
            close_deal_id = f"{parts[0]}-{(last_hex + 1):012x}"
    except Exception as e:
        logger.warning(f"Could not compute close deal_id: {e}")

    # Try transaction history API with lastPeriod (seconds)
    try:
        resp = client.get("/api/v1/history/transactions", {
            "lastPeriod": "86400",
            "type": "ALL"
        })
        transactions = resp.get("transactions", [])
        logger.info(f"P&L lookup: {len(transactions)} txns, looking for {close_deal_id or deal_id}")

        for tx in transactions:
            tx_deal_id = str(tx.get("dealId", ""))
            tx_ref = str(tx.get("reference", ""))
            tx_type = str(tx.get("transactionType", ""))

            # Match: close_deal_id (hex+1), or original deal_id
            matched = False
            if close_deal_id and tx_deal_id == close_deal_id:
                matched = True
            elif deal_id == tx_deal_id:
                matched = True
            elif deal_id in tx_ref or tx_ref in deal_id:
                matched = True

            if matched and tx_type == "TRADE":
                # 'size' field = P&L in account currency (SGD)
                pnl_raw = str(tx.get("size", "0")).replace(",", "")
                pnl = float(pnl_raw) if pnl_raw else 0

                # Also check profitAndLoss as secondary
                if pnl == 0:
                    import re
                    pnl_alt = str(tx.get("profitAndLoss", "0"))
                    pnl_alt = re.sub(r"[A-Z]{3}\s*", "", pnl_alt).replace(",", "").strip()
                    try:
                        pnl = float(pnl_alt) if pnl_alt else 0
                    except ValueError:
                        pass

                # Close price from available fields
                close_price = 0.0
                for field in ["closeLevel", "openLevel", "level"]:
                    val = tx.get(field, 0)
                    if val:
                        close_price = float(val)
                        break

                # Estimate close price from P&L if not in API
                if close_price == 0 and pnl != 0 and entry > 0 and size > 0:
                    if direction == "BUY":
                        close_price = entry + (pnl / size)
                    else:
                        close_price = entry - (pnl / size)

                if close_price == 0 and sl > 0:
                    close_price = sl

                logger.info(f"P&L found: {deal_id} close={close_price:.5f}, pnl={pnl:.2f} SGD")
                return close_price, pnl

        logger.warning(f"No matching transaction for {deal_id} (close_id={close_deal_id})")

    except Exception as e:
        logger.warning(f"Transaction API failed for {deal_id}: {e}")

    # Fallback: SL as close price, P&L = 0 (unknown)
    if sl > 0:
        logger.warning(f"P&L UNKNOWN for {deal_id} - using SL as close, P&L=0")
        return sl, 0.0

    logger.warning(f"No close details for {deal_id}")
    return 0, 0

def _fetch_close_details_OLD(client, deal_id, trade_info):
    """Fetch close price & P&L for broker-closed position."""
    import logging
    logger = logging.getLogger("execution")
    entry = float(trade_info.get("entry_price", 0) or 0)
    direction = trade_info.get("direction", "BUY")
    size = float(trade_info.get("size", 0) or 0)
    sl = float(trade_info.get("stop_loss", 0) or 0)

    try:
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        from_dt = (now - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%S")
        to_dt = now.strftime("%Y-%m-%dT%H:%M:%S")
        resp = client.get("/api/v1/history/activity", {"from": from_dt, "to": to_dt, "detailed": "true"})
        for act in resp.get("activities", []):
            for action in act.get("details", {}).get("actions", []):
                if action.get("dealId") == deal_id:
                    level = float(action.get("level", 0) or 0)
                    if level > 0 and entry > 0 and size > 0:
                        raw = (level - entry) if direction == "BUY" else (entry - level)
                        logger.info(f"API close: {deal_id} @ {level}, PnL={raw*size:.2f}")
                        return level, raw * size
    except Exception as e:
        logger.warning(f"Activity API failed: {e}")

    if sl > 0 and entry > 0 and size > 0:
        raw = (sl - entry) if direction == "BUY" else (entry - sl)
        logger.info(f"SL estimate: {deal_id} @ {sl}, PnL={raw*size:.2f}")
        return sl, raw * size

    return 0, 0

def sync_positions_with_db(client):
    """
    Restart recovery: reconcile broker positions with local DB.
    - Close DB trades that are no longer at broker.
    - Re-create trailing configs for known trades.
    """
    broker_positions = get_open_positions(client)
    broker_deal_ids = {p["deal_id"] for p in broker_positions}
    db_open = db.get_open_trades()

    # Close DB trades not at broker anymore
    closed_count = 0
    for t in db_open:
        if t["deal_id"] not in broker_deal_ids:
            close_price, pnl = _fetch_close_details(client, t["deal_id"], t)
            db.close_trade_record(t["deal_id"], close_price=close_price, pnl=pnl, reason="broker_closed")
            db.delete_trailing_config(t["deal_id"])
            closed_count += 1
            logger.info("Reconciled: %s no longer at broker, marked closed", t["deal_id"])

    logger.info("Position sync: %d broker, %d DB open, %d reconciled",
                len(broker_positions), len(db_open), closed_count)
    return broker_positions

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
