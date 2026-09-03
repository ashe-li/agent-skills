# Plan File Management

Scope: Apply when creating any plan markdown file.

- Use a semantic filename — avoid generic `plan.md` at project root
- Output path: `plans/active/<semantic-slug>.md`
- Archive path: `plans/completed/<semantic-slug>.md`（由 `/plan-archive` 處理）
- Backlog path: `plans/backlog/<semantic-slug>.md`（提案池：無阻塞、未核准、近期無新證據；不計入 active；升回 active 由使用者裁決，2026-09-03 起）

Why: semantic slugs make plans discoverable later; generic `plan.md` collides across tasks and gets lost on archive. Backlog 路徑把「還沒核准也沒新進展」的提案跟真正在推進的 active plan 分開，避免前者拉高 active 的稽核與清運成本。
