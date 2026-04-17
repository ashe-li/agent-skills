---
name: handoff
description: 產生跨 context 接手 prompt — 萃取本次對話的目標、進度、決策、未完成項目，輸出可直接貼到新 session 或 /compact 之後使用的自包含 prompt。
allowed-tools: Bash, Read, Glob, Grep, AskUserQuestion, Write
argument-hint: [可選：focus 主題或場景描述]
---

# /handoff — 跨 context 接手 prompt

當你要切換 context（開新對話 / 即將 `/compact` / 換機器繼續 / 隔天再戰），本 skill 會掃描當前狀態與對話脈絡，產出**自包含的接手 prompt**——貼到任何新 context 都能無縫接手，不需要再口頭交代背景。

> **使用情境（共用同一流程）：**
> 1. **新 context 接手** — 「給我新 context 完整需要的 prompt」
> 2. **/compact 前準備** — 「我要準備 compact 了，給我 compact 之後可以用的 prompt」
> 3. **跨機器 / 跨日繼續** — 「幫我整理 handoff」

> **與其他 skill 的差異：**
> - `everything-claude-code:save-session` 存整份 JSON，需配 `resume-session` 載入；本 skill 只輸出**純文字 prompt**，跨環境通用
> - `everything-claude-code:strategic-compact` 提示**何時**該 compact；本 skill 解決 compact **之後**怎麼接手
> - `/update` 沉澱**長期知識庫**；本 skill 為**短期接手橋樑**

---

## Step 1：偵測當前狀態（事實層）

並行收集當前環境快照（避免接手者誤判）：

```bash
git rev-parse --abbrev-ref HEAD          # 當前 branch
git status --short                        # 未 commit 變更
git log --oneline -10                     # 近期 commit
git stash list                            # stash 狀態
pwd                                       # 工作目錄
```

額外掃描（依當前情境條件性執行）：
- 是否在 worktree：檢查 `.git` 是檔案還是目錄
- 是否有進行中的 plan：`ls plans/active/*.md 2>/dev/null`
- 是否有 background process：本對話中尚未結束的 `Bash run_in_background`
- 是否有 stash：`git stash list`

> **路徑、branch、commit hash、檔名都必須記錄絕對值**——接手者沒有當前對話的記憶，模糊指涉（「那個檔案」「剛剛的 branch」）會直接報廢。

> **⚠️ 相關性標註（必要）：** 並非所有偵測到的環境項目都與本次工作相關。
> stash、`plans/active/*.md`、background process、其他 untracked 檔案——逐項判斷是否屬於本次任務脈絡：
> - **相關**：直接列出
> - **不相關**：在 Step 3 的「環境快照」中**明確標註「不相關於本任務」**（例：`Stash（不相關）：stash@{0}: 舊的 plan-archive 工作`）
>
> 不標註會讓接手者誤判：以為 stash 是任務遺留 → 嘗試 pop → 污染工作目錄。

---

## Step 2：對話脈絡萃取（建立 Context Manifest）

回顧本次對話，**逐項建立 manifest**（依據：DAMA-DMBOK Completeness）。Manifest 總數 = Step 5 完整率分母：

| # | 區塊 | 內容摘要 | 已寫入 prompt？ |
|---|------|---------|----------------|
| 1 | 任務目標 | 使用者最初提出什麼需求？ | 待比對 |
| 2 | 當前進度 | 已完成哪些步驟？已跑過哪些指令？ | 待比對 |
| 3 | 決策脈絡 | 為什麼選 X 不選 Y？已放棄哪些方案？ | 待比對 |
| 4 | 環境快照 | branch / worktree / 修改檔案 / 進行中 plan | 待比對 |
| 5 | 重要 context | 不會反映在 git diff 的隱含資訊（外部討論、效能數據、ticket 編號、社群共識/反面意見） | 待比對 |
| 6 | 待辦項目 | 還沒做的事，依優先順序排列 | 待比對 |
| 7 | 立即可執行的下一步 | 接手者第一個 prompt 該下什麼？（必須具體到檔案路徑與動作） | 待比對 |

**萃取原則：**
- 只記錄**真正討論/做過**的事，禁止揣測或補白
- 若某區塊不適用，明確標記 `N/A` 並附理由（例：「N/A — 本次無放棄方案」）
- 引用使用者原句時保留原文，不擅自改寫

---

## Step 3：產出 Handoff Prompt

依下列模板輸出，所有區塊必須對應 Step 2 manifest 七項：

````markdown
# Handoff Prompt（產出時間：{{YYYY-MM-DD HH:MM}}）

