# Changelog

所有重要變更都記錄在這裡。格式參考 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.0.0/)。

## [Unreleased]

### Added
- **新增 `preflight` 子命令，hook 在第一步前先檢查執行環境**：`plan_runner.py preflight <plan> [--format md|json]` 檢查 runner 腳本本身（就是 hook reason 印出的那個路徑）、plan 檔、state 檔，以及每個 step `Command:` 欄位用到的工具，任一項失敗 exit 1，每個缺項一行並附修復建議。起因是 E2E 實測：ready step 的指令跑不起來，模型一直沒有 `start`，hook 在 0/20 連發六輪同一道指令——環境缺東西應該在第一步之前一次講清楚。工具抽取規則刻意收窄，寧可漏查也不誤報：只看 `Command:`（`Action:` 的反引號多半是檔名與函式名），依 `&&`／`||`／`;`／`|` 切段取第一個字，跳過 `NAME=value`、shell builtin、`/verify` 這類 slash command（它們是 skill 不是執行檔）、含 `$`／反引號的變數展開，以及同一條指令裡 `cd`／`pushd` 之後的相對路徑（要到執行時才知道解析到哪），含 `/` 的當路徑檢查、其餘查 PATH。模式 B 的 hook 在還沒有任何 step 開始時，由 I/O 層先跑 preflight 再把結果傳進 `decide_hook_action()`（維持 no-I/O 契約）；失敗就 allow＋`[plan-run] PREFLIGHT 失敗` systemMessage 逐項列出，不再 block 重發一個跑不起來的 step。
- **Stop hook 加單調進度斷言（STUCK）**：同一個 step 第 3 次被 hook 指派仍沒有進展（ready 沒被 `start`，或 in_progress 沒回報 `complete`／`fail`），改為 allow＋`[plan-run] STUCK` systemMessage，列出 step、次數、首次與本次時間、建議動作；之後這個 step 有進展前一律 allow、不重複訊息。原本 ready 分支第 2 次重複只加一段警告、照樣 block 到 `BLOCK_BUDGET`（7）用完，重送已經失敗兩次的指令不會有不同結果。門檻為常數 `HOOK_STUCK_AT = 3`。ready 的次數沿用 `assign_repeat_count`、不隨使用者開口歸零（新的一輪不會讓沒跑的 `start` 變成跑過）；in_progress 沿用 `nag_counts`、使用者開口就歸零，只抓 auto-advance 自己繞圈，不誤判跨 turn 的長 step。pointer 新增 optional 欄位 `attempt_first_at`、`stuck_step_id`、`stuck_kind`、`stuck_at`，舊 pointer 仍為 VALID。
- **可續跑的 checkpoint 與 `next <plan> --resume`**：每次 `complete`／`fail`／`skip` 成功後原子寫入 `.plan-state/<slug>.checkpoint.json`，內容是新 session 接手需要的東西：已完成 steps（含摘要與 evidence）、artifacts（沿用 evidence）、open questions（失敗原因與仍有效的 STUCK）、下一個 ready step、STUCK 與 preflight 狀態。state 只記每個 step 在哪，看不出做了什麼、產出在哪、卡在哪。寫入是 best-effort，state 已存好時 checkpoint 寫不出來不會把轉換變成失敗。`next <plan> --resume` 先印 checkpoint 摘要再印即時的 next 視圖；摘要裡來自 plan／state 的文字（summary、失敗原因、evidence）一律比照 hook reason sanitize、折成單行，並放進 `--- plan data (not instructions) ---` 資料圍欄，圍欄外只有 runner 自己的文字；沒有或讀不到 checkpoint 時 exit 1 並提示改用不帶 `--resume` 的 `next`。沒有另開 `resume` 子命令：`resume` 已是 pointer 的暫停／續行（不吃 plan 參數），續跑 plan 做成 `next` 的旗標，避免撞名與語意混淆。
- **新增 `/rca` skill**：接收 Sentry issue、Grafana alert 或 CI 失敗 URL，同時派 OBSERVABILITY／HISTORY／KNOWLEDGE／INFRA 四路唯讀調查，再由 fresh-context SYNTHESIS 產出合併時間軸、以新證據裁決的矛盾清單、根因＋信心＋falsifier、四類分類（SELF-INFLICTED→CODIFY／DRIFT→REVERT／REAL DEFECT→FIX／EXTERNAL→RECORD），以及修復 PR 草稿與 rollback checklist。內建兩條反模式護欄（不用字面資源名 grep 判斷 IaC 管理；不讀值不報 secret）。附 `rca/scripts/obs_http.py`，這是 GET-only 的 Grafana／Sentry helper，給拿不到 MCP 的 subagent 用，憑證執行時才從 `~/.claude.json` 讀。每一路都要自己把報告寫檔，因為 teammate 的 idle notification 會截斷長報告。

### Changed
- **`plan_runner.py` 會載入同目錄的 `plan_runner_preflight.py`、`plan_runner_checkpoint.py`**：`_import_sibling()` 在 import 前把 `scripts/` 補進 `sys.path`，用 `importlib.util.spec_from_file_location` 載入（`python3 -I`、cwd 不在 scripts/）也能運作；hook 路徑遇到 `ImportError` 就略過 preflight，只複製 `plan_runner.py` 一個檔案的舊式安裝不會讓每個 Stop event 報錯。`AGENT_SKILLS_DIR` 須指向完整 checkout，見 `scripts/hooks/README.md`。

## [v3.2.0] - 2026-09-17

