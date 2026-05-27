# Plan File Management

Scope: Apply when creating any plan markdown file.

- Use a semantic filename — avoid generic `plan.md` at project root
- Output path: `plans/active/<semantic-slug>.md`
- Archive path: `plans/completed/<semantic-slug>.md`（由 `/plan-archive` 處理）

Why: semantic slugs make plans discoverable later; generic `plan.md` collides across tasks and gets lost on archive.
