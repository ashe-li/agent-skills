---
name: evidence-check
description: "獨立證據查驗 — 對技術決策或做法進行四維度並行調查（學術研究、業界標準、最佳實踐、社群共識+反面意見），偵測跨來源衝突，輸出可復用的結構化報告。不同於 /design 和 /assist 的 inline 驗證，本 skill 是事後獨立深度調查。"
allowed-tools: Read, Glob, Grep, Write, Agent, AskUserQuestion
argument-hint: "<技術決策或做法的描述>"
redundancy-peers: [design, assist]
---

# /evidence-check — 獨立證據查驗（不同於 /design 的 inline 驗證，本 skill 是獨立深度調查）

對單一技術主張(claim)從 4 個維度做獨立原始研究，與 /design 的 inline 驗證互補：
- /design：計畫建立過程中的 inline 驗證(驗證計畫中已有的引述是否充分)
- /evidence-check：事後獨立深度調查(對任意主張做原始多來源研究，不依賴既有 pipeline)

> 方法論依據： EBSE 四類證據分類(Kitchenham et al., 2004)、ITIL CMDB Reconciliation、DAMA-DMBOK Completeness

## Step 0: 成本確認(HITL)

本 skill 啟動 2 個並行 subagent 做深度調查，token 消耗較高。

使用 AskUserQuestion 詢問：

> 即將啟動 4 維度並行調查(2 subagents)。
> 預估額外 token 消耗：約 16,000-30,000 tokens。
> 調查對象：[摘要自 $ARGUMENTS]
>
> 此工具為獨立深度調查，不同於 /design 和 /assist 中的 inline 驗證。
>
> 1. 繼續 — 啟動調查
> 2. 取消 — 結束流程

若使用者選擇取消，結束流程。

## Step 1: 解析主張與維度 Manifest

### 1a. 標準化主張

從 $ARGUMENTS 提取被驗證的技術主張，重述為一句話：

> 被驗證主張： [標準化後的技術主張]

### 1b. 建立維度 Manifest

在 subagent 啟動前建立期望項目清單(DAMA-DMBOK Completeness pattern)：

| 維度 | 調查範圍 | 狀態 |
|------|---------|------|
| D1: 學術研究 | 相關論文、benchmark、實驗數據(arXiv、IEEE、ACM) | PENDING |
| D2: 業界標準 | RFC、W3C、OWASP、ITIL、NIST 等標準文件 | PENDING |
| D3: 最佳實踐 | 官方指引、知名 library canonical pattern、生產案例 | PENDING |
| D4: 社群共識+反面意見 | GitHub discussions、SO、Reddit、官方 forum | PENDING |

## Step 2: 組裝 Subagent Context

依 EBSE 證據分類(Kitchenham et al., 2004)，將 4 維度分為 2 組：

- Formal Sources(A 組)： D1 學術 + D2 業界標準 — 正式出版或標準化的來源
- Practitioner Sources(B 組)： D3 最佳實踐 + D4 社群 — 實踐者經驗與群體智慧

Subagent A prompt(Formal Sources)：

```
你是一個證據評估助理。請對以下主張搜集正式來源的證據。

主張：{CLAIM}

D1 學術研究：
- 使用 WebSearch 搜尋學術文獻(arXiv、IEEE、ACM、Google Scholar)
- 回報：論文標題、ID/DOI、年份、關鍵發現、與主張的相關性
- 若無學術文獻，明確聲明 "NOT-FOUND"，不可用不相關論文填充

D2 業界標準：
- 搜尋 RFC、OWASP、W3C、NIST、ITIL、12-Factor 等標準
- 回報：標準名稱、版本、章節、如何適用

每個維度須回傳：
- status: FOUND | NOT-FOUND | PARTIAL
- confidence: HIGH | MEDIUM | LOW(LOW = 僅間接相關，HIGH = 直接引用主張)
- sources: [來源清單，含 URL 或 DOI]
- summary: 2-3 句摘要
```

Subagent B prompt(Practitioner Sources)：

```
你是一個證據評估助理。請對以下主張搜集實踐者來源的證據。

主張：{CLAIM}

D3 最佳實踐：
- 搜尋官方文件、canonical pattern、生產案例
- 回報：來源、版本、pattern 描述

D4 社群共識+反面意見：
- 搜尋 GitHub discussions、Stack Overflow(高票答案)、Reddit、官方 forum
- 特別搜尋反面意見：「X considered harmful」、migration guides(說明為何放棄某做法)
- 回報：來源 URL、票數/星數、立場(FOR / AGAINST / NUANCED)、核心論點

每個維度須回傳：
- status: FOUND | NOT-FOUND | PARTIAL
- confidence: HIGH | MEDIUM | LOW
- sources: [來源清單，含 URL]
- summary: 2-3 句摘要
```

