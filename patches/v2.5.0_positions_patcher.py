import re
import os

print("v2.5.0 Phase 3: Enhanced /positions with News Risk + Buttons")
print("=" * 50)

BOT_DIR = os.path.join(os.getcwd(), "bot")
tb_path = os.path.join(BOT_DIR, "telegram_bot.py")

with open(tb_path) as f:
    tb = f.read()

changes = 0

if "positions_commands" not in tb:
    # Add import
    last_import = None
    for m in re.finditer(r"from\s+\w+_commands\s+import", tb):
        last_import = m
    if last_import:
        insert_pos = tb.index("\n", last_import.end()) + 1
        new_import = "from positions_commands import positions_cmd, guard_button_callback\n"
        tb = tb[:insert_pos] + new_import + tb[insert_pos:]
        changes += 1
        print("  + Added positions_commands import")

    # Find existing /positions handler and replace
    old_pattern = r'app\.add_handler\(CommandHandler\(["\'"]positions["\'"],\s*\w+\)\)'
    old_handler = re.search(old_pattern, tb)
    if old_handler:
        tb = tb[:old_handler.start()] + 'app.add_handler(CommandHandler("positions", positions_cmd))' + tb[old_handler.end():]
        changes += 1
        print("  + Replaced existing /positions handler")
    else:
        # Add new handler
        last_handler = None
        for m in re.finditer(r"app\.add_handler\(CommandHandler\(.+?\)\)", tb):
            last_handler = m
        if last_handler:
            insert_pos = tb.index("\n", last_handler.end()) + 1
            handler = '    app.add_handler(CommandHandler("positions", positions_cmd))\n'
            tb = tb[:insert_pos] + handler + tb[insert_pos:]
            changes += 1
            print("  + Added /positions command handler")

    # Add CallbackQueryHandler import if missing
    if "CallbackQueryHandler" not in tb:
        old_ext_import = re.search(r"from telegram\.ext import (.+)", tb)
        if old_ext_import:
            old_line = old_ext_import.group(0)
            new_line = old_line.rstrip() + ", CallbackQueryHandler"
            tb = tb.replace(old_line, new_line, 1)
            print("  + Added CallbackQueryHandler to telegram.ext import")

    # Add guard button callback handler
    if "guard_button_callback" not in tb:
        last_handler = None
        for m in re.finditer(r"app\.add_handler\(.+?\)", tb):
            last_handler = m
        if last_handler:
            insert_pos = tb.index("\n", last_handler.end()) + 1
            cb_handler = '    app.add_handler(CallbackQueryHandler(guard_button_callback, pattern="^guard_"))\n'
            tb = tb[:insert_pos] + cb_handler + tb[insert_pos:]
            changes += 1
            print("  + Added guard button callback handler")

    with open(tb_path, "w") as f:
        f.write(tb)
    print(f"  telegram_bot.py: {changes} changes")
else:
    print("  telegram_bot.py: positions_commands already integrated")

print()
print("=" * 50)
print("Done! Restart trading-bot to apply.")
print("/positions now shows news risk + action buttons when guard is active")
