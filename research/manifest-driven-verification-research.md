# Manifest-Driven Verification：學術/業界/社群研究綜述

> 研究日期：2026-04-06
> 目的：為全 12 個 skill 的交叉比對機制改進提供依據

## 問題

原有的「交叉比對」機制使用 free-form 驗證（讀取知識庫 → LLM 判斷是否完整），容易漏掉資料。需要找到有學術研究和業界標準支撐的改進方案。

## 學術研究

### RAG 完整性保證

- **Self-RAG**（arXiv:2310.11511, Asai et al., 2023）：引入 reflection tokens，模型在生成後對自身輸出打分。最接近「自我完整性驗證」的架構。
- **CRP-RAG**（Preprints.org, 2024）：以 perplexity 量化知識充分性，困惑度過高則觸發補充檢索。
- **Consistency-based Hallucination Detection**（arXiv:2511.12236, 2025）：對同一問題生成多個答案，利用一致性投票偵測幻覺。

### 關鍵洞見

學術界尚無公認的「RAG completeness guarantee」——現有方法均為啟發式（perplexity threshold、reflection token、self-consistency）而非形式化保證。

## 業界標準

### DAMA-DMBOK Completeness

八個資料品質維度中，**Completeness**（所有必要資料都必須可用）與 **Consistency**（跨系統保持一致）直接對應 agent 知識管理問題。

Data Quality Improvement Lifecycle：Define → Measure → Analyze → Improve → Control

### ITIL CMDB Reconciliation

業界最成熟的「多來源知識庫對齊」實作：
- 從多個來源取得資料，消除重複並統一格式
- 排程審計：跨雲端與資料中心持續交叉比對
- KPI 追蹤：freshness、completeness percentage、orphaned CI 數量

### 複式記帳類比

每筆交易必須在兩個帳目中同時記錄，強制產生內建完整性檢查。類比到知識管理：每個 claim 必須同時記錄「來源」與「被引用位置」。

## 社群共識

### Checklist-Driven > Free-Form（OpenAI Developer Community）

- Free-form（「你有漏掉什麼嗎？」）= 不可靠，LLM 傾向回答「沒有」
- Checklist-driven（「對照清單逐一確認」）= 更可靠
- 最佳實踐：先 free-form 生成，再 checklist 核查

### 窮舉的解決方案（按可靠性排序）

1. Metadata filtering + extraction（最可靠）
2. Iterative chaining
3. Index-time pre-computation
4. ReAct-style agents（最不可靠）

## 反面意見與陷阱

### 1. 偽置信（False Confidence）

自動驗證通過反而增加使用者對錯誤結果的信任。

### 2. 遞減回報

MetaGPT 實驗：72% 的 token 用於驗證階段。長 session 的 reflection 步驟效益遞減（arXiv:2509.09677）。

### 3. 驗證者與生成者同源

同一個 LLM 生成又驗證，系統性盲點無法被捕獲（arXiv:2509.18970）。

## 採用的改進策略

基於上述研究，選擇 **manifest-driven set difference** 作為核心改進方向：

1. 工作前建立「期望項目清單」（manifest）
2. 工作後用 grep/glob/count 做 set difference 比對
3. 差集 = 遺漏項，主動起草修正
4. 結構性工具（確定性）優先於 LLM 語意判斷（啟發式）

此策略結合了 DAMA-DMBOK 的 Completeness 定義、ITIL CMDB 的 Reconciliation 流程、以及社群對 checklist-driven 驗證的共識。
