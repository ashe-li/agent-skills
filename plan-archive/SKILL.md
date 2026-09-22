---
name: plan-archive
description: 將已完成的 plan 從 plans/active/ 歸檔至 plans/completed/，補上驗證結果與完成時間。適合在實作結束後呼叫。
allowed-tools: Bash, Read, Glob, Write, Edit
argument-hint: [plan 檔名或留空自動偵測]
---

# /plan-archive — 歸檔已完成的 Plan

將 `plans/active/` 中已完成的 plan 移至 `plans/completed/`，並補上驗證結果。

---

## Step 1：找出要歸檔的 Plan

如果有傳入引數（檔名或路徑），直接使用。
否則，列出 `plans/active/` 下的所有 `.md`：

```bash
ls plans/active/*.md 2>/dev/null
```

若有多個，依 mtime 排序，問使用者選哪一個。
若只有一個，直接用。
若目錄不存在或空，輸出「找不到待歸檔的 plan」並結束。

---

## Step 2：讀取 Plan 內容

讀取目標 plan 檔案，確認：
- 是否有實作步驟段落。**canonical 格式**（`/design` 產出、`plan_runner.py` 解析）的 step 是 `- [ ] **S<phase>.<num>** — <title>` 的 S-code 條目，phase 是**任何 `###` 標頭**（parser 就是 `^###\s+(.+)$`，慣例寫 `### Phase N — <title>`，冒號或破折號都可以）；**舊 plan 另有** `## Phase X` / `### Step X.Y` 標頭式寫法，兩種都要認
- 是否有 `## 驗證` 或 `## Verification` 段落
- 是否有 `## Industry & Standards Reference` 段落

列出 plan 裡所有 step 作為追蹤清單，逐項標記完成狀態。**先按 canonical S-code 格式抓**（`- [ ] **S1.1** — <title>`，phase 為其上方最近的 `###` 標頭）；抓不到再退回舊的標頭式格式（`## Phase X` / `### Step X.Y`）。**兩種都抓到空清單就停下來問使用者，不要當作「0 個 step」繼續**——空清單會讓 Step 3 的完成率失去意義。

**完成率的分母是這份清單的長度**（即 plan 內實際的 step 數），不是 task 數——`TaskCreate` 在預設模型上不存在（見 [`rules/task-tracking-availability.md`](../rules/task-tracking-availability.md)），拿 task 數當分母會在無工具環境直接歸零。session 若有 Task 工具，可另外用 `TaskCreate` 鏡射這份清單。

---

## Step 2.5：產生執行報告

檢查 `<plan-dir>/.plan-state/<slug>.state.json` 是否存在（`<slug>` 為 plan 檔名去掉 `.md`）。plan 若在 all_done 後由 runner 自動寫過 `<plan-dir>/.plan-state/<slug>.report.md`，可以先讀那份看個大概；但實際嵌入 plan 的內容一律以下面重新跑 `report` 的輸出為準——all_done 後每次 `complete`／`skip`（例如事後補摘要）都會重寫那份檔，但其他寫 state 的操作（如 `reset`）不會，以重新產生的為準最保險。

**存在**：跑

```bash
python3 ~/Documents/agent-skills/scripts/plan_runner.py report <plan>
```

把 stdout **原樣**放進 plan 的 `## 執行摘要` 段（該段已存在就整段取代，不重複附加），位置在 `## 驗證結果` 之前。**不要**加 `--output` 寫成旁檔——旁檔要記得跟著 Step 4 的 `mv` 一起搬，漏搬就變孤兒；KB ingest 也不會把旁檔和 plan 關聯起來；兩份檔案之後會各自漂移。

**不存在**：在 `## 執行摘要` 段寫一行「（本 plan 未經 /plan-run 推進，無執行紀錄）」，繼續下一步。

為什麼要在移動前做：state 放在 `.plan-state/` 隱藏目錄，Step 4 的 `mv` 只搬 `.md`，歸檔後 plan 和 state 就分開了，執行紀錄必須先嵌進 plan 本身才會被保存。報告格式對 `parse_plan` 無效（開頭固定 `### 執行摘要` 不含 Phase 字樣、各 phase 標題用 `####`、不用 `- [ ]` 列表、摘要每行以 `>` 引用開頭、其餘行也都不以空白、`-` 或 `#` 開頭，整段包在 `## 執行摘要` 底下），歸檔後的 plan 就算被重新 `init` 也不會多出 step 或 phase。

Step 3 的驗證表可以直接引用報告裡「未完成與例外」的 failed／skipped 清單，不必重新逐條核對。

---

## Step 3：補充驗證結果

在 plan 檔案頂部（緊接 `---` frontmatter 後）加上：

```markdown
**狀態：✅ 完成（YYYY-MM-DD）**
```

再追加或更新 `## 驗證結果` 段落，逐條比對 Step 2 建立的清單：

| # | Phase/Step | 預期結果 | 實際結果 | 狀態 |
|---|-----------|---------|---------|------|
| 1 | Phase 1: ... | ... | ... | PASS/FAIL |

FAIL 項目必須附說明（是否為可接受的偏差或待處理問題）。

若 plan 有 `## Industry & Standards Reference`，逐條對照確認落實情況（`APPLIED` / `PARTIAL` / `NOT_APPLIED`，後兩者附原因：環境限制、決策變更或遺漏）。

**完成率 = PASS 步驟數 / 總步驟數：**
- < 100%：**警告** — 有未完成步驟，確認是否為已知的可接受偏差
- < 80%：**阻止歸檔** — 需告知使用者，要求確認是否仍要歸檔

其他內容（測試通過數、任何偏差或補充說明）保留於此段落。

---

## Step 4：移動檔案

```bash
mkdir -p plans/completed
mv plans/active/<filename>.md plans/completed/<filename>.md
```

確認移動成功後輸出：`✅ 已歸檔：plans/completed/<filename>.md`。**最終回覆必須附上嵌入的執行摘要精簡版**：Progress 進度行、每個 phase 的 step 狀態表（可省略逐 step 摘要引文）、Step 2.5「未完成與例外」段全文——只把摘要嵌進歸檔後的 `.md` 不算交付，使用者要在這次回覆裡就看到。Step 2.5 判定為「無執行紀錄」的 plan，這裡照實回覆「（本 plan 未經 /plan-run 推進，無執行紀錄）」，不用假造摘要內容。

---

## 自動化（選用）

若希望每次 `ExitPlanMode` 後自動將 plan 存至 `plans/active/`，設定方式見 [`docs/hooks-setup.md`](../docs/hooks-setup.md)。

---

## 目錄規範

```
plans/
├── active/       # 進行中（Hook 自動存入 / /plan 手動建立）
├── completed/    # 已實作完成（/plan-archive 歸檔）
└── archived/     # 長期封存（不再參考的舊 plan）
```
