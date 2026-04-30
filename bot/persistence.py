"""
Capital.com Trading Bot v2.1 - Persistence Layer
SQLite storage for signals, trades, trailing configs, and errors.
Provides restart recovery and full audit trail.
"""
import sqlite3
import json
import os
import logging
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(DB_DIR, "bot.db")
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Thread-safe connection (one per thread)."""
    if not hasattr(_local, "conn") or _local.conn is None:
        os.makedirs(DB_DIR, exist_ok=True)
        _local.conn = sqlite3.connect(DB_PATH, timeout=10)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA busy_timeout=5000")
    return _local.conn


def init_db():
    """Create tables if they do not exist."""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            instrument TEXT NOT NULL,
            epic TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_price REAL,
            stop_loss REAL,
            take_profit REAL,
            risk_reward REAL,
            confluence INTEGER,
            zone_types TEXT,
            mss_type TEXT,
            rsi REAL,
            session TEXT,
            is_top5 INTEGER DEFAULT 0,
            regime TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            executed_at TEXT,
            skipped_at TEXT,
            expired_at TEXT,
            metadata TEXT
        );

        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id TEXT UNIQUE NOT NULL,
            deal_ref TEXT,
            signal_id INTEGER,
            timestamp TEXT NOT NULL,
            instrument TEXT NOT NULL,
            epic TEXT NOT NULL,
            direction TEXT NOT NULL,
            size REAL,
            entry_price REAL,
            stop_loss REAL,
            take_profit REAL,
            status TEXT DEFAULT 'open',
            close_time TEXT,
            close_price REAL,
            pnl REAL,
            pnl_r REAL,
            close_reason TEXT,
            session TEXT,
            zone_types TEXT,
            mss_type TEXT,
            confluence INTEGER,
            timeframe TEXT,
            regime TEXT DEFAULT '',
            spread_at_entry REAL,
            metadata TEXT,
            FOREIGN KEY (signal_id) REFERENCES signals(id)
        );

        CREATE TABLE IF NOT EXISTS trailing_configs (
            deal_id TEXT PRIMARY KEY,
            direction TEXT NOT NULL,
            trail_type TEXT NOT NULL,
            distance REAL NOT NULL,
            pct REAL,
            highest REAL,
            lowest REAL,
            last_updated TEXT
        );

        CREATE TABLE IF NOT EXISTS errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            category TEXT NOT NULL,
            message TEXT,
            details TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_signals_instrument ON signals(instrument, timeframe);
        CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
        CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
        CREATE INDEX IF NOT EXISTS idx_trades_deal ON trades(deal_id);
        CREATE INDEX IF NOT EXISTS idx_errors_category ON errors(category);
    """)
    conn.commit()
    logger.info("Database initialized at %s", DB_PATH)


# ---- SIGNALS ----

