# 主動式安全觸發（skill 整合契約）

`security-guidance` plugin 是 **hook 被動觸發**（改檔/turn/commit 後才反應）。
本契約讓主動入口 skill（`/design`、`/update`、`/pr`、`/assist`）在流程中**主動**帶到安全這層，
與 plugin 用**同一份**威脅模型，達成 design-time / update-time / PR-time 與 in-session 一致。

## 觸發閘（security-relevance heuristic）

不是每個任務都需要安全審查（避免燒 token）。**任務或變更觸及以下任一面向**即觸發：

- 認證 / 授權 / session / token / 密碼 / 私鑰 / secret
- 使用者輸入處理（query / body / header / URL param / 檔案上傳）
- API endpoint / 路由 / 對外介面
- DB query / SQL / ORM
- 反序列化（pickle、unmarshal）、動態執行（eval、`new Function`、shell）
- 檔案路徑 / 命令執行 / 外部 URL fetch（SSRF）
- DOM 注入（innerHTML、dangerouslySetInnerHTML）、加密 / 雜湊

**都不觸及** → 明示「無安全敏感面，跳過安全審查」，不空跑 agent。

## 兩種機制（都要）

### 機制 A — 委派 security-reviewer agent

```
Agent(subagent_type="everything-claude-code:security-reviewer", model="sonnet")
```

審查實際變更（diff / 程式碼），產出 severity 分級 findings（CRITICAL/HIGH/MEDIUM/LOW）。
model 用 sonnet（符合「安全審查用 sonnet」偏好）。**先確認 agent 可用**（對照各 skill 的 ECC 盤點）；
若已 defer，提示 restore 或退化為機制 B。

### 機制 B — 引用同一份 guidance 當 checklist

讀 `~/.claude/claude-security-guidance.md`（plugin 也讀這份）當審查清單，
逐條比對變更/計畫是否違反。這份是 secrets 政策 + 各語言規則的單一來源，確保各階段判準一致。

> guidance canonical 在 `rules/security-guidance/claude-security-guidance.md`，symlink 到 `~/.claude/`。

## 各 skill 插入點

| Skill | 何時觸發 | 機制 A（agent） | 機制 B（guidance checklist） |
|-------|---------|----------------|------------------------------|
| `/design` | plan 涉及上述面向 | plan 的實作後步驟須納入 security-reviewer | plan 必含「Security / Threat Model」章節，逐條對照 guidance；Step 4 品質閘**主動驗證**覆蓋（非只「已評估」打勾）|
| `/update` | session 變更涉及上述面向 | Step 2 在 code-reviewer 之外**並行**跑 security-reviewer | review prompt 附上 guidance 當判準 |
| `/pr` | 變更涉及上述面向 | Step 2 Quick Review **委派** security-reviewer（非只 inline）| quick review 以 guidance 為 checklist |
| `/assist` | 路由命中上述面向 | pipeline **預設附加** security-reviewer | 路由與 reviewer prompt 引用 guidance |

## 與 plugin 的關係

plugin（in-session，被動）+ 本契約（skill，主動）+ `/pr` 的 PR-time + Code Review + CI = defense-in-depth。
主動觸發補的是「在對的階段、用對的判準、主動發現」，不是取代 plugin 的事後攔截。
