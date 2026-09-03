# Changelog

所有重要變更都記錄在這裡。格式參考 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.0.0/)。

## [v2.2.0] - 2026-09-03

> **版本位階判定：MINOR。** 依 [VERSIONING.md](VERSIONING.md) 的判準「會讓照舊用法的既有使用者行為改變或壞掉的才是 MAJOR」核對：本次新增一支 skill、修一份 rules 文件，既有 skill 的唯一改動是 `plan-run/SKILL.md` 多一個 `redundancy-peers` 值——那是給 `/design` 讀的去重提示，不是對外介面，也不改 `/plan-run` 任何行為、旗標或機器可讀輸出。`/dispatch-loop` 與 `plans/backlog/` 對既有使用者都是純增量：不叫它、不建那個目錄，一切照舊。

### Added
- **`/dispatch-loop` — 委派推進迴圈**：把「主模型不下場，只指揮、裁決、抽查」這套操作模式從私有的 `~/.claude/skills/` 移進本 repo。與 `/plan-run` 的分工是「下一步做什麼」對「這一步怎麼派、怎麼驗、花多少 token」：六格派工 prompt 骨架（目標／動機／範圍／既有慣例／驗收條件／回報格式）＋五種型態的 agent/model 微調、不信任自報的抽查驗收、每 step token 預算與超支 2 倍停損、回收 agent 前的 KB gate。內容來自 2026-07-10 一次 28 steps／約 5M subagent tokens 的實跑。

  **移進來時解掉了兩處對外不可解的引用**：原文寫「用 `playbooks/30-delegation-templates.md` 模板」「量級參考見 `10-model-dispatch.md §8`」，那兩個檔在未公開的 `~/.claude/playbooks/`，外部安裝者照著做會撞到不存在的路徑且沒有任何錯誤訊息。判準是對本 repo 跑 `grep -rl "playbooks/" --include=SKILL.md`——零命中代表這份資產從不在公開範圍內，必須 inline 成自足摘要（數字類搬原值：實作 step 60–130K、headed 驗證 100–200K、30-agent 編隊 review 1.6M）。相對地 `_pending/`、`wiki/learned/` 有 `ship-ticket`、`release-pr` 的既有先例，維持原樣不動。`plan_runner.py` 則屬第三類——同 repo 但 `npx skills` 快照只同步 `SKILL.md` 不帶 `scripts/`，處置是加一句說明而非移除引用。

### Changed
- `plan-run/SKILL.md` 的 `redundancy-peers` 補上 `dispatch-loop`，與 `dispatch-loop` 列的 `plan-run` 對稱（沿用 3b053ef 建立的雙向慣例）。
- **`rules/plan-management.md` 補 `plans/backlog/` 提案池路徑**（#61，發版準備時補記）：把「無阻塞、未核准、近期無新證據」的提案從 `plans/active/` 分出去，不計入 active，升回 active 由使用者裁決。理由是這類提案會拉高 active 的稽核與清運成本，混在一起會讓「真正在推進的 plan 有幾份」這個問題答不出來。

## [v2.1.0] - 2026-09-02

> **版本位階判定：MINOR。** 依 [VERSIONING.md](VERSIONING.md) 的判準「會讓照舊用法的既有使用者行為改變或壞掉的才是 MAJOR」逐項核對：指令名未變、未移除任何子命令或旗標、plan 格式契約未變（Phase 標頭那條是**文件記載**錯誤，parser 行為一直如此）、既有子命令的機器可讀輸出（`--format=json`）未變、`init --no-attach` 的 stdout 與 base commit `e745670` 的 golden 檔逐位元組一致。
>
> 兩項對既有使用者確實可見的變化，判為向後相容：①`init` 預設 attach，多寫一個 pointer 檔並多印三行 stdout——`--no-attach` 完全還原，且未安裝 hook 時 pointer 是惰性的；②step action 模板由五行收斂為三行——那是給 LLM 讀的指令文字，不是機器契約。`/goal` 複合用法屬文件層級的推進建議，從來不是對外介面。

### Fixed
- **Stop hook 的 in_progress 催報分支沒有上限，會一路撞到 harness 的 8-block 硬截斷**：`_branch_in_progress` 是唯一沒有 ceiling 的 block 分支——背景輪詢有 `HOOK_BG_POLL_MAX`、完成宣告只 block 一次、ready step 走 `decide_budget`，只有它每輪 `nag_counts += 1` 卻沒人拿這個計數跟任何上限比。模型若始終不跑 `complete` / `fail`，它會每一輪都 block 直到 harness 自己的 8 次上限強制結束該輪——那正是 `BLOCK_BUDGET` 存在要避開的結局；沿途 footer 還會印出 `Auto-advance 7/6 — check-in after 0 more step(s)` 這種自相矛盾的字串。改為套用與 ready-step 分支相同的預算檢查，額度用盡時改 allow 並附一則講死「這是卡住不是檢查點」的 `systemMessage`。
- **同一個 cwd 的兩個 session 可能重複發出同一個 step**：`os.replace` 只保證讀者不會看到半截 JSON，擋不住 lost update。`hook-stop` 的「讀 pointer → 決策 → 寫 pointer」是無鎖序列，兩個 session 可能都讀到過期 lease、都接手成為 driver、都指派同一個 ready step，後寫的 pointer 還會蓋掉對方的 `consecutive_blocks` / lease 欄位；`start` 的「讀 pending → 寫 in_progress」同理。改為對 pointer 與 state 各加一支 `flock(LOCK_EX|LOCK_NB)` advisory lock（重試上限 20 × 25ms，因為這段跑在每輪都會執行的 Stop hook 裡，**絕不可 hang 住一輪**）。`save_state` 一併改為 tmp + `os.replace` 原子寫入，因為另一個 session 的 hook 每輪都在讀它。

  兩個細節是 review 修正後才對的：**①鎖要綁「實際治理這個 cwd 的 pointer」，不是 cwd 的雜湊。** 哪一份 pointer 治理某個 cwd 是一次**往上走的解析**——`repo/subdir` 的 hook 由掛在 `repo` 的 pointer 治理。用 cwd 雜湊當鎖會有兩個後果：nested 目錄自己的雜湊沒有對應檔案，於是**完全不取鎖**；而同一個 repo 底下兩個子目錄的 session 會拿到**兩把不同的鎖**卻寫同一份 pointer。現在改為先唯讀解析出 pointer 的身分、鎖它、再在鎖內重新解析。**②逾時不得繼續寫入。** 原本逾時放行不鎖，那等於把這支鎖要防的 lost update 原封不動放回來；現在逾時就靜默 allow 且完全不動 pointer（對方正在驅動這一輪，它的決策成立），`start` 則直接回錯要求稍後重試。
- **`settings.json` 的 `hooks` 不是 dict 時 `doctor` / `attach` 直接 traceback**：`data.get("hooks", {}).get("Stop", [])` 對 `"hooks": []` 或 `"hooks": null` 會噴 `AttributeError`，而 except 只接 `OSError` / `JSONDecodeError`——與該函式「格式壞掉一律讀成『未註冊』，絕不報錯」的契約相反。`hooks` 改為逐層型別檢查。
- **hook 注入的三處指令仍印裸 `plan_runner.py`**：`report_result`、`settle_background`、催報升級提示三個 renderer 沒接上 `_runner_invocation()`。其中 `report_result` 在每一個未回報的 in_progress step 上都會渲染，是整條流程印最多次的指令，而它印出來的形態不可執行——模型又得自己猜腳本在哪，正是 `_runner_invocation()` 當初存在的理由。三處改印絕對路徑，測試從 `assertIn("plan_runner.py ...")`（裸名也會通過，等於沒驗）改為比對完整的 `python3 <abs>` 形態。
- **兩則 `systemMessage` 直接內插未消毒的 `slug`**：`_branch_state_abandoned` 與 `_branch_stuck` 用 `state["slug"]` 組訊息，而 state 檔是使用者可寫、寫入後不再重新解析的；其餘 plan 欄位都走 `_sanitize_plan_field()`，這兩處漏了。已補上（CWE-116）。
- **文件與實作不一致三處**：①`plan-run/SKILL.md` 原本說 `doctor` 任一項 FAIL 就「先修 hook，不要硬推 plan」，但同一份文件下方就寫著沒裝 hook 可走手動退化模式——全新安裝必然在「hook 已註冊」這項拿 FAIL，等於被文件擋在自己的 fallback 之外。改為分流：只有註冊／wrapper 兩項 FAIL 代表「沒裝，走手動模式」，其餘 FAIL 才是「先修再推」。②`attach` 只接受 `$HOME` 底下的 plan 路徑，這條限制原本只寫在 CHANGELOG，`plan-run/SKILL.md`、`design/SKILL.md`、`README.md`、`docs/hooks-setup.md` 四處描述自動 attach 與跨 session 續推時都沒提，使用者只會看到「掛不上」而不知為何。四處補齊。③`scripts/hooks/README.md` 的移除章節仍標著「暫定版本，S2.7 實測後由 S3.2 取代」，但正式版早已在 `docs/hooks-setup.md` 落地；改為指向正式版，並把「以 command 字串比對、不得以陣列位置指定」與「`~/.claude/plan-run/` 是狀態不是安裝」兩條寫進摘要。

