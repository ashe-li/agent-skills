# security-guidance plugin 整合

官方 `security-guidance@claude-plugins-official` plugin 的設定來源（source of truth）。
plugin 本身靠 hooks 自動跑，無法經 `npx skills add` 散佈，故這裡只版本控管「擴充檔 + 設定記錄」。

## 三層防線（plugin 提供，最早的一層）

| 觸發點 | 機制 | 成本 |
|--------|------|------|
| 每次 file edit | per-edit pattern check（deterministic 字串/regex） | 0（無 model call）|
| 每個 turn 結束 | end-of-turn diff review（背景 model） | 每個改檔的 turn 都跑，最多連 3 次 |
| Claude 經 Bash commit/push | agentic review（讀周邊 code） | 每 commit，capped 20/hr |

跟既有防線的關係（defense-in-depth，不取代）：

```
plugin（in-session，被動）+ skill 主動觸發 → /pr 的 security-reviewer → Code Review（PR）→ CI scanner
```

plugin 是 hook **被動**觸發；主動入口 skill（`/design`、`/update`、`/pr`、`/assist`）的**主動**觸發契約見
[`skill-integration.md`](skill-integration.md)：定義觸發閘（security-relevance heuristic）+ 兩種機制
（委派 security-reviewer agent + 引用同一份 `claude-security-guidance.md`），確保各階段與 plugin 判準一致。

## 本機部署狀態（省 token 姿態）

`~/.claude/settings.json` 的 `env` 加了：

| 變數 | 值 | 作用 |
|------|----|------|
| `ENABLE_STOP_REVIEW` | `0` | 關掉「每 turn」review（最大成本來源）|
| `SG_AGENTIC_MODEL` | `claude-sonnet-4-6` | commit review 用的 model（預設 Opus）|

留下：per-edit pattern（免費）+ commit review（sonnet，配合 `/pr` 的 deliberate commit）。

兩個 review 各有獨立的 model 變數（別搞混）：
- `SECURITY_REVIEW_MODEL` → **end-of-turn**（每 turn）review 的 model
- `SG_AGENTIC_MODEL` → **commit/push** review 的 model

旋鈕：
- 恢復每 turn 覆蓋：`ENABLE_STOP_REVIEW=1`（要省 token 再設 `SECURITY_REVIEW_MODEL=claude-sonnet-4-6`）
- 全關 model review（連 commit review 一起）：`ENABLE_CODE_SECURITY_REVIEW=0`
- 整個 plugin 停用：`SECURITY_GUIDANCE_DISABLE=1`

## 擴充檔（symlink 到 ~/.claude/，全域生效）

```
~/.claude/claude-security-guidance.md  ->  rules/security-guidance/claude-security-guidance.md
~/.claude/security-patterns.json       ->  rules/security-guidance/security-patterns.json
```

- `claude-security-guidance.md`：餵給 model-backed review 的威脅模型/檢查清單（含 secrets 政策）。combined cap 8 KB。
- `security-patterns.json`：deterministic 規則（硬編 secret 前綴、私鑰、`subprocess shell=True`）。用 JSON 不用 YAML，避免 PyYAML 缺失時靜默忽略。

重建 symlink：

```bash
ln -sf ~/Documents/agent-skills/rules/security-guidance/claude-security-guidance.md ~/.claude/claude-security-guidance.md
ln -sf ~/Documents/agent-skills/rules/security-guidance/security-patterns.json ~/.claude/security-patterns.json
```

## 注意

- pattern check 會對「把危險字串當例子寫的 markdown/doc」誤報（每 pattern 每檔每 session 一次，無害）。
- commit review 只審 Claude 經 Bash 做的 commit；`!` shell escape 與你自己 shell 的 commit 不審。
- runtime 診斷：`~/.claude/security/log.txt`。