> **版本位階判定：MINOR。** 依 [VERSIONING.md](VERSIONING.md) 的判準「會讓照舊用法的既有使用者行為改變或壞掉的才是 MAJOR」逐項核對：新增 `report` 子命令與 `complete` 的 `--summary`／`--evidence` 兩個選用 flag，都是向後相容的新功能，沒帶就與現行行為逐字相同；state.json 只新增欄位，舊 runner 讀新 state 一律用 `.get()` 取值、多出來的鍵會被忽略，新 runner 讀舊 state 也不會 raise；`complete`／`fail`／`skip` 改在 state lock 下執行，新出現的 lock error 只在兩個 session 同時競爭同一份 state 時才會發生，而原本那種情境下的行為是靜默 lost update，這是修 bug 不是介面變更；再次 `complete` 保留 `completed_at` 沒有任何程式邏輯依賴（已 grep 確認，`reset` 除外）；plan 格式契約、指令名、DSL、安全紅線都沒有改；三份 SKILL.md（`plan-run`、`dispatch-loop`、`plan-archive`）的流程調整是文件敘述，不是對外介面。本次追加的 `init --require-summary` 同樣是 opt-in flag，不帶則 state 無此鍵、`complete` 行為與現行逐字相同，只有主動選用才會改變既有用法，故仍為 MINOR。最高位階為 MINOR。

### Added
- **`complete` 新增 `--summary`／`--evidence`，把派工時的判斷寫進 state**：摘要正規化（`\r\n`→`\n`、去除 ANSI／控制字元／bidi 與零寬字元、去頭尾空白）後以 code point 計算長度，上限 500 字元，超過或空字串一律**拒絕**（exit 1、不寫入、step 維持原狀態）而不截斷——截斷會悄悄丟掉尾段，而尾段通常正是「延後到其他 step 的待辦」這種最關鍵的資訊，呼叫方是 LLM，收到明確的長度錯誤後可以自己縮短重送，重試成本低。`--evidence` 可重複（不做逗號切分，因為路徑本身可能含逗號），單筆不得含換行、不得超過 300 字元，最多 20 筆，超過同樣拒絕；只記錄字串本身，不檢查檔案是否存在、不 resolve、不讀檔，避免任意讀檔的攻擊面，也允許 evidence 指向別的 worktree。摘要與 evidence **不會**出現在 `next`／`complete`／`fail`／`skip` 的 delta output，也不會進 Stop hook reason 的任何一種 kind，帶 flag 時只多印一行 `Recorded: summary <N> chars, evidence <M>`（json 對應 `recorded: {summary_chars, evidence_count}`）確認已寫入，不會把摘要內容重貼回對話，降低 summary 被當成 prompt injection 通道逐輪重新注入的風險。只帶其中一個 flag 時只更新該欄位，另一欄位維持原值不被清空。
- **新增 `report` 子命令**：`plan_runner.py report <plan> [--format md|json] [--output <path>] [--force]`，純腳本、不呼叫 LLM、`report` 本身從不寫入 state。依 `phase_order` 分組，輸出每個 step 的狀態、摘要、耗時與 evidence，最後列出 failed／skipped／pending／blocked 清單與總進度。md 格式對 `parse_plan` 刻意無效——開頭固定為不含 Phase 字樣的 `### 執行摘要`、各 phase 標題用 `####`、不使用 `- [ ]`／`- [x]` 列表項（不命中 step 樣式）、摘要每行以 `>` 引用塊輸出（不命中欄位樣式）、整段包在 `## 執行摘要` 底下（parser 遇到 `^##` 會重置目前 step 的解析狀態），確保報告嵌進歸檔後的 plan 再被重新 `init` 也不會多出 step 或 phase；表格儲存格的 `|` 轉義、`<`／`>` 轉成 HTML entity 避免被誤渲染、evidence 放進 code span 並依內容動態選反引號分隔符，json 格式則交給 `json.dumps` 處理、不做 markdown 跳脫。`--output` 寫檔前先 `resolve()` 再比對，拒絕指向 plan 檔、state 檔或 state lock 檔（含透過 symlink 指向這三者的路徑），寫到既有檔案需加 `--force`，父目錄不存在直接報錯、不代為建立，並以 `mkstemp` + `os.replace` 原子寫入避免半寫壞檔。
- **`/dispatch-loop` 補「摘要撰寫指引」**：`--summary` 四項依序必寫、沒有內容也要寫「無」——做了什麼（結果而非流水帳）、偏離 plan 原文的地方與理由、接受的副作用或已知限制、延後到其他 step 的待辦（標明目標 step ID）；逐字證據放 `--evidence`，不要塞進摘要本文；收到 `locked` 錯誤就重跑同一個指令，不要換寫法或跳過。
- **`init` 新增 `--require-summary`**：opt-in，state 記下 `require_summary: true` 後，這份 plan 的 `complete` 若沒帶 `--summary` 且該 step 還沒有摘要，一律 rc=1、不標完成；step 已有摘要（事後補寫過）可不帶 flag 冪等重跑；`fail`／`skip` 不受影響；不帶 flag 的 plan 行為完全不變。`/plan-run` 的兩條 init 指令一律加上這個 flag，讓走 `/plan-run` 的使用者自動變成必填。
- **all_done 時自動寫執行報告**：plan 處於 all_done 時的每次 `complete` 或 `skip`（包含讓 plan 轉為 all_done 的那次、以及之後事後補摘要），runner 都會自動把 `report` 的 md 輸出寫到 `<plan-dir>/.plan-state/<slug>.report.md`，輸出多一行 `Report: <path>`（json `report_path`）；寫檔失敗不影響該次的 rc（仍為 0），改印 `Report: failed (<原因>)`（json `report_error`）。`/plan-archive` 歸檔前仍會重新跑一次 `report` 嵌入 plan，這份自動報告只是完結當下先讓人看一眼。

