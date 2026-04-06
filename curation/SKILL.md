---
name: curation
description: Retrospective batch cleanup — 修復 learned skills 的格式缺陷（malformed frontmatter、legacy 評分格式），HITL 確認後刪除已廢棄項目。不重新評分、不提取新 patterns。
allowed-tools: Bash, Read, Glob, Grep, Edit, Write, AskUserQuestion
---

# /curation — Learned Skills 品質管控

按需執行的維護工具，用於清理和標準化 `~/.claude/skills/learned/` 中累積的 learned skills。不綁定任何自動化流程，使用者想整理時手動呼叫。

## Role: Retrospective Format Remediation

修復**既有** learned skills 的格式漂移。與 `/update` Step 4（在**新寫入**時強制格式）互補，非替代。

| 時機 | 工具 |
|------|------|
| 批量修復既有 learned skills | `/curation` |
| 新 skill 寫入時格式把關 | `/update` Step 4 (learn-eval) |

## Step 1: 掃描 learned skills

掃描 `~/.claude/skills/learned/` 目錄（含子目錄），對每個 .md 檔案收集：

| 欄位 | 來源 |
|------|------|
| slug（檔名） | 檔案名稱 |
| 有無 frontmatter | 檢查是否以 `---` 開頭 |
| frontmatter 完整度 | 是否包含 name/description/user-invocable/origin |
| 品質評分格式 | 表格 vs 單行 vs 無 |
| 最後修改日 | 檔案 mtime |
| 檔案大小 | bytes |

輸出統計表：

```
Learned Skills 掃描結果：
- 總數：XX 個
- 有 frontmatter：XX 個
- 無 frontmatter：XX 個
- 評分格式分布：表格 XX / 單行 XX / 無 XX
```

**建立問題 manifest（依據：DAMA-DMBOK Completeness）：**

掃描結束後，將每個有問題的檔案登錄至記憶體中的 manifest，格式如下：

| 檔案 slug | 問題類型 | 預期修正動作 | 狀態 |
|-----------|---------|------------|------|
| xxx.md | fmt-missing-frontmatter | 插入推斷的 frontmatter | PENDING |
| yyy.md | fmt-score-inconsistent | 轉換評分表格 | PENDING |

Manifest 為後續 Step 4 驗證的基準，**總問題數 = manifest 列數**。

## Step 2: 分類問題

將掃描結果分為四類：

| 問題類型 | 判斷標準 | 可自動修正 |
|----------|---------|-----------|
| `fmt-missing-frontmatter` | 檔案不以 `---` 開頭 | Yes — 從 H1 標題推斷 name，從第一段推斷 description |
| `fmt-incomplete-frontmatter` | 有 frontmatter 但缺少必要欄位 | Yes — 補全缺少的欄位 |
| `fmt-score-inconsistent` | 品質評分使用單行格式（`specificity=4, ...`）而非表格 | Yes — 轉換為 5 維度表格 |
| `deprecated-marked` | 在索引檔案（如 `~/.claude/skills/learned/index.md` 或目錄下的 README.md）中被 `~~strikethrough~~` 標記但 .md 檔案仍存在。若無索引檔案，跳過此分類並在報告中註明 | HITL — 確認後刪除 |

**明確不做：**

- 不自動合併「相似」的 skills（相似度判斷主觀，需人工決定）
- 不重新評分現有 skills（現有分數是提取時的判斷，有歷史價值）

## Step 3: HITL 確認（批次）

用 AskUserQuestion 展示分類結果：

```
Learned Skills 品質掃描結果：

總數：XX 個
格式問題：
  - 無 frontmatter：XX 個
  - frontmatter 不完整：XX 個
  - 評分格式不統一：XX 個
廢棄標記：
  - 已標記廢棄但檔案仍存在：XX 個

建議操作：
1. 全部自動修正（格式問題 + 刪除廢棄）
2. 只修正格式，廢棄項目逐一確認
3. 逐一確認所有修正
4. 只查看清單，不修正
```

## Step 4: 執行修正

根據使用者選擇執行：

**格式修正（自動）：**

- 無 frontmatter → 從內容推斷並插入：
  - `name`：從 H1 標題轉 kebab-case
  - `description`：取第一段的第一句話
  - `user-invocable: false`
  - `origin: auto-extracted`
- 不完整 frontmatter → 補全缺少欄位
- 評分格式轉換：`specificity=4, actionability=5, ...` → 5 維度表格（保留原有數值，將 `specificity=4` 對應填入 specificity 列的「分數 (1-5)」欄）

  | 維度 | 分數 (1-5) | 說明 |
  |------|-----------|------|
  | specificity | | 知識的具體程度 |
  | actionability | | 可直接應用的程度 |
  | scope fit | | 適用範圍是否恰當 |
  | non-redundancy | | 與現有 skills 的重複度 |
  | coverage | | 涵蓋場景的完整度 |

**廢棄清理（HITL）：**

- 對每個廢棄標記的 skill，展示名稱和描述
- 使用者確認後刪除 .md 檔案
- 同步更新索引檔案（移除 strikethrough 條目）

**修正後驗證（依據：ITIL CMDB Reconciliation）：**

每個修正動作執行後，立即執行確定性驗證：

- `fmt-missing-frontmatter` 修正後：`grep -c "^---" <file>` 確認 ≥ 2（開頭與結尾各一個 `---`）
- `fmt-incomplete-frontmatter` 修正後：`grep -E "^(name|description|user-invocable|origin):" <file>` 確認 4 個欄位全部存在
- `fmt-score-inconsistent` 修正後：`grep -c "| specificity |" <file>` 確認 ≥ 1（表格列存在）

每個驗證完成後更新 manifest 的「狀態」欄：
- 驗證通過 → `DONE`
- 驗證失敗 → `FAILED`（保留在 manifest 中，不標記為完成）

## Step 5: 輸出報告

```markdown
## /curation 執行結果

### 統計
- 掃描：XX 個 skills
- 修正格式：XX 個
- 刪除廢棄：XX 個
- 跳過：XX 個

### 修正明細
| Skill | 問題 | 動作 |
|-------|------|------|
| xxx.md | 無 frontmatter | 已補全 |
| yyy.md | 評分格式 | 已轉換為表格 |
| zzz.md | 已廢棄 | 已刪除 |

### Manifest 比對結果（依據：DAMA-DMBOK Completeness）
- Manifest 總問題數：XX 個
- 已修正（DONE）：XX 個
- 修正失敗（FAILED）：XX 個
- 使用者跳過：XX 個
- 完成率：XX / XX = XX%

<!-- 完成率 < 100% 時，列出未完成項目及原因 -->

### 剩餘問題
<!-- 若有使用者跳過或 FAILED 的項目，列出 manifest 中狀態非 DONE 的列 -->
```
