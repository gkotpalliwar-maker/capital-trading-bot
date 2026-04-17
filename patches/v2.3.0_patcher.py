import re, os
def patch(path, patches, lbl):
    if not os.path.exists(path): print(f"  ⚠️ {lbl}: not found"); return
    with open(path) as f: code = f.read()
    orig = code
    for n, fn in patches:
        r = fn(code)
        if r != code: code = r; print(f"  ✅ {lbl}: {n}")
        else: print(f"  ⏭️ {lbl}: {n} (done)")
    if code != orig:
        with open(path,"w") as f: f.write(code)

# scanner.py
def add_ml_imp(c):
    if "signal_scorer" in c: return c
    i = "\nfrom signal_scorer import should_take_signal, score_signal\nfrom trade_manager import init_trade_manager_schema, manage_trades, get_open_trades_for_management\n"
    for m in ["from trade_validator import", "from instrument_manager import"]:
        if m in c:
            idx = c.index(m); end = c.index("\n", idx)
            return c[:end+1] + i + c[end+1:]
    return i + c

def add_init_tm(c):
    if "init_trade_manager_schema()" in c: return c
    if "init_validation_schema()" in c:
        idx = c.index("init_validation_schema()"); end = c.index("\n", idx)
        return c[:end+1] + "    init_trade_manager_schema()\n" + c[end+1:]
    return c

def add_ml_filter(c):
    if "should_take_signal(sig_data)" in c: return c
    for p in [r'([ \t]+)(notify_signal\(sig_data\))', r'([ \t]+)(send_signal_alert\(sig_data\))', r'([ \t]+)(await.*notify.*signal)']:
        m = re.search(p, c, re.I)
        if m:
            ind = m.group(1)
            ml = (f"{ind}# v2.3.0: ML\n{ind}ml_ok, ml_sc, ml_r = should_take_signal(sig_data)\n"
                  f"{ind}sig_data['ml_score'] = ml_sc\n{ind}if not ml_ok:\n"
                  f'{ind}    logger.info(f"ML BLOCKED: {{sig_data.get(\'instrument\')}} - {{ml_r}}")\n{ind}    continue\n\n')
            return c[:m.start()] + ml + c[m.start():]
    return c

def add_tm_call(c):
    if "manage_trades(" in c: return c
    for p in [r'([ \t]+)(validate_all_open_trades\([^)]+\))', r'([ \t]+)(trailing_stop_monitor\([^)]+\))', r'([ \t]+)(logger\.info.*scan.*complete)']:
        m = re.search(p, c, re.I)
        if m:
            ind = m.group(1)
            tm = (f"\n\n{ind}# v2.3.0: Trade Mgmt\n{ind}try:\n{ind}    otr = get_open_trades_for_management()\n"
                  f"{ind}    if otr:\n{ind}        cpr = {{t['epic']: get_current_price(t['epic']) for t in otr}}\n"
                  f"{ind}        manage_trades(otr, cpr, update_position_sl, partial_close_position, get_instrument_atr, send_telegram_message)\n"
                  f'{ind}except Exception as e: logger.error(f"TM error: {{e}}")\n')
            return c[:m.end()] + tm + c[m.end():]
    return c

patch("bot/scanner.py", [("ML imports", add_ml_imp), ("init_tm", add_init_tm), ("ML filter", add_ml_filter), ("TM call", add_tm_call)], "scanner")

# telegram_bot.py
def add_tg_imp(c):
    if "signal_scorer_commands" in c: return c
    i = "\nfrom signal_scorer_commands import mlstats_cmd, retrain_cmd, mlthreshold_cmd\nfrom trade_manager_commands import breakeven_cmd, partialtp_cmd, trademanage_cmd\n"
    for m in ["from trade_validator_commands import", "from instrument_commands import", "import logging"]:
        if m in c:
            idx = c.index(m); end = c.index("\n", idx)
            return c[:end+1] + i + c[end+1:]
    return i + c

def add_tg_handlers(c):
    if 'CommandHandler("mlstats"' in c: return c
    for p in [r'(app\.add_handler\(CommandHandler\("validity"[^)]+\)\))', r'(app\.add_handler\(CommandHandler\("help"[^)]+\)\))']:
        m = re.search(p, c)
        if m:
            h = ('\n    # v2.3.0\n    app.add_handler(CommandHandler("mlstats", mlstats_cmd))\n'
                 '    app.add_handler(CommandHandler("retrain", retrain_cmd))\n'
                 '    app.add_handler(CommandHandler("mlthreshold", mlthreshold_cmd))\n'
                 '    app.add_handler(CommandHandler("breakeven", breakeven_cmd))\n'
                 '    app.add_handler(CommandHandler("partialtp", partialtp_cmd))\n'
                 '    app.add_handler(CommandHandler("trademanage", trademanage_cmd))\n')
            return c[:m.end()] + h + c[m.end():]
    return c

patch("bot/telegram_bot.py", [("TG imports", add_tg_imp), ("TG handlers", add_tg_handlers)], "telegram_bot")

# execution.py
def add_exec_funcs(c):
    if "def partial_close_position" in c: return c
    f = """

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
"""
    return c.rstrip() + "\n" + f

patch("bot/execution.py", [("exec funcs", add_exec_funcs)], "execution")
print("\n✅ Patches done.")