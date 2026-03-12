#!/usr/bin/env bash
# PreToolUse ExitPlanMode hook: rename session from plan H1 title

INPUT=$(cat)
printf '%s\n' "$INPUT"

printf '%s\n' "$INPUT" | python3 -c '
import json, sys, os, re, pathlib, tempfile, unicodedata

try:
    data = json.load(sys.stdin)
    tool_input = data.get("tool_input", {})

    content = tool_input.get("plan", "")
    if not content:
        sys.exit(0)

    # Extract H1 title
    title = ""
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("# "):
            title = line[2:].strip()
            break

    if not title:
        sys.exit(0)

    # Strip common prefixes
    for prefix in ["Plan:", "Implementation Plan:", "Plan："]:
        if title.startswith(prefix):
            title = title[len(prefix):].strip()
            break

    # Sanitize control characters
    title = "".join(c for c in title if unicodedata.category(c)[0] != "C")

    if not title:
        sys.exit(0)

    if len(title) > 80:
        title = title[:77] + "..."

    # Validate session_id
    session_id = data.get("session_id", "")
    if not session_id or not re.fullmatch(r"[a-zA-Z0-9_-]{8,128}", session_id):
        sys.exit(0)

    # Validate transcript_path stays within ~/.claude/
    active_file = data.get("transcript_path", "")
    if not active_file:
        sys.exit(0)
    allowed_root = pathlib.Path.home() / ".claude"
    resolved = pathlib.Path(active_file).resolve()
    if not str(resolved).startswith(str(allowed_root.resolve())):
        print(f"[plan-rename] Blocked path outside ~/.claude/: {active_file}", file=sys.stderr)
        sys.exit(0)
    if not resolved.is_file():
        sys.exit(0)

    # Write sidecar only (pending state).
    # custom-title is written by the Stop guard AFTER user accepts ExitPlanMode.
    # This avoids flashing titles on rejected plans.
    sidecar_dir = pathlib.Path.home() / ".claude" / "session-titles"
    sidecar_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    sidecar_path = sidecar_dir / f"{session_id}.json"

    # Atomic write: temp file + rename
    tmp_fd, tmp_path = tempfile.mkstemp(dir=sidecar_dir, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump({
                "title": title,
                "sessionId": session_id,
                "transcriptPath": str(resolved),
                "pending": True
            }, f)
        os.chmod(tmp_path, 0o600)
        os.rename(tmp_path, sidecar_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    print(f"[plan-rename] Pending title: {title}", file=sys.stderr)
except (KeyError, ValueError, json.JSONDecodeError):
    sys.exit(0)
except Exception as e:
    print(f"[plan-rename] Unexpected error: {e}", file=sys.stderr)
    sys.exit(0)
' >&2 || true
