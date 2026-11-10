---
name: dispatch-loop
description: 主模型指揮官委派迴圈 — /plan-run 下逐 step 派工 Agent/teammate、抽查驗收、狀態機回報、回收前 KB gate 的標準操作模式。適用於依 plan 推進多 step 實作、需要控管 token 與模型額度的場景。
allowed-tools: Bash, Read, Agent, SendMessage, AskUserQuestion, TaskCreate, TaskUpdate, TaskList
argument-hint: [可選：plan 路徑或當前 step 描述]
redundancy-peers: [plan-run]
---

# /dispatch-loop — 委派推進迴圈

來源：2026-07-10 client-f2e session 實證有效的互動模式（28 steps、~5M subagent tokens 完整走完 Phase 0–5）。核心：**主模型不下場，只指揮、裁決、抽查**。

## 角色分工

- **主模型**：讀 plan 狀態機輸出、寫派工 prompt、抽查回報、視覺/設計裁決、HITL、稀缺配額 MCP 呼叫（單次、落檔後派工）
- **Sonnet agent**：範圍明確的實作（bug fix、照 spec 做的 feature）、研究、機械改檔、文件整理
- **Opus agent**：複雜實作、深度審查、困難除錯；spec 清楚但本身困難的 step（例如遷移、效能最佳化）也歸這層——spec 清楚不等於該用 Sonnet，其中重大的遷移或重構用最高一級的模型（Fable）。不確定歸哪一層時從 Opus 起
- **Haiku agent**：只接「錯了很容易看出來」的工作——找檔、跑測試、讀 log、分類分診。不接長的 agentic loop，也不當 code 編輯的預設路由
- Agent tool **明帶 `model` 參數**，不讓 agent 繼承主模型——繼承等於每隻 agent 都吃主模型額度。內建 `Explore` 也一樣：不帶 `model` 時跟著 session 的模型走，不是固定 Haiku（主對話是 Fable 時，Anthropic 訂閱、Console 帳號或 LLM gateway 連線會落在 Opus，Bedrock、Google Cloud、Foundry 等平台仍是 Fable）
- `effort` 預設不帶，沿用模型預設值；要調的時機與順序見下方「升級階梯」。逐次呼叫帶 `effort` 需要 Claude Code v2.1.292 以上，fork 型 subagent 不適用

## 迴圈步驟（每個 plan step）

1. `plan_runner.py start <plan> <step> --task-id=<id>`（TaskCreate/TaskUpdate 同步，best-effort）
   - 該腳本在 `/plan-run` skill 的 **repo checkout** 底下；`npx skills` 快照只同步 `SKILL.md`，不會帶下 `scripts/`
2. **派工**：用下方「派工 prompt 骨架」；prompt 必含目標/動機/範圍/驗收條件/回報格式；設計稿等大素材給**檔案路徑**不貼內容
3. **同 working tree 的 step 循序派**（共改同檔、共用 lockfile 會衝突）；真正獨立且不改檔才並行
4. **抽查回報**（不信任自報）：檔案存在 `ls`、關鍵內容 `grep`、`git log` 比對、測試**親跑**一次；抽查過才 `complete`，疑點退回或補驗
5. **判斷要不要 complete**：抽查過關就往下走第 6 步；不過關 `plan_runner.py fail <plan> <step> --reason=...`，退回派工或補驗
6. **回收前 KB gate**：agent 回報收畢，回收前依序完成兩件事，順序固定不可反過來：
   (a) **教訓**寫進 KB——session 檔（`_pending/`），教訓成熟的落 `wiki/learned/`；
   (b) **摘要**用 `plan_runner.py complete <plan> <step> --summary="..." --evidence=<路徑> ...` 寫進 state——這一個指令同時完成記錄與標記完成，寫法見下方「摘要撰寫指引」

### 摘要撰寫指引

`--summary` 四項依序必寫，沒有內容也要寫「無」，不得省略：

1. 做了什麼（結果，不是流水帳）
2. 偏離 plan 原文的地方和理由（例：「Action 寫 vitest，實際沿用 repo 既有的 jest」）
3. 接受的副作用或已知限制
4. 延後到其他 step 的待辦（標明目標 step ID）

