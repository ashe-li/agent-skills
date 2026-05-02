---
name: learn-eval-deep
description: 深度 skill 品質驗證 — 對單一 learned skill 跑三系統客觀評估（結構審計 + 觸發品質 + 消融實驗），結果映射回 learn-eval 的 5 維度評分。適合在 /learn-eval 後手動確認品質。
allowed-tools: Bash, Read, Glob, Grep, AskUserQuestion
argument-hint: "[skill 路徑或名稱，留空則自動選最近儲存的 skill]"
---

# /learn-eval-deep — 深度 Skill 品質驗證

在 `/learn-eval` 給出自評分數後，用這個指令做客觀交叉驗證。

## Step 1: 定位 Skill

**有引數時：** 直接使用指定路徑或名稱。

若引數是名稱而非路徑，搜尋：
```bash
ls ~/.claude/skills/learned/*{引數}*.md 2>/dev/null
ls .claude/skills/learned/*{引數}*.md 2>/dev/null
```

**無引數時：** 找最近修改的 learned skill：
```bash
ls -t ~/.claude/skills/learned/*.md | head -1
```

確認目標後告知使用者：
> 目標：`{skill_name}` ({line_count} 行)

## Step 2: 選擇深度

提供三種模式讓使用者選：

> 選擇驗證深度：
> 1. **structural** — 結構分析（即時，0 tokens）
> 2. **trigger** — + 描述品質 + 觸發查詢生成（~6K tokens）
> 3. **full** — + 消融實驗，驗證 skill 是否真的有幫助（~18K tokens）
>
> 預設：1（直接按 Enter）

## Step 3: 執行 Bridge

```bash
python3 ~/Documents/skills-ecosystem-eval/src/learn_eval_bridge.py \
  "{skill_path}" --mode {mode}
```

如果 bridge 不存在，提示：
> Bridge 尚未安裝。請先 clone: `~/Documents/skills-ecosystem-eval/`

**Bridge 輸出完整性檢查（依據：arXiv:2509.18970 結構性驗證優先）：**

Bridge 執行後，在呈現分數前，用確定性工具驗證輸出包含所有預期欄位：

| 模式 | 必要欄位 | 驗證方式 |
|------|---------|---------|
| structural | `s3_score`, `code_score`, `density`, `section_score`, `redundancy_penalty` | 檢查 bridge stdout/JSON 鍵值存在 |
| trigger | + `s1_score`, `description_quality` | 同上 |
| full | + `s2_delta`, `with_skill_score`, `without_skill_score` | 同上 |

若任何必要欄位缺失，**停止計算並警告**：
```
[警告] Bridge 輸出缺少欄位：{缺失欄位列表}
分數計算不完整，映射結果不可靠。請確認 bridge 版本是否相容。
```

缺失欄位的對應維度分數標記為 `N/A`，不以 0 代入計算。

## Step 4: 呈現結果

### 三系統分數

以表格呈現原始分數：

| 系統 | 分數 | 說明 |
|------|------|------|
| System 3 (結構) | X/10 | 前置格式 + 章節 + 程式碼範例 + 內容密度 |
| System 1 (觸發) | X/10 | 描述品質（+ trigger F1 如有） |
| System 2 (消融) | delta | with_skill vs without_skill（full 模式才有） |

### learn-eval 5 維度映射

將三系統分數映射為 learn-eval 的 5 維度（1-5），與 learn-eval 自評並排顯示：

```
維度             客觀分數    learn-eval 閾值
─────────────────────────────────────────
Specificity      X.X/5      ≥ 3 ✓/✗
Actionability    X.X/5      ≥ 3 ✓/✗
Scope Fit        X.X/5      ≥ 3 ✓/✗
Non-redundancy   X.X/5      ≥ 3 ✓/✗
Coverage         X.X/5      ≥ 3 ✓/✗
─────────────────────────────────────────
總分             XX.X/25    Gate: PASS/FAIL
```

### 資料來源覆蓋率標註（依據：OpenAI Developer Community 共識 — Checklist-driven）

每個維度分數旁標註資料來源的可靠度：

| 維度 | 分數 | 資料覆蓋率說明 |
|------|------|--------------|
| Specificity | X.X/5 | S3 結構分析（確定性，1 次掃描）|
| Actionability | X.X/5 | S2 delta 基於 1 case × 1 run — 統計力有限，僅供 sanity check |
| Scope Fit | X.X/5 | S1 描述品質分析（LLM 語意判斷，補充性）|
| Non-redundancy | X.X/5 | S3 結構分析（確定性）|
| Coverage | X.X/5 | S3 結構分析（確定性）|

覆蓋率等級：
- **高**：確定性工具（grep/glob/AST），結果可重現
- **中**：LLM 語意判斷，結果可能因 prompt 變化而浮動
- **低**：單一 case 消融實驗，統計力不足以做強結論

### 標註重點

- 任何維度 < 3.0：明確標為 **[FAIL]** 並說明原因
- 冗餘度高：列出重疊的 skills 名稱
- 沒有程式碼範例：特別標註（最常見的扣分原因）
- 覆蓋率「低」的維度：加上 `[低統計力]` 標記，避免誤判

## Step 5: 建議動作

根據結果給出具體建議：

| 結果 | 建議 | 驗證信心度 |
|------|------|-----------|
| Gate PASS + 無 issue | 品質確認，無需動作 | 取決於使用的模式（structural 最低，full 最高）|
| Gate PASS + 有 issue | 列出改善項（如：加 code example） | 中（LLM 語意判斷補充）|
| Gate FAIL | 列出必要修正，問使用者是否要現在修 | 高（結構性驗證）|
| 冗餘度高 | 建議合併或差異化，列出重疊 skill | 高（grep 確定性搜尋）|
| delta ≤ 0 (full 模式) | skill 可能沒有實際幫助，建議檢視內容是否已被模型的 parametric knowledge 覆蓋 | 低 [低統計力] — 1 case 消融僅供方向參考，不宜直接退役 |

**驗證信心度摘要（依據：arXiv:2509.18970）：**
- structural 模式：高（純結構分析，可重現）
- trigger 模式：中（LLM 語意判斷，需人工確認）
- full 模式：中低（消融 1 case，需多次重跑才有統計意義）

**如果使用者同意修改：** 直接讀取 skill 檔案，根據建議進行修改（加 code examples、改 description 等），修改後重跑 bridge 驗證。

## 映射公式

Bridge 將三系統分數轉為 learn-eval 5 維度的邏輯：

| 維度 | 來源 | 計算 |
|------|------|------|
| Specificity | S3 code_score + density | 1 + (code/10)×2 + (density/10)×2 |
| Actionability | S2 delta + S3 structural | 3 + delta×2 + (structural-5)×0.2 |
| Scope Fit | S1 description_quality | 1 + (desc_quality/10)×4 |
| Non-redundancy | S3 redundancy_penalty | 5 - penalty |
| Coverage | S3 section_score + density | 1 + (section/10)×2 + (density/10)×2 |

（所有值 clamp 到 1.0-5.0）

## 限制

- `structural` 模式只看檔案結構，不呼叫 LLM
- `trigger` 模式生成查詢但不實際跑觸發測試（需要 `run_eval.py` 的 `.claude/commands/` 機制）
- `full` 模式的消融只跑 1 case × 1 run（統計力有限，但夠做 sanity check）
- Active agents（`~/.claude/agents/`）的結構和 learned skills 不同，部分維度不適用
