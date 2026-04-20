import re, os

# ============================================================
# v2.3.3 Patcher: Market Hours + Conflict Filter + Regime Block
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
            print(f"  \u23ed\ufe0f {lbl}: {n} (already applied)")
    if code != orig:
        with open(path, "w") as f:
            f.write(code)

# ── scanner.py patches ────────────────────────────────────────

def add_market_hours_import(c):
    """Add market_hours import to scanner.py"""
    if "market_hours" in c:
        return c
    # Insert after existing imports
    for marker in ["from trade_manager import", "from signal_scorer import", "from instrument_manager import"]:
        if marker in c:
            idx = c.index(marker)
            end = c.index("\n", idx)
            return c[:end+1] + "from market_hours import is_market_open, get_scannable_instruments\n" + c[end+1:]
    return "from market_hours import is_market_open, get_scannable_instruments\n" + c

def add_conflict_filter(c):
    """Add conflict filter after signal generation (before notification).
    When same instrument has BUY+SELL, keep highest confluence only."""
    if "# v2.3.3: Conflict filter" in c:
        return c
    # Find where signals are sorted/filtered before notification
    # Look for the top-5 sorting or notify pattern
    for p in [r"([ \t]+)(all_signals\.sort\(.*\))",
              r"([ \t]+)(top_signals\s*=)",
              r"([ \t]+)(signals_to_notify)"]:
        m = re.search(p, c)
        if m:
            ind = m.group(1)
            conflict_code = (
                f"\n{ind}# v2.3.3: Conflict filter - same instrument BUY+SELL, keep highest confluence\n"
                f"{ind}if all_signals:\n"
                f"{ind}    _cf_best = {{}}\n"
                f"{ind}    for _s in all_signals:\n"
                f"{ind}        _key = _s.get('epic', _s.get('instrument', ''))\n"
                f"{ind}        _conf = _s.get('confluence', 0)\n"
                f"{ind}        if _key not in _cf_best or _conf > _cf_best[_key].get('confluence', 0):\n"
                f"{ind}            _cf_best[_key] = _s\n"
                f"{ind}    _cf_before = len(all_signals)\n"
                f"{ind}    all_signals = list(_cf_best.values())\n"
                f"{ind}    if _cf_before != len(all_signals):\n"
                f'{ind}        logger.info("Conflict filter: %d -> %d signals", _cf_before, len(all_signals))\n'
            )
            return c[:m.start()] + conflict_code + c[m.start():]
    return c

def enforce_regime_block(c):
    """Change regime from advisory to hard block.
    Signals with 'blocked' in regime should not be sent."""
    if "# v2.3.3: Regime enforcement" in c:
        return c
    # Find where regime is checked as advisory and change to hard block
    # Look for pattern like: if regime and "blocked" in regime ... logger.info("REGIME (advisory)"
    old_pattern = 'REGIME (advisory)'
    if old_pattern in c:
        # Replace advisory with hard block
        c = c.replace(
            'REGIME (advisory)',
            'REGIME BLOCKED'
        )
        # Find the advisory log line and add a continue after it
        # Pattern: logger.info("...REGIME BLOCKED...") followed by next line without continue
        lines = c.split('\n')
        new_lines = []
        i = 0
        added = False
        while i < len(lines):
            new_lines.append(lines[i])
            if 'REGIME BLOCKED' in lines[i] and 'logger' in lines[i] and not added:
                # Check if next line is already a continue
                if i + 1 < len(lines) and 'continue' not in lines[i+1]:
                    indent = len(lines[i]) - len(lines[i].lstrip()) 
                    new_lines.append(' ' * indent + '# v2.3.3: Regime enforcement')
                    new_lines.append(' ' * indent + 'continue')
                    added = True
            i += 1
        return '\n'.join(new_lines)
    return c

