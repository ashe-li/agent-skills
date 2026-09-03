---
name: dispatch-loop
description: 主模型指揮官委派迴圈 — /plan-run 下逐 step 派工 Agent/teammate、抽查驗收、狀態機回報、回收前 KB gate 的標準操作模式。適用於依 plan 推進多 step 實作、需要控管 token 與模型額度的場景。
allowed-tools: Bash, Read, Agent, SendMessage, AskUserQuestion, TaskCreate, TaskUpdate, TaskList
argument-hint: [可選：plan 路徑或當前 step 描述]
redundancy-peers: [plan-run]
---

# /dispatch-loop — 委派推進迴圈

來源：2026-07-10 simpleinfo-f2e session 實證有效的互動模式（28 steps、~5M subagent tokens 完整走完 Phase 0–5）。核心：**主模型不下場，只指揮、裁決、抽查**。

## 角色分工

- **主模型**：讀 plan 狀態機輸出、寫派工 prompt、抽查回報、視覺/設計裁決、HITL、稀缺配額 MCP 呼叫（單次、落檔後派工）
- **Sonnet agent**：一般實作、研究、機械改檔、文件整理
- **Opus agent**：複雜實作、深度審查、困難除錯
- Agent tool **明帶 `model` 參數**，不讓 agent 繼承主模型——繼承等於每隻 agent 都吃主模型額度

## 迴圈步驟（每個 plan step）

1. `plan_runner.py start <plan> <step> --task-id=<id>`（TaskCreate/TaskUpdate 同步，best-effort）
   - 該腳本在 `/plan-run` skill 的 **repo checkout** 底下；`npx skills` 快照只同步 `SKILL.md`，不會帶下 `scripts/`
2. **派工**：用下方「派工 prompt 骨架」；prompt 必含目標/動機/範圍/驗收條件/回報格式；設計稿等大素材給**檔案路徑**不貼內容
3. **同 working tree 的 step 循序派**（共改同檔、共用 lockfile 會衝突）；真正獨立且不改檔才並行
4. **抽查回報**（不信任自報）：檔案存在 `ls`、關鍵內容 `grep`、`git log` 比對、測試**親跑**一次；抽查過才 `complete`，疑點退回或補驗
5. `plan_runner.py complete/fail` → 讀 delta output 進下一 step
6. **回收前 KB gate**：agent 回報收畢、標 completed 前，把該路產出摘要/教訓寫進 session 檔（`_pending/`）；教訓成熟的落 `wiki/learned/`。順序固定：記錄 → 回收，不可反過來

## 派工 prompt 骨架

每份派工填滿這六格，缺一格就先別發：

```
目標：{做什麼，一句話可判定}
動機：{為什麼要做、這結果拿去幹嘛}
範圍：只動 {檔案/目錄}；不碰 {明確排除項}
既有慣例：先讀 {參考檔案}，比照命名、import、錯誤處理、註解密度
驗收條件：
- {具體可判定條件，例：`pnpm test` 全綠、新增測試覆蓋 {行為清單}}
- build 通過（{指令}）
回報格式：
- 改了哪些檔（路徑:行號範圍）＋每檔一句話
- 驗收條件逐條標 PASS/FAIL，附指令輸出關鍵行（不是「應該會過」）
- 禁止流水帳；全文 ≤30 行
```

型態微調：

- **搜尋定位**：agent `Explore`、model `haiku`（範圍模糊或跨多命名慣例升 `sonnet`）；要求每筆附 `檔案:行號`，並列出試過的搜尋模式讓人判斷有沒有漏
- **實作**：agent `general-purpose`、model `sonnet`；驗收條件必含測試與 build 指令
- **重構/批次改檔**：同上（模式已定案的機械套用可降 `haiku`）；變換規則逐字列出、明寫「不順手修別的問題，看到疑似 bug 記下來回報」；會與其他 agent 並行改檔時加 `isolation: "worktree"`
- **研究查證**：model `sonnet`；來源優先序 官方文件 > 原始碼 > issue/changelog > 部落格，每個宣稱附出處，查不到明寫「查無」不推測補洞
- **審查驗收**：審查者必須是**沒參與實作的 fresh context**；開頭寫死「你是驗收者，預設懷疑，產出是不符合驗收條件之處，不是背書」，收尾要一行總判定 ACCEPT / REJECT

通用規則：

- 槽位不確定的，寧可多寫一句動機，不要留空
- 派工同時想好「我要怎麼驗它的回報」；**驗法不明的派工先不要發**
- 互相獨立的多個派工在同一則訊息並行發出
- 派工 prompt **明寫「親自執行，禁止再委派」**（原因見下方實證教訓）

派工後主模型的義務：收到回報先驗格式（驗收條件有無逐條 PASS/FAIL、宣稱有無出處），缺就退回重報不腦補；涉及 commit 的親眼 `git diff` 比對；失敗的派工記下軌跡（prompt + 輸出 + fail 原因）再升級。

## Token 紀律（本 skill 的存在理由）

- plan 內每 step 標【預期 agent 數 × 模型層級 × 預估 token】，phase 小計 + 全 plan 總計
- 單 step 超預算 2 倍 → 停下重估，不加派補洞
- review 關卡按「風險 × 不可逆性」裁剪：低風險 step 單一驗證即可，只有核心邏輯/不可逆操作才疊多重審查
- 量級參考（2026-07-10 實測，小型 landing page 專案）：
  - 單一實作 step ≈ 60–130K subagent tokens
  - headed 驗證 step ≈ 100–200K
  - **30-agent 編隊 code review ≈ 1.6M**——對小 repo 過度設計，約等於全部實作 step 總和的一半
  - repo <5K 行時，review 用單一 reviewer agent 或 ≤4 角度小編隊即可
- 追問既有 agent 用 SendMessage（附 agentId），不重派

## 實證教訓（本模式下的已知坑）

- 派工 prompt 明寫「親自執行，禁止再委派」：general-purpose agent 可能把任務轉派給自己 spawn 的背景 agent 後直接結束——該背景 agent 成為孤兒（完成通知回不到主對話），任務靜默蒸發
- 主模型視覺裁決不可用縮圖：先放大 3 倍 + 像素取樣再下結論（Figma 深色畫布會被誤讀成 UI 元素）；agent 量測與主觀印象衝突時，用 live 取樣裁決、不硬拗
- jsdom 測不到的真實回歸存在（pointer capture 案）：UI 行為改動必補 headed 驗證，unit 綠 ≠ 行為對
- subagent 回報會漂亮但需抽查（驗證不自驗）；headed 驗證類 step 產物截圖要留 scratchpad 供下游 step 用
- compaction 後素材遺失：先撈 session transcript jsonl（舊 tool result grep、使用者貼圖 base64 還原），再考慮重新取得
- 稀缺配額 MCP（如 Figma Starter）呼叫不下放 agent——agent 可能重試多呼叫；主對話單次呼叫、產物落檔後派工
