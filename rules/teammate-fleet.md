# Teammate Fleet Delegation（Teammate 編隊委派）

Scope: Apply when a task can be decomposed into ≥2 independent investigation/production tracks that can run as parallel background subagents (teammates).

## HITL 啟用閘門

預計並行 ≥2 個背景 teammate 時，先用 AskUserQuestion 問是否啟用編隊模式：

- 附 token 預估（每路約略估算＋總和）
- 第一選項「啟用 (Recommended)」
- 單路派工不問逕行；本 session 已同意過一次 → 同 session 後續同類 fan-out 不再問

## 編隊守則

- **背景為預設**：Agent 預設背景執行、完成時 harness 自動通知主對話。只有「沒這個結果就走不下去」的派工才同步等（`run_in_background: false`）。
- **派出即繼續**：派出後不空等，回頭做與派工**不重疊**的 inline 小事（≤3 檔快查、讀 PR comment、跑單條指令）；派出去的調查不要自己同時做一遍。
- **具名 + 續問**：派工時用 `name` 取名（例：`scan-vocus-styles`）；追問、補需求 → SendMessage 續用同一 teammate 的 context，不重派新 agent 從零讀起。
- **等待不輪詢**：禁止 sleep / 迴圈 poll；完成通知是主訊號，保險用長間隔喚醒（≥20 分鐘）或直接結束 turn 等通知。環境沒有這些工具就單純等通知。
- **收攏**：teammate 只回結論與 `檔案:行號` 證據，長產物落檔回傳路徑；主對話只留結論與衝突點，跨路矛盾列給使用者裁決，不默默取捨。

## 訊息交錯處理（Message Crossing）

teammate 的 idle 通知與 mailbox 訊息（SendMessage）走不同管道，送達順序不保證——協定層面無法消除，只能靠冪等訊息與證據優先判斷吸收。

**主對話側**：

- 收到 idle 通知但沒有對應結果回報時，先查客觀證據（worktree `git status`/`git log`、預期輸出檔案是否已產生、`TaskList`/`TaskGet` 狀態），不要只憑「收到 idle 通知」就判斷 teammate 已停擺或已完成。
- 剛派工後緊接著收到的 idle 通知，高機率是派工訊息與上一輪通知交錯（teammate 才剛接手、尚未開始執行），先等下一輪訊號再判斷，不要立刻當異常處理。
- 催動訊息務必冪等：附上「若已完成/已回報請忽略本訊息，直接告知先前回報的時間點或內容摘要」，避免對方重做已完成的工作。

**teammate 側**（供派工 prompt 模板引用）：

- 完成工作後必須先用 SendMessage 把完整結果回傳給派工者，才能結束 turn；idle 通知不等於回報，兩者走不同管道，派工者不會把 idle 通知當結果讀。
- 收到疑似重複的催動訊息時，先確認自己是否已回報過——若已回報，回覆時指向先前那則回報的時間點或內容摘要，不要重做一遍。

Why: 本 session（teammate 編隊模式推進）實測至少 4 次交錯：①派工後立即收到 idle 通知、誤判為尚未開始或已停擺；②完成回報與主對話催動訊息交錯，雙方互以為對方沒收到；③teammate 完成工作後只發 idle 通知、忘記先用 SendMessage 回報結果；④主對話催動後 teammate 回覆「其實已回報過」，浪費一輪往返。此為既有「等待不輪詢」原則（見上）的延伸——完成通知只是訊號，不能替代對結果內容本身的查核。

## 資源紀律（避免編隊變浪費）

- 路數由「互相獨立的調查軸」決定，不是越多越好；兩路會讀同一批檔案 → 合併成一路。
- 每路 scope 互斥（目錄/主題切開），派工 prompt 明寫「不碰 X（另一路負責）」。
- 搜尋/定位用唯讀 Explore 型 agent（讀摘錄不讀全檔）、機械活降小模型；不要每路都吃主模型。
- worktree isolation 只給會改檔的並行 agent（有建置成本），唯讀調查不用。
- 追問既有 teammate（SendMessage）優於重派 — 重派 = 重付整個 context 建置費。
- 任一路超出 token 預估 2 倍、或連錯兩次 → 停下重估路線，不加派更多 agent 補洞。
- trivial 任務（單檔已知位置、單條指令可答）不開編隊。

Why: 2026-07-08 Fable 主對話實測 — 背景編隊（具名 teammate、派出即繼續、SendMessage 續問）讓多路調查與主對話推進並行、等待時間歸零；但無閘門的大量委派會失控燒 token，故比照 Task Tracking 以 HITL 附 token 預估徵求啟用。本機另有 `~/.claude/playbooks/10-model-dispatch.md` §7.1 詳版與 CLAUDE.md 硬性偏好一行；本檔為 repo 可攜版本，供其他環境接線（不建議同時 symlink 進常駐，避免重複載入）。