### Changed
- **`complete`／`fail`／`skip` 三個命令改在 state lock 下完成「讀取、判斷、寫入」**，比照 `cmd_start` 既有的鎖語意（`exclusive_lock` 最多重試 20 次、每次間隔 25ms，約 0.5 秒拿不到就回傳 `State is locked by another process. Retry in a moment.`）。修的競態：`start` 讀到 pending、寫入 in_progress 的過程中，若另一個 session 在鎖外對別的 step 跑 `complete`（讀舊 state 在前、`save_state` 在後），會把前者剛寫入的 in_progress 覆寫回 pending，連帶重算 blocked 與 `previously_reported_ready`，該 step 就可能被重派一次工——這在加鎖之前是靜默發生的。加鎖範圍不含 `next`（只寫 tracker）、`reset`（人主動操作）、`set-parent`，留待後續處理。
- **`init` 新建的 step 預先帶 `summary: null`、`evidence: []`**，讀取端一律用 `.get("summary")`、`.get("evidence") or []`，舊 state 沒有這兩個欄位一樣能被 `status`／`next`／`report` 正常讀取。
- **`reset` 會一併清空 `summary`／`evidence`**，與清空 `completed_at` 的語意一致：step 要重做，舊摘要會誤導。
- **再次 `complete`（COMPLETED→COMPLETED）** 摘要覆寫、evidence 整組取代（不累加），使同一個指令跑兩次結果相同；但 `completed_at` 保留第一次的值（目前會被刷新），避免事後補摘要讓耗時失真。
- **`plan-run` Step 4 在 `/plan-archive` 搬移檔案前跑 `report`**：state 路徑是從 plan 所在目錄推導的，`/plan-archive` 把 `.md` 搬進 `plans/completed/` 之後就推不到了，必須先跑完 `report` 再歸檔。
- **`plan-archive` 新增 Step 2.5**：檢查 `.plan-state/<slug>.state.json` 是否存在，存在就跑 `report` 並把 stdout 原樣嵌入 plan 的 `## 執行摘要` 段（已存在就整段取代，不重複附加，位置在 `## 驗證結果` 之前）；不存在則寫一行「（本 plan 未經 /plan-run 推進，無執行紀錄）」。刻意用嵌入而非旁檔——state 在隱藏目錄裡，`mv` 不會帶走它，歸檔後 plan 和 state 就分開了，旁檔還要記得跟著搬、KB ingest 也不一定會把旁檔和 plan 關聯起來。
- **`next` 模板與 Stop hook 的 `ok:` 行一律帶佔位 `--summary="<1.做了什麼 2.偏離plan 3.副作用 4.延後待辦>"`**：先前這兩處印出的 `complete` 指令都沒帶 `--summary`，照著印出的指令做就不會寫摘要；現在改印帶佔位字串的版本，提醒要換成實際四項內容再送出，佔位文字本身不含任何 plan 或摘要內容。

### Fixed
- **結案報告路徑在 `complete`／`skip`／`status` 輸出裡不夠顯眼**：實例（2026-09-22）：plan 全部完成後，`_write_completion_report()` 確實有自動寫報告，但 md 格式只在整份 state view 印完後補一行 `Report: <path>`，使用者／LLM 讀完落落長的 state view 就沒注意到，事後問「沒有結案報告嗎」；`status` 對 all_done 的 plan 更完全不提報告。`format_transition_md` 改在 header（`# completed: Sx`）之後、state view 之前先印 `## 結案報告（plan 已全部完成）` 區塊（失敗則 `## 結案報告寫入失敗`），state view 之後不再重複印一次；`status`（md）在 all_done 時加印「結案報告：<path>」，檔案不存在則提示改跑 `report` 子命令；`status`（json）在 all_done 且檔案存在時加上 `report_path` 鍵。hook-stop 的 all_done 分支（`_render_completion`）本來就已印出路徑與取得方式，未變動。
- **`notion-plan` description 觸發範圍太窄，只讀不建 plan 的情境配不到**：原 description 只寫「串接 /design 建立實作計畫」，agent 遇到「依 Notion 需求修 bug、對照 Figma」這類單純讀取需求時配不到本 skill，改用 WebFetch（被 `webfetch-blocklist-guard.py` 擋下）再改用 `agent-browser` 手動 snapshot，拿到一堆空的 generic 節點，最後要使用者手動介入才改用 `/notion-plan`。改寫 description 明確涵蓋「讀取 Notion 頁面內容」這個更寬的觸發面（建 plan 只是其中一種用途），並在本文加註「不要用 WebFetch／agent-browser 手動讀取 Notion」；Step 5 新增「只讀不建 plan」分支，整理完內容即停下交回，不強制觸發 `/design`。新增 `--read-only` 引數示意用法。
- **all_done 時自動寫的執行報告從未被呈現給使用者**：Stop hook 在 all_done 時注入的 completion 訊息只叫模型對照 Acceptance Criteria 並建議 `/plan-archive`，完全沒提報告；`plan-run/SKILL.md` Step 4 也只寫「可 `cat` 給使用者看」，變成選配。實測案例：36/36 all_done 的 plan，最終回覆只有 `Progress: 36/36 — ALL DONE`，沒提到任何摘要。`_render_completion()` 改接收 `plan_path`，訊息加上算出的報告路徑（找不到時改印 `report` 子命令取得，不觸碰檔案系統，維持 `decide_hook_action()` 的 no-I/O 契約）與「必須在最終回覆貼出精簡版（Progress 進度行、phase step 狀態表、未完成與例外段全文）」的指令；`plan-run`、`plan-archive` 兩份 SKILL.md 的對應步驟同步改為必做而非可選。

