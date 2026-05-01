#!/usr/bin/env python3
"""Capital.com Trading Bot - Scanner with persistence, risk controls, and restart recovery."""
import sys, os, time, asyncio, logging, traceback
import signal as _signal
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler("bot.log")])
logger = logging.getLogger("scanner")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (CAPITAL_API_URL, CAPITAL_API_KEY, CAPITAL_EMAIL, CAPITAL_PASSWORD,
    SCAN_INTERVAL_SEC, MAX_SCAN_ROUNDS, DEFAULT_INSTRUMENTS, DEFAULT_TIMEFRAMES,
    WINNING_ZONE_COMBOS, resolve_instrument, get_current_session, HEARTBEAT_INTERVAL)
from capital_client import CapitalClient
from data_fetcher import fetch_candles, add_technical_indicators
from strategies.base import StrategyRegistry
from strategies.smc_ict import SMCICTStrategy
from execution import get_open_positions, _apply_trailing_sl, sync_positions_with_db
import persistence as db
import risk_manager
import regime_filter
import telegram_bot
from instrument_manager import get_merged_config
from trade_validator import validate_all_open_trades, init_validation_schema, compute_invalidation_price, store_pattern_context

from signal_scorer import should_take_signal, score_signal
from trade_manager import init_trade_manager_schema, manage_trades, get_open_trades_for_management

from mtf_confluence import check_mtf_alignment, clear_cache as clear_mtf_cache
from market_hours import is_market_open, get_scannable_instruments
from bot_trailing import TrailingManager
from execution import get_current_price, get_instrument_atr

try:
    from news_filter import check_news_risk, is_guard_active, NEWS_CONFLUENCE_PENALTY, NEWS_REQUIRED
except ImportError:
    check_news_risk = None

try:
    from retrace_entry import init_retrace_scanner, scan_retrace_entry
    _retrace_available = True
except ImportError:
    _retrace_available = False

# v2.8.0: Smart signal intelligence
try:
    from market_intelligence import MarketIntelligence
    from signal_guardrails import SignalGuardrails
    _intel = MarketIntelligence()
    _guardrails = SignalGuardrails(market_intel=_intel)
    HAS_GUARDRAILS = True
    logger.info("Smart signal guardrails initialized")
except ImportError as e:
    logger.warning(f"Guardrails not available: {e}")
    HAS_GUARDRAILS = False
    _guardrails = None

# v2.9.0: Initialize retrace-entry scanner
_retrace_scanner = None
if _retrace_available:
    try:
        _retrace_scanner = init_retrace_scanner()
        logger.info("Retrace-entry scanner initialized")
    except Exception as e:
        logger.warning(f"Retrace scanner init failed: {e}")
        _retrace_scanner = None

from version import BOT_VERSION

# v2.10.0: Signal Decision Engine
try:
    import signal_decision
    import news_filter as _news_filter_mod
    import signal_scorer as _ml_scorer_mod
    HAS_DECISION_ENGINE = True
    logger.info("Signal decision engine v2.10.0 loaded")
except ImportError as e:
    logger.warning(f"Decision engine not available, using legacy flow: {e}")
    HAS_DECISION_ENGINE = False
    signal_decision = None
    _news_filter_mod = None
    _ml_scorer_mod = None

_running = True

def _signal_handler(sig, frame):
    global _running
    logger.info("Shutdown signal received...")
    _running = False
_signal.signal(_signal.SIGTERM, _signal_handler)
_signal.signal(_signal.SIGINT, _signal_handler)


