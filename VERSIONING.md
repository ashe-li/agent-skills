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

1. 變更累積在 PR；CHANGELOG 的 `## [Unreleased]` 段隨手記錄
2. 依上表決定版本號，把 `[Unreleased]` 改名為 `## [vX.Y.Z] - YYYY-MM-DD`，並補 CHANGELOG 檔尾的 compare-links
3. merge 到 `main`
4. 在 merge commit 上打 annotated tag 並推送：

   ```bash
   git tag -a vX.Y.Z -m "vX.Y.Z"
   git push origin vX.Y.Z
   ```

5. 開 GitHub Release（notes 取 CHANGELOG 該版段落）：

   ```bash
   gh release create vX.Y.Z --title vX.Y.Z --notes-file <該版 changelog 片段>
   ```