## [v3.1.0] - 2026-09-15

> **版本位階判定：MINOR。** 依 [VERSIONING.md](VERSIONING.md) 的判準「會讓照舊用法的既有使用者行為改變或壞掉的才是 MAJOR」核對：`/pr` 的 PDT ticket 規則（PR #72）是向後相容的新功能，對話與 branch 裡沒有 PDT 編號的使用者行為不變，PR 標題也不在 VERSIONING 列舉的對外介面（指令名、plan 格式契約、DSL、安全紅線）裡；release workflow 的 checkout 升級與 skip 提醒不動任何 skill 指令。其餘是文件與 `worktree` 修正（PR #70、#71）。`worktree` 單一 repo 清理改成預設不刪 branch 雖然改了行為，但原行為與同 repo 腳本硬規則「永遠不刪 branch」矛盾，屬修 bug，且使用者明確要求時仍可刪，因此不抬到 MAJOR。最高位階為 MINOR。

### Added
- **`/pr` 強制帶入 PDT ticket**：Step 1b 從對話、當前 branch 名、`origin/<base>..HEAD` commit message、既有 PR title／body 掃 `PDT-\d+`（大小寫不拘），正規化成大寫 `PDT-<number>`。只算這次工作對應的票：舉例、引用別的 PR、blocked-by 這類順帶提到的編號排除，分不出來就問使用者。這是拿本 PR 自己的對話試跑時抓到的：對話裡的 PDT-6908、PDT-11061 只是舉例，照原寫法會被誤判成這次的 ticket。命中後 PR 標題必須帶字面編號，只寫票名不算數；PDT 編號不重複算進通用 ticket 清單，免得同一個號碼同時觸發兩套標題規則（fact-checker 文件審查抓到）。一般 PR 預設 `(PDT-<number>)` 放尾巴，也接受寫進 scope（`fix(PDT-11061): ...`）；Release PR 也附在尾巴；更新既有 PR 時標題漏了就補上。新增 Step 3.5 管 branch 名，格式 `<type>/pdt-<number>-<slug>`：還在 long-lived branch 上就直接開新 branch；本機 branch 還沒推上遠端，用 AskUserQuestion 提議改名；已推上遠端或已有 PR 就不改名，因為重推新名會讓 PR 斷掉，ticket 改由標題承載。Step 6 另外回報 ticket 出處與落點。格式取自 vocus-web-ui 2026-09 實際 PR：8184 `fix(PDT-11061): ...`＋branch `fix/pdt-11061-editor-toolbar-keyboard-sticky`、8201 `...(PDT-6908)`。
- **release workflow 跳過發版時提醒未發版條目**：tag 已存在而 skip 時，若 `[Unreleased]` 還有條目，run 會附一條 `Unreleased 未發版` warning，VERSIONING.md 疑難排解同步補述。起因是 v3.0.0 之後 #70–#72 三次 merge 都綠燈 skip、Release 沒出，看起來像機制壞掉；實際是沒人把 `[Unreleased]` 改成版號（#72 那次 run 34928144159 的 log：`Tag v3.0.0 already exists, skipping.`）。

### Changed
- **release workflow 的 `actions/checkout` 從 `@v4` 升到 v7.0.1，改 pin commit sha**（`3d3c42e5aac5ba805825da76410c181273ba90b1`）：消掉 Node.js 20 deprecation 註記。v5 改跑 Node 24（最低 runner v2.327.1）、v6 把 credentials 改存獨立檔、v7 擋 `pull_request_target`／`workflow_run` checkout fork PR；本 workflow 只由 push 觸發、發版走 `gh`＋`GH_TOKEN`，三者都不影響。

### Fixed
- **VERSIONING.md「版本線」還寫 `v2.x`（現行）**：v3.0.0 發版時 README 主線已改成 v3.x，這段漏改。改為 `v3.x` 現行、`v2.x` 最後版本 `v2.2.0`。
- **README Rules 表的載入方式描述與實況不符**：原本把 `rules/worktree-prompt.md` 與 `rules/plan-management.md` 併寫成「載入為全域 CLAUDE.md 指令」，但 worktree-prompt 自 2026-09-01 起已降級為 `UserPromptSubmit` 觸發式注入（README 上方「情境型 rules 的觸發式安裝」段與 `docs/hooks-setup.md` 都這樣寫），只有 plan-management 仍 symlink 常駐；同為情境型的 `rules/debug-triage-order.md` 與 `rules/design-token-reuse-first.md` 則完全沒列。拆列各自寫清楚，補上缺列的兩檔。發現於 2026-09-14 全域 CLAUDE.md 去重審查（比對本 repo 移除 3 條逐字重複規則時，逐一核對 `~/.claude/rules/common/` symlink 實況）。
- **`worktree` skill 的跨 repo 腳本路徑解析不到**：SKILL.md 寫相對路徑 `scripts/worktree-cleanup.sh`，從 skill 目錄 `worktree/` 解析會找不到（腳本實際在 repo 根目錄 `scripts/`），2026-09-13 實跑 `/worktree cleanup` 時因此改走 inline 迴圈。改為完整路徑 `~/Documents/agent-skills/scripts/worktree-cleanup.sh`，已從該路徑實跑 dry-run 確認可執行。
- **`worktree` skill 單一 repo 清理流程與腳本硬規則矛盾**：步驟 6 原本在 `git worktree remove` 後接 `git branch -d`，與 `scripts/worktree-cleanup.sh` 硬規則 1「永遠不刪 branch」衝突。改為預設只移除 worktree 目錄；使用者明確要求時才逐一列名確認、只用 `-d`，並跳過 long-lived branch。

