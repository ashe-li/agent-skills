# Skill Auto-Match

回應使用者前，先判斷是否有 skill 匹配。匹配就立即 invoke Skill tool。

## 排除條件（以下情境跳過 auto-match）

- 已在 skill 執行流程中（Skill tool 已被呼叫，正在執行步驟）
- 使用者已明確輸入 `/skill-name`（尊重明確指令）
- 純問答、知識詢問（無行動意圖的問題）
- Meta 查詢（「有哪些 skill」「這個 skill 怎麼用」）

## 任務 → Skill 映射

| 任務型別 | 觸發訊號 | Skill |
|----------|----------|-------|
| 建立實作計畫 / 設計功能 | 「怎麼實作」「幫我設計」「需求轉計畫」 | `/design` |
| Notion ticket 轉計畫 | 提供 notion.so / notion.site URL | `/notion-plan` |
| Commit、推送、開 PR、更新 PR | 「程式碼寫好了」「要 commit」「交 PR」 | `/pr` |
| 更新文件或提取知識 | 「session 收尾」「更新 docs」「知識沉澱」 | `/update` |
| Debug、排錯 | build 失敗、test 失敗、「一直報錯」「修不好」 | `/systematic-debugging` |
| Worktree 操作 | 「建 worktree」「切換分支隔離」「清理 worktree」 | `/worktree` |
| 清理 learned skills | 「curation」「learned skill 格式問題」 | `/curation` |
| 歸檔完成的 plan | 「plan 做完了」「歸檔 plan」 | `/plan-archive` |
| ECC skill 管理 | 「defer skill」「restore skill」「init token 優化」 | `/ecc-skill-defer` |
| 瀏覽器操作 | AWS Console、後台管理、需人工確認的瀏覽器自動化 | `/playwright-human-in-the-loop` |
| Skill 分流 / 退役 | 「消融數據」「退役 skill」「skill 分流」 | `/triage` |
| 深度驗證 skill | 「驗證 skill 品質」「消融實驗」 | `/learn-eval-deep` |
| 不確定 / 複雜任務 | 其他有行動意圖但無精確匹配 | `/assist` |

## 執行規則

1. 精確匹配 → 立即呼叫 Skill tool，不要先回答再呼叫
2. 無精確匹配但有行動意圖 → `/assist`
3. 符合排除條件 → 正常回應，不呼叫任何 skill
