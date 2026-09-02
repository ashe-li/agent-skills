# plan-run-stop.sh 安裝步驟

`plan-run-stop.sh` 是 Stop hook 的極薄 wrapper，唯一動作是把 stdin 原封不動轉交給
`plan_runner.py hook-stop`。本檔案只提供**安裝說明**，不會被任何程式自動執行。

**安裝需要人工操作 `~/.claude/settings.json`。這一步在 S1.5 不會自動執行，
只在此留下步驟供之後手動或另一 step 執行。**

## 1. 複製 wrapper script 到 `~/.claude/hooks/`

```bash
mkdir -p ~/.claude/hooks
cp scripts/hooks/plan-run-stop.sh ~/.claude/hooks/plan-run-stop.sh
chmod +x ~/.claude/hooks/plan-run-stop.sh
```

## 2. Additive 編輯 `~/.claude/settings.json`

**這是全域設定檔，且使用者現有的 `hooks.Stop` 陣列已經有 4 個 hook
（cmux-notify / attribute / mempal-save / value-diff）。以下編輯必須是
「在陣列末端追加一個物件」，絕對不可覆蓋、清空、或重寫整個 `hooks.Stop` 陣列。**

### 2.1 先備份

```bash
cp ~/.claude/settings.json ~/.claude/settings.json.bak.$(date +%Y%m%d%H%M%S)
```

### 2.2 追加的物件內容

```json
{"hooks":[{"type":"command","command":"bash ~/.claude/observability/bin/wrap.sh stop:plan-run bash ~/.claude/hooks/plan-run-stop.sh","timeout":10}]}
```

### 2.3 前後對照（示意，實際 4 個既有物件內容以使用者檔案為準）

編輯前（既有 4 個 hook，不可省略任何一個）：

```json
{
  "hooks": {
    "Stop": [
      { "hooks": [ { "...": "cmux-notify" } ] },
      { "hooks": [ { "...": "attribute" } ] },
      { "hooks": [ { "...": "mempal-save" } ] },
      { "hooks": [ { "...": "value-diff" } ] }
    ]
  }
}
```

編輯後（在陣列末端追加第 5 個，前 4 個原封不動）：

```json
{
  "hooks": {
    "Stop": [
      { "hooks": [ { "...": "cmux-notify" } ] },
      { "hooks": [ { "...": "attribute" } ] },
      { "hooks": [ { "...": "mempal-save" } ] },
      { "hooks": [ { "...": "value-diff" } ] },
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/.claude/observability/bin/wrap.sh stop:plan-run bash ~/.claude/hooks/plan-run-stop.sh",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

### 2.4 驗證

```bash
# JSON 語法驗證
python3 -c "import json; json.load(open(__import__('os').path.expanduser('~/.claude/settings.json')))" && echo "JSON OK"

# 確認 hooks.Stop 陣列長度是 5（4 個既有 + 1 個新增），且 4 個既有 hook 的 command
# 字串逐字未變。只比長度不夠：長度 5 也可能是「刪了一個舊的、加了兩個新的」。
# 把 <timestamp> 換成 2.1 步驟實際產生的備份檔名。
python3 -c "
import json, os
def commands(path):
    d = json.load(open(os.path.expanduser(path)))
    return [h.get('command') for e in d.get('hooks', {}).get('Stop', [])
            for h in e.get('hooks', [])]
before = commands('~/.claude/settings.json.bak.<timestamp>')
after = commands('~/.claude/settings.json')
assert after[:len(before)] == before, ('舊 hook 被改動或消失', before, after)
assert len(after) == len(before) + 1, ('新增數量不是 1', len(before), len(after))
assert 'plan-run-stop' in after[-1], after[-1]
print('既有', len(before), '個 hook 逐字未變；新增 1 個:', after[-1])
"

# 跑 doctor 確認 plan_runner 端也認得安裝狀態
python3 ~/Documents/agent-skills/scripts/plan_runner.py doctor
```

如果 JSON 驗證失敗，或 `Stop` 陣列長度不是預期值，**立即從備份還原**：

```bash
cp ~/.claude/settings.json.bak.<timestamp> ~/.claude/settings.json
```

## 3. 開發期指向規定（AGENT_SKILLS_DIR）

`plan-run-stop.sh` 預設讀取 `AGENT_SKILLS_DIR="${AGENT_SKILLS_DIR:-$HOME/Documents/agent-skills}"`，
也就是**主 repo checkout**。

如果要試用某個未合併的分支（例如在 sibling worktree，如
`~/Documents/agent-skills-stop-hook/` 本身，開發時），需要暫時指向該 worktree：

```bash
export AGENT_SKILLS_DIR=/Users/shiun/Documents/agent-skills-stop-hook
```

要在**真實 session**（而非手動跑指令）裡試用未合併的分支，`export` 不夠——它隨 shell 消失。
改動 wrapper 裡那一行的預設值才會每個 session 生效，並在上面留註解寫明何時要改回來。
`doctor` 是讀已安裝的 wrapper 取得預設值（不是自存一份常數），所以兩種指法它都認得。

**警告**：這個環境變數只在目前 shell / session 有效，且是全域 Stop hook 共用的設定。
若 `AGENT_SKILLS_DIR` 指向的 worktree 路徑之後被刪除（例如 `git worktree remove`），
hook 會在**每個 session** 靜默失效（因為 `plan-run-stop.sh` 找不到 `plan_runner.py` 就
直接 `exit 0`，不會有任何錯誤訊息）。

測試完畢後必須：

```bash
unset AGENT_SKILLS_DIR
python3 ~/Documents/agent-skills/scripts/plan_runner.py doctor
```

確認 `doctor` 顯示的 runner 路徑已經指回主 repo checkout（`~/Documents/agent-skills`），
不再是 worktree 路徑。

## 4. 移除方式

完整步驟（含備份、驗證表、狀態保留與否的取捨）見
[`docs/hooks-setup.md` 的「8. 移除」](../../docs/hooks-setup.md#8-移除)。摘要：

```bash
cp ~/.claude/settings.json ~/.claude/settings.json.bak.$(date +%Y%m%d%H%M%S)
# 手動編輯 ~/.claude/settings.json：移除 hooks.Stop 陣列中 command 字串含
# "plan-run-stop" 的那個物件——**用指令內容比對，不要數第幾個**，安裝後
# 使用者隨時可能加入或重排其他 hook。找不到就代表沒裝，不要刪任何東西。
python3 -c "import json; json.load(open(__import__('os').path.expanduser('~/.claude/settings.json')))" && echo "JSON OK"

rm -f ~/.claude/hooks/plan-run-stop.sh

# 驗證用「東西不在了」的直接檢查，**不要跑 doctor**：移除之後 doctor 依 S1.4 的
# 契約本來就會對「Stop hook 已註冊」「wrapper 存在且可執行」報 FAIL，拿它當移除
# 的判準會讓一次成功的移除看起來像壞掉。doctor 留到「還原安裝」之後再跑。
grep -c "plan-run-stop" ~/.claude/settings.json   # 預期 0
test ! -e ~/.claude/hooks/plan-run-stop.sh && echo "wrapper removed"
```

`~/.claude/plan-run/` 是**狀態不是安裝**：裡面是進行中的 pointer（lease、暫停旗標、
計數器）。解除安裝不需要刪它；刪了就等於放棄所有進行中 plan 的跨 session 續推。
