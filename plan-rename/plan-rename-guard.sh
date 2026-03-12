#!/usr/bin/env bash
# Guard: confirms pending titles + re-injects custom-title after compaction
# Runs as Stop hook — fast early-exit in bash when no sidecar exists

INPUT=$(cat)

# Fast path in bash: extract session_id and transcript_path with jq
SESSION_ID=$(printf '%s\n' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
[[ -z "$SESSION_ID" ]] && exit 0
[[ "$SESSION_ID" =~ ^[a-zA-Z0-9_-]{8,128}$ ]] || exit 0

TRANSCRIPT=$(printf '%s\n' "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null)
SIDECAR_DIR="$HOME/.claude/session-titles"

# Check if there's a pending file OR a confirmed sidecar
# Pending file: _pending_<project-hash>.json (project-scoped, survives session_id change)
# Confirmed sidecar: <session_id>.json (session-scoped, for compaction re-injection)
HAS_PENDING=false
HAS_SIDECAR=false

if [[ -n "$TRANSCRIPT" && -d "$SIDECAR_DIR" ]]; then
    PROJECT_DIR=$(dirname "$TRANSCRIPT")
    PROJECT_HASH=$(printf '%s' "$PROJECT_DIR" | shasum -a 256 | cut -c1-16)
    PENDING_FILE="${SIDECAR_DIR}/_pending_${PROJECT_HASH}.json"
    [[ -f "$PENDING_FILE" ]] && HAS_PENDING=true
fi

SIDECAR="${SIDECAR_DIR}/${SESSION_ID}.json"
[[ -f "$SIDECAR" ]] && HAS_SIDECAR=true

# Fast exit if nothing to do
[[ "$HAS_PENDING" == "false" && "$HAS_SIDECAR" == "false" ]] && exit 0

# Need Python for JSONL analysis
python3 -c '
import json, sys, os, re, pathlib, collections, tempfile, hashlib

try:
    data = json.load(sys.stdin)

    session_id = data.get("session_id", "")
    if not session_id or not re.fullmatch(r"[a-zA-Z0-9_-]{8,128}", session_id):
        sys.exit(0)

    # Validate transcript_path
    active_file = data.get("transcript_path", "")
    if not active_file:
        sys.exit(0)
    allowed_root = pathlib.Path.home() / ".claude"
    resolved = pathlib.Path(active_file).resolve()
    if not str(resolved).startswith(str(allowed_root.resolve())):
        sys.exit(0)
    if not resolved.is_file():
        sys.exit(0)

    sidecar_dir = pathlib.Path.home() / ".claude" / "session-titles"
    project_dir = str(resolved.parent)
    project_key = hashlib.sha256(project_dir.encode()).hexdigest()[:16]
    pending_path = sidecar_dir / f"_pending_{project_key}.json"
    sidecar_path = sidecar_dir / f"{session_id}.json"

    # --- Phase 1: Check project-scoped pending file ---
    if pending_path.is_file():
        with open(pending_path) as f:
            pending = json.load(f)

        title = pending.get("title", "")
        if title:
            # Tail-scan JSONL for ExitPlanMode rejection
            tail = collections.deque(maxlen=50)
            with open(resolved) as f:
                for line in f:
                    tail.append(line)
            tail_list = list(tail)

            # Find last ExitPlanMode tool_use in tail
            last_exit_idx = -1
            for i, line in enumerate(tail_list):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                content = entry.get("message", {}).get("content", [])
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("name") == "ExitPlanMode":
                            last_exit_idx = i

            # Check if ExitPlanMode was rejected
            rejected = False
            if last_exit_idx >= 0:
                for i in range(last_exit_idx + 1, len(tail_list)):
                    line = tail_list[i].strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    content = entry.get("message", {}).get("content", [])
                    if not isinstance(content, list):
                        continue
                    found_result = False
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "tool_result":
                            if "rejected" in str(c.get("content", "")).lower():
                                rejected = True
                            found_result = True
                            break
                    if found_result:
                        break

            if rejected:
                print(f"[plan-rename-guard] ExitPlanMode rejected, skipping", file=sys.stderr)
                sys.exit(0)

            # Accepted (or no ExitPlanMode in tail = context was cleared after accept)
            # Write custom-title to CURRENT transcript
            ct_entry = json.dumps({
                "type": "custom-title",
                "customTitle": title,
                "sessionId": session_id
            })
            with open(resolved, "a") as f:
                f.write(ct_entry + "\n")

            # Create confirmed session-scoped sidecar (for compaction re-injection)
            tmp_fd, tmp_path = tempfile.mkstemp(dir=sidecar_dir, suffix=".tmp")
            try:
                with os.fdopen(tmp_fd, "w") as f:
                    json.dump({"title": title, "sessionId": session_id}, f)
                os.chmod(tmp_path, 0o600)
                os.rename(tmp_path, sidecar_path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

            # Remove pending file
            try:
                pending_path.unlink()
            except OSError:
                pass

            print(f"[plan-rename-guard] Confirmed title: {title}", file=sys.stderr)
            sys.exit(0)

    # --- Phase 2: Check session-scoped sidecar (compaction re-injection) ---
    if sidecar_path.is_file():
        with open(sidecar_path) as f:
            sidecar = json.load(f)

        title = sidecar.get("title", "")
        if not title:
            sys.exit(0)

        # Tail-scan for existing custom-title
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
                    sys.exit(0)  # Already has title
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

        print(f"[plan-rename-guard] Re-injected title: {title}", file=sys.stderr)

except Exception as e:
    print(f"[plan-rename-guard] Error: {e}", file=sys.stderr)
    sys.exit(0)
' <<< "$INPUT" >&2 || true
