# Hook 設定

本 repo 有四組一次性安裝的 hook，彼此獨立，可只裝其中一部分：

| 章節 | Hook 類型 | 做什麼 | 搭配 |
|------|-----------|--------|------|
| [一](#一plan-自動儲存-hookposttooluse) | `PostToolUse` | `ExitPlanMode` 後自動把 plan 存進 `plans/active/` | `/plan-archive` |
| [二](#二plan-dag-推進-stop-hook) | `Stop` | 每輪結束查 plan state，把下一步注入回模型 | `/plan-run` |
| [三](#三情境型-rules-的觸發式安裝userpromptsubmit) | `UserPromptSubmit` | 命中情境時注入規則重點，取代常駐 rule | `rules/` |
| [四](#四驗證-gate-hookpretooluseposttoolusestop) | `PreToolUse`、`PostToolUse`、`Stop` | 開 PR 前要有快檢 receipt；結束前本 session 動過的 PR 要有驗收與 CI receipt | `/verify-change`、`/pr` |

---

# 一、Plan 自動儲存 Hook（PostToolUse）

若希望每次 `ExitPlanMode` 後**自動**將 plan 存至 `plans/active/`，
可設定 PostToolUse hook（搭配 `/plan-archive` 使用，一次性安裝，非每次執行都需要）。

## 1. 建立 hook script

`~/.claude/hooks/save-plan-on-exit.sh`：

```bash
#!/bin/bash
# PostToolUse hook for ExitPlanMode
# Reads JSON from stdin, copies plan file to project's plans/active/

INPUT=$(cat)

# Extract plan_file path from tool response
PLAN_FILE=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    # ExitPlanMode tool_response may contain the plan file path
    resp = d.get('tool_response', d)
    print(resp.get('plan_file', resp.get('planFile', '')))
except Exception:
    print('')
" 2>/dev/null)

if [ -z "$PLAN_FILE" ] || [ ! -f "$PLAN_FILE" ]; then
  exit 0
fi

# Only act if we're inside a git repo with a plans/ directory pattern
if [ ! -f "$(pwd)/.git/HEAD" ] && [ ! -d "$(pwd)/.git" ]; then
  exit 0
fi

DEST_DIR="$(pwd)/plans/active"
mkdir -p "$DEST_DIR"

SLUG=$(basename "$PLAN_FILE")
cp "$PLAN_FILE" "$DEST_DIR/$SLUG"
echo "[plan-archive] Plan saved to plans/active/$SLUG" >&2
```

```bash
chmod +x ~/.claude/hooks/save-plan-on-exit.sh
```

## 2. 設定 `~/.claude/settings.json`

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "ExitPlanMode",
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/.claude/hooks/save-plan-on-exit.sh"
          }
        ]
      }
    ]
  }
}
```

## 3. Rule（搭配使用）

在專案 `CLAUDE.md` 加入，確保 Claude 在實作完成後主動呼叫 `/plan-archive`：

```markdown
### Commit 前知識沉澱清單

5. **歸檔 plan** — 若本次工作有對應的 plan，實作完成後執行 `/plan-archive`
   將 `plans/active/<name>.md` 移至 `plans/completed/`，補上驗證結果
```

---

# 二、Plan DAG 推進 Stop Hook

`/plan-run` 的控制流靠這個 hook 生效。裝了之後，harness 會在**每一輪結束時**強制執行它——由它讀 plan state 決定要不要把下一步注入回模型。沒裝的話 `/plan-run` 仍可用，但控制流退回「靠模型自己想起來查狀態」，也就是本設計要解決的問題本身。

安裝是 additive 的：**追加**一個 hook 到既有的 `Stop` 陣列，不動任何既有 hook。

## 1. 建立 hook script

把 repo 的 wrapper 複製過去（不要手抄，以 repo 版本為準）：

```bash
cp ~/Documents/agent-skills/scripts/hooks/plan-run-stop.sh ~/.claude/hooks/plan-run-stop.sh
chmod +x ~/.claude/hooks/plan-run-stop.sh
```

wrapper 全文（`scripts/hooks/plan-run-stop.sh`，註解已略）：

```bash
#!/bin/bash
# plan-run-stop.sh — Stop hook wrapper for agent-skills' plan_runner.py.
# 不解析 stdin：Stop hook 的 JSON payload 原封不動交給 plan_runner.py hook-stop，
# 避免任何路徑／參數注入。所有失敗路徑都 exit 0（見下方「為什麼每條路徑都 exit 0」）。

AGENT_SKILLS_DIR="${AGENT_SKILLS_DIR:-$HOME/Documents/agent-skills}"

# 先解析再比較：裸 glob 比對是字串測試，"$HOME/../../elsewhere" 與
# "$HOME/<指向外部的 symlink>" 都能通過，但 kernel 開的是 $HOME 外的檔案。
# `cd -P` 收斂 `..` 與 symlink，比較的才是真實最終路徑。
AGENT_SKILLS_DIR=$(cd "$AGENT_SKILLS_DIR" 2>/dev/null && pwd -P) || exit 0