> **給接手 Claude：** 先讀完整個 prompt，再從「立即可執行的下一步」開始。
> 動手前先 `git status` 與 `git rev-parse --abbrev-ref HEAD` 確認與下方環境快照一致。

## 1. 任務目標
{{使用者原始需求，盡量保留原句}}

## 2. 當前進度
- **已完成：**
  1. ...
  2. ...
- **進行中：** ...
- **被擋住的事項：** {{若無則 N/A}}

## 3. 決策脈絡
- **採用：** 選 X 不選 Y，因為 ...（含依據：RFC/業界實踐/效能數據/社群共識）
- **已放棄：** 試過 A，因 B 放棄
- **反面意見/已知陷阱：** ...

## 4. 環境快照
- 工作目錄：`{{absolute path}}`
- Branch：`{{branch}}`
- Worktree：{{是 — 主 repo 在 X / 否}}
- 未 commit 變更（**本任務相關**）：
  ```
  {{僅列出本次工作產生的變更}}
  ```
- 未 commit 變更（**不相關**）：{{若有與其他任務交雜的變更，列在此處避免接手者誤動，或 N/A}}
- 進行中 plan：`{{plans/active/<slug>.md}}` 或 N/A
- 進行中 plan（不相關）：{{列出與本任務無關的 active plans 提醒接手者忽略，或 N/A}}
- Stash：{{若與本任務相關列出，否則明確標註「不相關 — stash@{0}: ...」或 N/A}}
- Background process：{{列出未結束的，或 N/A}}

## 5. 重要 context（不會反映在 diff）
- Ticket：{{PDT-XXXX / Notion URL / N/A}}
- 外部依據：{{引用過的 RFC/文件/社群討論連結}}
- 隱含知識：{{調查數據、效能比較、業界慣例}}

## 6. 待辦項目（依優先順序）
1. ...
2. ...

## 7. 立即可執行的下一步
> **直接複製以下作為第一個 prompt：**

```
{{1–2 句具體指令，含完整檔案路徑與要做的動作。
範例：「打開 /abs/path/to/file.ts 第 42 行，把 X 改成 Y，然後跑 npm test」}}
```
````

---

## Step 4：輸出方式（HITL）

使用 AskUserQuestion 確認：

> Handoff prompt 已準備好，要怎麼輸出？
>
> 1. **直接顯示** — 印在對話中供你複製
> 2. **寫入檔案** — 存到 `.claude/handoff/handoff-{{YYYY-MM-DD-HHMM}}.md`
> 3. **兩者皆要** — 顯示 + 存檔（建議：compact 前用此選項，compact 後若 prompt 沒留下還有檔案可救）

若選 2 或 3，自動建立目錄：

```bash
mkdir -p .claude/handoff
```

---

## Step 5：完整性驗證（依據：DAMA-DMBOK Completeness）

輸出後逐條比對 Step 2 manifest，產出驗證表：

| # | 區塊 | 涵蓋狀態 | 備註 |
|---|------|---------|------|
| 1 | 任務目標 | ✅ / ⚠️ / N/A | |
| 2 | 當前進度 | ✅ / ⚠️ / N/A | |
| 3 | 決策脈絡 | ✅ / ⚠️ / N/A | |
| 4 | 環境快照 | ✅ / ⚠️ / N/A | |
| 5 | 重要 context | ✅ / ⚠️ / N/A | |
| 6 | 待辦項目 | ✅ / ⚠️ / N/A | |
| 7 | 立即可執行的下一步 | ✅ / ⚠️ / N/A | |

**完整率 = (✅ + N/A) / 7**

- 完整率 = 100%：直接交付
- 完整率 < 100%：警告使用者哪些區塊缺漏，附建議補充方向
- 完整率 < 70%：**阻止輸出** — 對話資訊不足，要求使用者補充後重跑

---

## 觸發語句範例

下列任一句話應觸發本 skill：

- 「給我新 context 完整需要的 prompt」
- 「我要準備 compact 了，給我 compact 之後可以用的 prompt」
- 「幫我整理 handoff」
- 「這個對話太長了，幫我寫個接手 prompt」
- 「我要換機器繼續，給我接手用的 prompt」

---

## 設計原則

1. **自包含**：接手者不需要看到當前對話，光憑 prompt 就能繼續工作
2. **絕對路徑**：所有引用都用絕對路徑/branch 名/commit hash，禁止相對指涉
3. **動作明確**：「立即可執行的下一步」必須是可直接複製貼上的具體指令
4. **manifest 驗證**：產出後逐條比對，避免遺漏區塊
5. **不揣測**：對話中沒提到的事一律標 N/A，禁止補白
