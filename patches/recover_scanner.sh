#!/bin/bash
set -e
cd /opt/trading-bot
echo "=== RECOVERY: Restore scanner.py + re-apply MTF ==="

# Step 1: Find a clean backup of scanner.py
BACKUP=$(ls -td backups/*/scanner.py 2>/dev/null | head -1)
if [ -z "$BACKUP" ]; then
    echo "No backup found! Looking in /tmp/fixrisk..."
    BACKUP=""
fi

if [ -n "$BACKUP" ]; then
    echo "Restoring from: $BACKUP"
    cp "$BACKUP" bot/scanner.py
else
    echo "No backup available - running install.sh from scratch"
    bash /tmp/fixrisk/install.sh
fi

# Step 2: Verify syntax after restore
echo ""
echo "Checking syntax..."
venv/bin/python3 -c "import py_compile; py_compile.compile('bot/scanner.py', doraise=True)" 2>&1
if [ $? -ne 0 ]; then
    echo "Backup is also broken. Running full install.sh..."
    bash /tmp/fixrisk/install.sh
fi

# Step 3: Verify MTF is present (import + check)
if grep -q "check_mtf_alignment" bot/scanner.py; then
    echo "MTF import: OK"
else
    echo "MTF import missing - will be added by patcher"
    cd /opt/trading-bot && venv/bin/python3 /tmp/fixrisk/patches/v2.4.0_mtf_patcher.py
fi

# Step 4: Re-apply MTF check if not present in signal flow
if grep -q "MTF Confluence Check" bot/scanner.py; then
    echo "MTF check block: OK"
else
    echo "Adding MTF check to signal flow..."
    venv/bin/python3 << 'MTFPATCH'
path = "bot/scanner.py"
with open(path) as f:
    code = f.read()

target = "                        # Save then notify with Execute buttons"
if target in code and "MTF Confluence Check" not in code:
    mtf = """
                        # -- MTF Confluence Check --
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

"""
    code = code.replace(target, mtf + target, 1)
    with open(path, "w") as f:
        f.write(code)
    print("  + MTF check inserted")
else:
    print("  = MTF check already present or target not found")
MTFPATCH
fi

# Step 5: Add MTF env vars if missing
if ! grep -q "MTF_REQUIRED" bot/scanner.py; then
    venv/bin/python3 -c "
path = 'bot/scanner.py'
with open(path) as f:
    code = f.read()
code = code.replace(
    'from mtf_confluence import check_mtf_alignment, clear_cache as clear_mtf_cache',
    'from mtf_confluence import check_mtf_alignment, clear_cache as clear_mtf_cache
'
    "MTF_REQUIRED = os.environ.get('MTF_REQUIRED', 'false').lower() == 'true'
"
    "MTF_BONUS = int(os.environ.get('MTF_BONUS_CONFLUENCE', '2'))"
)
with open(path, 'w') as f:
    f.write(code)
print('  + MTF env vars added')
"
fi

# Step 6: Register /risk in telegram_bot.py (NO scanner.py auto-trigger)
if ! grep -q "risk_report_commands" bot/telegram_bot.py; then
    venv/bin/python3 -c "
path = 'bot/telegram_bot.py'
with open(path) as f:
    code = f.read()
code = code.replace(
    'from mtf_commands import mtf_cmd',
    'from mtf_commands import mtf_cmd
from risk_report_commands import risk_cmd'
)
code = code.replace(
    'CommandHandler("mtf", mtf_cmd)',
    'CommandHandler("mtf", mtf_cmd))
    app.add_handler(CommandHandler("risk", risk_cmd)'
)
with open(path, 'w') as f:
    f.write(code)
print('  + Registered /risk command')
"
else
    echo "/risk already registered"
fi

# Step 7: Final syntax check
echo ""
echo "Final syntax verification..."
venv/bin/python3 -c "import py_compile; py_compile.compile('bot/scanner.py', doraise=True)"
if [ $? -eq 0 ]; then
    echo ""
    echo "============================================"
    echo "✅ ALL CLEAR! Restart bot:"
    echo "   sudo systemctl restart trading-bot"
    echo "============================================"
else
    echo "❌ scanner.py still has issues"
    echo "   Show: sed -n '100,115p' bot/scanner.py"
fi