case "$AGENT_SKILLS_DIR" in
    "$HOME"/*) ;;
    *) exit 0 ;;
esac

RUNNER="$AGENT_SKILLS_DIR/scripts/plan_runner.py"

if [ ! -f "$RUNNER" ]; then
    exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
    exit 0
fi

# stdout 直通，不經 shell 變數來回（會破壞 JSON reason 內的跳脫序列）。
python3 "$RUNNER" hook-stop 2>/dev/null || exit 0

exit 0
```

### 為什麼每條失敗路徑都 exit 0

**Stop hook 的 exit 2 是 blocking error。** 一個尚未有 `hook-stop` 子命令的舊 checkout，argparse 會拒絕未知子命令並 exit 2——這台機器上**每個 session 的每一輪**都會被擋下，並把 argparse 的 usage dump 餵回模型。`|| exit 0` 是硬性需求而非防禦性裝飾：它讓過舊、損毀、或 rebase 到一半的 checkout 退化成「這個 hook 什麼都不做」，而不是全機故障。stderr 一併丟棄也是同理——不支援的 checkout 必須是安靜的，不是每輪吵一次。

### `AGENT_SKILLS_DIR` 的用途與警語

它決定這個 hook **每輪要執行哪一支 Python 檔**，預設 `~/Documents/agent-skills`。只在刻意、暫時地對另一份 checkout（例如 sibling worktree）測試時才覆寫。

兩種指法：

| 做法 | 生效範圍 | 適用 |
|---|---|---|
| `export AGENT_SKILLS_DIR=<path>` | **只有當前 shell** | 在終端機裡手動跑 `plan_runner.py` 對照 |
| 直接改 wrapper 裡那一行的預設值 | 每一個 session | 要在真實 session 裡試用未合併的分支 |

環境變數優先於 wrapper 的預設值，兩者 `doctor` 都看得到——`doctor` 是**讀已安裝的 wrapper**取得預設值，不是自己另存一份常數，所以你改了 wrapper 它就跟著改。

> **警語**：指向非預設路徑時，**那個路徑一旦消失（worktree 被刪、branch 被切走），hook 就全域失效**——而且是安靜失效，因為所有失敗路徑都 exit 0。改 wrapper 那一行時順手在上面留註解寫明「暫時指向哪裡、何時要改回來」，測完還原並跑一次 `doctor` 確認 runner 路徑已指回預設 checkout。

## 2. 追加到 `~/.claude/settings.json`

**這是 additive 步驟，不是覆蓋。** 下面的片段要**追加到現有 `Stop` 陣列的末端**，不是拿去取代整個 `hooks` 區塊。

先備份：

```bash
mkdir -p ~/.claude/backups/$(date +%F)-stop-hook
cp ~/.claude/settings.json ~/.claude/backups/$(date +%F)-stop-hook/settings.json
```

要追加的物件：

```json
{
  "hooks": [
    {
      "type": "command",
      "command": "bash ~/.claude/hooks/plan-run-stop.sh"
    }
  ]
}
```

前後對照（假設你原本已有兩個 Stop hook）：

```jsonc
// 之前
"Stop": [
  { "hooks": [ { "type": "command", "command": "bash ~/.claude/hooks/existing-a.sh" } ] },
  { "hooks": [ { "type": "command", "command": "bash ~/.claude/hooks/existing-b.sh" } ] }
]

// 之後 — 既有兩個原封不動，新的接在末端
"Stop": [
  { "hooks": [ { "type": "command", "command": "bash ~/.claude/hooks/existing-a.sh" } ] },
  { "hooks": [ { "type": "command", "command": "bash ~/.claude/hooks/existing-b.sh" } ] },
  { "hooks": [ { "type": "command", "command": "bash ~/.claude/hooks/plan-run-stop.sh" } ] }
]
```

落地後**先驗 JSON 再開新 session**：

```bash
python3 -c "import json;d=json.load(open('$HOME/.claude/settings.json'));print(len(d['hooks']['Stop']),'Stop hooks')"
```

數量應為「原本的數量 + 1」。但**數量對不代表沒出錯**——`N+1` 也可能是「刪了一個舊的、加了兩個新的」。要確定既有 hook 沒被動到，就跟備份逐字比對：

```bash
python3 - <<'EOF'
import json, os
cur = json.load(open(os.path.expanduser("~/.claude/settings.json")))["hooks"]["Stop"]
bak = json.load(open(os.path.expanduser("~/.claude/backups/<日期>-stop-hook/settings.json")))["hooks"]["Stop"]
print("既有 hook 逐字相同:", [json.dumps(h, sort_keys=True) for h in cur[:len(bak)]]
                          == [json.dumps(h, sort_keys=True) for h in bak])
EOF
```

## 3. 自檢

```bash
python3 ~/Documents/agent-skills/scripts/plan_runner.py doctor
```

六項全 PASS 或 INFO 才算裝好：

```
[PASS] python3 版本: 3.14.5（需 >= 3.9）
[INFO] ~/.claude/plan-run/ 可寫: /Users/<you>/.claude/plan-run 尚未建立（首次 attach 時自動建立，非錯誤）
[PASS] settings.json Stop hook 已註冊: hooks.Stop 含 plan-run-stop
[PASS] wrapper script 存在且可執行: /Users/<you>/.claude/hooks/plan-run-stop.sh
[PASS] wrapper 的 runner 支援 hook-stop: /Users/<you>/Documents/agent-skills/scripts/plan_runner.py
[INFO] 當前 cwd 有效 pointer: 當前 cwd 無 active plan（非錯誤）

4 PASS / 2 INFO / 0 FAIL — 安裝正常
```

（冒號後是實際查到的路徑，上面以 `<you>` 代替家目錄。）

`INFO` 不是錯誤，是「這件事還沒發生但很正常」，所以**全新安裝的最佳成績就是 4 PASS + 2 INFO，印不出 6/6**——最後一行的判定才是結論。等你 `attach` 過一份 plan，那兩項 INFO 會各自轉成 PASS（先變 `5 PASS / 1 INFO`，在有 active plan 的目錄下跑則是 `6 PASS / 0 INFO`）。有任一 FAIL 時 `doctor` exit 1，可直接當 CI gate 用。`doctor` 是唯讀的，可以隨時跑。

## 4. 與既有 Stop hook 共存

Claude Code 會執行 `Stop` 陣列裡的**每一個** hook，不是只執行第一個。本 hook 的設計是：**當前 cwd 沒有 active plan pointer 時安靜 `exit 0`**，對其他 session 零影響。

實測（本機四個既有 Stop hook 環境下）：

- 沒有 active plan 的一般 session，四個既有 hook 照常各觸發一次，`plan-run` 執行 0 次
- 有 pointer 但 `AGENT_SKILLS_DIR` 落回不支援 `hook-stop` 的 checkout：13 次執行全部 `out_bytes=0`、無任何干擾——上面那個 `|| exit 0` 在真實 session 條件下驗證通過
- 經第三方 wrapper（如 observability 的 `wrap.sh`）轉呼叫時，stdout 逐位元組相同，payload 未被改動

輸出形狀方面，本 hook 採 **top-level `{"decision":"block","reason":"..."}`**。實測另一個可行形狀 `hookSpecificOutput.additionalContext` 雖然也送得到模型，但**不寫進 transcript**——使用者看不到 hook 推了什麼。控制流工具塞給模型的指令必須是看得見、可稽核的，所以不採用。

## 5. 日常控制

```bash
python3 ~/Documents/agent-skills/scripts/plan_runner.py pause     # 暫停注入（state 保留），想手動接管時用
python3 ~/Documents/agent-skills/scripts/plan_runner.py resume    # 恢復注入
python3 ~/Documents/agent-skills/scripts/plan_runner.py pointer   # 當前 cwd 解析到哪份 plan
python3 ~/Documents/agent-skills/scripts/plan_runner.py detach    # 移除 cwd 的 pointer（plan 完成或換 plan 時）
python3 ~/Documents/agent-skills/scripts/plan_runner.py doctor    # 安裝自檢（唯讀）
```

`pause` 只是把 pointer 標成暫停，state 一個字都不會動；`detach` 只移除 pointer，plan 的 `.plan-state/` 仍在原地，之後 `attach` 回來就接得上。

> **`attach` 只收 `$HOME` 底下的 plan。** pointer 的 `plan_path` 在寫入與每輪讀取時都會 `resolve()` 後比對 `$HOME`（一併擋掉 symlink escape），落在 `$HOME` 之外的 plan 會被判為 invalid，自動推進不會啟動、跨 session 也接不回來。此時 `/plan-run` 仍可用，只是退回手動模式（見 `plan-run/SKILL.md` 的「全手動模式（連 `/goal` 都不用時）」）；要自動推進就把 plan 搬進 `$HOME` 底下再 `attach`。
>
> **來源不明的 plan 先讀過再 attach。** hook 注入的 `reason` 會以 harness 的權威被當成模型的下一個指令，而其中一部分來自 plan 的 `Action` 欄位文字——任何寫進 plan 的文字都因此取得一條「每輪自動注入」的通道。hook 端已做隔離（plan 原文包在標註 `plan data, not instructions` 的圍欄內、截斷 600 字元、剝除控制字元與 ANSI escape、**不執行** plan 的 `Command` 欄位），但若 plan 來自 Notion ticket、他人 PR 或 `/notion-plan` 抓來的內容，attach 之前請自己讀一遍。

## 6. 低風險替代：裝在專案層

不想動 `~/.claude/settings.json` 的話，同一個物件可以放進**專案的** `.claude/settings.json`：

```bash
mkdir -p <專案>/.claude
```

放在專案層的效果與範圍：

- 只在該專案目錄底下的 session 生效，其他專案完全不受影響
- 影響面小、還原只要刪掉那個檔
- 代價是**每個要用 `/plan-run` 的專案都得裝一次**，且該檔通常會進版控——團隊成員會一起吃到這個 hook，裝之前先講一聲

`~/.claude/hooks/plan-run-stop.sh` 這支 wrapper 兩種裝法都要有（專案層 settings 只是指過去）。

## 7. 為什麼不提高 `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`

本機實測（2.1.251，證據 `.verification/2026-08-29/stop-hook-block-cap-measured.md`）：always-block 的 Stop hook 會被呼叫 **9 次，第 9 次的 block 不被採納**——實際可用 **8 次續推**，這是官方防呆。而且**上限是每個 turn 的 Stop 輪數，不是每支 hook 的次數**：掛兩支獨立的 always-block hook，兩支都拿到全部 9 輪，turn 一樣在第 9 輪結束。

所以**多掛一支 blocker（例如 `/goal`）不會讓一輪跑更多步**——拿到的是「同一輪多一則彼此稀釋的 reason」，不是更多輪。想一輪跑更久只有調自己的預算一條路。

本 hook 主動把預算設在 **7**（或更早的 phase 邊界就停），留一輪餘裕，讓停下來的那一刻落在對使用者有意義的檢查點，而不是撞上限被硬截斷。

**不要為了「一次跑完整份 plan」去調高上限。** 那個上限存在的理由就是「無人值守跑很久」是被刻意擋住的行為；繞過它等於把 plan 推進變成一個沒有人在看的迴圈。本 repo 的做法是順著它設計：

- `plan_runner.py` **從不讀寫** harness 自己的 block-cap 環境變數
- 本 repo 自己的 `PLAN_RUN_BLOCK_BUDGET` 硬夾在 **8**（實測可用的續推數），設再大都無效。預設 7 留一輪餘裕，設 `8` 是用滿、設小於 7 是「我想更頻繁地被問一次」
- `stop_hook_active` 旗標一律照實回報，不偽造

撞到 check-in 就是該讓人看一眼。要繼續，回一句話就會從下一步接著跑。

## 8. 移除

移除分兩件事，不要混在一起：**解除安裝**（拿掉 hook）與**清除狀態**（丟掉進行中的 pointer）。`~/.claude/plan-run/` 是狀態，不是安裝的一部分——它由首次 `attach` 時自動建立，刪掉不影響安裝完整性，但會失去所有進行中的 pointer。

先備份：

```bash
mkdir -p ~/.claude/backups/$(date +%F)-stop-hook-uninstall
cp ~/.claude/settings.json ~/.claude/hooks/plan-run-stop.sh ~/.claude/backups/$(date +%F)-stop-hook-uninstall/
```

解除安裝（逐一列明確路徑，不用萬用字元）：

1. `settings.json` 的 `hooks.Stop` 移除含 `plan-run-stop` 的那一個物件（以 tmp + 原子 replace 落地，落地前先 parse）
2. `rm -f ~/.claude/hooks/plan-run-stop.sh`

清除狀態（可選，只在確定不再續推任何 plan 時做）：

3. `rm -rf ~/.claude/plan-run`

驗證回到移除前狀態——四項都要查，只查數量不夠：

| 檢查 | 期望 |
|---|---|
| `Stop` hook 數量 | 原本的數量 − 1 |
| 其餘既有 hook 的 JSON 與備份逐字相同 | True |
| `Stop` 以外的設定完全相同 | True |
| `settings.json` 內 `plan-run` 殘留字串數 | 0 |

然後開一個新 session 跑一輪，確認 turn 正常結束、既有 hook 各觸發一次、`plan-run` 事件數為 0。

移除之後 `plan_runner.py` 的既有子命令（`init` / `status` / `next` / `index` / `dag` / `set-parent` / `start` / `complete` / `fail` / `reset` / `skip` / `normalize` / `report`）全部仍可手動使用——退回 `/plan-run` 的手動模式，不會壞掉。

**還原時把備份裡的 hook 物件直接取回，不要重新編造**——否則「還原」可能寫進一個長得像但其實不同的物件。還原後補跑一次 `doctor`。

---

# 三、情境型 rules 的觸發式安裝（UserPromptSubmit）

`rules/` 底下的規則，一般用法是 symlink 進 `~/.claude/rules/common/`：

```bash
ln -s ~/Documents/agent-skills/rules/debug-triage-order.md ~/.claude/rules/common/
```

這樣做**每個 session 都會全文載入**。對「每次都要守」的紀律（coding style、
輸出格式）是合理的；但對**情境型**規則就是純浪費——`debug-triage-order` 只在
「debug 一個線上回報的 bug」時適用，`worktree-prompt` 只在「開工實作」那一刻適用，
其餘 session 付了 token 卻用不到。

實測：兩份合計約 1,120 tokens，佔某台機器常駐預算的 15%。

## 1. 兩種安裝模式，二選一

| | 常駐 rule | 觸發式 hook |
|---|---|---|
| 安裝 | symlink 進 `~/.claude/rules/common/` | 註冊進 `settings.json` 的 `UserPromptSubmit` |
| 每 session 成本 | 全文（數百 tokens） | **0** |
| 何時生效 | 一直在 context 裡 | 使用者的訊息命中偵測條件時注入 |
| 適合 | 每次都要守的紀律 | 情境型、只在特定任務適用 |
| 風險 | 長 context 稀釋注意力 | 偵測條件漏接就不會提醒 |

**不要兩個都裝**——會在同一個 session 裡看到規則兩次。已用 symlink 裝成常駐的人，
改裝 hook 前先 `rm ~/.claude/rules/common/<rule>.md`（那是 symlink，
來源檔在 repo 裡不會被刪）。

## 2. 追加到 `~/.claude/settings.json`

與第一、二篇同樣是 **additive 編輯**——在既有 `hooks.UserPromptSubmit` 陣列的
`hooks` 末端追加，不可覆蓋或重寫整個陣列。先備份：

```bash
cp ~/.claude/settings.json ~/.claude/settings.json.bak.$(date +%Y%m%d%H%M%S)
```

與第二篇同樣先把 script 複製到固定安裝路徑，`settings.json` 只引用那個路徑——
這樣 checkout 搬家或改名都不必回頭改設定：

```bash
mkdir -p ~/.claude/hooks
cp scripts/hooks/debug-triage-order-hint.sh ~/.claude/hooks/
cp scripts/hooks/worktree-prompt-hint.sh    ~/.claude/hooks/
chmod +x ~/.claude/hooks/debug-triage-order-hint.sh ~/.claude/hooks/worktree-prompt-hint.sh
```

追加的物件：

```json
{ "type": "command", "command": "bash ~/.claude/hooks/debug-triage-order-hint.sh" },
{ "type": "command", "command": "bash ~/.claude/hooks/worktree-prompt-hint.sh" }
```

**`AGENT_SKILLS_DIR` 只影響 hint 訊息裡「規則全文」那條路徑，不影響上面的安裝路徑。**
checkout 不在 `~/Documents/agent-skills` 時要設它，否則 hint 會指到不存在的規則檔：

```bash
export AGENT_SKILLS_DIR=/your/path/to/agent-skills   # 寫進 shell profile
```

未設時預設 `~/Documents/agent-skills`。這個值是在 hook 的 Python 段用 `os.environ`
讀取的，不依賴 shell 展開。

## 3. 自檢

每個 hook 都可以直接餵 JSON 測，命中會輸出 `hookSpecificOutput` JSON、不命中無輸出：

```bash
echo '{"prompt":"線上文章頁圖片壞掉，dev 正常，幫我查"}' \
  | bash scripts/hooks/debug-triage-order-hint.sh          # 應有輸出

echo '{"prompt":"這段 function 有 bug 幫我修"}' \
  | bash scripts/hooks/debug-triage-order-hint.sh          # 應無輸出（本地邏輯題）

echo '{"prompt":"幫我實作這個 plan","cwd":"/path/to/normal/repo"}' \
  | bash scripts/hooks/worktree-prompt-hint.sh             # 應有輸出
```

**漏接與誤報是兩種不同的失敗**，改偵測條件後兩邊都要跑，只測其中一邊會過度放寬
或過度收緊。

## 4. 設計原則

1. **只注入 hint，不阻擋。** 走 `additionalContext`，偵測錯了最多是多一段文字，
   不會擋住任何操作——與第二篇的 Stop hook 不同，那支會 block。
2. **slash command 開頭一律跳過。** 使用者已明確指定 skill 時不要插話。
3. **高精度優先於高召回。** `debug-triage-order-hint` 要**同時**命中「debug 訊號」
   與「可觀測環境訊號」才觸發——只講「這段程式有 bug」不算，那是本地邏輯題，
   prod-first probe 不適用。誤報的成本是雜訊，會讓人把整個 hook 關掉。
4. **hint 要自包含。** 訊息本身就帶可執行的重點（三條分流順序、worktree 指令），
   不能只寫「請參閱某某規則」——那等於沒提醒。

## 5. 移除

從 `~/.claude/settings.json` 的 `UserPromptSubmit` 陣列刪掉對應物件即可；
`scripts/hooks/*.sh` 留著不影響任何行為（沒有程式會自動執行它們）。
想改回常駐模式就重新 symlink 進 `~/.claude/rules/common/`。

---

# 四、驗證 gate hook（PreToolUse、PostToolUse、Stop）

三個選用的 gate，在 enforce 模式下把「未驗證就開 PR、未驗證就收工」從「靠模型記得」換成機械檢查。三個 gate **預設全部關閉**；observe 模式只記錄、不介入，不算防護，建議先用它量到數字，再逐一決定要不要升 enforce。

一支 wrapper `scripts/hooks/verify-gate.sh` 掛三個事件，第一個參數決定子命令：

| 子命令 | 事件 | 做什麼 |
|---|---|---|
| `pre-tool` | `PreToolUse`，matcher `Bash` | gate `pre_pr`：指令位置出現 `gh pr create` 時檢查快檢 receipt |
| `post-tool` | `PostToolUse`，matcher `Bash` | 只記錄、永不阻擋：把本 session 動過的 repo 加 branch 記下來 |
| `stop` | `Stop` | gate `ui_acceptance` 與 `ci_evidence`：檢查本 session 關聯過的每個 PR |

## 1. 三個 gate 各檢查什麼

| gate | 何時檢查 | 通過條件 | 訊號來源 |
|---|---|---|---|
| `pre_pr` | 指令位置的 `gh pr create` 之前 | 目前 `HEAD^{tree}` 有有效的快檢 receipt：有 receipt、工作樹現在乾淨、設定 digest 沒變、merge-base 沒變、引擎版本沒變、必要快檢都通過 | `<repo>/.verification/receipts/<tree>.json` |
| `ui_acceptance` | 每輪結束（Stop） | 本 session 關聯過的每個 UI PR 都有有效的驗收 receipt（驗收之後沒有再動 UI 路徑） | 驗收 receipt、PR 註冊表 |
| `ci_evidence` | 每輪結束（Stop） | 本 session 關聯過的每個 PR，其 head 都有有效的 CI receipt，且結論不是 red | CI receipt、PR 註冊表 |

訊號全部來自本機檔案。hook **不發網路請求、不呼叫 `gh`、不讀對話紀錄**；取得 PR 與 CI 資料的網路動作發生在模型流程裡的引擎指令（`verify_engine.py pr register --from-gh`、`receipt ci --from-gh`）。有效性判定一律呼叫 `scripts/verify_receipts.py`，與引擎共用同一份規則。

只對「知識來源裡恰好有一份 profile 對到」的 repo 生效。沒有 profile 的 repo 一律放行。

**本 session 關聯哪些 PR**：PostToolUse 在指令位置看到下列任一個成功的指令，就把目標 repo 加 branch 記進本 session 的關聯檔：

- `git push`：另外把推上去的 head 寫進註冊表的最新推送 head。一般寫法取被推送 branch 本機 ref 當下的 head；`<src>:<branch>`（例如從 detached HEAD 推 `HEAD:feat/x`）取 `<src>` 在該 repo 解析出的 SHA，記在目的 branch 名下；目的 branch 已知但 `<src>` 解析不出來時，把推送未解析的標記記在該 branch。只推 tag 與刪除遠端 branch 不記錄、不關聯。`--all`、`--mirror` 對註冊表有條目或本 session 已關聯的每個 branch 以本機 ref 記錄，不新增關聯。只有真的判斷不了的推送（參數含解析不出的變數、不認得的選項、目錄解析不出來）才把整個 repo 標為推送未解析，詳見第 2 節的表。
- `gh pr create`：另外在決策紀錄寫一筆 `link` 事件，把 session、repo、branch 接上 PR 號（從輸出中的 `/pull/<數字>` 取得）。帶 `--help`、`-h`、`--dry-run` 的不算建立 PR，不記錄。
- `verify_engine.py pr register`：包含 skill 裡的寫法 `$E pr register --repo . --from-gh`（`E` 在同一則指令內定義，或根本沒定義），以及 `python3 ${AGENT_SKILLS_HOME:-...}/scripts/verify_engine.py pr register ...`。只在工具輸出含引擎的註冊成功字樣時才關聯（JSON 模式的 `"result": "registered"`，或文字模式以 `registered <branch>: pr=<n> state=<s>` 開頭的一行）；輸出被丟掉（`> /dev/null`、`| tail -0`）就不會關聯。只做驗收、沒有 push 的 session 靠這一步進入檢查，所以這一步的輸出要留在工具結果裡。

`gh pr create` 成功（有 PR 號）或 `pr register` 成功，但目標 repo 或 branch 解析不出來時（目錄或 `--repo`、`--branch`、`--head` 的值含解析不出的變數），不關聯，改寫一筆 `link` 事件：`branch` 為 null、reason code `link-unresolved`、`pr` 填拿得到的號碼。量測看得到「這個 session 建了或註冊了一個沒被追蹤的 PR」。

每個關聯另外記下該 worktree 的 git common dir（存在狀態目錄的 `gate-worktrees.json`）。worktree 之後被移除時，Stop 改用同一個 repo 仍存在的其他 worktree 評估，全都不在才記 `repo-unavailable` 放行。receipt 存在被移除的 worktree 的 `.verification/` 裡，會跟著消失；gate 分不出「驗過但檔案沒了」與「從沒驗過」，所以在替代的 worktree 找不到 receipt 時不擋，記 `allow`、reason code `receipts-unavailable`。替代的 worktree 裡找得到 receipt（例如在那裡重跑過 `receipt ci --from-gh`）就照常評估。

**Stop 怎麼判**：

- 檢查本 session 關聯過的**所有** PR，不以目前 checkout 的 branch 篩選。push 完切到別的 branch 再結束，原本的 PR 一樣要檢查。
- 每個 PR 的 head 依契約文件的「head 決定順序」：原則上用本機 ref，但最新推送 head 不是本機 ref 的祖先（實際推上去的內容本機 ref 沒有）時改用最新推送 head；本機 ref 不存在時只能用最新推送 head 或時間更晚的註冊紀錄；推送未解析的標記晚於這些紀錄、或決定不了，就是 `head-unresolved`，視為缺證據，不會退回較早的 head、也不會沿用舊 receipt。
- 推了沒註冊的 branch：`pr-unregistered`，要求跑 `pr register --from-gh`，或以 `--none` 標記沒有 PR。
- 註冊為 closed 或 `--none` 的 branch 免檢，但只在 head 沒變時成立；head 變了是 `registration-stale`。
- `ui_acceptance` 只管 UI PR：branch 與預設 base 的 merge-base 到 head 之間，有檔案符合 profile 的 `Paths` `UI` 且不被 `UI-exclude` 排除。找不到 merge-base 時當作 UI PR。
- `ci_evidence`：profile 沒宣告 `Required-jobs` 時放行並記 `ci-not-configured`；`pending-declared` 與 `exempt` 放行但記 `declared-unverified`；沒有 receipt、只有舊 head 的、CI 設定在寫 receipt 之後變了（例如新增必要 job）、結論 red，都擋。
- `pre_pr` 的 receipt 判為 `PROFILE-STALE`（profile 跟 repo 脫節，不是變更本身的問題）時放行並記 `profile-stale`。

## 2. 指令判讀規則

hook 自己寫了一個保守的指令掃描器（`scripts/verify_gate_cmd.py`），只認「指令位置」：以 `;`、`&&`、`||`、`|`、`&`、換行分隔後，每段去掉開頭的 `NAME=value`、`if`／`then`／`else`／`elif`／`while`／`until`／`do`／`{`／`!` 這類關鍵字與包裝指令之後的第一個字。命令替換 `$(...)`、反引號與 `( ... )` subshell 裡的指令會執行，所以也會掃，各自沿用當下的目錄。

同一則指令裡先賦值再使用的變數（`WT=/abs/path; cd "$WT" && ...`、`export BR=feat/x`、`${WT}`）以賦值的靜態字串展開後照常判斷。值本身含命令替換或未知變數（`WT=$(git rev-parse --show-toplevel)`）、或在這則指令裡沒有賦值的變數，算解析不出來。只採信無條件執行的賦值：出現在 `if`／`then`／`do`／`{` 等關鍵字之後或複合指令裡、`&&`／`||`／`|` 右側、`|` 左側、subshell 或命令替換裡的賦值，以及被 `unset`、`read`、`for <名稱> in`、`export -n`、`local`、`declare` 碰過的名稱，一律視為未知，之後用到它就記 `cwd-unresolved`（或 `link-unresolved`），不會落到某個具體路徑。

認得的寫法（舉例）：

- `gh pr create --fill`、`GH_DEBUG=1 gh pr create ...`、`gh pr create --body "$(cat <<'EOF' ... EOF)"`
- 包裝指令之後：`command gh pr create`、`env [-i] [-u X] [NAME=V]... gh pr create`、`timeout [-s SIG] [-k 時間] [--foreground] <時間> gh pr create`、`nohup ...`、`exec ...`、`time [-p] ...`、`sudo [-u X] [-E] ...`、`nice [-n N] ...`，可疊用（`env X=1 timeout 60 command gh pr create`）
- 關鍵字之後：`if gh pr create ...; then`、`then gh pr create`、`do gh pr create`、`else ...`、`{ gh pr create; }`、`! gh pr create`
- 先賦值再使用：`WT=/abs/path; cd "$WT" && gh pr create`、`git -C "$WT" push`、`--repo "$WT"`、`--head "$BR"`
- 命令替換與 subshell 內：`URL=$(gh pr create --fill)`、`` echo `gh pr create` ``、`... || (gh pr create)`、`(cd x && git push)`
- `git push`、`git push -u origin HEAD`、`git -C <路徑> push origin <branch>`、`git -c k=v --no-pager push ...`
- `cd <路徑> && gh pr create`（`cd` 之後以 `&&`、`;`、換行接續的指令都視為在該目錄執行）。`cd` 出現在關鍵字之後或複合指令裡（`if …; then cd X && gh pr create; fi`、`{ cd X && …; }`、`for …; do cd X …`）時不採用：作用範圍不模擬，之後所有段落的目錄一律算解析不出來，記 `cwd-unresolved`、不擋
- `python3 scripts/verify_engine.py pr register --from-gh`、`E="python3 .../verify_engine.py"` 之後的 `$E pr register ...`、只有尾段是字面的 `python3 ${...}/scripts/verify_engine.py pr register ...`

明確不算的寫法（放行，不留紀錄）：

- 註解裡（`# gh pr create`）、heredoc 內文、引號內（`echo "gh pr create"`、`git commit -m "... gh pr create ..."`、`grep "gh pr create" f`）
- `gh pr create --help`、`-h`、`--dry-run`（不建立 PR）；`command -v gh`（查詢，不執行）

放行但留紀錄（decision `allow`，observe 與 enforce 都寫，讓量測看得到覆蓋缺口）：

| 情況 | reason code |
|---|---|
| 認得 `gh pr create`，但目錄解析不出來（`cd "$WT" && gh pr create` 的 `WT` 沒有在同一則指令裡賦值，或值含命令替換） | `cwd-unresolved` |
| 沒有認出任何 `gh pr create`（不論整段能不能解析），而引號、註解與 heredoc 之外出現 `gh pr create` 字樣：`xargs ... gh pr create` 這類不認得的包裝、process substitution、`case` 分支內等 | `unparsed` |

`unparsed` 會有少量假紀錄：未加引號、當成參數傳給別的程式的 `echo gh pr create` 也會記一筆。只影響量測，不會擋。

選項的值解析不出來時（`pr register --repo "$WT"`、`--branch "$BR"`、`gh pr create --head "$BR"`，且變數沒有在同一則指令裡賦值），不退回目前目錄或目前 branch 去猜，那一筆不關聯，改寫 `link-unresolved`；`git push` 的目錄解析不出來時，若 payload 的 `cwd` 是有 profile 的 repo，就把它標為推送未解析。

`git push` 推的是哪個 branch：

| 寫法 | 判定 |
|---|---|
| 沒有位置參數，或只有 remote；refspec 為 `HEAD` 或 `@` | 目前 checkout 的 branch（detached HEAD 時把該 repo 標為推送未解析） |
| `<remote> <branch>`、`<remote> +<branch>`、`<remote> refs/heads/<branch>`、`<remote> <branch>:<branch>` | 該 branch，取本機 ref；本機沒有這個 branch 時把標記記在該 branch |
| `<remote> <src>:<branch>`、`<remote> +<src>:refs/heads/<branch>`（兩邊不同名，例如 `HEAD:feat/x`、`v1:feat/x`） | 目的 branch；最新推送 head 取 `<src>` 解析出的 commit SHA（`<src>` 是 tag 也照常解析，tag 判斷只看目的），解析不出來就把標記記在該 branch |
| 多個 refspec（`<remote> a b`） | 逐一依上面各列處理 |
| `--tags` 且沒有 branch refspec、目的是 `refs/tags/*`、名稱在該 repo 解析為 tag 而不是 branch（`git push origin v1`）、`--delete`／`-d`、`<remote> :<branch>` | 不記錄、不標記、不關聯 |
| `--all`、`--branches`、`--mirror` | 註冊表有條目或本 session 已關聯的每個 branch，以本機 ref 記錄；不新增關聯、不打 repo 層級標記 |
| 目的是 `refs/` 下其他位置（例如 `HEAD:refs/remotes/x`）、不認得的選項、參數含解析不出的變數、目錄解析不出來 | 判斷不了：把整個 repo 標為推送未解析 |
| `-n`、`--dry-run` | 不算推送，不記錄 |

「成功」的判定（PostToolUse 只拿得到工具輸出，判不準時寧可多記）：

- `git push`：`interrupted` 為真，或輸出含 `failed to push`、`[rejected]`、`[remote rejected]`、`has no upstream branch`、`does not appear to be a git repository`、`could not read from remote repository`、`authentication failed`、`permission denied` 任一字樣（不分大小寫）時不算成功；其餘都算成功。多記的後果是多要求一次驗證，少記的後果是漏擋。
- `gh pr create`：stdout 有 `/pull/<數字>`（或 stderr 寫明 PR 已存在且附網址）就算成功並記 PR 號；沒有網址時，`interrupted` 為假且輸出不含 `error:`、`fatal:`、`could not`、`must first push`、`no commits between` 等失敗字樣，才算成功（只記關聯、不寫 `link` 事件）。
- `pr register`：只有輸出含 `"result": "registered"`，或有一行以 `registered <branch>: pr=<n> state=<s>` 開頭，才算成功。其餘一律不算，避免把不是引擎的 `$X pr register` 誤認進來。

## 3. 安裝 wrapper

```bash
mkdir -p ~/.claude/hooks
cp scripts/hooks/verify-gate.sh ~/.claude/hooks/verify-gate.sh
chmod +x ~/.claude/hooks/verify-gate.sh
```

wrapper 依序取 `AGENT_SKILLS_DIR`、`AGENT_SKILLS_HOME`，都沒設就用 `~/Documents/agent-skills` 當 checkout，以 `cd -P` 解析後必須落在 `$HOME` 之下，否則什麼都不做。stdin 原封不動交給 Python；未知子命令、缺 python3、checkout 不存在，一律 exit 0 且無輸出。唯一的例外是 `pre-tool` 在 enforce 下刻意擋下：exit 2，stderr 一行以 `[verify-gate]` 開頭的理由。

## 4. 追加到 `~/.claude/settings.json`

**這是 additive 步驟，不是覆蓋。** 三處各在既有陣列的末端追加一個物件，既有的 hook 一個都不動。先備份：

```bash
mkdir -p ~/.claude/backups/$(date +%F)-verify-gate
cp ~/.claude/settings.json ~/.claude/backups/$(date +%F)-verify-gate/settings.json
```

要追加的三個物件：

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Bash", "hooks": [ { "type": "command", "command": "bash ~/.claude/hooks/verify-gate.sh pre-tool", "timeout": 15 } ] }
    ],
    "PostToolUse": [
      { "matcher": "Bash", "hooks": [ { "type": "command", "command": "bash ~/.claude/hooks/verify-gate.sh post-tool", "timeout": 15 } ] }
    ],
    "Stop": [
      { "hooks": [ { "type": "command", "command": "bash ~/.claude/hooks/verify-gate.sh stop", "timeout": 15 } ] }
    ]
  }
}
```

上面是三個陣列各自「要多出來的那一個元素」，不是整段貼上取代。hook 自己有 8 秒的總時限，超過就放行；`timeout` 設 15 秒留餘裕。

落地後與備份逐字比對既有 hook，只比數量不夠：

```bash
python3 - <<'EOF'
import json, os, datetime
cur = json.load(open(os.path.expanduser("~/.claude/settings.json")))["hooks"]
bak_path = f"~/.claude/backups/{datetime.date.today()}-verify-gate/settings.json"
bak = json.load(open(os.path.expanduser(bak_path)))["hooks"]
for event in ("PreToolUse", "PostToolUse", "Stop"):
    old, new = bak.get(event, []), cur.get(event, [])
    same = [json.dumps(h, sort_keys=True) for h in new[:len(old)]] == [json.dumps(h, sort_keys=True) for h in old]
    print(event, "數量", len(old), "->", len(new), "既有逐字相同:", same)
EOF
```

每個事件都應是「原本數量 + 1」且「既有逐字相同: True」。

## 5. 模式

每個 gate 有三種模式：

| 模式 | 行為 |
|---|---|
| `off` | 什麼都不做，不讀也不寫狀態目錄 |
| `observe` | 只在決策紀錄追加一行（會擋的情境記 `would_block`），不輸出任何東西、不擋 |
| `enforce` | 擋下：`pre_pr` 讓 `gh pr create` 不執行並把理由交給模型；Stop 的兩個 gate 要求模型繼續補證據 |

每個 gate 的模式依序取：

1. 該 gate 自己的環境變數：`AGENT_SKILLS_VERIFY_GATE_PRE_PR`、`AGENT_SKILLS_VERIFY_GATE_UI_ACCEPTANCE`、`AGENT_SKILLS_VERIFY_GATE_CI_EVIDENCE`
2. 總開關 `AGENT_SKILLS_VERIFY_GATE`
3. 模式檔 `${XDG_CONFIG_HOME:-$HOME/.config}/agent-skills/verify-gate`

第一個有設定的來源決定結果；值不認得（例如打錯字）就是 `off`，三處都沒有也是 `off`。模式檔每行一個 `<gate>=<mode>`，`#` 之後是註解，不認得的 gate 名稱略過，同一個 gate 寫兩次以後面那行為準：

```text
# agent-skills verify gate
pre_pr=observe
ui_acceptance=observe
ci_evidence=observe
```

同一個 session 裡，同一個 gate 擋過兩次之後就放行，並在紀錄記 `released`。enforce 之後，每個 session 每個 gate 最多兩次、每次的理由不超過 400 字元，只含 gate 名、reason code、branch 名、PR 號與建議的下一條指令，不回顯指令原文或工具輸出。

## 6. observe 先行，量到數字再升 enforce

先把三個 gate 都設成 `observe` 跑一段時間。決策紀錄在狀態目錄的 `decisions.jsonl`（預設 `~/.claude/verify-gate/`，可用 `AGENT_SKILLS_VERIFY_STATE_DIR` 改），每行一筆，欄位見 `docs/knowledge-source-contract.md` 的「gate 決策紀錄」。依 gate 與 reason code 統計：

```bash
python3 - <<'EOF'
import collections, json, os
path = os.path.expanduser("~/.claude/verify-gate/decisions.jsonl")
rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
count = collections.Counter((r["gate"], r["decision"], r["reason_code"])
                            for r in rows if r["event"] == "decision")
for key, n in sorted(count.items(), key=str):
    print(n, *key)
EOF
```

看三件事再決定：`would_block` 佔 Stop 次數的比例（升 enforce 後會被擋的頻率）；`would_block` 裡有多少是誤判（逐筆對照當時的 PR，確認真的缺證據）；`declared-unverified` 與 `ci-not-configured` 的量（這兩類放行但沒有真正驗證，量測時要單獨看）；`cwd-unresolved`、`unparsed`、`link-unresolved` 的量（指令寫法超出掃描器能判讀的範圍，是覆蓋缺口）；`receipts-unavailable` 的量（worktree 移除後無從檢查）；`hook-timeout` 與 `hook-error` 的量（hook 自己 fail-open 放行的次數）。一次只升一個 gate。

**observe 不是防護。** off 與 observe 都屬「僅 observe」：只留紀錄、不阻止任何事；只有 enforce 才是機械化的檢查，而且仍有下面「已知盲點」列的缺口。

## 7. 自檢（stdin 冒煙）

以下指令在安裝後直接餵 JSON 給 wrapper。用一個專門的 session id（`smoke-test`），不影響真實 session 的擋下計數。

壞輸入：無輸出且 exit 0。

```bash
printf '%s' 'not json' | bash ~/.claude/hooks/verify-gate.sh stop; echo "rc=$?"
# 預期只印 rc=0
```

未命中：無輸出且 exit 0。

```bash
printf '%s' '{"session_id":"smoke-test","cwd":"'"$PWD"'","tool_name":"Bash","tool_input":{"command":"ls"}}' \
  | AGENT_SKILLS_VERIFY_GATE_PRE_PR=observe bash ~/.claude/hooks/verify-gate.sh pre-tool; echo "rc=$?"
# 預期只印 rc=0
```

必擋情境在 observe 下：在一個有 profile、目前 HEAD 沒有快檢 receipt 的 repo 根目錄執行。無輸出、exit 0，決策紀錄多一行 `would_block`。

```bash
LOG=~/.claude/verify-gate/decisions.jsonl
BEFORE=$(cat "$LOG" 2>/dev/null | wc -l)
printf '%s' '{"session_id":"smoke-test","cwd":"'"$PWD"'","tool_name":"Bash","tool_input":{"command":"gh pr create --fill"}}' \
  | AGENT_SKILLS_VERIFY_GATE_PRE_PR=observe bash ~/.claude/hooks/verify-gate.sh pre-tool; echo "rc=$?"
AFTER=$(wc -l < "$LOG")
echo "多了 $((AFTER - BEFORE)) 行"; tail -1 "$LOG"
# 預期：rc=0、多了 1 行、最後一行的 decision 為 would_block、reason_code 為 no-receipt
```

## 8. 與其他 Stop hook 共存

- Claude Code 會執行 `Stop` 陣列裡的每一支 hook。本 hook 擋下時輸出 top-level 的 `{"decision": "block", "reason": "..."}`，與第二篇的 plan-run 同一個形狀。
- Stop 的 block 輪數上限是每個 turn 共用的，不是每支 hook 各自一份（見第二篇「為什麼不提高 `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`」）。本 hook 的兩個 Stop gate 各自在同一個 session 至多擋兩次，佔用的輪數有上限；與 plan-run 同一輪都擋時，模型會同時收到兩則理由，彼此稀釋。
- `stop_hook_active` 為 true（上一輪已被某個 Stop hook 擋過）時，比照 plan-run 的做法：不偽造、不改寫這個旗標，也不因它就放行；本 hook 靠自己的「每 session 每 gate 至多兩次」收斂。與 plan-run 不同的是，這個計數不在使用者開口時歸零，整個 session 共用。
- 所有失敗路徑都 exit 0 且無輸出，與第二篇同理：Stop hook 一旦出錯就會擋住每一輪。

## 9. 移除

先備份：

```bash
mkdir -p ~/.claude/backups/$(date +%F)-verify-gate-uninstall
cp ~/.claude/settings.json ~/.claude/hooks/verify-gate.sh ~/.claude/backups/$(date +%F)-verify-gate-uninstall/
```

解除安裝（逐一列明確路徑，不用萬用字元）：

1. `settings.json` 的 `PreToolUse`、`PostToolUse`、`Stop` 三處，各移除 command 含 `verify-gate.sh` 的那一個物件。用指令內容比對，不要數第幾個；落地前先 parse 確認是合法 JSON。
2. `rm -f ~/.claude/hooks/verify-gate.sh`
3. 想保留 hook 但暫停時，不必移除：把三個 gate 的模式設回 `off`（刪掉模式檔或環境變數）即可。

清除狀態（可選）：`~/.claude/verify-gate/` 是狀態不是安裝，裡面有 PR 註冊表、session 關聯檔、worktree 對照（`gate-worktrees.json`）與決策紀錄。註冊表也是引擎 `pr register` 的寫入位置，刪掉會讓既有註冊失效；只在確定不再使用驗證引擎時才 `rm -rf ~/.claude/verify-gate`。

驗證：

```bash
grep -c "verify-gate" ~/.claude/settings.json   # 預期 0
test ! -e ~/.claude/hooks/verify-gate.sh && echo "wrapper removed"
```

並以第 4 節的比對腳本確認三個事件的其餘 hook 與備份逐字相同。

## 10. 已知盲點

設計上就擋不到的情況：

1. 既沒有 push、也沒有開 PR、也沒有註冊的 session 不會被檢查，這類單位在量測時會顯示為「gate 未涵蓋」。
2. Stop 觸發時宣稱文字已送出，gate 擋不住宣稱本身。
3. 以 `--evidence` 提供的證據檔由模型產生，gate 防的是遺漏，不是蓄意偽造。
4. 記了 `pending-declared` 之後仍宣稱完成，gate 攔不到。

實作上的取捨，同樣會漏：

- 指令掃描器仍認不得的寫法：`case` 分支內、process substitution、`xargs`、`find -exec`、`bash -c '...'`／`sh -c '...'`（引號內，連 `unparsed` 都不會記）、陣列展開（`"${E[@]}"`）、指令名由解析不出的變數組成（`$GIT push`）、`git --git-dir=...`。
- 目錄解析不出來的 `gh pr create` 只記 `cwd-unresolved`，不擋；認不出來但引號外出現 `gh pr create` 字樣的只記 `unparsed`，不擋。兩者都是為了避免誤擋，代價是換成這類寫法就繞得過 enforce。
- 關鍵字之後或複合指令裡的 `cd`、條件式裡的變數賦值都不採用，之後的目錄記 `cwd-unresolved`、不擋。
- `cd "$變數"` 而變數不在同一則指令內賦值，是 worktree 流程最常見的寫法之一，在 enforce 下同樣不會被擋。observe 期應單獨統計 `cwd-unresolved` 佔 `pre_pr` 紀錄的比例，再決定升 enforce 時要不要改成擋。
- `pre_pr` 檢查的是指令執行目錄的 `HEAD`：`gh pr create --head <別的 branch>` 或 `-R <別的 repo>` 時，檢查的不是 PR 實際的 head 或 repo。
- push 的 branch 判斷不了時只標記推送未解析，不會建立 session 關聯；該 branch 若從未以其他方式關聯，Stop 不會檢查它。
- worktree 被移除後，該 PR 不再被擋，只留 `receipts-unavailable` 紀錄：receipt 隨 worktree 一起消失，gate 分不出驗過與否。要保留檢查，就在移除前完成驗證，或從另一個 worktree 重跑 `receipt ci --from-gh`。
- `pr register`（含 `$E pr register`）只在輸出含引擎的註冊成功字樣時才關聯；輸出被丟掉、截斷或導向檔案時不會關聯。
- 目標 repo 或 branch 解析不出來的 `gh pr create` 與 `pr register` 只寫 `link-unresolved`，Stop 不會檢查那個 PR。
- 指令失敗時 Claude Code 是否仍觸發 PostToolUse 依版本而定；沒觸發時那次推送不會被記錄。
- hook payload 的欄位（`session_id`、`cwd`、`tool_input.command`、`tool_response`、`stop_hook_active`）取不到時一律放行。
- hook 的輸入格式（上述欄位的名稱與形狀、PreToolUse 以 exit 2 擋下）目前只以 stdin JSON 的測試驗證，尚未在真實 Claude Code session 驗證。安裝後先做第 7 節的自檢，再開一個真實 session 確認決策紀錄有寫入；欄位對不上時 gate 會全部放行，看起來像「沒有東西要擋」。
- 總時限（8 秒）或未預期例外造成的放行，會盡力寫一筆 decision `allow`、reason code `hook-timeout` 或 `hook-error`（`path` 為當時所在的路徑）；狀態目錄本身不可用時連這筆都寫不出來。