def save_signal(sig_data: Dict) -> int:
    """Save a signal and return its row id."""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    meta = {k: v for k, v in sig_data.items()
            if k not in ("instrument", "inst_name", "epic", "tf", "direction",
                         "entry", "sl", "tp", "rr", "confluence", "zone_types",
                         "mss_type", "rsi", "top5", "risk_pct", "session")}
    cur = conn.execute(
        """INSERT INTO signals
           (timestamp, instrument, epic, timeframe, direction, entry_price,
            stop_loss, take_profit, risk_reward, confluence, zone_types,
            mss_type, rsi, session, is_top5, regime, status, metadata)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (now, sig_data.get("instrument", ""), sig_data.get("inst_name", sig_data.get("epic", "")),
         sig_data.get("tf", ""), sig_data.get("direction", ""),
         sig_data.get("entry", 0), sig_data.get("sl", 0), sig_data.get("tp", 0),
         sig_data.get("rr", 0), sig_data.get("confluence", 0),
         sig_data.get("zone_types", ""), sig_data.get("mss_type", ""),
         sig_data.get("rsi", 0), sig_data.get("session", ""),
         1 if sig_data.get("top5") else 0, sig_data.get("regime", ""), "pending", json.dumps(meta)))
    conn.commit()
    return cur.lastrowid


def mark_signal(signal_id: int, status: str):
    """Mark a signal as executed, skipped, or expired."""
    conn = _get_conn()
    field = {"executed": "executed_at", "skipped": "skipped_at", "expired": "expired_at"}.get(status)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(f"UPDATE signals SET status=?, {field}=? WHERE id=?", (status, now, signal_id))
    conn.commit()


def get_recent_signals(instrument: str = None, timeframe: str = None,
                       hours: float = 4, limit: int = 50) -> List[Dict]:
    """Get recent signals, optionally filtered."""
    conn = _get_conn()
    sql = "SELECT * FROM signals WHERE timestamp > datetime('now', ?)"
    params = [f"-{hours} hours"]
    if instrument:
        sql += " AND (instrument=? OR epic=?)"
        params.extend([instrument, instrument])
    if timeframe:
        sql += " AND timeframe=?"
        params.append(timeframe)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_pending_signal_count(instrument: str, direction: str,
                              timeframe: str, hours: float = 2) -> int:
    """Count pending/executed signals for dedup check."""
    conn = _get_conn()
    return conn.execute(
        """SELECT COUNT(*) FROM signals
           WHERE (instrument=? OR epic=?) AND direction=? AND timeframe=?
           AND status IN ('pending','executed')
           AND timestamp > datetime('now', ?)""",
        (instrument, instrument, direction, timeframe, f"-{hours} hours")
    ).fetchone()[0]


# ---- TRADES ----

def save_trade(trade_data: Dict) -> int:
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    meta = json.dumps({k: v for k, v in trade_data.items()
                       if k not in ("deal_id", "deal_ref", "signal_id", "instrument",
                                    "epic", "direction", "size", "entry_price",
                                    "stop_loss", "take_profit", "session",
                                    "zone_types", "mss_type", "confluence",
                                    "timeframe", "spread_at_entry")})
    cur = conn.execute(
        """INSERT OR REPLACE INTO trades
           (deal_id, deal_ref, signal_id, timestamp, instrument, epic, direction,
            size, entry_price, stop_loss, take_profit, status, session,
            zone_types, mss_type, confluence, timeframe, regime, spread_at_entry, metadata)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (trade_data["deal_id"], trade_data.get("deal_ref", ""),
         trade_data.get("signal_id"), now,
         trade_data.get("instrument", ""), trade_data.get("epic", ""),
         trade_data.get("direction", ""), trade_data.get("size", 0),
         trade_data.get("entry_price", 0), trade_data.get("stop_loss", 0),
         trade_data.get("take_profit", 0), "open",
         trade_data.get("session", ""), trade_data.get("zone_types", ""),
         trade_data.get("mss_type", ""), trade_data.get("confluence", 0),
         trade_data.get("timeframe", ""), trade_data.get("regime", ""), trade_data.get("spread_at_entry", 0),
         meta))
    conn.commit()
    return cur.lastrowid


def close_trade_record(deal_id: str, close_price: float = 0, pnl: float = 0,
                       reason: str = "manual"):
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    # Calculate pnl_r
    row = conn.execute("SELECT entry_price, stop_loss, direction FROM trades WHERE deal_id=?",
                       (deal_id,)).fetchone()
    pnl_r = 0
    if row:
        risk = abs(row["entry_price"] - row["stop_loss"]) if row["stop_loss"] else 0
        if risk > 0 and close_price > 0:
            raw_pnl = (close_price - row["entry_price"]) if row["direction"] == "BUY" else (row["entry_price"] - close_price)
            pnl_r = raw_pnl / risk
    conn.execute(
        """UPDATE trades SET status='closed', close_time=?, close_price=?,
           pnl=?, pnl_r=?, close_reason=? WHERE deal_id=?""",
        (now, close_price, pnl, pnl_r, reason, deal_id))
    conn.commit()


def get_open_trades() -> List[Dict]:
    conn = _get_conn()
    return [dict(r) for r in conn.execute(
        "SELECT * FROM trades WHERE status='open'").fetchall()]


def get_today_trades() -> List[Dict]:
    conn = _get_conn()
    return [dict(r) for r in conn.execute(
        "SELECT * FROM trades WHERE timestamp > datetime('now', '-24 hours')").fetchall()]


def get_today_closed_pnl() -> float:
    conn = _get_conn()
    row = conn.execute(
        "SELECT COALESCE(SUM(pnl), 0) as total FROM trades WHERE status='closed' AND close_time > datetime('now', '-24 hours')"
    ).fetchone()
    return row["total"] if row else 0


