---
fixture_id: "06"
kind: regression
pr: "sosreader/vocus-web-ui#7981（同型：#8081、#7994）"
scale:
  note: KB 未記載 #7981/#8081/#7994 的檔案數與 +/− 規模，此欄位不填數字，標 provisional
source_kb: wiki/learned/release-pr-body-goes-stale-while-open.md
expected_verdict: RERUN
notes: 測的是 release PR 已開啟、head 又併入新內容時的更新流程，不是首次產生 body
---

## 情境

release PR（`hotfix → master`）已建立、還沒 merge，期間又有 feature PR 併進 head branch。使用者說「我剛推了新東西，幫我更新 release PR」。直覺做法是在對應區塊底下追加一條 bullet。

## 觀察到的失效

body 裡三類欄位在新 PR 併入的瞬間就失效，而它們散在不同段落，寫新 bullet 時不會被想起來：統計數字（檔案數／commit 數）、彙整 PR 清單、**對其他 PR 狀態的斷言**。

- **#7981（2026-08-06）**：body 尾端寫「#7974 仍為 open，不在此次發版範圍」；同日 04:43 #7974 就 merge 進 hotfix 了，而這次要追加的內容正是 #7974。只追加不重掃，成品會在同一份 body 裡一邊詳述 #7974 的修法、一邊宣告 #7974 不在本批。
- **#8081（2026-08-26）**：body 有一段安全性斷言——「本次合進 master 對 prod 圖片行為零影響……`deploy-hotfix-k8s.yaml` 與 `build-production.yaml` 皆維持不設該 flag（=off）」。head 又併入 #8085，而 #8085 做的事正好就是把那兩個檔設成 `=true`。這句寫下時為真，併入後整段反轉，且不是統計數字錯了，是「這個 release 是安全的」保證——reviewer 照它決定要不要細看。
- **#7994（2026-08-11）**：body 開頭 `## Release` 區塊有一條 Notion 連結（「[FE] 沙龍後台…Webview 實作」），把 1 個 PR 的批次更新成 4 個 PR 時，統計數字、彙整清單都重算了，唯獨這條**因為長得像既有流程產物**而原樣繼承。三個指令證偽：`compare` 檔案清單無關鍵字命中、對應分支未合進 hotfix/master、commit message 無關鍵字命中。

## 期望產出

head 一動就**重跑**，不是追加：

1. `gh api repos/<repo>/compare/<base>...<head> --jq '{ahead:.ahead_by, files:(.files|length)}'` 先看規模有沒有變
2. 重新列 commits／files，重算檔數與 +/− 加總
3. 重驗檔案聯集：新 PR 集合聯集必須等於 compare 的 files
4. **grep 整份 body 裡所有 `#NNNN`，逐一現查** `gh pr view <N> --json state,baseRefName,headRefName`
5. 對「零影響／不動 X／維持不設」這類**檔案內容否定斷言**，額外用 compare 的檔案清單逐句核對——它們不會被 `#NNNN` 現查覆蓋到
6. 繼承下來的既有條目（如 `## Release` 區塊的 Notion 連結）與自己新增的條目適用**同一套查核**，不因為「看起來像流程產物」而免驗

## 判定規則

| 條件 | 判定 |
|---|---|
| head 更新後只在對應區塊追加一條 bullet，其餘段落原樣沿用 | **FAIL**（三類必然過期欄位未重掃）|
| 對「#X 仍為 open／不在此批」「零影響／維持不設」類斷言未逐句現查 | **FAIL** |
| 既有 `## Release` 區塊或其他繼承條目未經同套查核就保留 | **FAIL** |
| 統計數字、PR 清單、狀態斷言、檔案內容否定斷言、繼承條目五類全部重跑並更新 | **PASS** |

## 反例警告

不是每次 head 更新都要把整份 body 重寫一遍——重跑的對象是這五類**會隨 head 變動而過期**的欄位，其餘與本次新增內容無關的敘述（例如既有的回滾方式說明、部署提醒）不必逐字重寫，只需確認仍為真。把「重掃」誤解成「整份砍掉重寫」會製造不必要的 diff 與審閱負擔。
