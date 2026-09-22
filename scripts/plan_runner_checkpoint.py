"""Resumable checkpoint for plan_runner.py.

State (`<slug>.state.json`) records *where* every step stands. What a fresh
session needs to pick the work back up is shorter and more pointed: what is
done and what it produced, what is still open, what runs next, and whether
the last session stopped because it was stuck or because the environment
failed preflight. `complete` / `fail` / `skip` rewrite that summary to
`.plan-state/<slug>.checkpoint.json` after every transition, and
`next <plan> --resume` prints it.

build_checkpoint() is pure; only write_checkpoint_atomic() and
load_checkpoint() touch the filesystem.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

CHECKPOINT_SCHEMA_VERSION = 1

_COMPLETED = "completed"
_SKIPPED = "skipped"
_FAILED = "failed"


def checkpoint_path_for(state_dir: Path, slug: str) -> Path:
    return state_dir / f"{slug}.checkpoint.json"


def _completed_steps(steps: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": sid,
            "title": step.get("title"),
            "summary": step.get("summary"),
            "evidence": list(step.get("evidence") or []),
        }
        for sid, step in steps.items()
        if step.get("status") == _COMPLETED
    ]


def _open_questions(steps: dict[str, Any], stuck: dict[str, Any] | None) -> list[str]:
    questions = [
        f"{sid} failed: {step.get('failure_reason') or '(no reason given)'}"
        for sid, step in steps.items()
        if step.get("status") == _FAILED
    ]
    if stuck:
        questions.append(
            f"{stuck.get('step_id')} STUCK ({stuck.get('kind')}) since {stuck.get('stuck_at')}"
        )
    return questions


def build_checkpoint(
    state: dict[str, Any],
    *,
    ready_steps: list[str],
    stuck: dict[str, Any] | None,
    preflight: dict[str, Any] | None,
    now: str,
) -> dict[str, Any]:
    steps = state.get("steps") or {}
    completed = _completed_steps(steps)
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "slug": state.get("slug"),
        "title": state.get("title"),
        "updated_at": now,
        "completed_steps": completed,
        "skipped_steps": [s for s, v in steps.items() if v.get("status") == _SKIPPED],
        "artifacts": [item for step in completed for item in step["evidence"]],
        "open_questions": _open_questions(steps, stuck),
        "next_ready_step": ready_steps[0] if ready_steps else None,
        "stuck": stuck,
        "preflight": preflight,
    }


def write_checkpoint_atomic(path: Path, data: dict[str, Any]) -> None:
    """tmp file in the same directory, then os.replace — a reader never sees
    a half-written checkpoint, and a failed write leaves no tmp behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def load_checkpoint(path: Path) -> dict[str, Any] | None:
    """The checkpoint dict, or None when absent. A corrupt file raises
    ValueError so the caller can say so instead of treating it as absent."""
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"checkpoint is not a JSON object: {path}")
    return data


def _done_lines(checkpoint: dict[str, Any]) -> list[str]:
    lines = []
    for step in checkpoint.get("completed_steps") or []:
        summary = step.get("summary") or "(no summary)"
        lines.append(f"- {step.get('id')}: {summary}")
    return lines or ["- (none)"]


def format_resume_md(checkpoint: dict[str, Any], path: Path) -> str:
    lines = [
        f"# Resume: {checkpoint.get('title') or checkpoint.get('slug')}",
        f"checkpoint: {path} (updated {checkpoint.get('updated_at')})",
        "",
        "## Done",
        *_done_lines(checkpoint),
    ]
    artifacts = checkpoint.get("artifacts") or []
    if artifacts:
        lines += ["", "## Artifacts", *[f"- {item}" for item in artifacts]]
    questions = checkpoint.get("open_questions") or []
    if questions:
        lines += ["", "## Open questions", *[f"- {q}" for q in questions]]
    preflight = checkpoint.get("preflight")
    if isinstance(preflight, dict) and not preflight.get("ok", True):
        failed = ", ".join(preflight.get("failed") or [])
        lines += ["", f"preflight: FAIL ({failed})"]
    lines += ["", f"next (at checkpoint): {checkpoint.get('next_ready_step') or '(none)'}"]
    return "\n".join(lines)