- **Task 追蹤工具在新模型上預設不存在，9 個檔仍當它可用（全 repo 適配）**：Claude Code **v2.1.233** 起，`TodoWrite` / `TaskCreate` / `TaskGet` / `TaskUpdate` / `TaskList` 在 **Opus 4.8、Sonnet 5、Fable 5、Mythos 5 及更新模型**上**預設不註冊**（官方理由：這些模型不需書面清單即可追蹤多步工作，而工具定義與 reminder 會佔 context；官方建議的因應方式是「什麼都不做」）。本 repo 的 `/design`、`/assist`、`/curation`、`/triage`、`/plan-run`、`/plan-archive`、`README`、`rules/teammate-fleet.md`、`scripts/plan_runner.py` 共 9 個檔仍把 `TaskCreate` 當可用工具在寫——其中 **`/curation`、`/triage`、`/plan-archive` 更把「用 TaskCreate 建 task」當作追蹤基準與完成率分母**，在預設模型上會在第一個追蹤步驟就落空，`/plan-archive` 的完成率甚至會直接歸零。修正：①新增 [`rules/task-tracking-availability.md`](rules/task-tracking-availability.md) 作單一來源（事實、三條有效 opt-in 途徑、易混淆項目對照、撰寫守則、可觀察的驗證指令），9 個檔改為引用而非各自重述；②**主線一律不依賴 Task 工具**——追蹤預設改為「在回覆內維護編號 Step 清單並逐項標記狀態」，Task 呼叫全部降為條件式 best-effort；③完成率分母改用本來就存在的東西（`/curation` = 掃描出的檔案數、`/triage` = 待退役表列、`/plan-archive` = plan 內 Phase/Step 數）；④`/design` Step 6 與 `/assist` Step 0 明訂 **session 沒有 Task 工具時該題直接從 HITL 批次剔除**——問一個當下開不了的開關（env var 要重啟 session 才生效）只是多一輪等待；⑤`scripts/plan_runner.py` 的 instruction 模板把 TaskCreate/TaskUpdate 標為 `best-effort, skip if no Task tools`，`--task-id` 改為可選（state machine 本來就不依賴 task_id，`## Required sync` 與 `## Next hints` 本來就是條件式輸出，故推進行為零變更）。
- **實測釐清：SKILL.md frontmatter 的 `allowed-tools: TaskCreate` 不構成 opt-in**。本 repo 有四個 SKILL.md（`/design`、`/assist`、`/plan-run`、`/ship-ticket`）在 frontmatter 列了 Task 工具，讀起來像宣告了依賴、實際上是死宣告——frontmatter 是「skill 執行期間可用工具的限縮清單」，工具沒註冊列了也不會出現。本機 v2.1.246 三組對照實測（`--output-format stream-json` 抓 `tool_use`，不採信模型自報工具清單——實測中模型對照組會答錯）：baseline 只印 `NOTOOL`；`--allowedTools TaskCreate` 與 `CLAUDE_CODE_ENABLE_TODO_TOOLS=1` 都印出 `"name":"TaskCreate"`；**只掛 frontmatter 則印 `NOTOOL`**。四處 frontmatter 予以保留（opt-in 環境下才不會被限縮擋掉），但守則明訂不得據此假設工具存在。
- **截圖驗收 gate 在 `/pr` 被繞過時靜默消失（`pr/SKILL.md` Step 5.5 + `pr-evidence-comment` description）**：Step 5.5 只寫在 `/pr` 內部，因此**任何不經 `/pr` 建立的 PR**（派工 subagent 直接 `gh pr create`、手動開、`gh pr edit` 更新）都不會觸發截圖驗收判定——而「請 agent 開 PR」正是最常見的路徑。同時 `pr-evidence-comment` 的 description 原本只列**使用者說法型**觸發詞（「headed 驗收這個 PR」「把驗收結果貼上去」「preview env 驗收」），缺**狀態型**觸發，於是「PR 已存在＋有 UI／行為變更＋尚無截圖證據」這個客觀狀態成立時不會自動命中。修正：①`pr-evidence-comment` description 補狀態型觸發，明寫「包含由 subagent 或手動 `gh pr create` 建立的 PR，繞過 `/pr` 不代表免驗收」，並補一種特別要抓的狀態——**本機已產出截圖／trace 但只落在 `.verification/` 等本機路徑、尚未上傳到 PR**；②`pr/SKILL.md` Step 5.5 開頭加引言框，明訂適用範圍不限於由 `/pr` 建立的 PR，**委派出去的是工作不是責任**。動機：2026-08-25 一個 session 完整跑完 headed 驗收（攔截故障注入、DOM 快照、retry trace、全頁截圖）後開了 PR，**證據全部落在本機 `.verification/`，一張都沒上傳**，使用者回「沒有看到驗證的圖片」才發現——診斷後確認本機 skill 版本與 repo `diff -rq` 完全一致（非版本落後），是觸發設計的缺口。
- **`plan-run/SKILL.md` 的 Plan 格式約束表把 Phase 標頭寫成「只認冒號」**：實際 regex 是 `^###\s+(.+)$`，任何以 `###` 加空白開頭的標頭都成立，破折號寫法（`### Phase 1 — 診斷`，真實 plan 的常見寫法）一直都是合法的。只有 `normalize` 那一步的 Phase 偵測較嚴格（`### Phase N:` / `### Phase N：`），表格未區分兩者，會讓人以為 canonical plan 必須改成冒號。
- **`doctor` 全數正常時印「4/6 PASS」**：六項檢查中有兩項天生是 INFO（`~/.claude/plan-run/` 尚未建立、當前 cwd 無 active plan），所以健康的全新安裝永遠印不出 6/6，讀起來像沒過。改為印三個計數並講死判定：`<n> PASS / <m> INFO / 0 FAIL — 安裝正常`（PASS 數會隨那兩項落在 PASS 或 INFO 而變，六項與這個摘要格式是同一份契約）。

