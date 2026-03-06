#!/usr/bin/env bash
set -e

INPUT=$(cat)
echo "$INPUT"

echo "$INPUT" | python3 -c '
import json, sys, os

try:
    data = json.load(sys.stdin)
    fp = data.get("tool_input", {}).get("file_path", "")
    content = data.get("tool_input", {}).get("content", "")

    # Only process plan files
    if "/.claude/plans/" not in fp or not fp.endswith(".md"):
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

    if len(title) > 80:
        title = title[:77] + "..."

    # Get sessionId from hook stdin
    session_id = data.get("sessionId", "")
    if not session_id:
        sys.exit(0)

    cwd = os.getcwd()
    project_slug = cwd.replace("/", "-")
    project_dir = os.path.expanduser(f"~/.claude/projects/{project_slug}")
    active_file = os.path.join(project_dir, f"{session_id}.jsonl")

    if not os.path.isfile(active_file):
        sys.exit(0)

    # Append custom-title entry (same format as /rename)
    entry = json.dumps({
        "type": "custom-title",
        "customTitle": title,
        "sessionId": session_id
    })

    with open(active_file, "a") as f:
        f.write(entry + "\n")

    print(f"[plan-rename] Session renamed to: {title}", file=sys.stderr)
except Exception:
    sys.exit(0)
' >&2 || true
