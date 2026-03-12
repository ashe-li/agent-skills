#!/usr/bin/env bash
# Guard: confirms pending titles + re-injects custom-title after compaction
# Runs as Stop hook — fast early-exit in bash when no sidecar exists

INPUT=$(cat)

# Fast path in bash: extract session_id with jq, skip Python entirely if no sidecar
SESSION_ID=$(printf '%s\n' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
[[ -z "$SESSION_ID" ]] && exit 0
[[ "$SESSION_ID" =~ ^[a-zA-Z0-9_-]{8,128}$ ]] || exit 0

SIDECAR="$HOME/.claude/session-titles/${SESSION_ID}.json"
[[ -f "$SIDECAR" ]] || exit 0

# Sidecar exists — need Python for JSONL analysis
python3 -c '
import json, sys, os, re, pathlib, collections, tempfile

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

    pending = sidecar.get("pending", False)

    # Resolve transcript path — prefer sidecar (set by hook), fallback to stdin
    active_file = sidecar.get("transcriptPath", "") or data.get("transcript_path", "")
    if not active_file:
        sys.exit(0)
    allowed_root = pathlib.Path.home() / ".claude"
    resolved = pathlib.Path(active_file).resolve()
    if not str(resolved).startswith(str(allowed_root.resolve())):
        sys.exit(0)
    if not resolved.is_file():
        sys.exit(0)

    # Tail-scan: check last 50 lines
    tail = collections.deque(maxlen=50)
    with open(resolved) as f:
        for line in f:
            tail.append(line)
    tail_list = list(tail)

    if pending:
        # --- Pending confirmation flow ---
        # Check if the most recent ExitPlanMode was accepted or rejected.
        # Scan forward to find the LAST ExitPlanMode tool_use, then check
        # if the tool_result that follows it contains rejection text.

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

        if last_exit_idx < 0:
            # No ExitPlanMode in tail — might be compacted away.
            # If pending, the session continued past plan mode, so assume accepted.
            pass  # fall through to write
        else:
            # Check tool_result after the last ExitPlanMode
            rejected = False
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
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    if c.get("type") == "tool_result":
                        result_text = str(c.get("content", ""))
                        if "rejected" in result_text.lower():
                            rejected = True
                        break  # found first tool_result after ExitPlanMode
                if rejected or any(
                    isinstance(c, dict) and c.get("type") == "tool_result"
                    for c in content
                    if isinstance(c, dict)
                ):
                    break

            if rejected:
                # Plan was rejected — keep sidecar pending for next attempt
                print(f"[plan-rename-guard] ExitPlanMode rejected, skipping title write", file=sys.stderr)
                sys.exit(0)

        # Accepted (or no ExitPlanMode in tail) — write custom-title
        entry = json.dumps({
            "type": "custom-title",
            "customTitle": title,
            "sessionId": session_id
        })
        with open(resolved, "a") as f:
            f.write(entry + "\n")

        # Update sidecar: mark as confirmed (pending=false)
        sidecar_dir = sidecar_path.parent
        tmp_fd, tmp_path = tempfile.mkstemp(dir=sidecar_dir, suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w") as f:
                json.dump({"title": title, "sessionId": session_id, "pending": False}, f)
            os.chmod(tmp_path, 0o600)
            os.rename(tmp_path, sidecar_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        print(f"[plan-rename-guard] Confirmed title: {title}", file=sys.stderr)

    else:
        # --- Compaction re-injection flow ---
        # Sidecar is confirmed. Check if custom-title still exists in JSONL.
        has_title = False
        for line in tail_list:
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

        # Re-inject custom-title (lost to compaction)
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