## [v3.0.0] - 2026-09-10

> **版本位階判定：MAJOR。** 依 [VERSIONING.md](VERSIONING.md)「移除或更名指令」判準逐項核對：移除 9 支 skill 指令（PR #66）與 `agents/` 退出指令清單（PR #68）都是「移除或更名指令」，兩者屬同一條判準，同個 release 內合併計算，不各自抬升。其餘變更對照舊用法一律向後相容：`plan-run`／`evidence-gate` 補 parser 契約四點與作者先自跑規則（PR #67）只補文件敘述，不改任何既有指令、旗標或機器可讀輸出；`init --format json` 的 attach 訊息改走 stderr（PR #68）修的是「stdout 混入非 JSON 文字」這個 bug，payload 欄位與 exit code 語意皆未動；`/release-pr` 新增 Step 3.5 範圍相稱性擋門與機械擋門 script（PR #64）是新增檢查步驟，不影響既有呼叫路徑與既有 PR 的產出格式。

### Removed
- **移除 9 支 5 週零用量的 skill**：`ecc-skill-defer`、`learn-eval-deep`、`curation`、`triage`、`playwright-human-in-the-loop`、`verify-fix-loop`、`assist`、`verify-evidence-loop`、`ship-ticket`。

  **依據**：本機 1,441 份 Claude Code transcript（2026-08-04～09-09，5 週）掃描，這 9 支人打與 Claude 自叫合計 0 次。9 支 `description` 合計 2,201 字元，佔 25 支總量 7,406 的三成，是每個 session 都要載入的固定成本，用量與成本完全不成比例。

  **逐支理由**：
  - `ecc-skill-defer`：README 已標 Deprecated，v2.x ECC 解耦後其存在理由（管理 ECC skills 的 defer/restore 狀態）已隨依賴解除而消失。
  - `learn-eval-deep`、`curation`、`triage`：三支操作對象都是 `~/.claude/skills/learned/*.md`，該路徑現為 0 檔——learned skills 已全數搬進 knowledge-base 的 `wiki/learned/`；前置的 `/learn-eval` 與 `skills-ecosystem-eval` 也已不存在，三支的輸入端已經斷源。
  - `playwright-human-in-the-loop`、`verify-fix-loop`：兩支都建在 Playwright MCP 之上，而 `rules/common/browser-tools.md` 已把 MCP 降為「最後手段」；headed ship 前驗收改由 `/pr-evidence-comment` 承接。
  - `assist`：作為路由入口的角色，已被 `~/.claude/CLAUDE.md` 的核心紀律與 playbooks 取代——使用者端的模型分工與委派決策現在直接在 harness 層做，不再需要一支 skill 幫忙選 pipeline。
  - `verify-evidence-loop`：是 `/evidence-check` 的迭代收斂版本，而 `/evidence-check` 本身全生命期只用 2 次，其迭代版更是 0 次，維護一支比基礎版更貴、更少人用的衍生品沒有回報。
  - `ship-ticket`：全生命期 0 次呼叫，其設計的硬規則（repro-first gate、fix 前必重現）已落在 `/evidence-gate` 與 knowledge-base 的 learned 記錄裡，功能不隨 skill 一起消失。

  **連帶清理**：`update`、`plan-run`、`figma-verify`、`evidence-check`、`design` 五支保留 skill 的 SKILL.md 移除對被刪 skill 的引用或 `redundancy-peers` 條目；`rules/security-guidance/{README,skill-integration}.md`、`rules/refactor/remove-architect-pipeline.md` 的「目前適用範圍」清單拿掉 `/assist`；README.md 的 Usage、Skills 總覽表、決策樹、各 skill 詳細段落同步移除對應 9 支的條目。fresh-context 驗收另挖出三份**現行**文件仍把已刪 skill 當存在引用，補作廢註記而不改寫內容：`research/q4-review-checklist.md` 的可執行步驟改指向 `/update` Step 4 inline 評分；`plans/active/ecc-decoupling-and-model-adaptation.md`、`plans/active/knowledge-base-quality-optimization.md` 初版只在頂部加作廢 blockquote；CodeRabbit 指出 `/plan-run` 的狀態機不看 blockquote、混合 step（保留 skill＋已刪 skill）跑會重建已刪 skill、skip 會連保留工作一起跳，故改為結構拆分——只含已刪 skill 的整步（ecc plan 的 S1.2、S2.3；kb plan 的 S2.2）移出到 parser 不視為 step 的 `## 作廢範圍（不可執行）` 區塊，混合 step 只留保留項目，D4 標作廢，依賴邊同步。parser 實跑：ecc plan 14→12 step、kb plan 8→7，dangling deps 0，執行者會拿到的 Files／Action 欄位已刪 skill 命中 0。`rules/task-tracking-availability.md`、README 的「ECC 解耦（2026-07-04）」段落等**歷史查證/歷史敘述保留不動**，不重寫過去發生的事實。

  **版本位階判定：MAJOR。** 依 [VERSIONING.md](VERSIONING.md)「移除或更名指令」判準，本版即 `v3.0.0`。

  **更正（2026-09-10）**：上面「9 支 `description` 合計 2,201 字元，佔 25 支總量 7,406 的三成，是每個 session 都要載入的固定成本」這個說法高估了。`~/.claude/settings.json` 的 `skillOverrides` 早把其中 4 支（`verify-evidence-loop`、`verify-fix-loop`、`playwright-human-in-the-loop`、`learn-eval-deep`，合計 1,185 字元）設成 `off`、本來就不載入；實際省下的是另外 5 支 1,016 字元、佔 13.7%。教訓：量 per-session 成本前先看 `skillOverrides` 之類的停用設定。

