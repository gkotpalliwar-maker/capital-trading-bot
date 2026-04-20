import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("risk_report")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "bot.db"

# Track if we already sent this week's report
_last_report_date = None


def generate_weekend_report(client) -> str:
    """Generate comprehensive weekend risk report."""
    now = datetime.now(timezone.utc)

    # ── Open Positions from API ──
    positions = []
    try:
        resp = client.session.get(
            f"{client.api_url}/api/v1/positions",
            headers=client.auth_headers
        )
        if resp.status_code == 200:
            positions = resp.json().get("positions", [])
    except Exception as e:
        logger.warning("Failed to fetch positions: %s", e)

    # ── Weekly P&L from DB ──
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    weekly_trades = conn.execute(
        "SELECT * FROM trades WHERE status='closed' AND closed_at >= ?",
        (week_start.isoformat(),)
    ).fetchall()

    all_closed = conn.execute(
        "SELECT * FROM trades WHERE status='closed' AND pnl IS NOT NULL"
    ).fetchall()

    open_db_trades = conn.execute(
        "SELECT * FROM trades WHERE status='open'"
    ).fetchall()

    # Weekly signals stats
    weekly_signals = conn.execute(
        "SELECT COUNT(*) as total, "
        "SUM(CASE WHEN status='executed' THEN 1 ELSE 0 END) as executed, "
        "SUM(CASE WHEN status='skipped' THEN 1 ELSE 0 END) as skipped, "
        "SUM(CASE WHEN status='mtf_blocked' THEN 1 ELSE 0 END) as mtf_blocked "
        "FROM signals WHERE timestamp >= ?",
        (week_start.isoformat(),)
    ).fetchone()

    conn.close()

    # ── Calculate metrics ──
    weekly_pnl = sum(float(t["pnl"] or 0) for t in weekly_trades)
    weekly_wins = sum(1 for t in weekly_trades if float(t["pnl"] or 0) > 0)
    weekly_losses = sum(1 for t in weekly_trades if float(t["pnl"] or 0) < 0)
    weekly_count = len(weekly_trades)
    weekly_wr = (weekly_wins / weekly_count * 100) if weekly_count > 0 else 0

    # R-multiples this week
    weekly_r = sum(float(t["pnl_r"] or 0) for t in weekly_trades)
    avg_r = (weekly_r / weekly_count) if weekly_count > 0 else 0

    # Best/worst trade
    if weekly_trades:
        best = max(weekly_trades, key=lambda t: float(t["pnl"] or 0))
        worst = min(weekly_trades, key=lambda t: float(t["pnl"] or 0))
    else:
        best = worst = None

    # All-time stats
    total_trades = len(all_closed)
    total_pnl = sum(float(t["pnl"] or 0) for t in all_closed)
    total_wins = sum(1 for t in all_closed if float(t["pnl"] or 0) > 0)
    total_wr = (total_wins / total_trades * 100) if total_trades > 0 else 0

    # ── Open position details ──
    total_unrealised = 0
    pos_lines = []
    for p in positions:
        pos = p.get("position", {})
        mkt = p.get("market", {})
        epic = mkt.get("epic", "?")
        direction = pos.get("direction", "?")
        size = float(pos.get("size", 0))
        upl = float(pos.get("upl", 0))
        total_unrealised += upl
        emoji = "\U0001f7e2" if direction == "BUY" else "\U0001f534"
        pos_lines.append(f"  {emoji} {epic} {direction} x{size} | P&L: {upl:+.2f}")

    # ── Weekend gap risk assessment ──
    gap_risk = "LOW"
    gap_warning = ""
    if len(positions) > 0:
        if abs(total_unrealised) > 10 or len(positions) >= 3:
            gap_risk = "HIGH"
            gap_warning = "\n\u26a0\ufe0f Consider reducing exposure before weekend!"
        elif abs(total_unrealised) > 5 or len(positions) >= 2:
            gap_risk = "MEDIUM"
            gap_warning = "\n\u26a0\ufe0f Monitor positions - moderate gap risk"

    # ── Exposure by direction ──
    buy_count = sum(1 for p in positions if p.get("position", {}).get("direction") == "BUY")
    sell_count = sum(1 for p in positions if p.get("position", {}).get("direction") == "SELL")

    # ── Build report ──
    report = []
    report.append("\U0001f4cb <b>Weekend Risk Report</b>")
    report.append(f"\U0001f4c5 Week of {week_start.strftime('%d %b %Y')}")
    report.append("\u2500" * 40)

    # Weekly performance
    report.append("")
    report.append("\U0001f4c8 <b>Weekly Performance</b>")
    pnl_emoji = "\U0001f7e2" if weekly_pnl >= 0 else "\U0001f534"
    report.append(f"  {pnl_emoji} P&L: {weekly_pnl:+.2f} SGD ({weekly_r:+.1f}R)")
    report.append(f"  Trades: {weekly_count} | W/L: {weekly_wins}/{weekly_losses} | WR: {weekly_wr:.0f}%")
    report.append(f"  Avg R: {avg_r:+.2f}")
    if best:
        report.append(f"  Best: {best['epic']} {best['direction']} {float(best['pnl'] or 0):+.2f} SGD")
    if worst and float(worst["pnl"] or 0) < 0:
        report.append(f"  Worst: {worst['epic']} {worst['direction']} {float(worst['pnl'] or 0):+.2f} SGD")

    # Signal stats
    if weekly_signals:
        sig_total = int(weekly_signals["total"] or 0)
        sig_exec = int(weekly_signals["executed"] or 0)
        sig_skip = int(weekly_signals["skipped"] or 0)
        sig_mtf = int(weekly_signals["mtf_blocked"] or 0)
        report.append("")
        report.append("\U0001f4e1 <b>Signals This Week</b>")
        report.append(f"  Total: {sig_total} | Executed: {sig_exec} | Skipped: {sig_skip}")
        if sig_mtf > 0:
            report.append(f"  MTF Blocked: {sig_mtf}")

    # Open positions
    report.append("")
    report.append(f"\U0001f4bc <b>Open Positions ({len(positions)})</b>")
    if pos_lines:
        report.extend(pos_lines)
        report.append(f"  \u2500\u2500\u2500")
        upl_emoji = "\U0001f7e2" if total_unrealised >= 0 else "\U0001f534"
        report.append(f"  {upl_emoji} Unrealised: {total_unrealised:+.2f} SGD")
    else:
        report.append("  No open positions \u2705")

    # Risk assessment
    report.append("")
    report.append("\u26a0\ufe0f <b>Weekend Risk</b>")
    risk_emoji = "\U0001f7e2" if gap_risk == "LOW" else "\U0001f7e1" if gap_risk == "MEDIUM" else "\U0001f534"
    report.append(f"  {risk_emoji} Gap Risk: {gap_risk}")
    report.append(f"  Direction Exposure: {buy_count} BUY / {sell_count} SELL")
    if gap_warning:
        report.append(gap_warning)

    # All-time summary
    report.append("")
    report.append("\U0001f4ca <b>All-Time</b>")
    report.append(f"  Trades: {total_trades} | P&L: {total_pnl:+.2f} SGD | WR: {total_wr:.0f}%")

    report.append("")
    report.append(f"\u23f0 {now.strftime('%H:%M UTC %d/%m/%Y')}")

    return "\n".join(report)


def should_send_report(now: datetime = None) -> bool:
    """Check if it's time to send the weekly report (Fri 21:50 UTC)."""
    global _last_report_date
    if now is None:
        now = datetime.now(timezone.utc)

    # Friday = 4, check 21:50-21:55 UTC window
    if now.weekday() != 4:
        return False
    if now.hour != 21 or now.minute < 50 or now.minute > 55:
        return False

    # Only send once per day
    today = now.date()
    if _last_report_date == today:
        return False

    _last_report_date = today
    return True


def mark_report_sent():
    """Mark report as sent for today."""
    global _last_report_date
    _last_report_date = datetime.now(timezone.utc).date()