def add_market_hours_check(c):
    """Add market hours check in the scan loop.
    Skip instruments where market is closed."""
    if "# v2.3.3: Market hours" in c:
        return c
    # Find the instrument scan loop: for inst in ... or for epic in ...
    for p in [r"([ \t]+)for (inst|name|instrument) in (scan_instruments|instruments_to_scan|cfg\[.scan_instruments.\])"]:
        m = re.search(p, c)
        if m:
            ind = m.group(1)
            # Find the line after the for loop header
            loop_end = c.index('\n', m.start())
            next_line_start = loop_end + 1
            # Find indentation of the loop body
            body_match = re.match(r'(\s+)', c[next_line_start:])
            body_ind = body_match.group(1) if body_match else ind + '    '
            mh_check = (
                f"{body_ind}# v2.3.3: Market hours check\n"
                f"{body_ind}try:\n"
                f"{body_ind}    _epic = INSTRUMENT_MAP.get({m.group(2)}) if '{m.group(2)}' != 'epic' else {m.group(2)}\n"
                f"{body_ind}    _mh_open, _mh_reason = is_market_open(_epic or '')\n"
                f"{body_ind}    if not _mh_open:\n"
                f'{body_ind}        logger.info("MARKET CLOSED: %s - %s", {m.group(2)}, _mh_reason)\n'
                f"{body_ind}        continue\n"
                f"{body_ind}except Exception:\n"
                f"{body_ind}    pass\n"
            )
            return c[:next_line_start] + mh_check + c[next_line_start:]
    return c

patch("bot/scanner.py", [
    ("Market hours import", add_market_hours_import),
    ("Market hours check", add_market_hours_check),
    ("Conflict filter", add_conflict_filter),
    ("Regime enforcement", enforce_regime_block),
], "scanner")

# ── telegram_bot.py patches ───────────────────────────────────

def add_recall_import(c):
    added = ""
    if "recall_commands" not in c:
        added += "from recall_commands import recall_cmd\n"
    if "pnl_commands" not in c:
        added += "from pnl_commands import fixpnl_cmd\n"
    if not added:
        return c
    for marker in ["from signal_scorer_commands import", "from trade_manager_commands import", "from recall_commands import"]:
        if marker in c:
            idx = c.index(marker)
            end = c.index("\n", idx)
            return c[:end+1] + added + c[end+1:]
    return c

def add_recall_handler(c):
    if '"recall"' in c and '"fixpnl"' in c:
        return c
    for p in [r'(app\.add_handler\(CommandHandler\("trademanage"[^)]+\)\))',
              r'(app\.add_handler\(CommandHandler\("mlstats"[^)]+\)\))',
              r'(app\.add_handler\(CommandHandler\("help"[^)]+\)\))']:
        m = re.search(p, c)
        if m:
            new_handlers = ""
            if '"recall"' not in c:
                new_handlers += '\n    app.add_handler(CommandHandler("recall", recall_cmd))'
            if '"fixpnl"' not in c:
                new_handlers += '\n    app.add_handler(CommandHandler("fixpnl", fixpnl_cmd))'
            return c[:m.end()] + new_handlers + "\n" + c[m.end():]
    return c

patch("bot/telegram_bot.py", [
    ("Recall import", add_recall_import),
    ("Recall handler", add_recall_handler),
], "telegram_bot")


# ── Fix missing await on edit_message_text ────────────────────
def fix_missing_await(c):
    lines = c.split("\n")
    fixed = 0
    for i, line in enumerate(lines):
        if "context.bot.edit_message_text(" in line and "await " not in line:
            lines[i] = line.replace("context.bot.edit_message_text(", "await context.bot.edit_message_text(")
            fixed += 1
    if fixed:
        print(f"  \u2705 telegram_bot: fixed {fixed} missing await(s)")
    return "\n".join(lines)

patch("bot/telegram_bot.py", [("Fix missing await", fix_missing_await)], "telegram_bot")

# ── Update /help ──────────────────────────────────────────────
with open("bot/telegram_bot.py") as f:
    code = f.read()
if "/recall" not in code or "Signal Recall" not in code:
    old_help = '/validate - Check open trade validity'
    new_help = '/validate - Check open trade validity\\n\\n"\n        "<b>Signal Recall (v2.3.3)</b>\\n"\n        "/recall - Recall signals (last 4h)\\n"\n        "/recall 8 - Last 8 hours\\n"\n        "/recall 2d - Last 2 days'
    if old_help in code:
        code = code.replace(old_help, new_help)
        with open("bot/telegram_bot.py", "w") as f:
            f.write(code)
        print("  \u2705 /help: Added /recall commands")

print("\n\u2705 v2.3.3 patches complete.")