### Added
- **`/notion-report` 新 skill（把成果寫回 Notion，`notion-report/` 843 行）**：`/notion-plan` 的反向——那個從 Notion 讀需求進來，這個往 Notion 寫結果出去。**兩條寫入路徑自動選路**（`NOTION_TOKEN` 有值走 API，否則走 browser，`--via` 可強制）：API 路徑不開瀏覽器、不注入 snapshot、幾乎不吃 context，但需自建 internal integration 且頁面要加進 Connections；browser 路徑沿用 `/notion-plan` 的 `notion-profile` 登入 session、免 token。**browser 不是次等品**——公司型 workspace 常需管理員核准才能建 integration（實測門檻：只有 workspace owner 能建 internal integration，掛頁面需 Full access），對很多人是唯一可行路徑；兩條路的「定對象 → 組稿 → dry-run」完全共用，只有寫入動作不同。**依收件對象調整內容**：`--to pm|design|ops|eng` 可多選，各自有該寫與刻意不寫的項目，未指定時以 `AskUserQuestion` 詢問、不自行推測。寫入模式 `append`（預設）／`comment`（僅 API），**只 append 與 comment，不刪除、不覆寫**既有內容；寫入前強制 dry-run 過目，寫入後讀回驗證（browser 路徑另檢查 `occurrences: 1` 防重複寫入）。browser 路徑用 synthetic paste event 讓 Notion 自己解析 Markdown，**不碰系統剪貼簿、不需 clipboard 權限**；實測修正一處：Step 4 的 `preventDefault` 回傳值不是成功訊號，不可據以判定寫入成功。
- **`rules/` 的情境型規則新增 UserPromptSubmit 觸發式安裝（`scripts/hooks/debug-triage-order-hint.sh`、`scripts/hooks/worktree-prompt-hint.sh`）**：`rules/` 底下的規則一般 symlink 進 `~/.claude/rules/common/`，那是**每個 session 全文載入**。對「每次都要守」的紀律（coding style、輸出格式）合理，但對情境型規則是純浪費——`debug-triage-order` 只在「debug 一個線上回報的 bug」時適用、`worktree-prompt` 只在「開工實作」那一刻適用，其餘 session 付了 token 卻用不到（實測兩份合計約 1,120 tokens）。兩支 hook 提供同樣規則的觸發式版本：每 session 成本 0，命中偵測條件才以 `additionalContext` 注入規則重點。與第二篇的 Stop hook 不同，**這兩支只注入不 block**。設計上高精度優先於高召回——`debug-triage-order-hint` 要**同時**命中「debug 訊號」與「可觀測環境訊號」才觸發（只講「這段程式有 bug」不算，那是本地邏輯題，prod-first probe 不適用），`worktree-prompt-hint` 在已身處 worktree（`.git` 是檔案）時自動跳過；兩者都對 slash command 開頭的訊息不干預。誤報的成本是雜訊，會讓人把整個 hook 關掉。**規則檔本身一字未改**，兩種安裝模式二選一（同時裝會在一個 session 裡看到規則兩次）。安裝、取捨與自檢指令見 `docs/hooks-setup.md` 第三篇。
- **`/plan-run` 的控制流搬到 Claude Code 官方 Stop hook 上（`scripts/hooks/plan-run-stop.sh` + `plan_runner.py hook-stop`）**：`plan-run/SKILL.md` 原本第一條設計原則寫「DAG 推進邏輯在 Python，LLM 不負責『下一步是什麼』的判斷」，實際上不成立——`plan_runner.py` 在 Claude Code 沒有任何註冊或強制機制，它是一支普通腳本，靠 SKILL.md 的文字請 LLM 自願用 Bash 呼叫；LLM 真正負責的是「要不要去問腳本下一步是什麼」，**控制流第一層仍在模型手上**。本次把那一層交給 harness：Stop hook 每輪結束強制執行，由它讀 state 決定要不要把下一步以 `{"decision":"block","reason":...}` 注入回來。輸出形狀經本機 v2.1.247 四變體實測定案採 **top-level `reason`**（會顯示成 `Stop hook feedback:`，使用者看得見）；實測 `hookSpecificOutput.additionalContext` 雖然也送達模型卻**不寫進 transcript**，控制流工具塞給模型的指令必須可稽核，故不採用。
- **Pointer registry（`~/.claude/plan-run/active/<sha256(realpath(cwd))[:16]>.json`）**：hook 靠它知道「這個 cwd 現在在推哪份 plan」。目錄 0700、tmp + `os.replace` 原子寫入；plan 路徑必須位於 `$HOME` 之內（沙箱／臨時目錄／外接磁碟上的 plan 一旦綁定，該目錄每一輪都會被它驅動）。跨 session 續推靠這個檔案——`/clear`、compaction、開新 session 之後第一輪結束就自動接上，這是相對官方 Workflow（只能同 session resume）的核心價值。
- **新子命令**：`hook-stop`（Stop hook 決策入口，從 stdin 讀 hook JSON）、`attach <plan>` / `detach [plan]` / `pause` / `resume` / `pointer`（cwd 的 pointer 控制與檢視）、`doctor`（唯讀安裝自檢，六項 PASS/INFO/FAIL，有 FAIL 時 exit 1 可當 CI gate）。`start` 新增可選 `--session-id`（僅供 audit）。
- **`docs/hooks-setup.md` 改為兩篇結構**，新增「Plan DAG 推進 Stop Hook」章節：完整 wrapper、additive 安裝步驟（備份 → 追加到 `Stop` 陣列末端 → JSON 驗證 → `doctor`）、與既有 Stop hook 共存的實測結論、`pause`/`resume`/`detach`/`doctor` 用法、專案層 `.claude/settings.json` 的低風險替代路徑、逐字採用實測定案的移除步驟（並把「解除安裝」與「清除狀態」分成兩件事——`~/.claude/plan-run/` 是狀態不是安裝的一部分）、`AGENT_SKILLS_DIR` 警語，以及一節說明為什麼不去提高 harness 的 block cap。
- **`/pr-evidence-comment` 截圖驗收 skill（新增）＋ `/pr` Step 5.5 截圖驗收 gate**：把一次 headed 驗收變成 PR 上 reviewer 打得開的證據。核心事實是 **`gh` CLI 與 GitHub REST API 都不支援 comment 附圖**（`user-attachments` 上傳端點只吃 session auth），agent-browser 內建 Chromium 走 Google Workspace SSO 會被擋（automation 指紋封鎖，headed 也擋），外部匿名圖床 2026-08 實測多半已關閉且有 private repo 曝光風險——唯一可行路徑是 **stock Chrome + 獨立 `--user-data-dir` + CDP 9222**（Chrome 136+ 禁止對預設 profile 開 CDP）。skill 內含：Step 0 變更類型分類（移除類拍不出來，改用量測）、Step 1 先列編號斷言再開瀏覽器（`B*` 未登入 / `C*` 登入）、Step 2 三種已知卡點解法（Radix Tabs `tabindex=-1`、無 role 巢狀 div、`.env.local` 帶引號值）、**Step 2.5 主對話目檢抽驗**（截圖以使用者本人身分公開發文，發文前主模型必須實際 Read PNG 檢查拍對斷言／無 email 外流／編號對得上，不採信派工 agent 自述）、Step 5 用 `user-attachments` 連結數驗證落地。同時在 `/pr` 新增 **Step 5.5 gate**（PR 建立/更新後執行，因為截圖要有 PR 才有落點）：依同一張分類表判定，需要時用 AskUserQuestion 三選一（現在就驗／只列清單自己驗／這次不需要），並在 preview env 未就緒時改問「等 preview」或「先用 local」；跑完把逐項 PASS/FAIL 回填 Test plan，FAIL 不得當「已驗收」帶過。`/pr` Step 2 檢查清單併加「視覺驗收面」判定，`/update` Step 7 明訂串接 `/pr` 跳過 Step 2 時**此判定不隨之跳過**（由 Step 5.5a 從 `gh pr diff --name-only` 補判），避免 gate 因無輸入誤跳過。與 `/figma-verify` 分工：figma-verify 比「有沒有照設計做」，本 skill 證「PR 上的東西真的動起來」。
- **`rules/design-token-reuse-first.md`（新規則，預設不 symlink 常駐）**：寫任何對應 design token 的樣式值之前，先全 repo grep 現成 utility/token；兩套樣式系統並存時預設用 Tailwind utility 承載 token 值（markup 掛 class，CSS-in-JS 只留 utility 蓋不到的部分），多 DOM 生產端逐一掛 class 並確認 content 掃描涵蓋；AMP 副本可例外（使用者裁決 2026-08-11）；真的掛不了才允許手抄值＋強制註記 token 名與原因。動機：vocus-web-ui PDT-10625 具體 case——repo 早有 `@utility label3-medium`（值與 Figma `Label3-Medium` 完全一致），實作卻因「styled-components 吃不到 @apply」把四個值手抄進 `PollNode.style.js`；判斷層級錯放在「當前樣式檔能不能 @apply」，正確層級是「markup 能不能掛 class」。
- **`rules/teammate-fleet.md` 補「訊息交錯處理（Message Crossing）」章節**：teammate 的 idle 通知與 mailbox 訊息（SendMessage）走不同管道、送達順序不保證，協定層面無法消除、只能靠冪等訊息與證據優先判斷吸收。主對話側：idle 通知無對應回報時先查客觀證據（`git status`/輸出檔/task 狀態）再判斷，剛派工後緊接的 idle 通知大概率是交錯不是異常，催動訊息附「若已回報請忽略」保持冪等；teammate 側（供派工 prompt 引用）：完成必先 SendMessage 回報再結束 turn，收到疑似重複催動時指向先前回報而非重做。動機：teammate 編隊模式推進中實測至少 4 次交錯（派工後誤判停擺、回報與催動交錯互不知情、忘記先回報只發 idle 通知、催動後對方回「其實已回報過」浪費一輪往返）。
- **`rules/debug-triage-order.md`（新規則，預設不 symlink 常駐）**：debug 順序三規則——① prod-first read-only probe（建本地重現環境前先對回報環境做唯讀探測，一次分流環境差異 bug vs 邏輯 bug）；② evidence-first before dispatch（派驗證 agent 前先盤點手上證據，1-2 指令可定案的 inline 跑）；③ verify-via-spec once（regression spec 寫好後驗收＝fresh context 實跑 spec，不散文重推導手動步驟、不疊第三輪驗證）。動機：2026-07-16 vocus 投票 RCA session 複盤，三浪費點合計 ~30-40% session 時間與 3 輪可避免派工。是否 symlink 進 `~/.claude/rules/common/`（常駐載入成本）由使用者 merge 後決定。
- **自持 `agents/` 目錄**（`complexity-triage` / `doc-reviewer` / `doc-updater` / `tdd-guide`）：收斂 v2.0.0 後散落 `design/SKILL.md`、`update/SKILL.md` 各處的裸 `general-purpose` 審查/更新 prompt 為單一權威定義（frontmatter + 檢查清單 + 紅旗），`design/SKILL.md` Step 4a 與 `update/SKILL.md` Step 1-2 均改為引用；維持 ECC 解耦（定義自持於本 repo、無 plugin runtime 依賴）。`planner` → 內建 `Plan` agent、`code-reviewer`（程式碼）→ `/code-review`、`security-reviewer` → `/security-review`、`refactor-cleaner` → `/simplify`、`learn-eval` → inline 5 維 rubric 維持原生替代不重建；`tdd-guide` 是 v2.0.0 唯一未落地明確替代的 agent，本次補回。
- **`/design` Step 2a 複雜度分診 subagent**：進入完整流程前先派 haiku 輕量 agent（Glob/Grep/Read 粗估、固定 JSON 輸出）判定 low/medium/multi-session；主模型保留最終裁決且衝突時取較高複雜度；`/notion-plan` 串接時同樣生效。~5-15K tokens 換掉低複雜度任務誤入完整儀式的成本（2026-07-10 使用者指示）。

