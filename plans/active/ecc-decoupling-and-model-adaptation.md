# ECC 解耦與 4.8+ 模型適配

- **Status**: APPROVED（2026-07-04 使用者裁決：D1-D5 全數採推薦值 (a)）
- **Date**: 2026-07-04
- **Audit 依據**: `~/Documents/knowledge-base/reports/2026-07-04-agent-skills-ecc-decoupling-audit.md`
- **目標**: 移除 repo 對 everything-claude-code（ECC）plugin 的 hard-runtime 依賴（~46 處 / 5 檔），同步把 Opus 4.5 時代的冗餘指令精簡至 4.8+ 水準（4,338 → ~2,669 行）
- **不動範圍**: historical 檔案（CHANGELOG、research/、plans/completed/）；官方 `security-guidance@claude-plugins-official` plugin（非 ECC）；`ecc-skill-defer/`（依 D4 過渡保留）

## 裁決點（實作前需確認；推薦值標 ★）

| # | 問題 | 選項 |
|---|---|---|
| D1 | learn-eval 替代 | ★(a) 5 維 rubric inline 進 `/update` Step 4；(b) 過渡期續用 ECC 標 deprecated；(c) 改接 skills-ecosystem-eval bridge |
| D2 | 孤兒計畫 ecc-190-workflow-integration.md | ★(a) 作廢搬 `plans/archived/` 附原因；(b) 用原生 Workflow 重寫 |
| D3 | knowledge-base-quality-optimization.md | ★(a) 重寫為去 ECC 版本後執行；(b) 凍結；(c) 照原案執行（不推薦） |
| D4 | ecc-skill-defer 去留 | ★(a) 保留標 deprecated，等 harness 端 ECC plugin 處置定案（含 gateguard D1 待辦）；(b) 直接刪除 |
| D5 | 精簡範圍 | ★(a) 全 18 檔（Phase 3 全做）；(b) 只動 ECC 相關 5 檔（Phase 3 跳過） |

## 替換原則（所有 Step 共用）

1. ECC agent → 內建：`planner`→`Plan`、`code-reviewer`(審 code)→`/code-review`、(審文件)→`general-purpose` fresh 驗收 agent、`security-reviewer`→`/security-review`、`refactor-cleaner`→`/simplify`、`doc-updater`→`general-purpose`+明確 prompt+主模型 git diff 驗證
2. 無前綴 ECC agent 名稱（`code-reviewer` 等）同屬 ECC 註冊，一併替換，不可只拿掉前綴
3. 精簡四刀：自建 manifest 儀式→原生 TaskCreate/TaskUpdate；裝飾性學術引用刪除；複製全域規則改一行引用；同規則單檔多次重複收斂為單一來源
4. **不可砍清單**（各 Step 的 read-back 驗收依據）：環境路徑/閾值數字/API 怪癖/差異化定位表/真實踩坑教訓。特別是 `playwright-human-in-the-loop:13-43,136-142`（三軸分級+安全底線）、`plan-run:27-45,198-241`（parser 契約）、`notion-plan` DOM 事實
5. 每檔一個 commit（主題分組），改前先 `git status` 確認無夾帶

## Implementation Steps

### Phase 1: 單點槓桿與低風險替換
- [ ] **S1.1** — security-guidance 機制 A 換原生 /security-review
  - Files: rules/security-guidance/skill-integration.md, rules/security-guidance/README.md
  - Agent: `general-purpose`
  - Action: 機制 A 的 `Agent(subagent_type="everything-claude-code:security-reviewer")` 呼叫規格改為委派內建 `/security-review` skill；移除 defer/restore fallback 段落（原生 skill 無 defer 問題）；README.md 防線關係圖同步改措辭
  - Dependencies: []
  - Why: 單點槓桿 — design/update/pr/assist 四個 skill 共用此規格，改一處四檔受益
- [ ] **S1.2** — verify-evidence-loop 誤標修正
  - Files: verify-evidence-loop/SKILL.md
  - Agent: `general-purpose`
  - Action: L11 把本 repo 的 `evidence-check` 誤標為「ECC 既有 primitives」→ 更正歸屬；順便刪 L385-401 裝飾性引用段
  - Dependencies: []
  - Why: 事實錯誤，一行修正避免未來 session 誤判依賴
