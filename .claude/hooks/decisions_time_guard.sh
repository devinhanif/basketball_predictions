#!/bin/sh
# Blocks a write to docs/DECISIONS.md whose new table row carries a guessed time ("HH:Mx" / "HH:xx").
# Incident 2026-10-23: rows were stamped with guessed times although the shell-timestamp rule
# (retro e19939a) existed in text. Enforce it mechanically instead.
exec /usr/bin/python3 -c '
import json, re, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
ti = d.get("tool_input") or {}
target = " ".join(str(ti.get(k, "")) for k in ("command", "file_path"))
if "DECISIONS.md" not in target:
    sys.exit(0)
text = "\n".join(str(ti.get(k, "")) for k in ("command", "new_string", "content"))
if re.search(r"\|\s*20[0-9]{2}-[0-9]{2}-[0-9]{2} [0-9]{2}:([0-9]x|xx)\b", text):
    sys.stderr.write("DECISIONS rows need a real shell time: use T=$(date \"+%Y-%m-%d %H:%M\") "
                     "(rule: shell timestamps, retro e19939a; incident 2026-10-23 guessed stamps)\n")
    sys.exit(2)
sys.exit(0)
'
