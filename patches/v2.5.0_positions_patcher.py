import re
import os

print("v2.5.0 Phase 3: Enhanced /positions with News Risk + Buttons")
print("=" * 50)

BOT_DIR = os.path.join(os.getcwd(), "bot")
tb_path = os.path.join(BOT_DIR, "telegram_bot.py")

with open(tb_path) as f:
    tb = f.read()

changes = 0

# Check if positions_commands is already imported
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

    # Check if there's an existing inline positions function and remove/replace handler
    # Find existing positions CommandHandler and replace it
    old_handler = re.search(r'app\.add_handler\(CommandHandler\(["']positions["'],\s*\w+\)\)', tb)
    if old_handler:
        # Replace with new handler pointing to imported positions_cmd
        tb = tb[:old_handler.start()] + 'app.add_handler(CommandHandler("positions", positions_cmd))' + tb[old_handler.end():]
        changes += 1
        print("  + Replaced existing /positions handler with positions_commands.positions_cmd")
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

    # Add CallbackQueryHandler for guard buttons
    if "CallbackQueryHandler" not in tb:
        # Add import
        tb = tb.replace(
            "from telegram.ext import",
            "from telegram.ext import CallbackQueryHandler, "
        )
        print("  + Added CallbackQueryHandler import")

    if "guard_button_callback" not in tb:
        # Find last add_handler and add callback handler after it
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