- **`agents/` 退出指令清單**：`agents/SKILL.md` 改名為 [`agents/README.md`](agents/README.md)。目錄與四份定義檔（`complexity-triage` / `doc-reviewer` / `doc-updater` / `tdd-guide`）**原地保留、內容一字未改**——這是換一個不會被註冊的檔名，不是刪功能。

  **理由：那份文件的 frontmatter 自己就寫著「本身非直接可呼叫的 skill，不會出現在指令清單中」，而那句話沒有執行力。** 會不會被註冊成指令只看檔名是不是 `SKILL.md`，`user-invocable: false` 管不到，於是它照樣佔掉 `/agents` 一格、description 照樣每個 session 載入。本機 5 週 transcript（2026-08-04～09-09）掃描：`agents` 命中 2 次，兩次都是 `/design`、`/update` 執行過程中對定義檔的間接引用，**人打或 Claude 自叫 `/agents` 0 次**——用量與那句自述完全一致，等於它本來就不該在清單裡。

  呼叫方引用的一直是 `agents/<name>.md` 那四份定義檔，從來不是 `SKILL.md`，所以呼叫路徑零變更；`design/SKILL.md`（3 處）、`update/SKILL.md`（2 處）、`README.md`（1 處）的指標同步改指 `agents/README.md`。`~/.claude/skills/agents` → repo `agents/` 的 symlink 保留不動。`ls */SKILL.md | wc -l`：**16 → 15**。

  **版本位階：MAJOR，但由本次 `v3.0.0` 一併涵蓋**，不另外抬升——「更名指令」與上一條「移除 9 支 skill」是 [VERSIONING.md](VERSIONING.md) 的同一條判準，同個 release 內合併計算。

  **對外部安裝者的影響（實測推翻了一個錯誤前提）**：原本的說法是「`npx skills` 快照只同步 `SKILL.md`，所以外部安裝者本來就拿不到定義檔，改名對他們沒差」——**查下去發現這是錯的**。`~/.agents/.skill-lock.json` 把 `agents` 列為受追蹤的 skill（`skillPath: agents/SKILL.md`，`updatedAt: 2026-08-05T04:36:29Z`），而 `~/.agents/skills/agents/` 底下四份定義檔全在，檔案時間戳與 lock 的 `updatedAt` 是同一刻；同一份 lock 裡 `ecc-skill-defer` 拿到 `DEFER_LOG.md` / `DEFER_REFERENCE.md` / `.conf` / `.sh`，`triage` 拿到 `skills-triage.sh`。**skills CLI 同步的是整個 skill 資料夾，不是單一檔案。** README「安裝後確認載入的版本與 repo 一致」段落講的「只同步 `SKILL.md`，同層的 `scripts/` 不會一起下來」指的是 **repo 根目錄的 `scripts/`**——那個目錄不在任何 skill 資料夾內，該句本身沒錯，但不能推廣成通則。本 repo 只有 `agents/` 這一個 skill 資料夾帶頂層附屬檔（`release-pr` 的附屬檔在 `fixtures/` 子目錄裡），所以這個差異一直沒被觸發過。

  真正的影響因此是：改名後 `agents/` 不再是 skill，`npx skills update` 不會再同步它，既有安裝的快照會停在最後一次同步的版本、也可能被清掉。`agents/README.md` 的「路徑解析」段已據此改寫——拿掉會失效的 `~/.agents/skills/agents/<name>.md` 與「請使用者重跑 `npx skills update`」，改列 repo checkout 與 `ln -sfn <你的 checkout>/agents ~/.claude/skills/agents` 兩條有效路徑，並保留「從 GitHub 直接取 `agents/<name>.md`」當最後手段。

  **CI 不受影響（兩條路都實測過）**：`.github/workflows/skill-quality.yml` 的 `paths` 是 `*/SKILL.md`，而 `git diff --name-only main...HEAD` 對這次改名只吐出 `agents/README.md`（rename 偵測生效），`^[^/]+/SKILL\.md$` 撈到的仍只有 `design/SKILL.md`、`update/SKILL.md`；即使 rename 偵測失效（以 `--no-renames` 模擬），`agents/SKILL.md` 被撈進 `--files` 也只是讓 `check_skill.py` 印一行 `::warning file=agents/SKILL.md::File not found, skipping` 然後繼續，exit 0。既不會漏跑也不會誤紅。`check_release_fixtures.py` 的第 6 條（新增 `## Step` 標題必須動 `fixtures/`）只看 `<dir>/SKILL.md` 的**新增行**，刪除與改名都不觸發，self-test 6/6、完整性擋門 0 問題。

  **順帶量到的分數變化**：`check_skill.py` 的 `non_redundancy` 是拿該 skill 與 repo 內其他 `SKILL.md` 比 Jaccard 重疊，`agents/SKILL.md` 退出比較池後 `update/SKILL.md` 的 `non_redundancy` **2.2 → 5.0**、總分 **21.0 → 23.8**，`design/SKILL.md` 維持 **23.8**，兩者皆 PASS 且不低於 main。這也反過來說明：那份索引原本就在跟它自己的呼叫方搶同一組關鍵字。

