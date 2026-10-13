#!/bin/bash
# Warn (never block) when a Bash command looks like it reads season 2025 (the frozen holdout).
# CLAUDE.md non-negotiable 2: the holdout is never touched without a HOLDOUT_ACCESS_LOG row first.
cmd=$(jq -r '.tool_input.command // empty')
[ -z "$cmd" ] && exit 0
if printf '%s' "$cmd" | grep -qiE "season[^0-9<]{0,6}(=|==|>=|>)[[:space:]]*'?2025|season[[:space:]]+in[[:space:]]*\([^)]*2025|--season[= ]+2025|2025-26"; then
  msg="HOLDOUT WARNING: this command looks like it reads season 2025 (2025-26), the frozen holdout. Non-negotiable 2: a HOLDOUT_ACCESS_LOG row must be committed BEFORE any read. If this is not a holdout read (e.g. a guard test or a filter that excludes 2025), say so; otherwise stop, log the row, then run."
  jq -n --arg m "$msg" '{systemMessage: $m, hookSpecificOutput: {hookEventName: "PreToolUse", additionalContext: $m}}'
fi
exit 0
