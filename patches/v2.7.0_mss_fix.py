
import os
import shutil
from datetime import datetime

print("=" * 60)
print("  v2.7.0: SL/TP Fix + MSS Confirmation Patcher")
print("=" * 60)

BOT_DIR = os.path.join(os.getcwd(), "bot")
STRATEGY_DIR = os.path.join(BOT_DIR, "strategies")
BACKUP_DIR = os.path.join(os.getcwd(), "backups", datetime.now().strftime("%Y%m%d_%H%M%S"))
os.makedirs(BACKUP_DIR, exist_ok=True)
os.makedirs(STRATEGY_DIR, exist_ok=True)

mss_path = os.path.join(STRATEGY_DIR, "mss_bos.py")

# Backup existing
if os.path.exists(mss_path):
    shutil.copy(mss_path, os.path.join(BACKUP_DIR, "mss_bos.py.bak"))
    print(f"  Backed up: {mss_path}")

# Copy new version from GitHub clone
src = os.path.join("/tmp", "v270", "bot", "strategies", "mss_bos.py")
if os.path.exists(src):
    shutil.copy(src, mss_path)
    print(f"  \u2705 Replaced mss_bos.py")
else:
    print(f"  \u274c Source not found: {src}")
    exit(1)

# Verify it compiles
try:
    with open(mss_path) as f:
        compile(f.read(), mss_path, "exec")
    print(f"  \u2705 mss_bos.py compiles!")
except SyntaxError as e:
    print(f"  \u274c Syntax error: line {e.lineno}: {e.msg}")
    exit(1)

print("\n" + "=" * 60)
print("  v2.7.0 deployment complete.")
print("  Restart bot: sudo systemctl restart trading-bot")
print("=" * 60)