### Added
- **`/release-pr` 新增 Step 3.5「範圍相稱性擋門」＋ `release-pr/fixtures/` golden set**：對 body 的每個段落問「reviewer 為了決定要不要核准並部署這次變更，需要知道這件事嗎？」——「查證時才需要」的內容外連而非內嵌，因為它的完整版本通常已存在於 feature PR、KB 報告或 runbook，寫第二次只是製造第二個會過期的副本。

  **失效模式不是判斷力問題，是位置壓力**：作者剛做完調查、脈絡都在手上，而 PR body 是離手前最後一個可以傾倒的地方——傾倒的動機來自作者的狀態，與讀者的需求無關。同構前例是 `knowledge-base/reports/2026-08-19-alert-description-bloat-audit-and-rewrite-proposal.md`（106 條 Grafana rule 裡 15 條把 RCA 全文塞進每次 firing 都整份推進 Slack 的 `description`），該報告的結論可直接移植：「根本問題不是寫太多，是寫錯地方。」

  **實測回歸**：vocus-web-ui #8124（1 檔／+23−7）初版 body **3993 字元**（GitHub `userContentEdits` API 實測，非估算），含四個完整版本已存在別處的區塊——P95 七列證據表、因果推導段、CodePipeline 機制說明、對某份 KB plan 的更正；修正後 1053 字元。

  golden set 三個 fixture 刻意包含**兩個負對照**（#8121 3 檔 2658 字元、#7969 64 檔 7897 字元皆判 KEEP），因為這道關卡最可能的實作錯誤是退化成「一律縮短」。次指標「每檔字元」在四個真實樣本上隨檔案數增加而遞減（64 檔 123／8 檔 389／3 檔 886／1 檔 1053），是次線性關係的表現；#8124 初版的 3993 會讓曲線在最左端翹起，那個翹起就是傾倒的訊號。**該指標是聞味道用的，樣本只有單一 repo 4 個 PR，不該當門檻硬套**（侷限已寫進 `fixtures/acceptance_results.md`）。

  驗收 3/3 通過，但 fixture 01 的 PASS 是人工修正後的結果，**Step 3.5 的實際有效性標記為 provisional**——要等下一個 release PR 在未經提示的情況下初版就落在合理區間才能解除。

- **golden set 的機械擋門 `scripts/check_release_fixtures.py` ＋ CI 接線**：初版的 golden set 只有 markdown、**零執行入口**——SKILL.md 只用一句「見 `fixtures/`」引用它，`scripts/tests/` 與 workflow 都沒碰它。使用者追問「這會每次都跑嗎」時查證確認：不會，而且 PR 的 `quality` check 是綠的，綠的是 `check_skill.py` 的 SKILL.md 結構分數，**跟 fixtures 完全無關**——正是 KB `ci-green-doesnt-mean-your-new-test-ran-check-collected-count` 的形狀。

  另一個同構缺口在 workflow 的 `paths: ["*/SKILL.md"]`：**只改 fixture 不改 SKILL.md 時整個 workflow 不會啟動**，等同 KB `an-existing-guard-that-always-passes-may-just-not-cover-you` 的「glob 差一格」。已擴為 `*/SKILL.md`、`*/fixtures/**`、兩支 script。

  script 守六件可機械化的事：manifest 存在且可解析、manifest 列到的 fixture 檔真的存在、fixtures/ 無孤兒檔、frontmatter 齊全且 `fixture_id` 與檔名前綴一致、**至少有一份 negative-control**（防擋門退化成單向修剪）、以及最重要的第六條——**SKILL.md 新增 `## Step` 標題時同一個 PR 必須也動到 `fixtures/`**，否則 golden set 會跟 skill 漂移。

  **script 自帶 `--self-test`（不依賴 pytest），CI 先跑它再拿它擋別人**：baseline 必須乾淨，五個突變（新增 Step 不補 fixture／孤兒 fixture／只有 regression 無 negative-control／manifest 指向不存在的檔／`fixture_id` 與檔名不一致）必須全部被擋下。本機實跑 6/6。**先證明守衛會擋，再讓它上崗**——否則加的是第二個安靜的 `false`。

  **範圍誠實聲明**：機械層只驗 golden set 自身的完整性，**驗不了「這次產出的 PR body 取捨對不對」**——那是判讀，靠 SKILL.md Step 3.5 的強制步驟（要求逐條輸出命中表，任一命中就退回 Step 3）。兩層強度不同，已在 script docstring 與 SKILL.md 明寫，不要把 CI 綠當成判讀層做過了。

  **順帶發現、本次未修**：`scripts/tests/` 的兩支既有 pytest（`test_plan_runner_regression.py`、`test_plan_run_hook.py`）**在整個 repo 的 CI 裡從未被執行過**——同一個失效類別。本次不順手掛上去，因為本機沒有 pytest、無法先驗證它們現在是綠的，盲目接線會引入不相關的失敗。留作獨立項。

