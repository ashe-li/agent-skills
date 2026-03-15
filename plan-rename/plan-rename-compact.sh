#!/usr/bin/env bash
# SessionStart compact hook: re-inject custom-title after compaction
# Runs ONLY on compaction (matcher: "compact"), not every response

INPUT=$(cat)

# Extract session_id and transcript_path without jq (handle optional whitespace around colon)
SESSION_ID=$(printf '%s\n' "$INPUT" | grep -oE '"session_id"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | cut -d'"' -f4)
[[ -z "$SESSION_ID" ]] && exit 0
[[ "$SESSION_ID" =~ ^[a-zA-Z0-9_-]{8,128}$ ]] || exit 0

TRANSCRIPT=$(printf '%s\n' "$INPUT" | grep -oE '"transcript_path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | cut -d'"' -f4)
[[ -z "$TRANSCRIPT" ]] && exit 0

SIDECAR_DIR="$HOME/.claude/session-titles"
SIDECAR="${SIDECAR_DIR}/${SESSION_ID}.json"

# No confirmed sidecar → nothing to re-inject
[[ -f "$SIDECAR" ]] || exit 0

python3 -c '
import json, sys, os, re, pathlib, collections

try:
    data = json.load(sys.stdin)

    session_id = data.get("session_id", "")
    if not session_id or not re.fullmatch(r"[a-zA-Z0-9_-]{8,128}", session_id):
        sys.exit(0)

    active_file = data.get("transcript_path", "")
    if not active_file:
        sys.exit(0)
    allowed_root = pathlib.Path.home() / ".claude"
    resolved = pathlib.Path(active_file).resolve()
    try:
        resolved.relative_to(allowed_root.resolve())
    except ValueError:
        sys.exit(0)
    if not resolved.is_file():
        sys.exit(0)

    sidecar_dir = pathlib.Path.home() / ".claude" / "session-titles"
    sidecar_path = sidecar_dir / f"{session_id}.json"

    if not sidecar_path.is_file():
        sys.exit(0)

    with open(sidecar_path) as f:
        sidecar = json.load(f)

    title = sidecar.get("title", "")
    if not title:
        sys.exit(0)

    # Tail-scan JSONL for existing custom-title
    tail = collections.deque(maxlen=50)
    with open(resolved) as f:
        for line in f:
            tail.append(line)

    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if entry.get("type") == "custom-title":
                sys.exit(0)  # Already has title, no action needed
        except json.JSONDecodeError:
            continue

    # Re-inject custom-title (lost to compaction)
    ct_entry = json.dumps({
        "type": "custom-title",
        "customTitle": title,
        "sessionId": session_id
    })
    with open(resolved, "a") as f:
        f.write(ct_entry + "\n")

    print(f"[plan-rename-compact] Re-injected title: {title}", file=sys.stderr)

    # Cleanup: remove sidecars older than 30 days (skip _pending_* and current session)
    import time
    now = time.time()
    cutoff = now - 30 * 86400
    for entry in sidecar_dir.iterdir():
        if not entry.is_file():
            continue
        if entry.name.startswith("_pending_"):
            continue
        if entry == sidecar_path:
            continue
        if entry.suffix == ".json" and entry.stat().st_mtime < cutoff:
            try:
                entry.unlink()
            except OSError:
                pass

except Exception as e:
    print(f"[plan-rename-compact] Error: {e}", file=sys.stderr)
    sys.exit(0)
' <<< "$INPUT" >&2 || true