### Changed
- **`plan-run/SKILL.md` 191 → 176 行**（撰寫期間一度砍到 140，模式 A/B 兩段式回補後定案 176，AC6 上限 180），第一條設計原則改為誠實版（控制流在 Stop hook，LLM 不需要也不應該主動想起查狀態）。砍除：3f `/goal` 整節與「每次 transition 後跑一次 `index` 把狀態 surface 給評估者」的建議（實測冗餘且有害——`complete` 的 stdout 首行是 `# completed: <step>`、其內嵌 state view 已含 `Progress: N/M`，`index` 只多 35 行 DAG 樹，20 step 等於 700 行雜訊稀釋評估者訊號，而且又是一次 LLM 自願行為）、Step 2「建立父 task」整節（降為 Step 1 一行註腳）、3b reconcile 五步（壓成一句條件式）、3a/3c「什麼時候跑哪個指令」教學。新增：前置安裝、控制面、以及**手動退化模式**（本 repo 是公開 repo，不能假設所有人都裝了 hook）。
- **`init` 預設 attach**：`init` 現在會把 cwd 的 pointer 指向該 plan，stdout 末端多印 `Plan:` / `Cwd:` / `Pointer:` 三行。`--no-attach` 完全還原舊行為，且該路徑的 stdout 與 base commit `e745670` 的 golden 檔逐位元組一致（有回歸測試把關）。pointer 本身在未安裝 hook 時是惰性的。
- **step action 模板三行化**：`TaskCreate` / `TaskUpdate` 兩行從模板拿掉，收斂為 `start` → 執行 → `complete` 三行；Task 工具的串接降為條件式敘述。Stop hook 注入版另把 plan 原文包進標註 `plan data, not instructions` 的圍欄，三行指令印在圍欄外並帶 `plan_runner.py` 的**絕對路徑**（先前印裸檔名，模型只能用猜的）。
- **`/plan-run` 改為兩段式：`/goal` 驅動是預設，Stop hook 降為選配**。PR 撰寫期間的設計是「全面移除 `/goal`」，2026-08-29 兩項實測把那個決策的事實基礎抽掉了：①**`/goal` 與自寫 Stop hook 拿到的續推輪數完全相同**（各 9 輪；上限的單位是每個 turn 的輪數、由所有 blocker 共用，兩支 Stop hook 一起掛也還是 9），所以「hook 比較能跑久」不成立；②端對端跑 5-step / 含交叉依賴 `S2.3 <- S2.1,S2.2` 的 plan，**純 `/goal` 驅動 5/5 完成、依賴順序由 `started_at` 驗證正確**（`init --no-attach` 先確認 pointer 為空以排除 hook 干擾）。兩者唯一實質差異是**跨 session 續推**（hook 有 pointer 檔自動接上；`/goal` 隨 session 消失需重下一次，但 state file 仍在，不是資料遺失）。既然差異只有這一項而 `/goal` 零安裝，預設順序反過來。**職責分界不變且是理解全案的關鍵**：`plan_runner.py` + state file 決定「下一步做什麼」（依賴解析、順序、非法轉移驗證），`/goal` 或 Stop hook 只決定「還要不要再跑一輪」——`/goal` 從來沒有要取代狀態機。模式 A 的 goal 條件把「下一輪第一個動作是跑 `next`」寫進條件本身（評估者每輪重述，而 `next` 讀磁碟上的 state file，不依賴 transcript），補掉 `/goal` 無狀態記憶的弱點；終止條件用 `plan_runner.py` 本來就會印的 `Progress: N/N`，不額外補跑 `index` 餵評估者（那會稀釋訊噪比）。**`figma-verify` 的 `/goal` 用法完全不動**；同 session 要跑視覺 gate 時把 `/goal` 讓出來、改用模式 B，是選擇 Stop hook 的第二個正當理由。
- **check-in 節奏**：hook 主動在第 7 步（或更早的 phase 邊界）停下來做 check-in。上限本身**經本機實測定案**（2.1.251，`.verification/2026-08-29/stop-hook-block-cap-measured.md`）：always-block 的 Stop hook 被呼叫 9 次、第 9 次的 block 不被採納 = 實際可用 8 次續推；且上限是每個 turn 的輪數、由所有 blocker 共用（兩支 always-block hook 各拿滿 9 輪）。本 repo 順著它設計而非繞過——`plan_runner.py` **從不讀寫** harness 自己的 block-cap 環境變數、不偽造 `stop_hook_active`，自有的 `PLAN_RUN_BLOCK_BUDGET` 硬夾在實測上限 8 以下，預設 7 留一輪餘裕。撰寫本 PR 時這個「8」只有 WebSearch 摘要來源、被列為待實測，現已補測。
- **`/design` HITL 合併為單一批次詢問**：Task 追蹤（原 Step 0）、Worktree（原 Step 6）、推進方式（原 Step 7）、編隊授權（原全域規則觸發時散問）四題合併成 plan 寫入後的**一次 AskUserQuestion**（工具單次支援 4 題），答案已知的題目自動剔除、全剔除則跳過；批次後不再為這四類決策二次發問，實作中新裁決發問前先派出不依賴答案的工作。另補：plan 呈現與批次詢問前可發 `PushNotification` 提醒（若 harness 支援）。動機：2026-07-16 PDT-10398 session 實測，3.5h 全程 ~85–100 min 為 HITL 等待（~45%），其中 ~40 min 來自 4 個分散 AskUserQuestion 各自 block、43 min 來自使用者不知 plan 已就緒的 approval 空窗——瀏覽器自斷等技術問題僅損 ~5 min，等待才是主要時間浪費。
- **`/design` 新增低複雜度快速路徑**：單一 bug fix / ≤3 檔 / 無架構決策的任務，計畫只要求需求拆解、技術方案、依賴、風險、驗收（S-code 格式不變，/plan-run 相容）；跳過業界參照表、社群共識表、RTM、逐 step token 預算；Step 4a 改主模型 6 項 self-check、不派 subagent 審查。動機：PDT-10428 前例——P2 顯示 bug 走完整儀式被 61K-token 審查 agent 以格式官僚項目打回，4 個 FAIL 無一改變實作方向。診斷 gate（live 驗證）明文標為不可裁剪。
- **`/notion-plan` 補 headless session 不穩定止損守則**：第一次 snapshot 撈齊 properties/comments；互動展開重試上限 2 次，失敗標註缺口交 HITL，不無限重試。

## [v2.0.0] - 2026-07-08

> ⚠️ **BREAKING CHANGE — 本 repo 首次 major bump。**
> 本版移除對 **everything-claude-code (ECC) plugin** 的所有 hard-runtime 依賴（約 46 處），改用 Claude Code 內建 primitives。skill 對外介面（指令名、plan 格式契約、DSL、安全紅線）不變，但**內部呼叫的 agent 全數更換**。若你的環境靠 ECC agents 被這些 skill 呼叫、或有硬編 `everything-claude-code:*` 名稱的 hook/腳本，見下方 **Migration**。要維持 ECC 行為請 pin `v1.28.0`。

### Removed
- **移除對 everything-claude-code (ECC) plugin 的 hard-runtime 依賴**（約 46 處）：`/design`、`/update`、`/pr`、`/assist` 不再呼叫任何 ECC agent，改用 Claude Code 內建 primitives。對照：
  - `planner` → 內建 `Plan` agent
  - `code-reviewer`（審程式碼）→ `/code-review`；（審文件）→ `general-purpose` fresh 驗收 agent
  - `security-reviewer` → `/security-review`
  - `refactor-cleaner` → `/simplify`
  - `doc-updater` → `general-purpose` + 明確 prompt + 主模型 `git diff` 驗證
  - `/update` Step 4 `learn-eval` → inline 5 維 rubric（格式契約不變；深度交叉驗證仍可走 `/learn-eval-deep`）
  - 無前綴 agent 名稱（`code-reviewer` 等）經查證同屬 ECC plugin 雙重註冊，故解耦改內建 primitives 而非只拿掉前綴

### Changed
- **4.8+ 模型適配精簡**：18 個 SKILL.md 總行數 4,338 → 2,976（-31%）— 刪自建 manifest 完成率儀式（改原生 task tracking）、單檔重複 3-7 次的規則收斂為單一來源、裝飾性學術引用、與 CLAUDE.md 全域規則重複的段落；parser 契約、DSL 表、DOM 腳本、安全紅線（playwright-hitl 三軸分級）逐字保留
- `rules/security-guidance/skill-integration.md` 機制 A 改委派內建 `/security-review`（單點槓桿，design/update/pr/assist 同步生效）
- `rules/refactor/remove-architect-pipeline.md` 替代方案表改指內建 `Plan` agent（原表自身仍推薦 ECC planner 的矛盾修正）
- `plan-archive` 的 Hook 安裝教學移出執行期文件至 `docs/hooks-setup.md`
- `README.md` 全面同步解耦後架構；「ECC Agent 退化警告」改寫為歷史決策記錄
- `plans/active/ecc-190-workflow-integration.md` 作廢歸檔至 `plans/archived/`（依賴 2026-04-01 已禁用的 architect，從未執行）；`plans/active/knowledge-base-quality-optimization.md` 去 ECC 重寫為現況盤點（多數項目已被本次 update/curation 重寫涵蓋）

### Deprecated
- `/ecc-skill-defer` 標記 deprecated：等 harness 端 ECC plugin 處置定案後移除（gateguard 待辦仍引用它，暫保留）

### Fixed
- `verify-evidence-loop` L11 將本 repo 自有的 `evidence-check` 誤標為 ECC primitives 的歸屬錯誤
- `design` 多 session 路徑引用已不存在的 `/blueprint` → 改交 `/plan-run` 跨 session 推進

### Migration（v1.x → v2.0.0）
- **一般使用者**：無需動作。skill 指令名、plan 格式契約、DSL、安全紅線皆不變；內部改用內建 primitives，行為等價或更佳，且不再需要安裝 ECC plugin。
- **想維持 ECC 版行為**：pin 在最後的 ECC 依賴版 `v1.28.0`：
  ```bash
  git clone https://github.com/ashe-li/agent-skills && cd agent-skills && git checkout v1.28.0
  # 依該版 README 的 Install 指示安裝
  ```
  `v1.x` 線維護凍結、不再收新功能（見 [VERSIONING.md](VERSIONING.md)）。
