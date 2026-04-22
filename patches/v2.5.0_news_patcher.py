import re
import os

print("v2.5.0: News Filter Integration Patcher")
print("=" * 50)

BOT_DIR = os.path.join(os.getcwd(), "bot")

# ── 1. Patch telegram_bot.py — register news commands ──
tb_path = os.path.join(BOT_DIR, "telegram_bot.py")
with open(tb_path) as f:
    tb = f.read()

changes = 0

# Add import for news_commands
if "news_commands" not in tb:
    # Find last import of commands module
    last_import = None
    for m in re.finditer(r"from\s+\w+_commands\s+import", tb):
        last_import = m
    if last_import:
        insert_pos = tb.index("\n", last_import.end()) + 1
        news_import = (
            "from news_commands import news_cmd, activate_guard_cmd, "
            "deactivate_guard_cmd, guard_status_cmd, summary_cmd\n"
        )
        tb = tb[:insert_pos] + news_import + tb[insert_pos:]
        changes += 1
        print("  + Added news_commands import")

    # Register command handlers - find the last app.add_handler line
    last_handler = None
    for m in re.finditer(r"app\.add_handler\(CommandHandler\(.+?\)\)", tb):
        last_handler = m
    if last_handler:
        insert_pos = tb.index("\n", last_handler.end()) + 1
        handlers = (
            '    app.add_handler(CommandHandler("news", news_cmd))\n'
            '    app.add_handler(CommandHandler("activateguard", activate_guard_cmd))\n'
            '    app.add_handler(CommandHandler("deactivateguard", deactivate_guard_cmd))\n'
            '    app.add_handler(CommandHandler("guardstatus", guard_status_cmd))\n'
            '    app.add_handler(CommandHandler("summary", summary_cmd))\n'
        )
        tb = tb[:insert_pos] + handlers + tb[insert_pos:]
        changes += 1
        print("  + Added 5 command handlers (news, activateguard, deactivateguard, guardstatus, summary)")

    with open(tb_path, "w") as f:
        f.write(tb)
    print(f"  telegram_bot.py: {changes} changes")
else:
    print("  telegram_bot.py: news_commands already integrated")

# ── 2. Patch scanner.py — add news check before signal emission ──
sc_path = os.path.join(BOT_DIR, "scanner.py")
with open(sc_path) as f:
    sc = f.read()

sc_changes = 0

if "news_filter" not in sc:
    # Add import at top (safe try/except)
    import_line = "\ntry:\n    from news_filter import check_news_risk, is_guard_active, NEWS_CONFLUENCE_PENALTY, NEWS_REQUIRED\nexcept ImportError:\n    check_news_risk = None\n"

    # Insert after existing imports
    import_section_end = 0
    for m in re.finditer(r"^(?:import |from )", sc, re.MULTILINE):
        import_section_end = sc.index("\n", m.end()) + 1

    if import_section_end > 0:
        sc = sc[:import_section_end] + import_line + sc[import_section_end:]
        sc_changes += 1
        print("  + Added news_filter import to scanner.py")

    # Find the MTF check block we added in v2.4.0 and add news check after it
    mtf_end = sc.find("# END MTF check")
    if mtf_end == -1:
        mtf_end = sc.find("mtf_bonus")
        if mtf_end != -1:
            mtf_end = sc.index("\n", sc.index("\n", mtf_end) + 1) + 1

    if mtf_end > 0:
        insert_pos = sc.index("\n", mtf_end) + 1
        news_check = '''
                    # ── v2.5.0 NEWS CHECK ──
                    if check_news_risk is not None and is_guard_active():
                        try:
                            news_risk, news_events, news_reason = check_news_risk(epic)
                            if news_risk == "blocked" and NEWS_REQUIRED:
                                logger.info(f"NEWS BLOCKED: {epic} {direction} - {news_reason}")
                                continue
                            elif news_risk == "blocked":
                                confluence -= NEWS_CONFLUENCE_PENALTY
                                logger.info(f"NEWS CAUTION: {epic} {direction} - {news_reason} (-{NEWS_CONFLUENCE_PENALTY} conf)")
                            elif news_risk == "caution":
                                confluence -= max(1, NEWS_CONFLUENCE_PENALTY // 2)
                                logger.info(f"NEWS ADVISORY: {epic} {direction} - {news_reason}")
                        except Exception as ne:
                            logger.warning(f"News check failed: {ne}")
                    # END NEWS check
'''
        sc = sc[:insert_pos] + news_check + sc[insert_pos:]
        sc_changes += 1
        print("  + Added news check block to scanner signal flow")

    with open(sc_path, "w") as f:
        f.write(sc)
    print(f"  scanner.py: {sc_changes} changes")
else:
    print("  scanner.py: news_filter already integrated")

print()
print("=" * 50)
print("Done! Restart trading-bot to apply.")
print("New commands: /news, /activateguard, /deactivateguard, /guardstatus, /summary")