上限 500 字元，正規化後超過會被拒絕（rc=1，不截斷），要自己縮短再送一次；逐字證據放 `--evidence=<路徑>`（可重複，不逗號切分），不要塞進摘要本文。摘要**不會**出現在後續的 delta output 或 Stop hook reason，不需要為了省 token 刻意寫短到丟資訊——尤其是第 2、4 項，丟了就等於白寫。收到 `locked` 錯誤就重跑同一個指令，不要換寫法或跳過。

`/plan-run` 下 `init` 一律帶 `--require-summary`，摘要必填：`next`／hook 印出的 `ok:` 那行帶的是佔位字串 `--summary="<1.做了什麼 2.偏離plan 3.副作用 4.延後待辦>"`，只是提醒格式，不能照抄送出——必須換成這一步實際的四項內容，否則會被拒絕（rc=1）。

範例：

```
plan_runner.py complete plans/active/foo.md S3.2 \
  --summary="1. 改寫 dispatch-loop 第 6 步與新增摘要撰寫指引；2. 無偏離；3. 無已知限制；4. README 其餘 plan-run 段落留給後續 step" \
  --evidence=dispatch-loop/SKILL.md --evidence=README.md
```

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
- **重構/批次改檔**：同上，model 維持 `sonnet`；變換規則逐字列出、明寫「不順手修別的問題，看到疑似 bug 記下來回報」；會與其他 agent 並行改檔時加 `isolation: "worktree"`。要降 `haiku` 得三個條件都成立：規則已逐字列出、結果能用一條指令全數判定（grep 計數、測試）、批次短
- **研究查證**：model `sonnet`；來源優先序 官方文件 > 原始碼 > issue/changelog > 部落格，每個宣稱附出處，查不到明寫「查無」不推測補洞
- **審查驗收**：審查者必須是**沒參與實作的 fresh context**；開頭寫死「你是驗收者，預設懷疑，產出是不符合驗收條件之處，不是背書」，收尾要一行總判定 ACCEPT / REJECT

通用規則：

- 槽位不確定的，寧可多寫一句動機，不要留空
- 派工同時想好「我要怎麼驗它的回報」；**驗法不明的派工先不要發**
- 互相獨立的多個派工在同一則訊息並行發出
- 派工 prompt **明寫「親自執行，禁止再委派」**（原因見下方實證教訓）
- 派工 prompt **不得包含 merge 進 prod 等價分支的動作**（例如會部署到 prod 的 `hotfix` 分支）；agent 只備好 PR 並回報，merge 由人執行（見 [`rules/merge-by-human-preview-first.md`](../rules/merge-by-human-preview-first.md)）

派工後主模型的義務：收到回報先驗格式（驗收條件有無逐條 PASS/FAIL、宣稱有無出處），缺就退回重報不腦補；涉及 commit 的親眼 `git diff` 比對；失敗的派工記下軌跡（prompt + 輸出 + fail 原因）再升級。

## 升級階梯（派工結果不夠好時）

照順序走：

1. **先補自我驗證**：派工 prompt 有沒有給 agent 一條它自己能跑的檢查（測試、build、打 endpoint 的 script）？沒有就補上，同模型、同 `effort` 重派。這一步通常比調 `effort` 省，延遲也低
2. **`effort` 升一級**（Agent tool 的 `effort` 參數），模型不變。適用在 agent 需要想更久、多呼叫工具、多檢查自己產出的情況。缺的是知識或能力時，加 `effort` 補不起來，直接走第 3 階
3. **升一級模型，`effort` 從比原本低的等級起跳**。已經到 `high` 還不夠時，先試這一階再考慮 `xhigh`／`max`——過了 `high` 再加一級的花費可能跟換上一級模型一樣，兩種做法都要在自己的任務上比過

階梯決定的是「下一次重派要改什麼」，不是多給重試次數：同一路連錯兩次照 [`rules/teammate-fleet.md`](../rules/teammate-fleet.md) 停下重估，升模型是重估後的選項之一，不加派補洞。