def scan_and_notify(client, strategy, instruments, timeframes):
    """Run signal scan with persistence, dedup, and risk checks."""
    import pandas as pd
    all_signals = []
    sessions = get_current_session()
    session_str = ", ".join(s.value for s in sessions)
    logger.info("Scanning %d instruments x %d timeframes...", len(instruments), len(timeframes))
    clear_mtf_cache()

    for inst in instruments:
        inst_name = resolve_instrument(inst)
        for tf in timeframes:
            try:
                df = fetch_candles(client, inst, tf, count=500)
                if df.empty or len(df) < 50:
                    continue
                df = add_technical_indicators(df)
                regime = regime_filter.detect_regime(df)
                signals = strategy.generate_signals(df, inst, tf)
                # v2.9.0: Add retrace-entry signals
                if _retrace_scanner is not None:
                    try:
                        retrace_sigs = scan_retrace_entry(df, inst, tf)
                        for rs in retrace_sigs:
                            sig_obj = type('Sig', (), {
                                'direction': rs['direction'],
                                'entry_price': rs['entry'],
                                'stop_loss': rs['sl'],
                                'take_profit': rs['tp'],
                                'risk_reward_ratio': lambda self: rs['rr_ratio'],
                                'confluence': rs['confluence'],
                                'metadata': rs,
                            })()
                            signals.append(sig_obj)
                        if retrace_sigs:
                            logger.info(f'Retrace: {len(retrace_sigs)} signals for {inst} {tf}')
                    except Exception as e:
                        logger.warning(f'Retrace scan error {inst} {tf}: {e}')

                for sig in signals:
                    # ── Build sig_data (same structure for DB/Telegram compat) ──
                    zt = sig.metadata.get("zone_types", "")
                    is_top5 = zt in WINNING_ZONE_COMBOS
                    rsi = df["rsi"].iloc[-1] if "rsi" in df.columns else 0
                    risk_pct = abs(sig.entry_price - sig.stop_loss) / sig.entry_price * 100 if sig.entry_price else 0
                    sig_data = {
                        "instrument": inst, "inst_name": inst_name, "tf": tf,
                        "direction": sig.direction, "entry": sig.entry_price,
                        "sl": sig.stop_loss, "tp": sig.take_profit,
                        "rr": sig.risk_reward_ratio(),
                        "confluence": sig.metadata.get("smc_confluence", sig.metadata.get("confluence", 0)),
                        "zone_types": zt, "mss_type": sig.metadata.get("mss_type", "none"),
                        "rsi": float(rsi) if not pd.isna(rsi) else 0,
                        "top5": is_top5, "risk_pct": risk_pct,
                        "session": session_str,
                        "metadata": sig.metadata,
                    }
                    sig_data["regime"] = regime.get("label", "")

                    # ── v2.10.0: Signal Decision Engine ──
                    if HAS_DECISION_ENGINE and signal_decision is not None:
                        decision = signal_decision.evaluate_signal_candidate(
                            signal=sig_data,
                            df=df,
                            client=client,
                            instrument=inst,
                            timeframe=tf,
                            regime=regime,
                            guardrails=_guardrails,
                            risk_manager=risk_manager,
                            news_filter_mod=_news_filter_mod,
                            ml_scorer_mod=_ml_scorer_mod,
                            mtf_func=check_mtf_alignment,
                        )
                        sig_data["decision"] = decision
                        sig_data["guardrail_text"] = decision.get("telegram_text", "")
                        sig_data["guardrail_score"] = decision["modifiers"].get("guardrail_raw", 0)

                        status = decision["status"]
                        if status == signal_decision.BLOCK:
                            logger.info("  BLOCKED: %s %s [%s] score=%d blocks=%s",
                                inst_name, sig.direction, tf, decision["score"],
                                decision["blocks"][:2])
                            sig_row_id = db.save_signal(sig_data)
                            db.mark_signal(sig_row_id, "blocked")
                            all_signals.append(sig_data)
                            continue
                        elif status == signal_decision.WATCH:
                            logger.info("  WATCH: %s %s [%s] score=%d quality=%s",
                                inst_name, sig.direction, tf, decision["score"],
                                decision["quality"])
                            sig_row_id = db.save_signal(sig_data)
                            db.mark_signal(sig_row_id, "watch")
                            all_signals.append(sig_data)
                            continue
                        elif status == signal_decision.ALERT:
                            logger.info("  ALERT: %s %s [%s] score=%d quality=%s",
                                inst_name, sig.direction, tf, decision["score"],
                                decision["quality"])
                            sig_row_id = db.save_signal(sig_data)
                            sig_data["_db_id"] = sig_row_id
                            sig_data["_created_at"] = time.time()
                            telegram_bot.notify_signal(sig_data, executable=False)
                            all_signals.append(sig_data)
                            continue
                        else:  # EXECUTABLE
                            logger.info("  EXECUTABLE: %s %s [%s] score=%d quality=%s",
                                inst_name, sig.direction, tf, decision["score"],
                                decision["quality"])
                            sig_row_id = db.save_signal(sig_data)
                            sig_data["_db_id"] = sig_row_id
                            sig_data["_created_at"] = time.time()
                            telegram_bot.notify_signal(sig_data)
                            all_signals.append(sig_data)
                            continue

                    # ── Legacy flow (fallback if decision engine unavailable) ──
                    if HAS_GUARDRAILS and _guardrails is not None:
                        try:
                            _eval = _guardrails.evaluate_signal(
                                df=df, instrument=inst, direction=sig.direction,
                                timeframe=tf
                            )
                            sig.metadata["guardrail_score"] = _eval["final_score"]
                            sig.metadata["guardrail_quality"] = _eval["quality"]
                            sig.metadata["guardrail_text"] = _eval["telegram_text"]
                            if not _eval["passed"]:
                                logger.info("Signal BLOCKED: %s %s %s (score:%s)"
                                           % (inst, tf, sig.direction, _eval["final_score"]))
                                continue
                            else:
                                logger.info("Signal PASSED: %s %s %s (score:%s, quality:%s)"
                                           % (inst, tf, sig.direction, _eval["final_score"], _eval["quality"]))
                        except Exception as _ge:
                            logger.error("Guardrail error: %s" % _ge)

                    if is_top5:
                        if "retrace" not in zt:
                          regime_ok, regime_reason = regime_filter.is_setup_allowed(
                              regime, zt, sig.direction)
                          if not regime_ok:
                              logger.info("  REGIME BLOCKED: %s %s [%s] - %s", inst_name, sig.direction, tf, regime_reason)
                              continue

                        is_dup, dup_reason = risk_manager.check_duplicate_signal(
                            inst, sig.direction, tf)
                        if is_dup:
                            logger.info("  DEDUP: %s %s [%s] - %s", inst_name, sig.direction, tf, dup_reason)
                            sig_row_id = db.save_signal(sig_data)
                            db.mark_signal(sig_row_id, "skipped")
                            all_signals.append(sig_data)
                            continue

                        try:
                            aligned, mtf_adj, mtf_reason = check_mtf_alignment(
                                inst, sig.direction, client)
                            if not aligned and MTF_REQUIRED:
                                logger.info("  MTF BLOCKED: %s %s - %s", inst_name, sig.direction, mtf_reason)
                                sig_row_id = db.save_signal(sig_data)
                                db.mark_signal(sig_row_id, "mtf_blocked")
                                all_signals.append(sig_data)
                                continue
                            if mtf_adj != 0:
                                old_conf = sig_data.get("confluence", 0)
                                sig_data["confluence"] = old_conf + mtf_adj
                                logger.info("  MTF %s: %s %s conf %d->%d (%s)",
                                    "ALIGNED" if aligned else "COUNTER",
                                    inst_name, sig.direction, old_conf,
                                    sig_data["confluence"], mtf_reason)
                        except Exception as mtf_err:
                            logger.warning("  MTF check failed: %s", mtf_err)

                        sig_row_id = db.save_signal(sig_data)
                        sig_data["_db_id"] = sig_row_id
                        sig_data["_created_at"] = time.time()
                        telegram_bot.notify_signal(sig_data)
                        logger.info("  TOP5: %s %s [%s] | Zones: %s", inst_name, sig.direction, tf, zt)
                    else:
                        sig_row_id = db.save_signal(sig_data)
                        sig_data["_db_id"] = sig_row_id

                    all_signals.append(sig_data)
            except Exception as e:
                db.log_error("strategy", f"Scan error {inst}/{tf}", traceback.format_exc())
                logger.info("  SCAN ERROR %s/%s: %s", inst, tf, e)

    top5_count = sum(1 for s in all_signals if s.get("top5"))
    logger.info("Scan complete: %d total | %d top-5", len(all_signals), top5_count)

    # v2.3.4: Trade Mgmt
    try:
        otr = get_open_trades_for_management()
        if otr:
            cpr = {t['epic']: get_current_price(t['epic']) for t in otr}
            manage_trades(otr, cpr, update_position_sl, partial_close_position, get_instrument_atr, send_telegram_message)
    except Exception as e: logger.error(f"TM error: {e}")

    return all_signals, top5_count


