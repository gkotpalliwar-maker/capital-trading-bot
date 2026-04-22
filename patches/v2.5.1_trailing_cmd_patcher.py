import re
import os

print("v2.5.1: Trailing Stop Command Integration")
print("=" * 50)

BOT_DIR = os.path.join(os.getcwd(), "bot")
tb_path = os.path.join(BOT_DIR, "telegram_bot.py")

with open(tb_path) as f:
    tb = f.read()

changes = 0

if "trailing_commands" not in tb:
    # Add import
    last_import = None
    for m in re.finditer(r"from\s+\w+_commands\s+import", tb):
        last_import = m
    if last_import:
        insert_pos = tb.index("\n", last_import.end()) + 1
        new_import = "from trailing_commands import trailing_cmd\n"
        tb = tb[:insert_pos] + new_import + tb[insert_pos:]
        changes += 1
        print("  + Added trailing_commands import")

    # Add handler
    last_handler = None
    for m in re.finditer(r"app\.add_handler\(CommandHandler\(.+?\)\)", tb):
        last_handler = m
    if last_handler:
        insert_pos = tb.index("\n", last_handler.end()) + 1
        handler = '    app.add_handler(CommandHandler("trailing", trailing_cmd))\n'
        tb = tb[:insert_pos] + handler + tb[insert_pos:]
        changes += 1
        print("  + Added /trailing command handler")

    with open(tb_path, "w") as f:
        f.write(tb)
    print(f"  telegram_bot.py: {changes} changes")
else:
    print("  telegram_bot.py: trailing_commands already integrated")

print()
print("=" * 50)
print("Done! /trailing command registered.")
print("Commands: /trailing, /trailing on, /trailing off")
