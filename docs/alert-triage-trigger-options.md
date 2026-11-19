# alert-triage 觸發層選項分析

| 選項 | 機制 | 優點 | 缺點 | 適用階段 |
|------|------|------|------|---------|
| **A 手動貼** | 使用者貼 payload 跑 `/alert-triage` | 零基建、HITL 天然內建、可控 | 需人起手、非即時 | 現況 / MVP |
| **C cron 輪詢** | cron 定期撈 firing alerts 批次 triage | 實作簡單、頻率可控、無常駐 webhook | 非即時、需去重避免重跑同 alert | 過渡 |
| **B alertmanager webhook** | AM webhook → headless Claude SDK agent | 即時、無人值守、可自動關 benign | 需 webhook 接收端 + 認證 + 去重 + rate limit + 防 alert storm 自我 DoS | 目標（成熟後）|

**推薦演進路徑**：A（現在，驗證分類品質）→ C（過渡，低風險自動化）→ B（目標，需 SDK headless + 去重 + rate limit + G1/G3 HITL 保留）。觸發層實作不在本 skill 範圍，本節僅供選型。