- **有硬編 `everything-claude-code:*` agent 名稱的自建 hook/腳本**：改指上方 **Removed** 對照的內建 primitives。

### Why
Skill 集建於 Opus 4.5 時代：當時以 ECC agents 補足能力、以過細指令與 manifest 儀式補償模型判斷力。模型升級（Opus 4.8+）後兩者都成負債 — ECC 依賴阻礙 plugin 退場，過細指令浪費 context 且造成僵化。無前綴 agent 名稱（`code-reviewer` 等）經查證同屬 ECC plugin 雙重註冊，故解耦必須改內建 primitives 而非只拿掉前綴。環境事實（路徑/API 怪癖/真實踩坑教訓）與模型強弱無關，全數保留。審計與裁決記錄：knowledge-base `reports/2026-07-04-agent-skills-ecc-decoupling-audit.md`；實作計畫：`plans/active/ecc-decoupling-and-model-adaptation.md`（D1-D5 裁決）。

## [v1.28.0] - 2026-06-24

### Changed
- `/notion-plan`：因應 Notion 主網域 `notion.so` → `notion.com` 遷移，更新 URL 辨識與登入
  - 解析表新增 `notion.com`（新主網域）、`app.notion.com`（含 `/p/<workspace>/` 路徑前綴）；`notion.so` 標為舊網域（301 轉址到 `notion.com`）
  - 登入改用 `https://www.notion.com/login`（cookie 綁實際落地網域，避免舊 `notion.so` session 轉址後失效）
  - pageId 抽取（結尾 32 字元 hex）與網域/子網域/路徑前綴無關，核心邏輯不變
- `README.md`：更新 `/notion-plan` 支援網域清單與 Usage 範例

### Security
- `/notion-plan` 網域白名單改 dot-boundary 比對（host 等於或以 `.notion.com` / `.notion.so` / `.notion.site` 結尾），擋掉 `evilnotion.com`、`notion.com.attacker.tld` 等同尾巴/同前綴假冒網域；先前「結尾為 notion.com」描述會誤收 `evilnotion.com`

### Why
Notion 已將主網域遷至 `notion.com` 並新增 `app.notion.com/p/...` 連結格式；硬編 `notion.so` 的工具會漏接新格式 URL。dot-boundary 比對在放寬任意子網域（`www.`/`app.`/`<workspace>.`）的同時，保留網域邊界的安全性。

## [v1.27.0] - 2026-05-27

### Added
- `rules/security-guidance/`：官方 `security-guidance@claude-plugins-official` plugin 的整合設定來源
  - `claude-security-guidance.md`：model-backed review 的威脅模型/檢查清單（secrets 政策 + TS/Python/Go/Swift 規則），symlink 到 `~/.claude/`
  - `security-patterns.json`：per-edit deterministic patterns（硬編 secret 前綴、PEM 私鑰、`subprocess shell=True`）；用 JSON 不用 YAML 避免缺 PyYAML 時靜默忽略
  - `README.md`：三層防線說明、省 token env 設定（`ENABLE_STOP_REVIEW=0` + `SG_AGENTIC_MODEL=sonnet`）、symlink 部署與還原指令
- `README.md`：新增「Rules / 整合設定」段，連向 `rules/security-guidance/`
- `rules/security-guidance/skill-integration.md`：**主動式安全觸發契約** — 定義觸發閘（security-relevance heuristic）+ 兩種機制（委派 security-reviewer agent + 引用同一份 `claude-security-guidance.md`）

### Changed
- `/design`、`/update`、`/pr`、`/assist`：主動把安全這層串進流程（不再只靠 plugin 被動 hook）
  - `/design` Step 3：plan 觸及安全敏感面時必含 Security / Threat Model 章節 + 實作後納入 security-reviewer；Step 4a 品質閘主動驗證安全覆蓋（非只「已評估」打勾）
  - `/update` Step 2：觸及安全敏感面時與 code-reviewer 並行委派 security-reviewer
  - `/pr` Step 2：觸及安全敏感面時委派 security-reviewer（非只 inline quick review）；從 `/update` 串接時去重
  - `/assist` Step 3：路由命中安全敏感面時 pipeline 預設附加 security-reviewer
  - 共同觸發閘：認證/輸入/endpoint/DB/反序列化/檔案/shell/SSRF/DOM/加密；都不觸及則明示跳過，不空跑 agent（省 token）

### Why
此 plugin 是 hook-based、無法經 `npx skills add` 散佈，故 repo 只版本控管「擴充檔 + 設定記錄」，canonical 放 repo 並 symlink 到 `~/.claude/` 達成 config-as-code 與零漂移。plugin 是**被動**事後攔截；主動入口 skill 用**同一份** guidance 在規劃/審查/PR 階段**主動**帶到安全這層，與 plugin 形成 defense-in-depth，不取代。

## [v1.26.0] - 2026-05-22

### Changed
- `/design`: Step 7 推進選項拆為 `1a` / `1b` 子選項
  - `1a`. `/plan-run` 狀態機（手動）— 每 step 完成後手動 enter 繼續
  - `1b`. `/plan-run` + `/goal` 自動推進（強烈推薦於高複雜度）— 用 Claude Code 內建 `/goal` 包外層，自動跑到 `all_done=true` 或 N turns
  - 原 option 2（LLM 自主推進）、option 3（暫不開始）位置不變
- `/design`: 複雜度推薦表第三列改為「`/plan-run` + `/goal` 自動推進（強烈推薦）」對應高複雜度 plan
- `/design`: Step 7 文案明示「1b 仍走 `/plan-run` Step 3d HITL failure gate」`/goal` 不自動跳過 fail

### Fixed
- `/plan-run`: 還原 Step 3f「自動推進（optional）— `/goal` 包外層」及「與其他 skill 的關係」表格中的 `/goal` 列
  - Regression: commit `413fc39`（task_id sync + sliding-window hint + CodeRabbit fixes）誤刪 commit `563b276` 加入的 Step 3f 與 `/goal` 表格列
  - `/design` Step 7 的 1b 文案引用 `/plan-run` Step 3f，若不還原則為 dead link

### Why
解決「plan → 執行的自動推進路徑被埋」：`plan-run/SKILL.md` Step 3f 文件化 `/goal` 整合（commit 563b276，本次還原），但 `/design` 的退出選單原本不含此選項，使用者必須自己翻文件才會知道可以這樣用。把 1b 拉到 Step 7 後，`/design` 完成即可直接接 `/plan-run + /goal` 一鍵自動推進。

## [v1.25.0] - 2026-05-04

### Added
- `scripts/worktree-cleanup.sh`: 跨 repo 批次清理已 merge worktree 的 shell 腳本，補強 `/worktree cleanup` 既有的單一 repo 互動流程
  - 掃描 `~/Documents`（可 `--root` 覆寫）下所有 sibling worktree（`.git` 為檔案而非目錄者）
  - 對每個 worktree 透過 `gh pr list --head <branch>` 查詢 PR 狀態，MERGED/CLOSED 列入清理候選
  - **預設 dry-run**：純列表輸出 ACTION/STATE/PATH/BRANCH/PR/FLAGS，不動任何資源
  - `--apply`：實際執行 `git worktree remove --force` + 嘗試刪除已 merge 的本地分支
  - **髒目錄保護**：未 commit 變更的 worktree 預設 `skip-dirty`，需明確 `--force-dirty` 才會清除
  - **PR 不明處理**：`gh` 查詢失敗或無 PR 時標記 `unknown`/`no-pr`，預設保留，需 `--include-unknown` 才列入清理
  - `worktree/SKILL.md` 補上 cleanup 區塊指引：單 repo 走 skill 互動流程、跨 repo 批次走 script
- 實測 2026-05-04 一次清掉 15 個 MERGED worktree（deployment-eks / vocus-trends / vocus-web-ui），跳過 6 個髒目錄與 3 個 OPEN/no-PR

## [v1.24.0] - 2026-05-04

### Added
- `/verify-fix-loop`: 新增 verify→fix 迭代迴圈 skill — 透過 local Playwright MCP（headed 模式）執行「驗證 → 診斷 → 修正 → 重新驗證」迴圈，每輪以 snapshot + console + network 為證據；**完成 2 輪後（Round 3 起每輪，HITL_AFTER=2）強制 HITL 詢問是否繼續**，避免盲目迭代。
  - **Headed 模式必要**：MCP server 須以 `--headed` 啟動，Step 0a 檢查；使用者同步觀察、HITL 時可視覺確認、debug 體驗大幅優於 headless
  - **PASS 條件 DSL**：`url:` / `element:` / `not-element:` / `text:` / `console: no-error` / `network: no-5xx` / `eval:` 7 種型別機械對照，與 Phase A 驗證項表格一一對應；自由文字輸入會自動轉為 DSL 並回讀使用者確認
  - **每輪 4 階段**：Verify (checklist) → Diagnose（snapshot + console + network 證據三聯）→ Fix（限 allowed_paths 硬邊界）→ Wait reload
  - **HITL Gate**：完成 2 輪後（Round 3 起每輪，`HITL_AFTER=2`，`if n > HITL_AFTER`）`AskUserQuestion`，提供繼續 / 停止 / 改策略 / 轉 `/design` 四選項
  - **Hard cap = 5 rounds**：依 METR 2025 agent degradation 證據；達上限即使選「繼續」也強制停止
  - **Dev server 預設不自動啟動**：避免 long-running process 殘留與 token budget 持續佔用；使用者另開 terminal 為預設策略
  - **持久化 round log**：`.claude/verify-fix-loop/<timestamp>-<slug>.md`，跨 session 接手（搭配 `/handoff`）、PR description 引用、回溯 debug 軌跡
  - **硬性禁止清單**：改測試 assert 放水、改 PASS 條件本身、catch swallow error、跨範圍改架構、hardcoded 繞過 — 防止「為過而過」非真修復
  - **與既有 skill 差異**：`playwright-human-in-the-loop` 為單次操作型，本 skill 為迴圈型修復；`verify-evidence-loop` 為技術主張的文獻驗證，本 skill 為程式碼行為驗證
