import re, os

print("v2.4.0: Weekend Risk Report Integration")
print("=" * 50)

# ── 1. Patch scanner.py: auto-trigger report at Fri 21:50 UTC ──
scanner_path = "bot/scanner.py"
with open(scanner_path) as f:
    code = f.read()

if "risk_report" not in code:
    # Add import after mtf_confluence import
    code = code.replace(
        "from mtf_confluence import check_mtf_alignment, clear_cache as clear_mtf_cache",
        "from mtf_confluence import check_mtf_alignment, clear_cache as clear_mtf_cache\n"
        "from risk_report import should_send_report, generate_weekend_report, mark_report_sent"
    )

    # Insert report check in the main scan loop (after scan completes)
    # Find the notify_scan_summary line and insert after it
    report_check = """
            # ── Weekend Risk Report (Fri 21:50 UTC) ──
            try:
                if should_send_report():
                    logger.info("Sending weekend risk report...")
                    report_text = generate_weekend_report(client)
                    telegram_bot.send_message(report_text, parse_mode="HTML")
                    mark_report_sent()
                    logger.info("Weekend risk report sent.")
            except Exception as rpt_err:
                logger.warning("Weekend report failed: %s", rpt_err)
"""
    # Insert after the scan summary notification
    if "notify_scan_summary" in code:
        # Find the line and insert after the block
        pattern = r"(\s*telegram_bot\.notify_scan_summary\([^)]*\))"
        match = re.search(pattern, code)
        if match:
            insert_pos = match.end()
            code = code[:insert_pos] + report_check + code[insert_pos:]
            print("  + Added weekend report auto-trigger after scan summary")
        else:
            # Fallback: insert before the sleep/wait at end of loop
            code += "\n# TODO: integrate weekend report check\n"
            print("  ! Could not find ideal insertion point")
    else:
        print("  ! notify_scan_summary not found - manual integration needed")

    with open(scanner_path, "w") as f:
        f.write(code)
    print("  + scanner.py patched for weekend report")
else:
    print("  = risk_report already in scanner.py")

# ── 2. Patch telegram_bot.py: register /risk command ──
tg_path = "bot/telegram_bot.py"
with open(tg_path) as f:
    tg_code = f.read()

if "risk_report_commands" not in tg_code:
    # Add import
    if "from mtf_commands import mtf_cmd" in tg_code:
        tg_code = tg_code.replace(
            "from mtf_commands import mtf_cmd",
            "from mtf_commands import mtf_cmd\n"
            "from risk_report_commands import risk_cmd"
        )
    else:
        # Find last import and add after
        lines = tg_code.split("\n")
        last_import_idx = 0
        for i, line in enumerate(lines):
            if line.startswith("from ") or line.startswith("import "):
                last_import_idx = i
        lines.insert(last_import_idx + 1, "from risk_report_commands import risk_cmd")
        tg_code = "\n".join(lines)
    print("  + Added risk_report_commands import")

    # Register handler
    if 'CommandHandler("mtf"' in tg_code:
        tg_code = tg_code.replace(
            'CommandHandler("mtf", mtf_cmd)',
            'CommandHandler("mtf", mtf_cmd))\n'
            '    app.add_handler(CommandHandler("risk", risk_cmd)'
        )
    else:
        # Generic: find add_handler block and append
        tg_code = tg_code.replace(
            "# end command handlers",
            '    app.add_handler(CommandHandler("risk", risk_cmd))\n    # end command handlers'
        )
    print("  + Registered /risk command handler")

    with open(tg_path, "w") as f:
        f.write(tg_code)
    print("  + telegram_bot.py patched")
else:
    print("  = risk_report_commands already in telegram_bot.py")

print("\nDone! New command: /risk")
print("Auto-trigger: Friday 21:50 UTC")