def main():
    global _running

    logger.info("=" * 60)
    logger.info("  CAPITAL.COM TRADING BOT v%s - STARTING", BOT_VERSION)
    logger.info("=" * 60)

    # Initialize database
    db.init_db()
    logger.info("  Database initialized")

    # Initialize Capital.com client
    client = CapitalClient(CAPITAL_API_URL, CAPITAL_API_KEY, CAPITAL_EMAIL, CAPITAL_PASSWORD)

    if not client.ping():
        logger.error("Failed to connect to Capital.com")
        db.log_error("auth", "Failed to connect on startup")
        sys.exit(1)

    accs = client.get_accounts()
    for acc in accs.get("accounts", []):
        logger.info("  Account: %s | Balance: %s %s",
                     acc["accountId"], acc["balance"]["balance"], acc["currency"])

    # Restart recovery: sync positions with broker
    broker_positions = sync_positions_with_db(client)

    logger.info("  Restart recovery: %d open positions at broker", len(broker_positions))

    # v2.7.3: Initialize bot-side trailing manager
    trailing_manager = TrailingManager(client)
    logger.info("  Trailing manager initialized (enabled=%s, breakeven=%.1fR, trail_start=%.1fR)",
                trailing_manager.state is not None, 1.0, 1.5)

    # Initialize strategy
    registry = StrategyRegistry()
    smc = SMCICTStrategy()
    registry.register(smc)

    # Initialize Telegram bot
    telegram_bot._client = client
    app = telegram_bot.setup_telegram_app()
    if app:
        telegram_bot.start_polling_background()
        time.sleep(2)
        telegram_bot.send_message_sync(
            "\U0001f916 <b>Trading Bot v" + BOT_VERSION + " Started</b>\n"
            "\u23f0 " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") + "\n"
            "\U0001f501 Scanning every " + str(SCAN_INTERVAL_SEC // 60) + " min\n"
            "\U0001f6e1 Risk controls active\n"
            "\U0001f4be SQLite persistence enabled\n"
            "\U0001f4f1 /help for commands | /about for info")

    instruments = DEFAULT_INSTRUMENTS
    scan_count = 0
    round_num = 0

    while _running:
        round_num += 1

        # If MAX_SCAN_ROUNDS > 0, enforce limit
        if MAX_SCAN_ROUNDS > 0 and round_num > MAX_SCAN_ROUNDS:
            break

        now_utc = datetime.now(timezone.utc)
        sessions = get_current_session()
        session_str = ", ".join(s.value for s in sessions)

        # Check for manual scan request
        if telegram_bot.manual_scan_requested:
            telegram_bot.manual_scan_requested = False
            manual_tfs = telegram_bot.manual_scan_timeframes or DEFAULT_TIMEFRAMES
            telegram_bot.manual_scan_timeframes = None

            logger.info("=" * 60)
            logger.info("  MANUAL SCAN -- %s -- TFs: %s", now_utc.strftime("%H:%M:%S UTC"), manual_tfs)

            try:
                all_signals, top5_count = scan_and_notify(client, smc, instruments, manual_tfs)
                telegram_bot.send_message_sync(
                    "\U0001f50d <b>Manual Scan Complete</b>\n"
                    "TFs: " + ", ".join(manual_tfs) + "\n"
                    "\U0001f4ca " + str(len(all_signals)) + " signals, " + str(top5_count) + " top-5")
            except Exception as e:
                logger.error("Manual scan error: %s", e)
                db.log_error("strategy", "Manual scan failed", traceback.format_exc())
                telegram_bot.send_message_sync("\u274c Manual scan error: " + str(e))

        # Check if scanner is active (can be paused via /stop)
        if not telegram_bot.scanner_active:
            if round_num % 20 == 0:
                logger.info("  Scanner paused (use /start to resume)")
            # Still check positions and trailing SL even when paused
            # v2.7.3: Bot-side trailing even when scanner paused
            try:
                updates = trailing_manager.update_all()
                if updates:
                    logger.info("  [PAUSED] Trail updates: %d", len(updates))
            except Exception as e:
                db.log_error("trailing", "Trailing SL error while paused", str(e))
            # Sleep 15 seconds then check again
            for _ in range(15):
                if not _running: break
                if telegram_bot.manual_scan_requested: break
                if telegram_bot.scanner_active: break
                time.sleep(1)
            continue

        # Regular scheduled scan
        scan_count += 1
        logger.info("=" * 60)
        logger.info("  SCAN #%d (round %d) -- %s", scan_count, round_num, now_utc.strftime("%H:%M:%S UTC"))
        logger.info("  Sessions: %s", session_str)

        try:
            all_signals, top5_count = scan_and_notify(client, smc, instruments, DEFAULT_TIMEFRAMES)
        except Exception as e:
            logger.error("Scan error: %s", e)
            db.log_error("strategy", "Scheduled scan failed", traceback.format_exc())
            all_signals, top5_count = [], 0

        # v2.7.3: Bot-side trailing (breakeven + ratcheting SL)
        positions_count = trail_updates = 0
        try:
            positions = get_open_positions(client)
            positions_count = len(positions)
            # Run TrailingManager (handles breakeven at 1R, trail after 1.5R)
            updates = trailing_manager.update_all()
            trail_updates = len(updates)
            for u in updates:
                logger.info("  TRAIL: %s SL->%.5f (%s)", u['deal_id'], u['new_sl'], u['reason'])
            # Cleanup state for closed positions
            open_deal_ids = {pos.get('position', {}).get('dealId') for pos in positions}
            trailing_manager.cleanup_closed(open_deal_ids)
            logger.info("  Positions: %d | Trail updates: %d", positions_count, trail_updates)
        except Exception as e:
            logger.error("Position/trailing error: %s", e)
            db.log_error("trailing", "Bot-side trailing failed", traceback.format_exc())

        # Account status
        bal_str = pnl_str = "N/A"
        try:
            accs = client.get_accounts()
            acc = accs.get("accounts", [{}])[0]
            bal = acc.get("balance", {})
            bal_str = str(bal.get("balance", "N/A"))
            pnl_str = str(bal.get("profitLoss", "N/A"))
            logger.info("  Balance: %s | P&L: %s", bal_str, pnl_str)
        except Exception as e:
            db.log_error("auth", "Account fetch failed", str(e))

        # Heartbeat / Hourly Telegram summary
        if scan_count % HEARTBEAT_INTERVAL == 0 and app:
            risk_status = risk_manager.get_risk_status()
            telegram_bot.notify_scan_summary(
                scan_count, MAX_SCAN_ROUNDS or "inf", len(all_signals),
                top5_count, positions_count, bal_str, pnl_str, session_str,
                risk_status)

        # Periodic DB cleanup (once a day ~ every 96 scans)
        if scan_count % 96 == 0:
            db.cleanup_old_data(days=90)

        # Sleep until next scan (check for manual scan/stop every second)
        if _running:
            next_scan = now_utc + timedelta(seconds=SCAN_INTERVAL_SEC)
            logger.info("  Next scan at %s", next_scan.strftime("%H:%M:%S UTC"))
            for _ in range(SCAN_INTERVAL_SEC):
                if not _running: break
                if telegram_bot.manual_scan_requested: break
                if not telegram_bot.scanner_active: break
                time.sleep(1)

    logger.info("Scanner stopped.")
    if app:
        telegram_bot.send_message_sync("\U0001f6d1 <b>Trading Bot Stopped</b>")
        telegram_bot.stop_polling()


if __name__ == "__main__":
    main()