- `README.md`: 新增 `/verify-fix-loop` 至 Usage、Skills 總覽、決策樹

### 方法論依據
- Self-Refine (arXiv:2303.17651) — 迭代修正模式
- Reflexion (arXiv:2303.11366) — 失敗證據回饋下一輪
- METR 2025 agent degradation — >3 輪 drift 風險，故 hard cap = 5、HITL gate = 2
- OpenAI dev community — checklist-driven verification > free-form
- DAMA-DMBOK Completeness — round_log + final report manifest-driven
- arXiv:2509.18970 — 結構性分類（PASS criteria checklist + DSL）優先於逐案語意判斷

## [v1.23.0] - 2026-04-18

### Added
- `/handoff`: 新增跨 context 接手 prompt skill — 萃取本次對話的目標、進度、決策、未完成項目，輸出可直接貼到新 session 或 `/compact` 之後使用的自包含 prompt。單一 skill 同時涵蓋「新 context 接手」和「compact 前準備」兩種情境（本質都是缺對話記憶）
  - 7 區塊 manifest：任務目標 / 當前進度 / 決策脈絡 / 環境快照 / 重要 context / 待辦項目 / 立即可執行的下一步（對齊 v1.21.0 DAMA-DMBOK Completeness 慣例）
  - HITL 三選項輸出：直接顯示 / 寫入 `.claude/handoff/handoff-<timestamp>.md` / 兩者皆要
  - 完整率驗證：< 70% 阻止輸出，要求補充對話資訊後重跑
  - 環境快照「相關性標註」：stash、`plans/active/*.md`、其他 untracked 檔案逐項標註相關 vs 不相關，避免接手者誤判（誤 pop stash、誤碰其他任務的 plan）
  - 與既有方案差異：純文字 prompt 載體（vs `everything-claude-code:save-session` 的 JSON），跨環境/跨機器/跨 LLM 通用，不需特定 runtime
- `README.md`: 新增 `/handoff` 至 Usage 與 Skills 總覽

## [v1.22.0] - 2026-04-18

### Added
- `/verify-evidence-loop`: 新增迭代式證據驗證 skill — 組合既有 primitive（evidence-check Generator + santa-method Dual Reviewer + iterative-retrieval gap refinement），不重造。4 維蒐集 × 最多 3 輪 iteration × dual Sonnet reviewer 收斂迴圈，適合高風險決策。
  - Haiku × 2 並行蒐證（D1 學術 + D2 標準 / D3 實踐 + D4 社群 + Strong Dissent probe），Sonnet × 2 並行獨立判讀（fresh per iteration），B ∧ C 必須同時 PASS 才 NICE
  - Strong Dissent 為一等公民：要求 source_url + verbatim_quote + argument ≥2 句；reviewer 獨立判定 strength 不信任 subagent 自評標籤；無 dissent 必須明確 `NO-STRONG-DISSENT-FOUND`
  - Hard cap=3（METR 2025 agent degradation 實證），耗盡後輸出 partial report 並要求人工裁決
  - Budget guard：soft 60k / hard 120k，**pre-flight 檢查**（不在 Phase A 啟動後才發現超預算）
  - Prompt injection 結構性防禦：CLAIM 用非 XML `---CLAIM-START---` / `---CLAIM-END---` 分隔 + 確定性剝 `<`/`>`；WebSearch 結果顯式不可信；evidence bundle 包 `<evidence>` tag 且禁 `##` heading 污染 reviewer prompt；耗盡迭代時只輸出 summary-only partial report
  - Verdict 區分：STRONG dissent 存在 ≠ `CONFLICTED`；只有跨維度對主張本身互斥才 CONFLICTED
- `README.md`: 新增 `/verify-evidence-loop` 至 Usage、Skills 總覽、決策樹

### 方法論依據
- Self-Refine (arXiv:2303.17651)、Reflexion (arXiv:2303.11366)、Multi-agent debate (arXiv:2305.14325)、LLM-as-Judge (arXiv:2306.05685)
- IEEE 1012-2016 V&V、NIST SP 800-160、DAMA-DMBOK Completeness
- Anthropic "Building Effective Agents" (2024)
- 反面：Huang et al. (arXiv:2310.01798, LLMs cannot self-correct)、Dziri et al. (arXiv:2305.18654, Faith & Fate)、METR 2025 agent degradation

## [v1.21.1] - 2026-04-16

### Changed
- `/ecc-skill-defer`: conf 依 ECC 1.10.0 `install-modules.json` 結構更新；新增 operator-workflows 模組區塊；defer 60 → 71（+11）
- 新增 defer：`manim-video`、`remotion-video-creation`（media-generation）、`brand-voice`、`social-graph-ranker`（business-content）、`nestjs-patterns`、`laravel-plugin-discovery`（framework-language）、`connections-optimizer`、`customer-billing-ops`、`google-workspace-ops`、`project-flow-ops`、`workspace-surface-audit`（operator-workflows）
- `DEFER_REFERENCE.md`: 同步新增 skills 與 operator-workflows 模組
- `README.md`: defer 數量 61 → 71，結構版本標示 1.9.0 → 1.10.0

## [v1.21.0] - 2026-04-06

### Changed
- 全 12 個 skill 導入 manifest-driven 完整性驗證（依據：DAMA-DMBOK Completeness、ITIL CMDB Reconciliation、arXiv:2509.18970）
- `/update`: Step 1 新增「變更 Manifest」；Step 2 改為逐條 set difference 比對；Step 3 新增「知識寫入 Manifest」；Step 5 改為 manifest-driven + grep/glob 確定性驗證
- `/design`: Step 3 新增需求追蹤矩陣（Requirements Traceability Matrix）；Step 4a 新增「需求覆蓋率」審查維度
- `/assist`: Handoff Protocol 新增 Completeness Declaration 欄位；Industry/Community 欄位升級為強制填寫
- `/pr`: Step 1b 新增 Context Manifest；Changes 新增 commits 計數驗證；Context 新增逐條比對
- `/curation`: Step 1 新增問題 manifest；Step 4 新增修正後驗證（grep -c）；Step 5 新增完成率
- `/learn-eval-deep`: Step 3 新增 Bridge 輸出完整性檢查；Step 4 新增資料來源覆蓋率標註
- `/triage`: Step 2 新增退役前影響分析（grep 依賴搜尋）；新增 Step 5 退役後驗證
- `/plan-archive`: Step 2 新增步驟完成 manifest；Step 3 改為逐條 PASS/FAIL + 完成率閾值
- `/worktree`: status 新增一致性檢查（orphan 偵測）；cleanup 新增操作後驗證
- `/notion-plan`: Step 2d 新增擷取完整性檢查；Step 4 改為 4 條 checklist PASS/FAIL
- `/ecc-skill-defer`: 核心 skill 保護升級為 HITL Guard；apply/restore 新增操作驗證
- `/playwright-human-in-the-loop`: Step 3 新增強制 snapshot checklist；Step 4 改為 manifest-driven 報告

### Fixed
- `/pr`: Step 1a 修正 stale local branch 陷阱 — 所有 `git log/diff <base-branch>..HEAD` 改為 `origin/<base-branch>..HEAD`，新增 `git fetch origin` 前置步驟與 `gh pr diff` 交叉驗證機制

## [v1.20.0] - 2026-04-06

### Added
- `/evidence-check`: 新增獨立證據查驗 skill — 四維度並行調查(D1 學術研究、D2 業界標準、D3 最佳實踐、D4 社群共識+反面意見)，2 個 haiku subagent 並行，跨來源衝突偵測(AGREE/PARTIAL/CONFLICT/NO-DATA)，5 級 verdict，輸出與 /design plan 格式相容
- `README.md`: 新增 `/evidence-check` 至 Usage、Skills 總覽、決策樹

## [v1.19.0] - 2026-04-05

### Changed
- `/design`: Step 3 planner 要求新增「社群共識」和「反面意見與已知陷阱」；Step 4a 品質檢查新增對應維度；plan 模板新增 Community Consensus & Dissenting Views 表格
- `/assist`: 新功能和重構 pipeline 的 planner 標記含社群共識/反面意見；handoff protocol 新增 Community Consensus section
- `/pr`: Step 1b 對話脈絡分析新增「社群共識與反面意見」提取項；PR description Context 模板新增社群共識範例
- `/pr`: 新增 Step 2c plan 歸檔檢查 — commit 前自動掃描 `plans/active/` 已完成的 plan 並歸檔
- `README.md`: 同步 /design（subagent 隔離審查、社群共識）、/assist（路由表社群共識）、/pr（社群共識提取、Step 2c）描述

