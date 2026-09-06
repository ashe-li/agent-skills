# release-pr golden set — failure mode ↔ fixture coverage

這張表把 `/release-pr` skill 已知的失效模式對到對應的 fixture 與 KB 出處，供 CI script 機械檢查每一列的 fixture 檔案確實存在。`fixture` 欄只填檔名的編號前綴（`01`、`04`、`01, 02, 03` 這種形式），不填完整檔名。

| failure mode | fixture | KB 出處 | 狀態 |
|---|---|---|---|
| scope bloat（查證時才需要的內容塞進核准欄位） | 01, 02, 03 | wiki/learned/verification-time-knowledge-does-not-belong-in-decision-time-surfaces.md | 已涵蓋 |
| 否定式敘述／絕對數字的 scope 塌陷（commit scope 誤植為 release scope） | 04 | wiki/learned/release-notes-transcribed-from-commits-inherit-their-errors.md | 已涵蓋 |
| 批次內自我修正被誤分類為 Bug Fix（問題從未上線） | 05 | wiki/learned/intra-batch-self-correction-is-not-a-bug-fix.md | 已涵蓋 |
| release PR 開啟期間 head 又併入新內容，body 只追加不重掃 | 06 | wiki/learned/release-pr-body-goes-stale-while-open.md | 已涵蓋 |
| session 口頭敘事（「X+Y 一起上線」）未核對每個被點名 PR 的實際 merge state | 07 | wiki/learned/release-pr-scope-audit-narrative-vs-merge-state.md | 已涵蓋 |
| squash-merge 的 PR 不留 merge commit，用 `git log --merges` 枚舉會漏算 | 08 | wiki/learned/release-pr-enumeration-needs-gh-search-not-git-log-merges.md | 已涵蓋 |
| workflow 的 branch filter 未涵蓋此 PR 的 base，check 全綠卻是空綠 | 09 | wiki/learned/ci-workflow-branch-filter-makes-checks-vacuous.md | 已涵蓋 |
| body 引用的 commit hash 未經 hex 合法性／來源存在性／主題一致性三層驗證即被信任 | 10 | wiki/learned/commit-hash-hex-validity-fabrication-detector.md | 已涵蓋 |

## 未涵蓋 / 待補

以下是這次盤點過程中發現、但本輪未寫 fixture 的失效模式，誠實列出：

- **merge commit 數的拆分判準**（`wiki/learned/merge-commit-detection-by-parent-count-not-message-prefix.md`）——用 commit message 前綴判定「N commits：X merge + Y non-merge」會兩個方向都錯（vocus-web-ui #7981 實測：寫成「6 non-merge + 5 merge」，`.parents|length` 重數後是「7 merge + 4 non-merge」），且 KB 該篇明講「commit 數根本不是歸因單位，檔案聯集才是」。與 fixture 08（squash-merge 枚舉）主題相鄰但判準不同（parents count vs. merge commit 存在與否），值得獨立成一個 fixture。
- **驗收清單與正文互斥，生出假 bug 回報**（`wiki/learned/release-notes-acceptance-list-is-executable-not-prose.md`）——release notes 正文的範圍宣告（例：「四支內容頁」）若與驗收重點列的範圍不一致（例：驗收清單多列了作者頁／標籤頁／沙龍頁），會有人照驗收清單去查一個正文根本沒宣稱涵蓋的範圍，回報成不存在的 bug。這個失效模式在 `/release-pr` SKILL.md 目前的 Step 2.5／3.5／4.5 都沒有對應檢查點，補這個 fixture 前建議先確認 SKILL.md 是否要新增對應 step，否則 fixture 會測到 skill 本身尚未涵蓋的行為。

兩者都不在本次交辦的 7 條清單內，未動手；若要補齊，建議下一輪連同 SKILL.md 是否需要新增對應查核步驟一併評估。
