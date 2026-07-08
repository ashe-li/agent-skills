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
- 是否有 `## Phase` 或 `## Step` 段落（實作步驟）
- 是否有 `## 驗證` 或 `## Verification` 段落
- 是否有 `## Industry & Standards Reference` 段落

用 TaskCreate 為每個 `## Phase X` / `### Step X.Y` 建一個 task 追蹤完成狀態，取代手刻表格；task 總數即為 Step 3 完成率的分母。

---

## Step 3：補充驗證結果

在 plan 檔案頂部（緊接 `---` frontmatter 後）加上：

```markdown
**狀態：✅ 完成（YYYY-MM-DD）**
```

再追加或更新 `## 驗證結果` 段落，逐條比對 Step 2 建立的 task：

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

確認移動成功後輸出：`✅ 已歸檔：plans/completed/<filename>.md`

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
