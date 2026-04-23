
"""Bot-side trailing stop implementation.

Capital.com REST API v1 does NOT support trailing stops via
trailing_stop/sl_distance params (returns 400). This module
implements trailing by polling positions and updating SL via
PUT /positions/{dealId} with just stopLevel.

Usage:
    from bot_trailing import TrailingManager
    trailing = TrailingManager(client)
    # In scan loop:
    trailing.update_all()
"""
import os
import logging
import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

logger = logging.getLogger("trailing")

# Config from env
TRAILING_ENABLED = os.environ.get("TRAILING_STOP_ENABLED", "false").lower() == "true"
BREAKEVEN_TRIGGER_R = float(os.environ.get("BREAKEVEN_TRIGGER_R", "1.0"))
TRAIL_START_R = float(os.environ.get("TRAIL_START_R", "1.5"))  # Start trailing after 1.5R
TRAIL_DISTANCE_ATR = float(os.environ.get("TRAIL_DISTANCE_ATR", "1.0"))  # Trail 1 ATR behind

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TRAIL_STATE_FILE = DATA_DIR / "trailing_state.json"
_state_lock = Lock()


class TrailingManager:
    """Manages bot-side trailing stops for open positions."""
    
    def __init__(self, client):
        self.client = client
        self.state = self._load_state()
    
    def _load_state(self):
        """Load trailing state from disk."""
        try:
            if TRAIL_STATE_FILE.exists():
                with open(TRAIL_STATE_FILE) as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load trailing state: {e}")
        return {}
    
    def _save_state(self):
        """Persist trailing state to disk."""
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with _state_lock:
                with open(TRAIL_STATE_FILE, 'w') as f:
                    json.dump(self.state, f)
        except Exception as e:
            logger.warning(f"Failed to save trailing state: {e}")
    
    def update_all(self):
        """Poll all positions and update trailing SLs."""
        if not TRAILING_ENABLED:
            return []
        
        try:
            resp = self.client.get("/api/v1/positions")
            positions = resp.get("positions", [])
        except Exception as e:
            logger.warning(f"Failed to fetch positions for trailing: {e}")
            return []
        
        updates = []
        for pos in positions:
            try:
                result = self._update_position(pos)
                if result:
                    updates.append(result)
            except Exception as e:
                deal_id = pos.get("position", {}).get("dealId", "?")
                logger.warning(f"Trailing update failed for {deal_id}: {e}")
        
        if updates:
            self._save_state()
        return updates
    
    def _update_position(self, pos_data):
        """Update trailing SL for a single position."""
        p = pos_data.get("position", {})
        m = pos_data.get("market", {})
        
        deal_id = p.get("dealId")
        direction = p.get("direction")
        entry = float(p.get("level", 0))
        current_sl = p.get("stopLevel")
        current_tp = p.get("profitLevel")
        size = float(p.get("size", 0))
        
        # Current market price
        bid = float(m.get("bid", 0))
        ask = float(m.get("offer", 0) or m.get("ask", 0))
        current_price = bid if direction == "BUY" else ask
        
        if not deal_id or entry <= 0 or current_price <= 0:
            return None
        
        # Get/init state for this position
        if deal_id not in self.state:
            self.state[deal_id] = {
                "highest": current_price if direction == "BUY" else None,
                "lowest": current_price if direction == "SELL" else None,
                "entry": entry,
                "direction": direction,
                "original_sl": current_sl,
                "breakeven_hit": False,
                "trailing_active": False,
            }
        
        state = self.state[deal_id]
        
        # Calculate P&L in price terms and as R-multiple
        original_sl = state.get("original_sl") or current_sl
        if original_sl:
            risk = abs(entry - float(original_sl))
        else:
            risk = 0
        
        if direction == "BUY":
            pnl_price = current_price - entry
            # Track highest
            if state["highest"] is None or current_price > state["highest"]:
                state["highest"] = current_price
        else:
            pnl_price = entry - current_price
            # Track lowest
            if state["lowest"] is None or current_price < state["lowest"]:
                state["lowest"] = current_price
        
        current_r = pnl_price / risk if risk > 0 else 0
        
        # ── Logic: Breakeven at 1R, Trail after TRAIL_START_R ──
        new_sl = None
        reason = None
        
        # 1. Breakeven trigger
        if current_r >= BREAKEVEN_TRIGGER_R and not state["breakeven_hit"]:
            # Move SL to entry + small buffer (0.5 pips or 0.0005)
            buffer = 0.0005 if entry < 100 else 0.5  # FX vs index/commodity
            if direction == "BUY":
                new_sl = entry + buffer
                if current_sl is None or new_sl > float(current_sl):
                    state["breakeven_hit"] = True
                    reason = f"Breakeven at {current_r:.1f}R"
            else:
                new_sl = entry - buffer
                if current_sl is None or new_sl < float(current_sl):
                    state["breakeven_hit"] = True
                    reason = f"Breakeven at {current_r:.1f}R"
        
        # 2. Trailing after TRAIL_START_R
        if current_r >= TRAIL_START_R and state["breakeven_hit"]:
            state["trailing_active"] = True
            # Trail distance = risk * TRAIL_DISTANCE_ATR (simplified: use original risk as proxy for ATR)
            trail_dist = risk * TRAIL_DISTANCE_ATR if risk > 0 else pnl_price * 0.3
            
            if direction == "BUY":
                trail_sl = state["highest"] - trail_dist
                if current_sl is None or trail_sl > float(current_sl):
                    new_sl = round(trail_sl, 5)
                    reason = f"Trail from high {state['highest']:.5f}"
            else:
                trail_sl = state["lowest"] + trail_dist
                if current_sl is None or trail_sl < float(current_sl):
                    new_sl = round(trail_sl, 5)
                    reason = f"Trail from low {state['lowest']:.5f}"
        
        # ── Apply the SL update ──
        if new_sl and reason:
            try:
                data = {"stopLevel": new_sl}
                if current_tp:
                    data["profitLevel"] = current_tp  # Must include TP to not clear it
                self.client.put(f"/api/v1/positions/{deal_id}", data=data)
                logger.info(f"Trailing {deal_id}: SL -> {new_sl:.5f} ({reason})")
                return {"deal_id": deal_id, "new_sl": new_sl, "reason": reason}
            except Exception as e:
                logger.warning(f"Failed to update trailing SL for {deal_id}: {e}")
        
        return None
    
    def cleanup_closed(self, open_deal_ids: set):
        """Remove state for positions no longer open."""
        closed = [did for did in self.state if did not in open_deal_ids]
        for did in closed:
            del self.state[did]
        if closed:
            self._save_state()
            logger.info(f"Cleaned up trailing state for {len(closed)} closed positions")
