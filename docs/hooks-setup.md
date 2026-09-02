# Hook 設定

本 repo 有三組一次性安裝的 hook，彼此獨立，可只裝其中一部分：

| 章節 | Hook 類型 | 做什麼 | 搭配 |
|------|-----------|--------|------|
| [一](#一plan-自動儲存-hookposttooluse) | `PostToolUse` | `ExitPlanMode` 後自動把 plan 存進 `plans/active/` | `/plan-archive` |
| [二](#二plan-dag-推進-stop-hook) | `Stop` | 每輪結束查 plan state，把下一步注入回模型 | `/plan-run` |
| [三](#三情境型-rules-的觸發式安裝userpromptsubmit) | `UserPromptSubmit` | 命中情境時注入規則重點，取代常駐 rule | `rules/` |

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

移除之後 `plan_runner.py` 的既有子命令（`init` / `status` / `next` / `index` / `dag` / `set-parent` / `start` / `complete` / `fail` / `reset` / `skip` / `normalize`）全部仍可手動使用——退回 `/plan-run` 的手動模式，不會壞掉。

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

追加的物件：

```json
{ "type": "command", "command": "bash ~/Documents/agent-skills/scripts/hooks/debug-triage-order-hint.sh" },
{ "type": "command", "command": "bash ~/Documents/agent-skills/scripts/hooks/worktree-prompt-hint.sh" }
```

repo 不在預設位置時設 `AGENT_SKILLS_DIR`，hint 訊息裡的規則全文路徑會跟著調整。

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