## [v1.18.1] - 2026-04-04

### Added
- `rules/worktree-prompt.md`: 新增 Worktree 路徑慣例 — 禁止 `.claude/worktrees/`（EnterWorktree 預設路徑），改為 sibling 目錄格式 `<project>-<slug>/`
- `rules/refactor/remove-architect-pipeline.md`: 新增 architect agent 禁用規則，基於消融實驗結果，列出 planner 等替代方案

## [v1.18.0] - 2026-04-01

### Changed
- `/design`: 移除消融實驗表現最差的 ECC architect agent（delta=-0.50），將架構審查職責重新分配至 planner（Step 3 架構決策）和品質審查（Step 4a subagent 隔離審查）
- `/design`: Step 4a 改用 general-purpose subagent 隔離審查，含 PASS/FAIL 結構化回報和回饋迭代（最多 2 次）；新增可擴展性審查維度；業界支撐改為主動驗證
- `/design`: Step 2 複雜度評估恢復低/中等差異化路徑（低複雜度跳過架構審查）
- `/assist`: 新功能和重構 pipeline 標記 planner 含架構決策
- `check_skill.py`: 新增 `redundancy-peers` frontmatter 支援排除 sibling skill 互相扣分；跳過隱藏目錄避免 worktree 干擾

## [v1.17.2] - 2026-03-25

### Added
- `/pr`: Release PR 標題格式 — base branch 為 master/main 時強制使用 `Release vX.Y.Z: <摘要>`
- `/pr`: CHANGELOG 檢查步驟 — Release PR 時自動比對 commits 與 CHANGELOG.md，缺少記錄會提示更新

## [v1.17.1] - 2026-03-23

### Changed
- `/ecc-skill-defer`: conf 依 ECC 1.9.0 `install-modules.json` 模組結構重組；新增 swift-apple（6 skills）和 framework-specific security（4 skills）；113 → 52 active（61 deferred）
- `DEFER_REFERENCE.md`: 改為模組對齊的雙表格格式（Whole Modules / Within-Module）
- `README.md`: defer 數量更新 24 → 61

## [v1.17.0] - 2026-03-21

### Changed
- `/design`: 資源盤點去版本化，新增 docs-lookup/typescript-reviewer agents 和 /docs /aside /skill-health /prompt-optimize /blueprint /context-budget /save-session /resume-session commands；Step 2 新增多 session 複雜度路徑
- `/assist`: agent 表新增 docs-lookup/typescript-reviewer；commands 表新增 7 項 1.9.0 commands；routing 表新增 5 項情境
- `/ecc-skill-defer`: 新增 `--reason` 支援 defer 原因追蹤（DEFER_LOG.md）；新增 /skill-health 整合建議；conf 新增 39 個 1.9.0 語言/領域/媒體 skills
- `/triage`: Step 1 新增 /skill-health 補充視圖建議
- `README.md`: 總覽表補齊 /triage 和 /learn-eval-deep；決策樹新增對應入口

## [v1.16.1] - 2026-03-20

### Fixed
- `pr/SKILL.md`: allowed-tools 補上 `Agent`（Step 2b 委派 refactor-cleaner 需要 Agent tool 權限）
- `pr/SKILL.md`: fenced code block 加上 python 語言標識（MD040）
- `pr/SKILL.md`: 「適用所有修正」→「套用所有修正」錯字修正
- `design/SKILL.md`: ECC Resources 表格與 Phase 2 checklist 補上「重複程式碼合併」，與 pr/README 一致

## [v1.16.0] - 2026-03-19

### Added
- `/simplify` 並行互補整合：code-reviewer（診斷）後自動加入 refactor-cleaner（治療）
  - `/pr`: 新增 Step 2b 自動修正步驟，Quick Review 後委派 refactor-cleaner 修正 dead code、命名、nesting
  - `/assist`: 新功能、Bug 修復、Review pipeline 自動附加 `/simplify`（重構和文件 pipeline 除外）
  - `/design`: Plan 模板 Phase 2 品質保障加入 `/simplify`，ECC Resources 表格加入 refactor-cleaner 範例
  - 所有自動修正步驟含 HITL 確認（套用全部 / 逐一確認 / 跳過）
- `README.md`: 新增 `/simplify` skill 描述、Usage quick-reference、選什麼流程圖條目

### Unchanged
- `/update`: 文件審查不適用程式碼簡化，保持原樣

## [v1.15.0] - 2026-03-17

### Removed
- `plan-rename`: 移除整個 skill（SKILL.md + 3 個 hook 腳本 + v2 實作計畫）— Claude Code 已內建 Plan Mode 自動命名功能，不再需要自訂 hook
- `README.md`: 移除 Background Hooks 段落

## [v1.14.0] - 2026-03-14

### Added
- `/curation`: Learned Skills 品質管控 skill
  - 掃描 `~/.claude/skills/learned/` 格式問題（frontmatter、評分格式、廢棄標記）
  - 自動修正格式問題（從內容推斷 name/description）、HITL 確認後刪除廢棄項目
  - 批次操作模式（全部修正 / 只修格式 / 逐一確認 / 只查看）
- `/update` Step 3: 對話 context 整理（新增步驟，位於 code-reviewer 之後、learn-eval 之前）
  - 從對話中提取決策脈絡、研究成果、架構演進、Bug 根因等有價值的 context
  - 三層分流：專案知識庫（給人讀）、learned skills（給 Claude 學）、MEMORY.md（跨 session 狀態）
  - 知識庫目錄不硬編碼，HITL 確認寫入位置

### Changed
- `/update` Step 4 (原 Step 3, learn-eval): 新增寫入格式強制規範
  - 強制 frontmatter（name/description/user-invocable/origin）
  - 品質評分統一為 5 維度表格格式，廢棄單行格式
- `/update` Step 5 (原 Step 4, 知識庫交叉比對): 從被動報告改為主動寫入
  - 偵測遺漏時起草修正內容，HITL 確認後直接寫入
  - 新增 MEMORY.md 路徑定位規則與自動建立邏輯
  - 新增 Step 3 context 寫入完整性確認
- `/update`: 步驟重新編號（原 Step 3-6 → Step 4-7）

## [v1.13.0] - 2026-03-14

### Added
- `rules/worktree-prompt.md`: 實作 plan 或大範圍變更前，agent 自動詢問是否使用 worktree 隔離開發
  - 觸發條件：實作 `plans/active/` 中的計畫、跨 5+ 檔案的 migration/refactoring、基礎設施變更
  - 跳過條件：使用者已明確表態、當前目錄已是 worktree、單檔小修

## [v1.12.0] - 2026-03-12

### Changed
- `/design`: 新增 Step 0 條件式 HITL — agent 判斷任務複雜度後詢問是否啟用 task tracking
  - frontmatter 新增 `TaskCreate, TaskUpdate, TaskList` 至 allowed-tools
  - 各步驟加入條件式 task tracking 標記（含 activeForm、addBlockedBy）
- `/assist`: 新增 Step 0 條件式 HITL — agent 判斷任務複雜度後詢問是否啟用 task tracking
  - frontmatter 新增 `TaskCreate, TaskUpdate, TaskList` 至 allowed-tools
  - Step 4 Pipeline 執行加入條件式 task tracking 標記（含 activeForm、addBlockedBy）
- `~/.claude/CLAUDE.md`: Task Tracking 規則從「超過 3 步驟主動啟用」改為「agent 判斷 + HITL 詢問，不可自動啟用」

### Fixed
- `/pr`: 移除 frontmatter 中從未使用的 `Task` allowed-tool

## [v1.11.0] - 2026-03-12

### Added
- `plan-rename`: 將 hook 系統從 `~/.claude/scripts/` 遷移至 agent-skills repo，納入版本控制
  - `plan-rename/plan-rename-hook.sh`：PreToolUse ExitPlanMode hook，從 Plan H1 標題自動命名 session
  - `plan-rename/plan-rename-guard.sh`：Stop hook，compaction 後自動重新注入 custom-title
  - `plan-rename/SKILL.md`：完整文件（`user_invocable: false`），含使用前須知、成本分析、穩定性風險、安裝步驟

### Changed
- `README.md`: 新增 Background Hooks 區塊，說明 plan-rename 非使用者呼叫的 hook skill

### Removed
- `~/.claude/scripts/plan-rename-hook.sh`：遷移至 repo，不再為孤兒檔案
- `~/.claude/scripts/plan-rename-guard.sh`：同上
- `~/.claude/skills/learned/claude-code-session-rename-hook.md`：內容已併入 `plan-rename/SKILL.md`

## [v1.10.1] - 2026-03-11

### Changed
- `/pr`: PR 標題自動帶入 Notion ticket 資訊（ticket 編號或票名，二擇一），偵測對話中的 `[A-Z]+-\d+`、Notion URL 或「Notion Ticket」字樣

## [v1.10.0] - 2026-03-10