def get_trade_stats(days: int = 30) -> Dict:
    """Get trading statistics for the last N days."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM trades WHERE status='closed' AND close_time > datetime('now', ?)",
        (f"-{days} days",)).fetchall()
    if not rows:
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0,
                "total_pnl": 0, "avg_r": 0, "best_r": 0, "worst_r": 0,
                "by_combo": {}, "by_instrument": {}, "by_session": {}}
    trades = [dict(r) for r in rows]
    wins = [t for t in trades if (t.get("pnl") or 0) > 0]
    losses = [t for t in trades if (t.get("pnl") or 0) <= 0]
    r_values = [t.get("pnl_r", 0) or 0 for t in trades]

    # Breakdown by combo
    by_combo = {}
    for t in trades:
        combo = t.get("zone_types", "unknown") or "unknown"
        if combo not in by_combo:
            by_combo[combo] = {"count": 0, "wins": 0, "pnl": 0}
        by_combo[combo]["count"] += 1
        if (t.get("pnl") or 0) > 0:
            by_combo[combo]["wins"] += 1
        by_combo[combo]["pnl"] += t.get("pnl", 0) or 0

    # Breakdown by instrument
    by_instrument = {}
    for t in trades:
        inst = t.get("epic", "unknown") or "unknown"
        if inst not in by_instrument:
            by_instrument[inst] = {"count": 0, "wins": 0, "pnl": 0}
        by_instrument[inst]["count"] += 1
        if (t.get("pnl") or 0) > 0:
            by_instrument[inst]["wins"] += 1
        by_instrument[inst]["pnl"] += t.get("pnl", 0) or 0

    # Breakdown by regime
    by_regime = {}
    for t in trades:
        reg = t.get("regime", "unknown") or "unknown"
        if reg not in by_regime:
            by_regime[reg] = {"count": 0, "wins": 0, "pnl": 0}
        by_regime[reg]["count"] += 1
        if (t.get("pnl") or 0) > 0:
            by_regime[reg]["wins"] += 1
        by_regime[reg]["pnl"] += t.get("pnl", 0) or 0

    # Breakdown by session
    by_session = {}
    for t in trades:
        sess = t.get("session", "unknown") or "unknown"
        if sess not in by_session:
            by_session[sess] = {"count": 0, "wins": 0, "pnl": 0}
        by_session[sess]["count"] += 1
        if (t.get("pnl") or 0) > 0:
            by_session[sess]["wins"] += 1
        by_session[sess]["pnl"] += t.get("pnl", 0) or 0

    return {
        "total": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate": len(wins) / len(trades) * 100 if trades else 0,
        "total_pnl": sum(t.get("pnl", 0) or 0 for t in trades),
        "avg_r": sum(r_values) / len(r_values) if r_values else 0,
        "best_r": max(r_values) if r_values else 0,
        "worst_r": min(r_values) if r_values else 0,
        "by_combo": by_combo,
        "by_instrument": by_instrument,
        "by_session": by_session,
        "by_regime": by_regime,
    }


# ---- TRAILING CONFIGS ----

def save_trailing_config(deal_id: str, config: Dict):
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT OR REPLACE INTO trailing_configs
           (deal_id, direction, trail_type, distance, pct, highest, lowest, last_updated)
           VALUES (?,?,?,?,?,?,?,?)""",
        (deal_id, config.get("direction", ""), config.get("type", "fixed"),
         config.get("distance", 0), config.get("pct", 0),
         config.get("highest"), config.get("lowest"), now))
    conn.commit()


def get_trailing_configs() -> Dict[str, Dict]:
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM trailing_configs").fetchall()
    result = {}
    for r in rows:
        result[r["deal_id"]] = {
            "direction": r["direction"], "type": r["trail_type"],
            "distance": r["distance"], "pct": r["pct"],
            "highest": r["highest"], "lowest": r["lowest"]}
    return result


def update_trailing_config(deal_id: str, highest: float = None, lowest: float = None):
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    if highest is not None:
        conn.execute("UPDATE trailing_configs SET highest=?, last_updated=? WHERE deal_id=?",
                     (highest, now, deal_id))
    if lowest is not None:
        conn.execute("UPDATE trailing_configs SET lowest=?, last_updated=? WHERE deal_id=?",
                     (lowest, now, deal_id))
    conn.commit()


def delete_trailing_config(deal_id: str):
    conn = _get_conn()
    conn.execute("DELETE FROM trailing_configs WHERE deal_id=?", (deal_id,))
    conn.commit()


# ---- ERRORS ----

def log_error(category: str, message: str, details: str = ""):
    """Log an error to the database. Categories: auth, data, strategy, order, position, telegram, trailing"""
    try:
        conn = _get_conn()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("INSERT INTO errors (timestamp, category, message, details) VALUES (?,?,?,?)",
                     (now, category, message, details))
        conn.commit()
    except Exception:
        pass  # DB error logging should never crash the bot


def get_recent_errors(hours: float = 24, limit: int = 20) -> List[Dict]:
    conn = _get_conn()
    return [dict(r) for r in conn.execute(
        "SELECT * FROM errors WHERE timestamp > datetime('now', ?) ORDER BY id DESC LIMIT ?",
        (f"-{hours} hours", limit)).fetchall()]


# ---- CLEANUP ----

def cleanup_old_data(days: int = 90):
    """Remove data older than N days."""
    conn = _get_conn()
    cutoff = f"-{days} days"
    conn.execute("DELETE FROM signals WHERE timestamp < datetime('now', ?)", (cutoff,))
    conn.execute("DELETE FROM trades WHERE timestamp < datetime('now', ?) AND status='closed'", (cutoff,))
    conn.execute("DELETE FROM errors WHERE timestamp < datetime('now', ?)", (cutoff,))
    conn.commit()
    logger.info("Cleaned up data older than %d days", days)
