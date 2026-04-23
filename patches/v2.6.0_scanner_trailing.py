
import os
import re

print("\nv2.6.0: Scanner Bot-Trailing Integration Patcher")
print("=" * 55)

scan_path = os.path.join(os.getcwd(), "bot", "scanner.py")
if not os.path.exists(scan_path):
    print(f"  ERROR: {scan_path} not found")
    exit(1)

with open(scan_path) as f:
    code = f.read()
orig = code
changes = []

# ── 1. Add import for bot_trailing if not present ──
if "from bot_trailing import" not in code and "import bot_trailing" not in code:
    # Find a good import location (after other bot imports)
    import_marker = re.search(r"(from \w+ import [^\n]+\n)+", code)
    if import_marker:
        insert_pos = import_marker.end()
        import_line = "\ntry:\n    from bot_trailing import TrailingManager\n    HAS_TRAILING = True\nexcept ImportError:\n    HAS_TRAILING = False\n\n"
        code = code[:insert_pos] + import_line + code[insert_pos:]
        changes.append("Added bot_trailing import")

# ── 2. Initialize TrailingManager after client is set ──
if "trailing_manager" not in code.lower() and "TrailingManager" in code:
    # Find where client is initialized (usually after login)
    client_init = re.search(r"(client\s*=\s*[^\n]+\n)", code)
    if client_init:
        insert_pos = client_init.end()
        init_line = "\n# Bot-side trailing manager\ntrailing_manager = TrailingManager(client) if HAS_TRAILING else None\n"
        code = code[:insert_pos] + init_line + code[insert_pos:]
        changes.append("Added TrailingManager initialization")

# ── 3. Add trailing update call in the scan loop ──
# Find the main scan loop (typically while True: ... or for ... in scan...)
if "trailing_manager" in code and ".update_all()" not in code:
    # Look for position sync or trade management section
    sync_marker = re.search(r"(sync_positions_with_db\([^)]*\))", code)
    if sync_marker:
        insert_pos = sync_marker.end()
        update_call = "\n\n        # Bot-side trailing stop updates\n        if trailing_manager:\n            try:\n                updates = trailing_manager.update_all()\n                if updates:\n                    logger.info(f\"Trailing updates: {len(updates)}\" )\n            except Exception as e:\n                logger.warning(f\"Trailing update error: {e}\")\n"
        code = code[:insert_pos] + update_call + code[insert_pos:]
        changes.append("Added trailing_manager.update_all() in scan loop")

if code != orig:
    with open(scan_path, 'w') as f:
        f.write(code)
    print(f"  ✅ scanner.py patched ({len(changes)} changes)")
    for c in changes:
        print(f"     - {c}")
else:
    print("  ⏭️  No scanner changes needed (or already patched)")

print("\n" + "=" * 55)
print("v2.6.0 scanner.py patch complete.")