- [ ] **S1.3** — pr/SKILL.md 改寫（330 → ~200 行）
  - Files: pr/SKILL.md
  - Agent: `general-purpose`
  - Action: L89-91 security-reviewer 呼叫→依 S1.1 新規格；L113-136 Step 2b→改委派 `/simplify`；L138-151 Step 2c→改委派 `/plan-archive`；PR description 清單 5 種包裝收斂為 1；保留 stale-branch 陷阱、base branch 防護、CHANGELOG 檢查、Title 格式等環境規則
  - Dependencies: S1.1
  - Why: 替代方案全部現成，低風險先行建立換裝範式

### Phase 2: 核心 pipeline 重寫
- [ ] **S2.1** — update/SKILL.md 重寫（417 → ~160 行）
  - Files: update/SKILL.md
  - Agent: `general-purpose`
  - Action: Step 1 doc-updater→`general-purpose`+文件更新 prompt+manifest 輸出（主模型 git diff 驗證維持）；Step 2 code-reviewer→`general-purpose` fresh 驗收 agent；Step 3 security-reviewer→依 S1.1；Step 4 learn-eval→依 D1 裁決（預設 (a)：5 維 rubric inline，格式契約 L249-259 原樣保留）；manifest 樣板 5 次重複收斂為 1 次定義；刪逐字 HITL 對話腳本；Step 7 `$ARGUMENTS` 手工串接精簡（暫不改原生 Workflow，另議）
  - Dependencies: S1.1
  - Why: 全 repo 耦合之冠（4 個字面呼叫），含唯一無原生替代的 learn-eval
- [ ] **S2.2** — design/SKILL.md 重寫（407 → ~230 行）
  - Files: design/SKILL.md
  - Agent: `general-purpose`
  - Action: Step 1 盤點表改列內建資源（`Plan`/`Explore`/`general-purpose`/`/code-review`/`/security-review`/`/simplify`），清除 stale 項（`/aside`、`/blueprint`、`/prompt-optimize`、`/docs`）；Step 3 `everything-claude-code:planner`→內建 `Plan` agent（沿用 L134 architect 移除先例的寫法）；Step 0 複製全域規則段改一行引用；L122-126 重複編號修正；plan 格式契約 L268-293 原樣保留
  - Dependencies: S1.1
  - Why: Step 1/3 為硬性阻塞依賴；格式契約是 /plan-run 的 API 不可動
- [ ] **S2.3** — assist/SKILL.md 路由表重建（292 → ~145 行）
  - Files: assist/SKILL.md
  - Agent: `general-purpose`
  - Action: Step 2 盤點與 Step 3 路由表整個重建於內建 primitives；刪除無對應項（`/quality-gate`、`/context-budget`、`tdd-guide` 路由列改 `general-purpose`+TDD prompt 或刪除）；Step 0 改引用全域規則；Handoff 三重完整性宣告與 Step 5 manifest 儀式改原生 Task tracking
  - Dependencies: S2.1, S2.2
  - Why: 路由表引用 update/design 的新行為，須等兩者定稿後重建
- [ ] **S2.4** — remove-architect-pipeline 規則更新
  - Files: rules/refactor/remove-architect-pipeline.md
  - Agent: `general-purpose`
  - Action: L15-17 替代方案表「→ everything-claude-code:planner」改「→ 內建 Plan agent / plan mode」
  - Dependencies: S2.2
  - Why: 禁用規則自己還推薦另一個 ECC agent，需與 S2.2 的新寫法一致

### Phase 3: 全面精簡批次（依 D5；與 Phase 2 不同檔，可並行）
- [ ] **S3.1** — 高削減組精簡
  - Files: plan-archive/SKILL.md, handoff/SKILL.md, curation/SKILL.md, triage/SKILL.md, learn-eval-deep/SKILL.md
  - Agent: `general-purpose`
  - Action: plan-archive L116-194 Hook 安裝教學移出至 `docs/hooks-setup.md`（205→~75）；handoff 四處重複規則收斂+刪「設計原則」覆誦段（191→~145）；curation manifest 儀式→原生 Task（161→~102）；triage 逐字 bash 腳本壓縮（138→~95）；learn-eval-deep 重複可靠度陳述合併（159→~105）。各檔保留點依 audit 清單逐項核對
  - Dependencies: []
  - Why: 削減比例最高的一批，無 ECC 耦合，純冗餘
