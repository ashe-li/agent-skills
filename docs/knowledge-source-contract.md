# 知識來源契約

agent-skills 只提供通用的引擎、schema 與 skill，不含任何公司或 repo 專屬的知識。每個使用者的驗證知識（哪個 repo 要跑哪些快檢、踩過哪些坑）放在自己的知識庫，這份文件定義 agent-skills 如何找到那個知識庫，以及找不到時怎麼辦。

解析邏輯的參考實作是 `scripts/kb_source.py`，行為以 `scripts/tests/test_kb_source.py` 為準。

## 設定值與解析順序

知識來源根目錄（下稱 KB 根目錄）依下列順序決定，第一個有值的就用：

1. 環境變數 `AGENT_SKILLS_KB_HOME`。空字串視同未設定。
2. 檔案 `${XDG_CONFIG_HOME:-$HOME/.config}/agent-skills/kb-home` 的第一行，去掉頭尾空白。檔案不存在、空檔都視同未設定。
3. 都沒有，視為「未設定」，這是正常狀態，不是錯誤。

設定值必須指向一個存在的目錄。指向不存在的路徑、或指向檔案，一律視同未設定，並附一句可讀的原因。較高順位的值無效時，不會往下一順位退回；打錯字應該看得見，而不是被另一個來源悄悄蓋過。

## 目錄結構

契約只強制一個目錄：

```
<KB 根目錄>/
  verification/
    <name>.md          一個 repo 一份 profile
    _common.md         所有 repo 共用的 checklist（底線開頭，不參與 profile 比對）
    _candidates/       候選項目（底線開頭，不參與 profile 比對，永不執行）
```

KB 根目錄底下的其他內容由使用者自行安排，agent-skills 不要求也不檢查。

profile 與 repo 的對應靠 profile frontmatter 的 `remotes` 清單。比對時，repo 的 `origin` URL 會先正規化成小寫的 `host/owner/repo`（`git@host:owner/repo.git`、`https://user:token@host/owner/repo.git/`、`ssh://git@host:22/owner/repo.git` 都會變成同一個值），profile 裡的每個值也先正規化再比。

- 零份符合：`no-profile`
- 恰好一份符合：採用該 profile
- 兩份以上符合：`ambiguous`，列出檔名，不猜。請修掉重複的 `remotes`

`README.md` 與底線開頭的檔案不會被當成 profile。realpath 落在 KB 根目錄之外的檔案（例如 symlink 指到別處）也會被略過。

## 降級矩陣

找不到知識來源不是錯誤，所有情況的 exit code 都是 0，由呼叫端決定如何降級。

| 情況 | `kb_source.py` 輸出 | exit code | 呼叫端該怎麼做 |
|---|---|---|---|
| 未設定 | `NO-KB (原因)` | 0 | 印一行降級說明，只跑內建通用步驟 |
| 設定值指向不存在的目錄 | `NO-KB (原因，含該路徑)` | 0 | 同上，並提示使用者修正設定 |
| 已設定，但沒有符合的 profile | `NO-PROFILE (原因)` | 0 | 只套用通用項目與 `_common.md`（若存在） |
| 已設定，多份 profile 符合 | `AMBIGUOUS a.md b.md` | 0 | 不選擇任何一份，請使用者處理重複 |
| 恰好一份符合 | `MATCH <檔名>` | 0 | 使用該 profile |

repo 沒有 `origin` remote、或 `git` 不存在時，比對結果為 `NO-PROFILE`，不會丟例外。

## 各 skill 共用的解析片段

純 skill（沒有腳本可呼叫）用下面兩行解析出 KB 根目錄。POSIX sh 可執行，結果放在 `KB_HOME`，空字串代表未設定：

```sh
KB_HOME="${AGENT_SKILLS_KB_HOME:-$(head -n 1 "${XDG_CONFIG_HOME:-$HOME/.config}/agent-skills/kb-home" 2>/dev/null)}"
[ -d "$KB_HOME" ] || KB_HOME=""
```

第二行同時處理「未設定」與「指向不存在的目錄」，所以之後只要檢查 `[ -n "$KB_HOME" ]`。這個片段不會去除設定檔第一行的頭尾空白，需要時請使用 `kb_source.py resolve`。

要用到 KB 根目錄底下的子路徑時，先用 `[ -e "$KB_HOME/<子路徑>" ]` 確認存在；不存在就降級，不要 `cd` 進去。

## 命令列

```
python3 scripts/kb_source.py resolve
python3 scripts/kb_source.py match --repo <path>
```

`resolve` 印出解析到的 KB 根目錄或 `NO-KB`。`match` 讀取 `<path>` 的 `origin` 後印出上表的結果。兩者都只讀，不寫任何檔案。

## 選用慣例路徑

契約只強制 `verification/`。檢索工具不列在表內：知識來源若提供就用，否則用 `grep -rn`。下表列出各 skill 可能會用到、但「存在才用、不存在就降級」的路徑。

| 路徑（相對 KB 根目錄） | 用途 | 使用的 skill |
|---|---|---|
| `reports/` | 既往 RCA 報告目錄；存在才查詢、才寫入新報告，不存在就改寫 `.verification/` | alert-triage、rca |
| `reports/INDEX.md` | 報告索引；存在才掛新報告 | rca |
| `wiki/learned/`、`wiki/learned/INDEX.md` | 可重複使用的做法與其索引；索引存在才 grep，目錄存在才寫入新條目 | alert-triage、rca、dispatch-loop |
| `wiki/patterns/` | pattern 先驗目錄，存在才 grep | alert-triage |
| `wiki/rules/INDEX.md`、`wiki/decisions/INDEX.md` | 規則與決策索引，存在才查 | rca |
| `_pending/` | 回收 agent 前寫教訓的 session 檔目錄；不存在就改寫 `--summary` 或 `.verification/` | dispatch-loop、rca |

## Schema（S3.2 補齊）

profile、checklist 項目、候選項目的欄位定義與驗證規則會在 S3.2 補在這裡。目前只有 profile 的 `remotes` 欄位（字串清單）被 `kb_source.py` 讀取，其餘欄位由引擎使用。

## 信任邊界

profile 的 Quick checks 會被引擎當成指令執行，所以安裝一份 profile 就等同於授權它的快檢；請只把自己信任的內容放進 KB 根目錄。細節（只執行落在 KB 根目錄內的 profile、`--dry-run`、候選項目不執行）在 S3.x 補齊。