### Added
- `/notion-plan`: 貼上 Notion URL，自動抓取頁面需求內容並串接 `/design` 建立實作計畫
  - 支援 `notion.so`、`notion.site`、短網址等多種 URL 格式
  - 雙路徑策略：WebFetch（快速）→ Playwright MCP（完整 JS 渲染 fallback）
  - 自動處理長頁面捲動載入、Toggle 展開、登入偵測
  - 擷取內容整理為結構化 Markdown 後，自動觸發 `/design` 建立 plan.md
  - 內容品質確認步驟，空白或不完整時提示使用者

### Changed
- `README.md`: 新增 `/notion-plan` skill 描述、Usage、選擇流程圖

## [v1.9.0] - 2026-03-10

### Added
- `/playwright-human-in-the-loop`: Playwright Human-in-the-Loop 瀏覽器操作 skill
  - 操作分級：重大操作（建立/刪除資源、修改權限、安全敏感欄位、費用、不可逆操作）需 `AskUserQuestion` 確認
  - 非重大操作（導航、填寫 metadata、搜尋、截圖）自動執行
  - 安全敏感欄位（Policy JSON、IAM policy document）即使是填寫也視為重大操作
  - 4 步驟執行流程：確認 MCP → 理解任務 → 執行 → 報告
  - 頁面載入失敗允許一次重試

### Changed
- `README.md`: 新增 `/playwright-human-in-the-loop` skill 描述、Usage、選擇流程圖

## [v1.8.0] - 2026-03-09

### Added
- `/update`: Step 6 Pipeline 串接 — 支援 `/update /pr` 一條指令完成知識沉澱 + PR 交付
  - 檢查 `$ARGUMENTS` 中的 skill 名稱，完成後自動觸發下游 skill
  - `[PIPELINE: from /update]` 標記通知下游跳過已完成步驟
  - 資源去重：`/pr` Step 2 (Quick Review) 自動跳過（`/update` Step 2 已用 code-reviewer agent 完成）
  - 支援傳遞參數（如 `/update /pr 7238`）

### Changed
- `README.md`: 新增 pipeline 串接說明、去重表格、`/update /pr` 用法範例、選擇流程圖更新

## [v1.7.3] - 2026-03-07

### Fixed
- `plan-rename`: 從 PostToolUse Write 改為 PreToolUse ExitPlanMode — Plan Mode 使用 `ExitPlanMode` 存檔而非 `Write`，且 `ExitPlanMode` 不觸發 PostToolUse，導致 hook 永遠不會被 Plan Mode 觸發
- `plan-rename`: 移除 Write fallback 路徑，只處理 `ExitPlanMode` 的 `tool_input.plan`

### Changed
- `plan-rename/README.md`: 改寫為 PreToolUse ExitPlanMode 機制；新增 Prerequisites、Known limitations；移除過時的 Troubleshooting
- `README.md`: plan-rename 區段更新機制說明，新增 claude-hud 依賴提醒和手動設定提醒

## [v1.7.2] - 2026-03-07

### Fixed
- `plan-rename`: `sessionId` → `session_id`（hook stdin 使用 snake_case，非 camelCase）
- `plan-rename`: 移除 `os.getcwd()` slug 路徑拼接，改用 hook stdin 提供的 `transcript_path` 直接定位 session JSONL

## [v1.7.1] - 2026-03-07

### Fixed
- `plan-rename`: path filter 改用 `realpath` + `startswith` 防止 traversal 繞過
- `plan-rename`: sessionId 新增 regex 驗證，防止 `os.path.join` path traversal
- `plan-rename`: exception 分層處理，unexpected error 輸出 stderr 可觀測
- `plan-rename`: `echo` 改為 `printf '%s\n'`，避免 backslash 解析問題

### Changed
- `plan-rename/README.md`: 新增 Troubleshooting 區段、手動重命名覆蓋提醒、截斷描述精確化

## [v1.7.0] - 2026-03-07

### Added
- `plan-rename`: PostToolUse hook，Plan Mode 自動從 H1 標題重命名 session
  - 攔截 Write tool，篩選 `~/.claude/plans/*.md`
  - 擷取 H1 標題，去除 `Plan:` 等前綴，截斷 80 字元
  - 直接 append `custom-title` 到 transcript JSONL（與 `/rename` 相同機制）
  - 從 hook stdin 取 sessionId，多 session 並行安全

## [v1.6.0] - 2026-03-07

### Added
- 全 skill 業界/學術參照機制：規劃階段須附上業界標準（RFC、W3C、OWASP、12-Factor）、學術研究或標準化方案依據
- 全 skill ECC 資源分配介入：核心 skill 深度整合盤點確認，輔助/輕量 skill 加入資源感知 blockquote
- `/design`: plan.md 模板新增 `## Industry & Standards Reference` 表格
- `/assist`: 新功能需求 pipeline 加入業界/學術方案調研；新增 ECC 資源分配原則；Handoff Protocol 新增 Industry & Standards Referenced 欄位
- `/pr`: 對話脈絡分析和 PR Description Context 新增業界/學術依據
- `/update`: learn-eval 提取範圍新增業界標準應用與標準化方案選型；交叉比對新增「業界標準是否已記錄到知識庫」確認項
- `/plan-archive`: 歸檔驗證新增業界/學術參照落實情況
- `/ecc-skill-defer`: 新增 Notes — 核心規劃 skill 保護提醒

## [v1.5.0] - 2026-03-07

### Changed
- `/assist`: 新增 `harness-optimizer`、`loop-operator` 至 agent 表格與路由規則
- `/design`: ECC 資源盤點納入 v1.8 新增 agents 與 commands；計畫品質檢查表新增 Eval 基線維度
- `/ecc-skill-defer`: README 更新數字（23 deferred / 65 total），移除過時的 token 數量描述
- 同步 ECC v1.8.0 的 agent harness 定位與 eval-driven 開發概念

## [v1.4.2] - 2026-03-07

### Fixed
- `/ecc-skill-defer`: 支援 marketplace 安裝路徑（`plugins/marketplaces/`），優先偵測 marketplace 再 fallback 至 cache

### Changed
- `/ecc-skill-defer`: 配合 ECC v1.8.0 更新，v1.8 新增的 9 個 skills 保持 active（42 active / 23 deferred）

## [v1.4.1] - 2026-03-07

### Changed
- `/ecc-skill-defer`: 調整預設 defer 清單 — meta skills 區只保留 `continuous-learning`，其餘 6 個改為 active（33 active / 23 deferred）

## [v1.4.0] - 2026-03-07

### Added
- `/ecc-skill-defer`: ECC Skill 漸進式載入管理，減少 init token 消耗
  - `apply` 一鍵 defer config 中列出的 skills（SKILL.md → SKILL.deferred.md）
  - `restore <name>` / `restore --all` 按需啟用
  - `status` / `list` 檢視目前 active/deferred 狀態
  - 預設 defer 29 skills（Django/Spring Boot/Java/C++/business/meta），省 ~1,800 init tokens
  - 附帶 `ecc-skill-defer.conf` 可自訂 defer 清單

## [v1.3.0] - 2026-03-05

### Added
- `/plan-archive`: 新增 plan 生命週期管理 skill，自動化 active → completed 歸檔流程
  - 自動偵測 `plans/active/` 中待歸檔的 plan
  - 補充「狀態：✅ 完成」標記與驗證結果段落
  - 內建 PostToolUse Hook 設定（ExitPlanMode 自動存 active）
  - 內建 CLAUDE.md Rule 範本（提醒實作後歸檔）
  - 目錄規範：`plans/active/` → `plans/completed/` → `plans/archived/`

## [v1.2.0] - 2026-03-05

### Changed
- `/update` Step 4: 新增知識庫交叉比對（HITL 確認），session 結束前逐一確認 MEMORY.md、learned skills、專案文件是否正確更新
- `/update`: 原 Step 4 總結報告移至 Step 5，新增「知識庫交叉比對」欄位

## [v1.1.0] - 2026-03-05

### Changed
- `/update`: 更新前強制 HITL 確認計畫修改的檔案清單
- `/update`: 偵測「文件庫/知識庫」歧義，不確定時詢問使用者
- `/update` Step 2: 加入 cross-check，確認無遺漏 / 無錯誤修改的文件

## [v1.0.0] - 2026-03-05

### Added
- `/pr`: 自動分析 git diff + 對話脈絡，生成完整 PR description；包含 Quick Review 與 base branch 防護
- `/update`: 依序執行 doc-updater → code-reviewer → learn-eval，將 session 變更沉澱為文件與知識
- `/design`: 透過 planner + architect 建立實作計畫，輸出 plan.md 供確認後才進入實作
- `/assist`: 萬用助手，智慧路由至最佳 agent pipeline

<!-- 版本比較連結（Keep a Changelog 慣例）；補歷史版本連結時比照下方格式沿用即可 -->
[Unreleased]: https://github.com/ashe-li/agent-skills/compare/v2.2.0...HEAD
[v2.2.0]: https://github.com/ashe-li/agent-skills/compare/v2.1.0...v2.2.0
[v2.1.0]: https://github.com/ashe-li/agent-skills/compare/v2.0.0...v2.1.0
[v2.0.0]: https://github.com/ashe-li/agent-skills/compare/v1.28.0...v2.0.0
[v1.28.0]: https://github.com/ashe-li/agent-skills/releases/tag/v1.28.0