- [ ] **S3.2** — 中削減組精簡
  - Files: notion-plan/SKILL.md, verify-fix-loop/SKILL.md, verify-evidence-loop/SKILL.md, plan-run/SKILL.md
  - Agent: `general-purpose`
  - Action: notion-plan 刪通用 Markdown 教學、保留全部 DOM/eval 踩坑（314→~210）；verify-fix-loop 刪 pseudocode 重述+hard-cap 規則單一來源化+清 frontmatter 失效宣告（312→~230）；verify-evidence-loop Phase D pseudocode→prose、injection-hardening 三處收斂（401→~200）；plan-run 重複敘述收斂、parser 契約與狀態機原樣保留（247→~165）
  - Dependencies: S1.2
  - Why: 機制保留、文件精簡；plan-run 狀態機經查證非冗餘不動
- [ ] **S3.3** — 低削減組精簡
  - Files: worktree/SKILL.md, figma-verify/SKILL.md, evidence-check/SKILL.md, playwright-human-in-the-loop/SKILL.md
  - Agent: `general-purpose`
  - Action: worktree 刪文末覆誦段（196→~155）；figma-verify 四處重複規則收斂（149→~115）；evidence-check 輕度合併重複表格（196→~165）；playwright-human-in-the-loop 僅刪裝飾引用與報告格式重疊，**三軸分級與安全底線一字不動**（142→~115）
  - Dependencies: []
  - Why: 環境事實密度高，精簡空間小，最後做

### Phase 4: 文件同步與收尾
- [ ] **S4.1** — active plans 處置
  - Files: plans/active/ecc-190-workflow-integration.md, plans/active/knowledge-base-quality-optimization.md
  - Agent: `general-purpose`
  - Action: 依 D2/D3 裁決執行（預設：前者作廢搬 plans/archived/ 附原因一行；後者重寫為去 ECC 版本）
  - Dependencies: S2.1
  - Why: 消除與解耦目標對撞的待執行計畫；kb-quality-optimization 重寫需以 S2.1 的新 Step 4 為基礎
- [ ] **S4.2** — README.md 全面同步
  - Files: README.md
  - Agent: `general-purpose`
  - Action: skill 總覽表、pipeline 描述、`/ecc-skill-defer` 說明（依 D4 標 deprecated）、L481-490「ECC Agent 退化警告」表全面改寫為解耦後架構
  - Dependencies: S1.3, S2.3, S3.3
  - Why: README 是即時架構主文件，最後統一改避免多次返工
- [ ] **S4.3** — CHANGELOG 新增條目
  - Files: CHANGELOG.md
  - Agent: `general-purpose`
  - Action: 附加新版本條目記錄 ECC 解耦+精簡；不回改任何歷史條目
  - Dependencies: S4.2
  - Why: 版本紀錄慣例
- [ ] **S4.4** — 總驗收
  - Files: （唯讀驗證）
  - Agent: `general-purpose`
  - Action: 跑下方驗收指令 4 條；fresh-context agent 逐檔 read-back「不可砍清單」項目仍存在；`/pr` 與 `/update` 各實跑一輪真實 flow（非 mock）確認替換後呼叫可執行
  - Dependencies: S4.3
  - Why: 驗證不自驗 — 改寫 agent 與驗收 agent 分離

## 驗收條件

```bash
# 1. hard-runtime 殘留 = 0（白名單：historical 與 ecc-skill-defer 過渡期）
grep -rn 'everything-claude-code' --include='*.md' --include='*.sh' ~/Documents/agent-skills \
  | grep -v -e CHANGELOG -e research/ -e plans/completed/ -e plans/archived/ -e ecc-skill-defer/
# 預期輸出：空

# 2. 總行數 ≤ 3,000（D5=(a) 時）
find ~/Documents/agent-skills -name 'SKILL.md' -not -path '*/.git/*' | xargs wc -l | tail -1

# 3. 每檔 git log 一 commit 一主題，無夾帶 hunk
git -C ~/Documents/agent-skills log --oneline --stat

# 4. /plan-run 相容性：本 plan 可被 parser 讀取
python3 ~/Documents/agent-skills/scripts/plan_runner.py init plans/active/ecc-decoupling-and-model-adaptation.md
```

## Rollback

全程 git 管理：任一 Step 驗收失敗 → `git revert` 該檔 commit 即可；Phase 間無不可逆操作。
