# Versioning Policy（版本策略）

本 repo 依 [Semantic Versioning 2.0.0](https://semver.org/lang/zh-TW/) 發版。變更記錄見 [CHANGELOG.md](CHANGELOG.md)（[Keep a Changelog](https://keepachangelog.com/zh-TW/1.0.0/) 格式），每個 tag 對應一個 GitHub Release。

## 版本號規則

| 位階 | 何時 bump | 例 |
|---|---|---|
| **MAJOR** (`x.0.0`) | Breaking change：移除或更名指令、移除 consumer 依賴的外部 plugin/agent、變更 plan 格式契約或既有 skill 的對外介面 | `v1.28.0 → v2.0.0`（移除 everything-claude-code 依賴） |
| **MINOR** (`1.x.0`) | 新增 skill / rule / 向後相容的功能 | `v1.27.0 → v1.28.0` |
| **PATCH** (`1.28.x`) | 修 bug、文件修正、不改對外介面的內部調整 | `v1.21.0 → v1.21.1` |

判準：會讓「照舊用法的既有使用者」行為改變或壞掉的，就是 MAJOR。skill 指令名、plan 格式契約、DSL、安全紅線屬對外介面；skill 內部呼叫哪個 agent 屬實作細節，但若該實作是使用者環境必須另外安裝的依賴（如 ECC plugin），移除它同樣算 breaking。

## 版本線與 pin

- **`v2.x`（現行）** — ECC 解耦版。只依賴 Claude Code 內建 primitives（`Plan` / `/code-review` / `/security-review` / `/simplify` / `general-purpose`），**無需安裝 everything-claude-code plugin**。新功能都落在這條線。
- **`v1.x`（維護凍結，pin 點 = `v1.28.0`）** — 最後的 ECC 依賴版。若你的環境仍靠 everything-claude-code plugin 被這些 skill 呼叫，pin 在 `v1.28.0`：

  ```bash
  git clone https://github.com/ashe-li/agent-skills && cd agent-skills && git checkout v1.28.0
  # 依該版 README 的 Install 指示安裝
  ```

  `v1.x` 不再收新功能，僅重大安全問題視情況 backport。一般使用者請走 `v2.x`（`npx skills add ashe-li/agent-skills --global` 取最新）。

v1 → v2 的完整遷移說明見 [CHANGELOG.md](CHANGELOG.md) 的 `v2.0.0` → **Migration** 段。

## 發版流程

人要做的事只到第 3 步；tag 與 GitHub Release 由 `.github/workflows/release.yml`（`Auto Release from CHANGELOG`）自動產生，**不要手動 `git tag`**。

1. 變更累積在 PR；CHANGELOG 的 `## [Unreleased]` 段隨手記錄
2. 依上表決定版本號，把 `[Unreleased]` 改名為 `## [vX.Y.Z] - YYYY-MM-DD`，並補 CHANGELOG 檔尾的 compare-links
3. merge 到 `main`

merge 之後，workflow 會自動接手：

- **觸發條件**（兩個 AND 條件都要滿足）：push 到 `main` **且** 該次 push 有動到 `CHANGELOG.md`。只改別的檔案的 PR 合進 `main` 不會觸發發版。
- **版本號來源**：`grep -m1 '^## \[v' CHANGELOG.md`，抓 CHANGELOG 裡**第一個** `## [vX.Y.Z]` 標題。這代表那個標題的位置與拼法就是發版契約——打錯字會發錯版號，或抓不到版本號時整個 workflow 直接 skip。
- **同名 tag 已存在會 skip**：workflow 會先檢查 tag 是否已存在，存在就不重複發版，重跑安全。
- **tag 由 `gh release create` 順帶建立**：workflow 裡沒有獨立的 `git tag` 步驟；`gh release create vX.Y.Z ...` 在 tag 不存在時會自動建立一個指向當下 commit 的 tag，再開 Release。
- **release notes 自動取該版段落**：用 awk 擷取該版本標題到下一個 `## [` 之間的內容當作 `--notes-file`。也就是說 CHANGELOG 那個版本段落寫成什麼樣，GitHub Release 頁面就長什麼樣，發版前要把該段落當成正式文案看待。

### 疑難排解：merge 到 main 後沒看到 release

1. 先確認該次 merge 到 `main` 的 diff 有沒有動到 `CHANGELOG.md`——沒有動到就不會觸發 workflow，這是 by-design。
2. 到 GitHub Actions 找 `Auto Release from CHANGELOG` 的 run，看是哪個 step 沒過或被 skip。
3. 確認 CHANGELOG 最上面（第一個）版本標題格式是不是 `## [vX.Y.Z]`——格式跑掉（漏中括號、漏 `v` 前綴等）會讓版本號抓不到而直接 skip。
