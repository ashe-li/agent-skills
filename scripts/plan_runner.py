#!/usr/bin/env python3
"""Deterministic plan runner — parse plan.md, drive DAG, output next steps.

Used by the /plan-run skill. The LLM calls this CLI between actions to know
what to do next. DAG progression is enforced in code (not LLM judgment) so
behavior is reproducible across sessions and resilient to context loss.

State file: <plan-dir>/.plan-state/<slug>.state.json

Usage:
    plan_runner.py init plans/active/foo.md
    plan_runner.py next plans/active/foo.md
    plan_runner.py start plans/active/foo.md S0.1 --task-id=tsk_abc
    plan_runner.py complete plans/active/foo.md S0.1
    plan_runner.py fail plans/active/foo.md S0.1 --reason="..."
    plan_runner.py skip plans/active/foo.md S0.2
    plan_runner.py status plans/active/foo.md
    plan_runner.py reset plans/active/foo.md --step=S0.1
    plan_runner.py set-parent plans/active/foo.md --task-id=tsk_parent
    plan_runner.py dag plans/active/foo.md [--format=dot]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STEP_ID_PATTERN = r"S\d+(?:\.\d+)?[a-z]?"

PENDING = "pending"
IN_PROGRESS = "in_progress"
COMPLETED = "completed"
FAILED = "failed"
BLOCKED = "blocked"
SKIPPED = "skipped"

VALID_TRANSITIONS: dict[str, set[str]] = {
    PENDING: {IN_PROGRESS, BLOCKED, SKIPPED},
    BLOCKED: {PENDING, SKIPPED},
    IN_PROGRESS: {COMPLETED, FAILED},
    FAILED: {PENDING, IN_PROGRESS, SKIPPED},
    COMPLETED: {COMPLETED},
    SKIPPED: {PENDING},
}

FIELD_KEYS = (
    "Files", "Action", "Agent", "Skill", "Command",
    "Agent/Skill", "Dependencies", "Risk", "Why", "Input", "Output",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Plan parsing
# ---------------------------------------------------------------------------

def parse_plan(plan_path: Path) -> dict[str, Any]:
    """Parse plan.md into step graph."""
    text = plan_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    title = ""
    for line in lines:
        if line.startswith("# "):
            title = line[2:].strip()
            break

    steps: dict[str, dict[str, Any]] = {}
    phase_order: list[str] = []
    current_phase = ""
    current_step_id: str | None = None
    current_action_lines: list[str] = []
    parse_warnings: list[str] = []

    phase_re = re.compile(r"^###\s+(.+)$")
    step_re = re.compile(
        rf"^-\s+\[[ x]\]\s+\*\*({STEP_ID_PATTERN})\*\*\s*[—\-:：]?\s*(.*)$"
    )
    field_re = re.compile(
        rf"^\s+-\s+(?P<key>{'|'.join(FIELD_KEYS)})\s*[:：]\s*(?P<val>.*)$",
        re.IGNORECASE,
    )

    def flush_action() -> None:
        nonlocal current_action_lines
        if current_step_id and current_action_lines:
            joined = " ".join(s.strip() for s in current_action_lines).strip()
            existing = steps[current_step_id].get("action") or ""
            steps[current_step_id]["action"] = (
                (existing + " " + joined).strip() if existing else joined
            )
        current_action_lines = []

    in_action_block = False

    for raw in lines:
        m_phase = phase_re.match(raw)
        if m_phase:
            flush_action()
            in_action_block = False
            phase_name = m_phase.group(1).strip()
            if "Phase" in phase_name or "phase" in phase_name:
                current_phase = phase_name
                if phase_name not in phase_order:
                    phase_order.append(phase_name)
            continue

        m_step = step_re.match(raw)
        if m_step:
            flush_action()
            in_action_block = False
            step_id = m_step.group(1)
            step_title = m_step.group(2).strip()
            if step_id in steps:
                parse_warnings.append(f"Duplicate step id: {step_id}")
            steps[step_id] = {
                "id": step_id,
                "title": step_title,
                "phase": current_phase,
                "deps": [],
                "files": None,
                "action": None,
                "agent": None,
                "skill": None,
                "command": None,
                "risk": None,
            }
            current_step_id = step_id
            continue

        if current_step_id:
            m_field = field_re.match(raw)
            if m_field:
                key = m_field.group("key").lower()
                val = m_field.group("val").strip()
                if key == "dependencies":
                    deps = re.findall(rf"{STEP_ID_PATTERN}", val)
                    steps[current_step_id]["deps"] = deps
                    if "~" in val or "..." in val:
                        parse_warnings.append(
                            f"{current_step_id}: range syntax in deps "
                            f"({val!r}) — only explicit IDs extracted: {deps}"
                        )
                    in_action_block = False
                elif key == "files":
                    steps[current_step_id]["files"] = val
                    in_action_block = False
                elif key == "action":
                    steps[current_step_id]["action"] = val
                    current_action_lines = []
                    in_action_block = True
                elif key in ("agent", "agent/skill"):
                    steps[current_step_id]["agent"] = val.strip("`")
                    in_action_block = False
                elif key == "skill":
                    steps[current_step_id]["skill"] = val.strip("`")
                    in_action_block = False
                elif key == "command":
                    steps[current_step_id]["command"] = val.strip("`")
                    in_action_block = False
                elif key == "risk":
                    steps[current_step_id]["risk"] = val
                    in_action_block = False
                continue

            if in_action_block and raw.startswith("    "):
                current_action_lines.append(raw)
                continue

            if raw.startswith("##"):
                flush_action()
                in_action_block = False
                current_step_id = None

    flush_action()

    return {
        "slug": plan_path.stem,
        "title": title,
        "steps": steps,
        "phase_order": phase_order,
        "warnings": parse_warnings,
    }


def validate_dag(parsed: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    steps = parsed["steps"]

    if not steps:
        errors.append("No steps found in plan")
        return errors

    for sid, step in steps.items():
        for dep in step["deps"]:
            if dep not in steps:
                errors.append(f"Step {sid} depends on unknown step {dep}")

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {sid: WHITE for sid in steps}

    def dfs(node: str, path: list[str]) -> bool:
        color[node] = GRAY
        for dep in steps[node]["deps"]:
            if dep not in steps:
                continue
            if color[dep] == GRAY:
                cycle = " -> ".join(path + [node, dep])
                errors.append(f"Cycle detected: {cycle}")
                return True
            if color[dep] == WHITE:
                if dfs(dep, path + [node]):
                    return True
        color[node] = BLACK
        return False

    for sid in steps:
        if color[sid] == WHITE:
            dfs(sid, [])

    return errors


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def state_dir_for(plan_path: Path) -> Path:
    return plan_path.parent / ".plan-state"


def state_path_for(plan_path: Path) -> Path:
    return state_dir_for(plan_path) / f"{plan_path.stem}.state.json"


def load_state(plan_path: Path) -> dict[str, Any] | None:
    sp = state_path_for(plan_path)
    if not sp.exists():
        return None
    return json.loads(sp.read_text(encoding="utf-8"))


def save_state(plan_path: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = now_iso()
    state_dir_for(plan_path).mkdir(parents=True, exist_ok=True)
    state_path_for(plan_path).write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def init_state(plan_path: Path, parsed: dict[str, Any]) -> dict[str, Any]:
    steps_state = {}
    for sid, step in parsed["steps"].items():
        steps_state[sid] = {
            "id": sid,
            "title": step["title"],
            "phase": step["phase"],
            "deps": step["deps"],
            "agent": step["agent"],
            "skill": step["skill"],
            "command": step["command"],
            "files": step["files"],
            "action": step["action"],
            "risk": step["risk"],
            "status": PENDING,
            "task_id": None,
            "started_at": None,
            "completed_at": None,
            "failure_reason": None,
        }

    return {
        "plan_path": str(plan_path),
        "slug": parsed["slug"],
        "title": parsed["title"],
        "phase_order": parsed["phase_order"],
        "parent_task_id": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "steps": steps_state,
    }


# ---------------------------------------------------------------------------
# DAG operations
# ---------------------------------------------------------------------------

def deps_all_completed(state: dict[str, Any], step_id: str) -> bool:
    deps = state["steps"][step_id]["deps"]
    for d in deps:
        if d not in state["steps"]:
            continue
        if state["steps"][d]["status"] not in (COMPLETED, SKIPPED):
            return False
    return True


def any_dep_failed(state: dict[str, Any], step_id: str) -> bool:
    return any(
        state["steps"][d]["status"] == FAILED
        for d in state["steps"][step_id]["deps"]
        if d in state["steps"]
    )


def compute_ready_steps(state: dict[str, Any]) -> list[str]:
    return sorted(
        sid for sid, s in state["steps"].items()
        if s["status"] == PENDING and deps_all_completed(state, sid)
    )


def compute_blocked_steps(state: dict[str, Any]) -> list[str]:
    return sorted(
        sid for sid, s in state["steps"].items()
        if s["status"] not in (COMPLETED, SKIPPED) and any_dep_failed(state, sid)
    )


def recompute_blocked_status(state: dict[str, Any]) -> None:
    for sid, step in state["steps"].items():
        if step["status"] == PENDING and any_dep_failed(state, sid):
            step["status"] = BLOCKED
        elif step["status"] == BLOCKED and not any_dep_failed(state, sid):
            step["status"] = PENDING


def transition_step(
    state: dict[str, Any],
    step_id: str,
    new_status: str,
    **kwargs: Any,
) -> None:
    step = state["steps"][step_id]
    current = step["status"]
    if new_status not in VALID_TRANSITIONS.get(current, set()):
        raise ValueError(
            f"Invalid transition for {step_id}: {current} -> {new_status}. "
            f"Allowed from {current}: {sorted(VALID_TRANSITIONS.get(current, set()))}"
        )
    step["status"] = new_status
    if new_status == IN_PROGRESS:
        step["started_at"] = now_iso()
        if kwargs.get("task_id"):
            step["task_id"] = kwargs["task_id"]
    elif new_status == COMPLETED:
        step["completed_at"] = now_iso()
    elif new_status == FAILED:
        step["completed_at"] = now_iso()
        step["failure_reason"] = kwargs.get("reason", "")
    recompute_blocked_status(state)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def step_to_instruction(state: dict[str, Any], step_id: str) -> dict[str, Any]:
    step = state["steps"][step_id]
    dep_task_ids = [
        state["steps"][d]["task_id"]
        for d in step["deps"]
        if d in state["steps"] and state["steps"][d].get("task_id")
    ]
    title_short = step["title"][:60] + ("..." if len(step["title"]) > 60 else "")
    return {
        "id": step_id,
        "title": step["title"],
        "phase": step["phase"],
        "agent": step["agent"],
        "skill": step["skill"],
        "command": step["command"],
        "files": step["files"],
        "action": step["action"],
        "risk": step["risk"],
        "deps": step["deps"],
        "dep_task_ids": dep_task_ids,
        "task_create": {
            "subject": f"{step_id}: {step['title']}",
            "activeForm": f"{step_id} {title_short} 處理中",
            "addBlockedBy": dep_task_ids,
        },
    }


def summary(state: dict[str, Any]) -> dict[str, Any]:
    counts = {PENDING: 0, IN_PROGRESS: 0, COMPLETED: 0, FAILED: 0, BLOCKED: 0, SKIPPED: 0}
    for s in state["steps"].values():
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    total = len(state["steps"])
    done = counts[COMPLETED] + counts[SKIPPED]
    return {
        "total": total,
        "by_status": counts,
        "progress": f"{done}/{total}",
        "all_done": done == total,
    }


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# LLM-optimized markdown formatters
# ---------------------------------------------------------------------------

def _format_addblocked(ids: list[str]) -> str:
    return "[" + ",".join(ids) + "]" if ids else "[]"


def _format_step_action_block(step: dict[str, Any]) -> list[str]:
    """Build the executable action sequence for one ready step."""
    lines: list[str] = []
    tc = step["task_create"]
    lines.append(f"  1. TaskCreate(subject={tc['subject']!r}, "
                 f"activeForm={tc['activeForm']!r}, "
                 f"addBlockedBy={_format_addblocked(tc['addBlockedBy'])}) -> save task_id")
    lines.append(f"  2. plan_runner.py start <plan> {step['id']} --task-id=<task_id>")
    sid = step["id"]
    if step.get("agent"):
        lines.append(f"  3. Agent(subagent_type={step['agent']!r}, prompt=<files + action below>)")
    elif step.get("command"):
        lines.append(f"  3. Execute command {step['command']}")
    elif step.get("skill"):
        lines.append(f"  3. Apply skill {step['skill']}")
    else:
        lines.append(f"  3. (no agent/command/skill specified — manual execution per Action)")
    lines.append(f"  4. ok: plan_runner.py complete <plan> {sid} + TaskUpdate(task_id, completed)")
    lines.append(f"     err: plan_runner.py fail <plan> {sid} --reason=<msg> + TaskUpdate(task_id, failed)")
    return lines


def _format_full_step_block(step: dict[str, Any]) -> list[str]:
    """Full ready-step block: header + fields + next action sequence."""
    lines: list[str] = []
    title = step["title"]
    phase = step["phase"]
    phase_tag = f" [{phase}]" if phase else ""
    lines.append(f"### {step['id']} — {title}{phase_tag}")
    for k in ("agent", "skill", "command"):
        if step.get(k):
            lines.append(f"- {k}: {step[k]}")
    if step.get("files"):
        lines.append(f"- files: {step['files']}")
    if step.get("action"):
        lines.append(f"- action: {step['action']}")
    deps = step["deps"]
    if deps:
        dep_ids = ",".join(deps)
        dtids = step.get("dep_task_ids") or []
        tids = ",".join(dtids) if dtids else "no task_ids"
        lines.append(f"- deps: {dep_ids} (task_ids: {tids})")
    if step.get("risk"):
        lines.append(f"- risk: {step['risk']}")
    lines.append("- next:")
    lines.extend(_format_step_action_block(step))
    return lines


def _format_state_view_lines(data: dict[str, Any]) -> list[str]:
    """Markdown rendering. Skips empty sections to save tokens.
    Ready-steps split into 'new' (full block) and 'still' (IDs only)."""
    s = data["summary"]
    lines: list[str] = []
    progress = s["progress"]
    lines.append(f"Progress: {progress}" + (" — ALL DONE" if s["all_done"] else ""))
    parent = data.get("parent_task_id")
    if parent:
        lines.append(f"Parent task: {parent}")
    counts = s["by_status"]
    counts_str = " | ".join(f"{k}:{v}" for k, v in counts.items() if v)
    if counts_str:
        lines.append(counts_str)

    new_ready = data.get("ready_steps_new", [])
    still_ready = data.get("ready_steps_still", [])

    if new_ready:
        lines.append("")
        lines.append(f"## Newly unlocked ({len(new_ready)})")
        for step in new_ready:
            lines.append("")
            lines.extend(_format_full_step_block(step))

    if still_ready:
        lines.append("")
        lines.append(f"## Still ready ({len(still_ready)}): {', '.join(still_ready)}")
        lines.append("(instructions already shown; call `next` to re-bootstrap)")

    ip = data.get("in_progress_steps", [])
    if ip:
        lines.append("")
        lines.append(f"## In progress ({len(ip)})")
        for s_ in ip:
            lines.append(f"- {s_['id']} {s_['title']} (task: {s_['task_id']})")

    blocked = data.get("blocked_steps", [])
    if blocked:
        lines.append("")
        lines.append(f"## Blocked ({len(blocked)})")
        for s_ in blocked:
            failed = ",".join(s_["failed_deps"])
            lines.append(f"- {s_['id']} {s_['title']} (failed deps: {failed})")

    if not (new_ready or still_ready or ip or blocked):
        lines.append("")
        lines.append("(no ready / in_progress / blocked steps)")

    return lines


def format_next_md(data: dict[str, Any]) -> str:
    lines = ["# Plan state (full bootstrap)"] + _format_state_view_lines(data)
    return "\n".join(lines)


def format_index_md(data: dict[str, Any]) -> str:
    """Ultra-compact trace view — ID + status only."""
    s = data["summary"]
    lines = [
        f"# {data['title']}",
        f"Progress: {s['progress']}" + (" — ALL DONE" if s["all_done"] else ""),
    ]
    counts = s["by_status"]
    counts_str = " | ".join(f"{k}:{v}" for k, v in counts.items() if v)
    if counts_str:
        lines.append(counts_str)
    lines.append("")
    icon = {
        COMPLETED: "x", IN_PROGRESS: ">", FAILED: "!",
        BLOCKED: "B", SKIPPED: "-", PENDING: " ",
    }
    current_phase = None
    for step in data["steps"]:
        phase = step["phase"]
        if phase != current_phase:
            phase_short = phase.split("：")[0].split(":")[0] if phase else ""
            lines.append(f"\n[{phase_short}]" if phase else "")
            current_phase = phase
        deps = step["deps"]
        dep_str = f" <- {','.join(deps)}" if deps else ""
        lines.append(f"{icon[step['status']]} {step['id']}{dep_str}")
    return "\n".join(lines)


def format_status_md(data: dict[str, Any]) -> str:
    s = data["summary"]
    lines: list[str] = [f"# {data['title']}"]
    lines.append(f"Progress: {s['progress']}"
                 + (" — ALL DONE" if s["all_done"] else ""))
    parent = data.get("parent_task_id")
    if parent:
        lines.append(f"Parent task: {parent}")
    counts = s["by_status"]
    counts_str = " | ".join(f"{k}:{v}" for k, v in counts.items() if v)
    if counts_str:
        lines.append(counts_str)
    lines.append("")
    icon = {
        COMPLETED: "[x]", IN_PROGRESS: "[>]", FAILED: "[!]",
        BLOCKED: "[B]", SKIPPED: "[-]", PENDING: "[ ]",
    }
    current_phase = None
    for step in data["steps"]:
        phase = step["phase"]
        if phase != current_phase:
            lines.append(f"\n## {phase}" if phase else "")
            current_phase = phase
        deps = f"  <- {','.join(step['deps'])}" if step["deps"] else ""
        tid = f"  task:{step['task_id']}" if step.get("task_id") else ""
        fr = f"  reason:{step['failure_reason']}" if step.get("failure_reason") else ""
        lines.append(f"{icon[step['status']]} {step['id']} {step['title']}{deps}{tid}{fr}")
    return "\n".join(lines)


def format_init_md(data: dict[str, Any]) -> str:
    lines = [
        f"# Initialized: {data['title']}",
        f"State: {data['state_path']}",
        f"Steps: {data['total_steps']} across {len(data['phase_order'])} phases",
        "",
        "Phases:",
    ]
    for ph in data["phase_order"]:
        lines.append(f"  - {ph}")
    lines.append("")
    lines.append(f"Ready now: {', '.join(data['ready_steps']) or '(none)'}")
    if data.get("warnings"):
        lines.append("")
        lines.append("Warnings:")
        for w in data["warnings"]:
            lines.append(f"  - {w}")
    return "\n".join(lines)


def format_transition_md(verb: str, data: dict[str, Any]) -> str:
    """Formatter for start/complete/fail/skip. Embeds full state view so caller
    can skip the next `next` call—data needed to drive next iteration is here."""
    lines = [f"# {verb}: {data.get('step', '?')}"]
    if data.get("task_id"):
        lines.append(f"Task: {data['task_id']}")
    if data.get("reason"):
        lines.append(f"Reason: {data['reason']}")
    if "ready_steps" in data or "summary" in data:
        lines.append("")
        lines.extend(_format_state_view_lines(data))
    return "\n".join(lines)


def emit_formatted(data: dict[str, Any], fmt: str, md_func) -> None:
    if fmt == "json":
        emit(data)
    else:
        print(md_func(data))


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    plan_path = Path(args.plan).resolve()
    if not plan_path.exists():
        emit({"error": f"Plan not found: {plan_path}"})
        return 1
    parsed = parse_plan(plan_path)
    errors = validate_dag(parsed)
    if errors:
        emit({"error": "DAG validation failed", "details": errors, "warnings": parsed["warnings"]})
        return 1
    existing = load_state(plan_path)
    if existing and not args.force:
        emit({
            "error": "State already exists. Use --force to reinit.",
            "state_path": str(state_path_for(plan_path)),
        })
        return 1
    state = init_state(plan_path, parsed)
    save_state(plan_path, state)
    payload = {
        "status": "initialized",
        "slug": state["slug"],
        "title": state["title"],
        "state_path": str(state_path_for(plan_path)),
        "total_steps": len(state["steps"]),
        "phase_order": state["phase_order"],
        "ready_steps": compute_ready_steps(state),
        "warnings": parsed["warnings"],
    }
    emit_formatted(payload, args.format, format_init_md)
    return 0


def _require_state(plan_path: Path) -> dict[str, Any]:
    state = load_state(plan_path)
    if not state:
        emit({"error": "No state. Run `init` first."})
        sys.exit(1)
    return state


def _build_state_view(state: dict[str, Any], mode: str = "delta") -> dict[str, Any]:
    """Shared state-view payload — embed in transition outputs so callers
    don't need a follow-up `next` call.

    mode="delta" (default for transitions): only emit full instruction blocks
        for *newly* unlocked ready steps; previously-shown ready steps
        appear as IDs only. Saves tokens in parallel waves.
    mode="full" (used by `next`): emit full instructions for ALL ready
        steps. Use for session resume / bootstrap.

    Side effect: updates `state["previously_reported_ready"]` to current
    ready set so the next call's delta is computed correctly.
    """
    current_ready = compute_ready_steps(state)
    in_progress = sorted(
        sid for sid, s in state["steps"].items() if s["status"] == IN_PROGRESS
    )
    blocked = compute_blocked_steps(state)

    if mode == "full":
        prev_reported: set[str] = set()
    else:
        prev_reported = set(state.get("previously_reported_ready", []))

    newly = [sid for sid in current_ready if sid not in prev_reported]
    still = [sid for sid in current_ready if sid in prev_reported]

    # Update tracker so next call diffs correctly
    state["previously_reported_ready"] = current_ready

    return {
        "summary": summary(state),
        "parent_task_id": state.get("parent_task_id"),
        "ready_steps_new": [step_to_instruction(state, sid) for sid in newly],
        "ready_steps_still": still,  # IDs only — Claude already saw these
        "in_progress_steps": [
            {
                "id": sid,
                "title": state["steps"][sid]["title"],
                "task_id": state["steps"][sid]["task_id"],
            }
            for sid in in_progress
        ],
        "blocked_steps": [
            {
                "id": sid,
                "title": state["steps"][sid]["title"],
                "failed_deps": [
                    d for d in state["steps"][sid]["deps"]
                    if state["steps"].get(d, {}).get("status") == FAILED
                ],
            }
            for sid in blocked
        ],
    }


def cmd_next(args: argparse.Namespace) -> int:
    plan_path = Path(args.plan).resolve()
    state = _require_state(plan_path)
    payload = _build_state_view(state, mode="full")
    save_state(plan_path, state)  # persist previously_reported_ready update
    emit_formatted(payload, args.format, format_next_md)
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    """Ultra-compact ID+status view. Use for trace verification, not driving."""
    plan_path = Path(args.plan).resolve()
    state = _require_state(plan_path)
    payload = {
        "slug": state["slug"],
        "title": state["title"],
        "summary": summary(state),
        "steps": [
            {
                "id": sid,
                "phase": s["phase"],
                "status": s["status"],
                "deps": s["deps"],
            }
            for sid, s in state["steps"].items()
        ],
    }
    emit_formatted(payload, args.format, format_index_md)
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    plan_path = Path(args.plan).resolve()
    state = _require_state(plan_path)
    sid = args.step
    if sid not in state["steps"]:
        emit({"error": f"Unknown step: {sid}"})
        return 1
    if not deps_all_completed(state, sid):
        unmet = [
            d for d in state["steps"][sid]["deps"]
            if state["steps"][d]["status"] not in (COMPLETED, SKIPPED)
        ]
        emit({"error": "Deps not satisfied", "unmet": unmet})
        return 1
    try:
        transition_step(state, sid, IN_PROGRESS, task_id=args.task_id)
    except ValueError as e:
        emit({"error": str(e)})
        return 1
    save_state(plan_path, state)
    payload = {"status": "started", "step": sid, "task_id": args.task_id}
    emit_formatted(payload, args.format, lambda d: format_transition_md("started", d))
    return 0


def cmd_complete(args: argparse.Namespace) -> int:
    plan_path = Path(args.plan).resolve()
    state = _require_state(plan_path)
    sid = args.step
    if sid not in state["steps"]:
        emit({"error": f"Unknown step: {sid}"})
        return 1
    try:
        transition_step(state, sid, COMPLETED)
    except ValueError as e:
        emit({"error": str(e)})
        return 1
    view = _build_state_view(state)
    save_state(plan_path, state)
    payload = {"status": "completed", "step": sid, **view}
    emit_formatted(payload, args.format, lambda d: format_transition_md("completed", d))
    return 0


def cmd_fail(args: argparse.Namespace) -> int:
    plan_path = Path(args.plan).resolve()
    state = _require_state(plan_path)
    sid = args.step
    if sid not in state["steps"]:
        emit({"error": f"Unknown step: {sid}"})
        return 1
    try:
        transition_step(state, sid, FAILED, reason=args.reason or "")
    except ValueError as e:
        emit({"error": str(e)})
        return 1
    view = _build_state_view(state)
    save_state(plan_path, state)
    payload = {"status": "failed", "step": sid, "reason": args.reason, **view}
    emit_formatted(payload, args.format, lambda d: format_transition_md("failed", d))
    return 0


def cmd_skip(args: argparse.Namespace) -> int:
    plan_path = Path(args.plan).resolve()
    state = _require_state(plan_path)
    sid = args.step
    if sid not in state["steps"]:
        emit({"error": f"Unknown step: {sid}"})
        return 1
    try:
        transition_step(state, sid, SKIPPED)
    except ValueError as e:
        emit({"error": str(e)})
        return 1
    view = _build_state_view(state)
    save_state(plan_path, state)
    payload = {"status": "skipped", "step": sid, **view}
    emit_formatted(payload, args.format, lambda d: format_transition_md("skipped", d))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    plan_path = Path(args.plan).resolve()
    state = _require_state(plan_path)
    payload = {
        "slug": state["slug"],
        "title": state["title"],
        "summary": summary(state),
        "parent_task_id": state.get("parent_task_id"),
        "steps": [
            {
                "id": sid,
                "phase": s["phase"],
                "title": s["title"],
                "status": s["status"],
                "deps": s["deps"],
                "task_id": s["task_id"],
                "failure_reason": s.get("failure_reason"),
            }
            for sid, s in state["steps"].items()
        ],
    }
    emit_formatted(payload, args.format, format_status_md)
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    plan_path = Path(args.plan).resolve()
    state = _require_state(plan_path)

    def reset_step(s: dict[str, Any]) -> None:
        s["status"] = PENDING
        s["task_id"] = None
        s["started_at"] = None
        s["completed_at"] = None
        s["failure_reason"] = None

    if args.all:
        for s in state["steps"].values():
            reset_step(s)
    elif args.step:
        if args.step not in state["steps"]:
            emit({"error": f"Unknown step: {args.step}"})
            return 1
        reset_step(state["steps"][args.step])
    else:
        emit({"error": "Pass --all or --step=<id>"})
        return 1

    recompute_blocked_status(state)
    save_state(plan_path, state)
    emit({"status": "reset", "summary": summary(state)})
    return 0


def cmd_set_parent(args: argparse.Namespace) -> int:
    plan_path = Path(args.plan).resolve()
    state = _require_state(plan_path)
    state["parent_task_id"] = args.task_id
    save_state(plan_path, state)
    emit({"status": "set_parent", "parent_task_id": args.task_id})
    return 0


def cmd_dag(args: argparse.Namespace) -> int:
    plan_path = Path(args.plan).resolve()
    state = _require_state(plan_path)
    status_color = {
        COMPLETED: "palegreen", IN_PROGRESS: "lightyellow",
        FAILED: "salmon", BLOCKED: "lightgray",
        SKIPPED: "lightgray", PENDING: "white",
    }
    status_icon = {
        COMPLETED: "[x]", IN_PROGRESS: "[>]",
        FAILED: "[!]", BLOCKED: "[B]",
        SKIPPED: "[-]", PENDING: "[ ]",
    }

    if args.format == "dot":
        print("digraph plan {")
        print("  rankdir=LR;")
        for sid, s in state["steps"].items():
            color = status_color.get(s["status"], "white")
            print(f'  "{sid}" [style=filled, fillcolor={color}];')
        for sid, s in state["steps"].items():
            for dep in s["deps"]:
                if dep in state["steps"]:
                    print(f'  "{dep}" -> "{sid}";')
        print("}")
    else:
        for phase in state["phase_order"] or [""]:
            if phase:
                print(f"\n# {phase}")
            for sid, s in state["steps"].items():
                if s["phase"] != phase:
                    continue
                icon = status_icon.get(s["status"], "[?]")
                deps = f"  <- {','.join(s['deps'])}" if s["deps"] else ""
                print(f"  {icon} {sid}: {s['title']}{deps}")
        print()
        print(f"Progress: {summary(state)['progress']}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic plan runner")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_format_flag(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--format", choices=["md", "json"], default="md",
            help="Output format: 'md' (default, LLM-optimized) or 'json' (machine-readable)",
        )

    p_init = sub.add_parser("init", help="Initialize state from plan")
    p_init.add_argument("plan")
    p_init.add_argument("--force", action="store_true")
    add_format_flag(p_init)
    p_init.set_defaults(func=cmd_init)

    p_next = sub.add_parser("next", help="Show ready steps")
    p_next.add_argument("plan")
    add_format_flag(p_next)
    p_next.set_defaults(func=cmd_next)

    p_start = sub.add_parser("start", help="Mark step in_progress")
    p_start.add_argument("plan")
    p_start.add_argument("step")
    p_start.add_argument("--task-id", default=None)
    add_format_flag(p_start)
    p_start.set_defaults(func=cmd_start)

    p_complete = sub.add_parser("complete", help="Mark step completed")
    p_complete.add_argument("plan")
    p_complete.add_argument("step")
    add_format_flag(p_complete)
    p_complete.set_defaults(func=cmd_complete)

    p_fail = sub.add_parser("fail", help="Mark step failed")
    p_fail.add_argument("plan")
    p_fail.add_argument("step")
    p_fail.add_argument("--reason", default="")
    add_format_flag(p_fail)
    p_fail.set_defaults(func=cmd_fail)

    p_skip = sub.add_parser("skip", help="Mark step skipped")
    p_skip.add_argument("plan")
    p_skip.add_argument("step")
    add_format_flag(p_skip)
    p_skip.set_defaults(func=cmd_skip)

    p_status = sub.add_parser("status", help="Show all steps and statuses")
    p_status.add_argument("plan")
    add_format_flag(p_status)
    p_status.set_defaults(func=cmd_status)

    p_index = sub.add_parser("index", help="Ultra-compact ID+status trace view")
    p_index.add_argument("plan")
    add_format_flag(p_index)
    p_index.set_defaults(func=cmd_index)

    p_reset = sub.add_parser("reset", help="Reset step(s) to pending")
    p_reset.add_argument("plan")
    p_reset.add_argument("--step", default=None)
    p_reset.add_argument("--all", action="store_true")
    p_reset.set_defaults(func=cmd_reset)

    p_parent = sub.add_parser("set-parent", help="Record parent TaskCreate id")
    p_parent.add_argument("plan")
    p_parent.add_argument("--task-id", required=True)
    p_parent.set_defaults(func=cmd_set_parent)

    p_dag = sub.add_parser("dag", help="Print DAG visualization")
    p_dag.add_argument("plan")
    p_dag.add_argument("--format", choices=["text", "dot"], default="text")
    p_dag.set_defaults(func=cmd_dag)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