量級參考（Opus 5.5 在 SWE-bench Pro，[Anthropic 2026-11-09 文章](https://claude.dev/blog/which-claude-model-should-you-build-with/)）：`medium` 比 `high` 低約 2.5 分、成本約 70%；`low` 低約 8 分、成本約三分之一；`xhigh` 高約 1.4 分、成本是 `high` 的 2.5 倍。這是別人任務上的數字，只用來決定從哪裡起跳。

## Token 紀律（本 skill 的存在理由）

- plan 內每 step 標【預期 agent 數 × 模型層級 × 複雜度量級（小／中／大）】，不寫具體 token 數字；量級判準見 [`rules/teammate-fleet.md`](../rules/teammate-fleet.md)；`effort` 不是預設值時一併標出
- 單 step 實際跑成比預估高一級的形狀 → 停下重估，不加派補洞
- review 關卡按「風險 × 不可逆性」裁剪：低風險 step 單一驗證即可，只有核心邏輯/不可逆操作才疊多重審查
- 量級參考（2026-07-10 實測，小型 landing page 專案）：實作 step 屬「中」、headed 驗證屬「大」；**30-agent 編隊 code review 約等於全部實作 step 總和的一半**——對小 repo 過度設計，repo <5K 行時 review 用單一 reviewer agent 或 ≤4 角度小編隊即可
- 追問既有 agent 用 SendMessage（附 agentId），不重派
- 產出能自動判定的批次 step（測試、row count、grep 計數）：先全部用 `low` 跑，只把沒過的用 `high` 重跑。文章同一組 SWE-bench Pro 子集上，Opus 5.5 這樣跑通過約 97%、每題約 $0.17；全部用 `high` 跑通過 95.3%、每題 $0.29
- 主對話中途換模型有代價：cache 屬於寫入它的模型，換過去第一輪要對整段對話重付 cache write；往下換還會丟掉先前的 reasoning。要換就先 `/compact`，或用 `/handoff` 帶一份短計畫開新 session

## 何時不用本模式

- **一條相依的步驟鏈，或一個 context window 裝得下的工作**：交給一隻 agent 做完，不逐 step 拆派。文章談 multiagent orchestration 時的說法是，這兩種情況下拆工與合併的成本高過省下的
- **路由配置沒量過就不要當成定案**：多模型配置只有在贏過每一個單模型做法時才值得留。量法——抽幾個真實做過的 step，各配一個結果檢查（測試通過、列數正確）；每個候選模型跑兩到三個 `effort`，各開獨立 session 避免共用 cache；記每個完成任務的成本，把分數對花費排出來比。快速版：同一個 backlog 任務用 `/model` 在兩個模型上各跑一次，比 `/usage` 的回報。本機歷史用量可從 `~/.claude/projects/*/*/subagents/*.jsonl` 的 usage 撈。本模式把 code 編輯放在 Sonnet／Opus agent；文章的預設是編輯留在主模型，並點名 `opusplan`（Opus 規劃、Sonnet 執行）這類配置要先量過再當預設

## 實證教訓（本模式下的已知坑）

- 派工 prompt 明寫「親自執行，禁止再委派」：general-purpose agent 可能把任務轉派給自己 spawn 的背景 agent 後直接結束——該背景 agent 成為孤兒（完成通知回不到主對話），任務靜默蒸發
- 主模型視覺裁決不可用縮圖：先放大 3 倍 + 像素取樣再下結論（Figma 深色畫布會被誤讀成 UI 元素）；agent 量測與主觀印象衝突時，用 live 取樣裁決、不硬拗
- jsdom 測不到的真實回歸存在（pointer capture 案）：UI 行為改動必補 headed 驗證，unit 綠 ≠ 行為對
- subagent 回報會漂亮但需抽查（驗證不自驗）；headed 驗證類 step 產物截圖要留 scratchpad 供下游 step 用
- compaction 後素材遺失：先撈 session transcript jsonl（舊 tool result grep、使用者貼圖 base64 還原），再考慮重新取得
- 稀缺配額 MCP（如 Figma Starter）呼叫不下放 agent——agent 可能重試多呼叫；主對話單次呼叫、產物落檔後派工
