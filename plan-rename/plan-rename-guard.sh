#!/usr/bin/env bash
# Guard: re-injects custom-title if lost after compaction/clear
# Runs as Stop hook — fast early-exit in bash when no sidecar exists

INPUT=$(cat)

# Fast path in bash: extract session_id with jq, skip Python entirely if no sidecar
SESSION_ID=$(printf '%s\n' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
[[ -z "$SESSION_ID" ]] && exit 0
[[ "$SESSION_ID" =~ ^[a-zA-Z0-9_-]{8,128}$ ]] || exit 0

SIDECAR="$HOME/.claude/session-titles/${SESSION_ID}.json"
[[ -f "$SIDECAR" ]] || exit 0

# Sidecar exists — need Python for JSONL tail-scan + re-injection
python3 -c '
import json, sys, os, re, pathlib, collections

try:
    data = json.load(sys.stdin)

    session_id = data.get("session_id", "")
    if not session_id or not re.fullmatch(r"[a-zA-Z0-9_-]{8,128}", session_id):
        sys.exit(0)

    sidecar_path = pathlib.Path.home() / ".claude" / "session-titles" / f"{session_id}.json"
    if not sidecar_path.is_file():
        sys.exit(0)

    with open(sidecar_path) as f:
        sidecar = json.load(f)

    title = sidecar.get("title", "")
    if not title:
        sys.exit(0)

    # Validate transcript_path stays within ~/.claude/
    active_file = data.get("transcript_path", "")
    if not active_file:
        sys.exit(0)
    allowed_root = pathlib.Path.home() / ".claude"
    resolved = pathlib.Path(active_file).resolve()
    if not str(resolved).startswith(str(allowed_root.resolve())):
        sys.exit(0)
    if not resolved.is_file():
        sys.exit(0)

    # Tail-scan: only check last 50 lines (custom-title is always near the end)
    tail = collections.deque(maxlen=50)
    with open(resolved) as f:
        for line in f:
            tail.append(line)

    has_title = False
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if entry.get("type") == "custom-title":
                has_title = True
                break
        except json.JSONDecodeError:
            continue

    if has_title:
        sys.exit(0)

    # Re-inject custom-title
    entry = json.dumps({
        "type": "custom-title",
        "customTitle": title,
        "sessionId": session_id
    })

    with open(resolved, "a") as f:
        f.write(entry + "\n")

    print(f"[plan-rename-guard] Re-injected title: {title}", file=sys.stderr)
except Exception as e:
    print(f"[plan-rename-guard] Error: {e}", file=sys.stderr)
    sys.exit(0)
' <<< "$INPUT" >&2 || true