## Step 3: 並行啟動 Subagent

同時啟動 2 個 subagent，兩個 Agent() 呼叫放在同一個 message 中以實現並行：

```
Agent(subagent_type="general-purpose", model="haiku")
— prompt: Subagent A context(D1 學術 + D2 業界標準)

Agent(subagent_type="general-purpose", model="haiku")
— prompt: Subagent B context(D3 最佳實踐 + D4 社群共識)
```

合併規則：

1. 等待兩個 subagent 完成
2. 將 4 個維度的結果合併為統一維度表
3. 若 subagent 回傳格式不符預期，將該維度標記為 NO-DATA(不中斷流程)
4. 更新 Step 1b 的維度 Manifest 狀態欄(PENDING -> FOUND / NOT-FOUND / PARTIAL / NO-DATA)

## Step 4: 衝突偵測與 Verdict

### 4a. 跨維度衝突分析

從合併結果中識別主張的各個面向，對每個面向比對 4 維度的立場：

| 衝突點 | D1 學術 | D2 業界標準 | D3 最佳實踐 | D4 社群 | 判定 |
|--------|---------|-----------|-----------|---------|------|
| [主張面向 A] | ... | ... | ... | ... | AGREE / PARTIAL / CONFLICT / NO-DATA |
| [主張面向 B] | ... | ... | ... | ... | ... |

判定定義(依據 ITIL CMDB Reconciliation)：

- AGREE：所有有資料的維度指向相同方向
- PARTIAL：多數同意但有一個維度提出保留意見或缺少資料
- CONFLICT：至少兩個維度主動矛盾
- NO-DATA：少於兩個維度有該面向的資料

對每個 CONFLICT 判定，撰寫衝突說明：
1. 衝突內容是什麼
2. 哪個維度的證據較強(根據時效性、引用數、官方地位)
3. 此衝突是已知辯論還是情境依賴

### 4b. 維度摘要

| 維度 | 覆蓋狀態 | 信心度 | 主要依據 |
|------|---------|--------|---------|
| D1 學術研究 | FOUND / NOT-FOUND / PARTIAL | HIGH / MEDIUM / LOW | [top source] |
| D2 業界標準 | ... | ... | ... |
| D3 最佳實踐 | ... | ... | ... |
| D4 社群共識 | ... | ... | ... |

### 4c. 整體 Verdict

根據維度覆蓋和衝突分析，判定為以下之一：

| Verdict | 條件 |
|---------|------|
| WELL-SUPPORTED | 3-4 維 FOUND + 衝突表全為 AGREE 或 PARTIAL |
| CONDITIONALLY-SUPPORTED | 2+ 維 FOUND，至少一個 PARTIAL，無 CONFLICT |
| WEAKLY-SUPPORTED | 僅 1 維 FOUND 或多數 NOT-FOUND |
| CONFLICTED | 衝突表有至少一個 CONFLICT 判定 |
| INSUFFICIENT-EVIDENCE | 3+ 維 NOT-FOUND 或 NO-DATA |

> 免責聲明： WELL-SUPPORTED 不代表絕對正確 -- subagent 使用同一基底模型，可能存在系統性盲點(arXiv:2509.18970)。請自行驗證關鍵來源的 URL 和引述。

### 4d. /design 相容輸出

生成可直接貼入 /design plan 的表格：

Industry & Standards Reference：

| 技術決策 | 參照依據 | 類型 | 來源 |
|----------|---------|------|------|
| [主張] | [D1/D2 來源] | 學術研究 / 業界標準 | [URL/DOI] |

Community Consensus & Dissenting Views：

| 技術決策 | 社群共識 | 反面意見/已知陷阱 | 來源 |
|----------|---------|------------------|------|
| [主張] | [D4 FOR 立場摘要] | [D4 AGAINST 立場摘要] | [URL] |

### 4e. 報告持久化(可選)

使用 AskUserQuestion 詢問：

> 是否將完整報告寫入 plans/active/evidence-<slug>.md？
>
> 1. 寫入 — 持久化供後續參考
> 2. 不寫入 — 僅顯示在對話中

Slug 生成規則：從主張文字取 kebab-case，前綴 evidence-，最多 60 字元。
