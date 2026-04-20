import re, os

# ============================================================
# v2.4.0 Patcher: MTF Confluence Integration
# Patches: scanner.py, telegram_bot.py
# ============================================================

def patch(path, patches, lbl):
    if not os.path.exists(path):
        print(f"  \u26a0\ufe0f {lbl}: not found")
        return
    with open(path) as f:
        code = f.read()
    orig = code
    for n, fn in patches:
        r = fn(code)
        if r != code:
            code = r
            print(f"  \u2705 {lbl}: {n}")
        else:
            print(f"  \u23ed\ufe0f {lbl}: {n} (done)")
    if code != orig:
        with open(path, "w") as f:
            f.write(code)

# ── scanner.py patches ─────────────────────────────────────

def add_mtf_import(c):
    if "mtf_confluence" in c:
        return c
    # Add after trade_manager imports
    for marker in ["from trade_manager import", "from execution import", "from signal_scorer import"]:
        if marker in c:
            idx = c.index(marker)
            end = c.index("\n", idx)
            imp = "\nfrom mtf_confluence import check_mtf_alignment, clear_cache as clear_mtf_cache\n"
            return c[:end+1] + imp + c[end+1:]
    return c

def add_mtf_env(c):
    """Add MTF config loading from .env"""
    if "MTF_REQUIRED" in c:
        return c
    for marker in ["PARTIAL_TP_RATIO", "TRAILING_AFTER", "ML_CONFIDENCE", "BREAKEVEN_TRIGGER"]:
        if marker in c:
            idx = c.index(marker)
            end = c.index("\n", idx)
            env_lines = ('\nMTF_REQUIRED = os.getenv("MTF_REQUIRED", "false").lower() == "true"\n'
                        'MTF_BONUS_CONFLUENCE = int(os.getenv("MTF_BONUS_CONFLUENCE", "2"))\n')
            return c[:end+1] + env_lines + c[end+1:]
    # Fallback: add after imports
    if "import os" in c:
        idx = c.index("import os")
        end = c.index("\n", idx)
        env_lines = ('\nMTF_REQUIRED = os.getenv("MTF_REQUIRED", "false").lower() == "true"\n'
                    'MTF_BONUS_CONFLUENCE = int(os.getenv("MTF_BONUS_CONFLUENCE", "2"))\n')
        return c[:end+1] + env_lines + c[end+1:]
    return c

def add_mtf_cache_clear(c):
    """Clear MTF cache at start of each scan cycle"""
    if "clear_mtf_cache" in c and "clear_mtf_cache()" in c:
        return c
    # Add before the scan loop starts (after "Scanning X instruments")
    for p in [r'(logger\.info.*[Ss]canning.*instruments.*\n)']:
        m = re.search(p, c)
        if m:
            return c[:m.end()] + "    clear_mtf_cache()\n" + c[m.end():]
    return c

def add_mtf_check(c):
    """Add MTF alignment check after signal generation, before sending"""
    if "check_mtf_alignment" in c and "MTF" in c:
        return c
    # Find where signals are filtered (after ML filter, before notify)
    for p in [r'([ \t]+)(sig_data\[.ml_score.\]\s*=\s*ml_sc\n)',
              r'([ \t]+)(notify_signal\(sig_data\))',
              r'([ \t]+)(send_signal_alert\(sig_data\))',
              r'([ \t]+)(await.*notify.*signal)']:
        m = re.search(p, c, re.I)
        if m:
            ind = m.group(1)
            mtf_block = (
                f"\n{ind}# v2.4.0: MTF Confluence\n"
                f"{ind}try:\n"
                f"{ind}    mtf_ok, mtf_adj, mtf_reason = check_mtf_alignment(\n"
                f"{ind}        sig_data.get('epic',''), sig_data.get('direction',''),\n"
                f"{ind}        client, MTF_REQUIRED, MTF_BONUS_CONFLUENCE)\n"
                f"{ind}    sig_data['mtf_aligned'] = mtf_ok\n"
                f"{ind}    sig_data['mtf_reason'] = mtf_reason\n"
                f"{ind}    if mtf_adj != 0:\n"
                f"{ind}        sig_data['confluence'] = sig_data.get('confluence',0) + mtf_adj\n"
                f"{ind}    if not mtf_ok:\n"
                f'{ind}        logger.info(f"MTF BLOCKED: {{sig_data.get(\'epic\')}} {{sig_data.get(\'direction\')}} - {{mtf_reason}}")\n'
                f"{ind}        continue\n"
                f'{ind}except Exception as e: logger.warning(f"MTF check error: {{e}}")\n\n'
            )
            return c[:m.start()] + mtf_block + c[m.start():]
    return c

patch("bot/scanner.py", [
    ("MTF imports", add_mtf_import),
    ("MTF env config", add_mtf_env),
    ("MTF cache clear", add_mtf_cache_clear),
    ("MTF alignment check", add_mtf_check),
], "scanner")

# ── telegram_bot.py patches ────────────────────────────────

def add_mtf_tg_import(c):
    if "mtf_commands" in c:
        return c
    for marker in ["from pnl_commands import", "from recall_commands import"]:
        if marker in c:
            idx = c.index(marker)
            end = c.index("\n", idx)
            return c[:end+1] + "from mtf_commands import mtf_cmd\n" + c[end+1:]
    return c

def add_mtf_handler(c):
    if '"mtf"' in c:
        return c
    for p in [r'(app\.add_handler\(CommandHandler\("fixpnl"[^)]+\)\))',
              r'(app\.add_handler\(CommandHandler\("recall"[^)]+\)\))',
              r'(app\.add_handler\(CommandHandler\("help"[^)]+\)\))']:
        m = re.search(p, c)
        if m:
            return c[:m.end()] + '\n    app.add_handler(CommandHandler("mtf", mtf_cmd))' + c[m.end():]
    return c

patch("bot/telegram_bot.py", [
    ("MTF import", add_mtf_tg_import),
    ("MTF handler", add_mtf_handler),
], "telegram_bot")

print("\n\u2705 v2.4.0 patches complete (MTF Confluence).")