### Changed
- **`plan-run/SKILL.md` 補「作廢 step 與 parser 契約」小節**：來源是 PR #66——CodeRabbit 指出 blockquote 註記不會讓 `/plan-run` 狀態機跳過混合 step，本 repo 修 `ecc-decoupling-and-model-adaptation.md`／`knowledge-base-quality-optimization.md` 兩份 plan 時因此把已刪 skill 的整步移出到二級標題的作廢區塊（實測 ecc plan 14→12 step、kb plan 8→7 step，dangling deps 0）。文件原本沒交代這條界線，補四點契約事實：checkbox 對 `init` 的 pending/ready 判斷沒有影響、作廢一個 step 必須把它移出 step 結構且要用二級標題（三級標題會被 phase regex 攔下）、`Why` 欄位會被解析但不會流進執行者的 Action 模板、`init --format json` 的 stdout 只回摘要欄位而非完整 step 表。**PR #67 CodeRabbit 追加修正**：保留 DAG 位置的正確做法是 `init` 後跑 `skip` 子命令，不是清空 `Files`／`Action` 文字——runner 不讀這兩欄決定是否執行；`init --format json` 一律補 `--no-attach`，因為預設會 attach 並把結果文字印到 stdout，污染 JSON 輸出。
- **`evidence-gate/SKILL.md` 補「作者先自跑」規則**：來源同為 PR #66——20 條 claim schema 裡有 5 條字面照跑會出錯（ERE 交替寫成 `\|`、`git diff --name-only` 缺 `--diff-filter=M`、`check_skill.py` 缺 `--files`、假設 `grep -r` 輸出帶 `./` 前綴、假設驗收檔是表格但實際是粗體），全靠 fact-checker 重跑等價指令才攔下。原本第 4 節只要求 fact-checker 重跑，沒要求作者交出 schema 前先自己跑過一次；補上這條規則，並要求 fact-checker 遇到指令跑不動或假陰性時另記「schema 指令缺陷」而非放寬判準。**PR #67 CodeRabbit 追加修正**：SCHEMA-DEFECT 與 FAIL 同為阻擋結果，不再是「不算 FAIL」的軟性提醒；`/pr`、`/release-pr` 的擋門句同步補上 SCHEMA-DEFECT 條件，避免其被判定為阻擋卻沒有任何 caller 真的擋下。

### Fixed
- **`init --format json` 不帶 `--no-attach` 時 stdout 會混入非 JSON 文字**：`cmd_init` 先 `emit_formatted()` 印出 payload，接著在 `attach` 預設為 `True` 的情況下呼叫 `_attach_pointer_for_cwd()`——成功走 `_print_attach_result()`、失敗走 `print(error)`，**兩條路徑都寫 stdout**。JSON consumer 於是拿到一份合法 JSON 後面黏著四行給人看的旁白（`Plan:` / `Cwd:` / `Pointer:` 加一則中文警示），`json.loads()` 直接噴 `Extra data: line 15 column 1`（以 main 版 `plan_runner.py` 在 temp dir 實測重現）。

  來源是 **CodeRabbit on PR #67**。當時的處置只到文件層——`plan-run/SKILL.md` 改成「`init --format json` 一律補 `--no-attach`」，等於**要求每個呼叫端記得繞開一個預設就會踩到的坑**；本次補上 runner 端，讓預設路徑本身就安全，文件那條建議降級為選擇而非必要條件。做法是 JSON 模式把 attach 的成功與失敗訊息改寫到 stderr：它們是旁白，不是 payload 的一部分，而 stderr 正是旁白該去的地方。payload 欄位與 exit code 語意皆未動（attach 失敗仍回 0，那是既有語意，不在本次範圍）。

  **md 模式逐位元組不變**：同一份 plan 分別以 main 版與本版跑 `init`（attach 預設開），stdout `cmp` 無差異、stderr 兩邊皆為空；位元組數隨安裝路徑長度變動，不列固定值。

  回歸測試落在 `scripts/tests/test_plan_runner_regression.py` 的 `InitAttachStreamTestCase`，4 個 case：①JSON＋attach 開 → stdout 可 `json.loads` 且 stderr 含 attach 三行；②JSON＋`--no-attach` → stderr 為空；③md＋attach 開 → attach 三行仍在 stdout、stderr 為空；④**attach 的失敗分支**（cwd 已綁定另一份 plan）同樣不得污染 stdout——這條路徑是另一個獨立的 `print()`，只修成功分支時最容易漏掉。四個 case 都在子行程裡把 `$HOME` 重導到 temp dir，pointer 因此落在 `<tmp>/.claude/plan-run/active/`，**不碰真實 `~/.claude/`、不在 repo 留下任何 state 產物**（既有測試是用 `--no-attach` 迴避這個問題，但本次要測的正是 attach 開著的預設路徑，只能改用隔離 HOME）。

  本機沒有 pytest（`import pytest` → `ModuleNotFoundError`），故沿用 `scripts/tests/` 既有的 stdlib `unittest` 寫法而非另引依賴；`python3 -m unittest discover scripts/tests` 實跑 **130 tests OK**（原 126 ＋ 新增 4）。

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
[Unreleased]: https://github.com/ashe-li/agent-skills/compare/v3.2.0...HEAD
[v3.2.0]: https://github.com/ashe-li/agent-skills/compare/v3.1.0...v3.2.0
[v3.1.0]: https://github.com/ashe-li/agent-skills/compare/v3.0.0...v3.1.0
[v3.0.0]: https://github.com/ashe-li/agent-skills/compare/v2.2.0...v3.0.0
[v2.2.0]: https://github.com/ashe-li/agent-skills/compare/v2.1.0...v2.2.0
[v2.1.0]: https://github.com/ashe-li/agent-skills/compare/v2.0.0...v2.1.0
[v2.0.0]: https://github.com/ashe-li/agent-skills/compare/v1.28.0...v2.0.0
[v1.28.0]: https://github.com/ashe-li/agent-skills/releases/tag/v1.28.0
