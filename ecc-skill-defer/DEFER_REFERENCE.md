# ECC Skill Defer 參考表

> 依 ECC 1.9.0 `install-modules.json` 模組結構組織。
> 執行 `ecc-skill-defer.sh restore <name>` 可隨時恢復。

## Whole Modules — 整個模組跳過

| Module | Skills | 功能 | defer 原因 |
|--------|--------|------|-----------|
| **supply-chain-domain** | carrier-relationship-management | 貨運商管理/費率談判 | 非相關產業 |
| | customs-trade-compliance | 海關/關稅/貿易合規 | 非相關產業 |
| | energy-procurement | 電力/天然氣採購 | 非相關產業 |
| | inventory-demand-planning | 庫存需求預測/安全庫存 | 非相關產業 |
| | logistics-exception-management | 貨運異常/理賠處理 | 非相關產業 |
| | production-scheduling | 生產排程/瓶頸分析 | 非相關產業 |
| | quality-nonconformance | 品質不符/CAPA 管理 | 非相關產業 |
| | returns-reverse-logistics | 退貨/逆物流管理 | 非相關產業 |
| **media-generation** | video-editing | 影片剪輯工作流 | 未使用影片功能 |
| | videodb | 影音索引/搜尋/編輯 | 未使用影片功能 |
| | fal-ai-media | fal.ai 圖片/影片/音訊生成 | 未使用 AI 媒體生成 |
| **social-distribution** | crosspost | 多平台社群發佈 | 未使用社群功能 |
| | x-api | X/Twitter API 整合 | 未使用 X API |
| **business-content** | article-writing | 文章撰寫 | 非開發用途 |
| | content-engine | 內容生產引擎 | 非開發用途 |
| | investor-materials | 投資者簡報/文件製作 | 非開發用途 |
| | investor-outreach | 投資者接觸策略 | 非開發用途 |
| | market-research | 市場調研分析 | 非開發用途 |
| **document-processing** | nutrient-document-processing | Nutrient SDK 文件處理 | 未使用該 SDK |
| | visa-doc-translate | 簽證文件翻譯 | 非開發用途 |
| **swift-apple** | foundation-models-on-device | Apple FoundationModels on-device LLM | 未使用 Swift |
| | liquid-glass-design | iOS 26 Liquid Glass 設計系統 | 未使用 Swift |
| | swift-actor-persistence | Swift actor 持久化模式 | 未使用 Swift |
| | swift-concurrency-6-2 | Swift 6.2 並行模式 | 未使用 Swift |
| | swift-protocol-di-testing | Swift DI 測試模式 | 未使用 Swift |
| | swiftui-patterns | SwiftUI 架構模式 | 未使用 Swift |

## Within-Module — 保留模組中 defer 不需要的 skills

| Module | Skills | 功能 | defer 原因 |
|--------|--------|------|-----------|
| **framework-language** | android-clean-architecture | Android Clean Architecture | 未使用 Android |
| *(保留: coding-standards,* | compose-multiplatform-patterns | Compose Multiplatform UI | 未使用 KMP |
| *frontend/backend-patterns,* | cpp-coding-standards | C++ 編碼規範 | 未使用 C++ |
| *golang-*, python-*,* | cpp-testing | C++ GoogleTest TDD | 未使用 C++ |
| *api-design,* | django-patterns | Django 架構模式 | 未使用 Django |
| *mcp-server-patterns)* | django-tdd | Django TDD 工作流 | 未使用 Django |
| | django-verification | Django 驗證迴圈 | 未使用 Django |
| | frontend-slides | 前端簡報製作 | 非核心開發 |
| | java-coding-standards | Java 編碼規範 | 未使用 Java |
| | kotlin-coroutines-flows | Kotlin Coroutines/Flow | 未使用 Kotlin |
| | kotlin-exposed-patterns | JetBrains Exposed ORM | 未使用 Kotlin |
| | kotlin-ktor-patterns | Ktor server 模式 | 未使用 Kotlin |
| | kotlin-patterns | Kotlin 慣用模式 | 未使用 Kotlin |
| | kotlin-testing | Kotlin Kotest TDD | 未使用 Kotlin |
| | laravel-patterns | Laravel 架構模式 | 未使用 Laravel |
| | laravel-tdd | Laravel TDD | 未使用 Laravel |
| | laravel-verification | Laravel 驗證迴圈 | 未使用 Laravel |
| | perl-patterns | Perl 5.36+ 慣用模式 | 未使用 Perl |
| | perl-testing | Perl 測試模式 | 未使用 Perl |
| | rust-patterns | Rust 慣用模式 | 未使用 Rust |
| | rust-testing | Rust 測試模式 | 未使用 Rust |
| | springboot-patterns | Spring Boot 架構模式 | 未使用 Spring |
| | springboot-tdd | Spring Boot TDD | 未使用 Spring |
| | springboot-verification | Spring Boot 驗證迴圈 | 未使用 Spring |
| **security** | django-security | Django 安全實踐 | 未使用 Django |
| *(保留: security-review,* | laravel-security | Laravel 安全實踐 | 未使用 Laravel |
| *security-scan)* | perl-security | Perl 安全實踐 | 未使用 Perl |
| | springboot-security | Spring Boot 安全實踐 | 未使用 Spring |
| **database** | clickhouse-io | ClickHouse 資料庫操作 | 未使用 ClickHouse |
| *(保留: postgres-patterns,* | jpa-patterns | JPA/Hibernate ORM | 未使用 Java |
| *database-migrations)* | | | |
| **workflow-quality** | continuous-learning | 舊版持續學習系統 | 被 v2 取代 |
| | project-guidelines-example | 專案指南範例模板 | 範例檔，非實際功能 |
| **agentic-patterns** | autonomous-loops | 自主迴圈控制 | 被 continuous-agent-loop 取代 |
| | data-scraper-agent | 自動化資料採集 agent | 未使用爬蟲功能 |

## Agent Types — 語言專用 agents

> 以下為 ECC agent types，可能不存在獨立 SKILL.md，列入 conf 供完整性追蹤。

| Skills | 功能 | defer 原因 |
|--------|------|-----------|
| cpp-build | C++ build error 修復 | 未使用 C++ |
| cpp-review | C++ code review | 未使用 C++ |
| cpp-test | C++ TDD 工作流 | 未使用 C++ |
| rust-build | Rust build error 修復 | 未使用 Rust |
| rust-review | Rust code review | 未使用 Rust |
| rust-test | Rust TDD 工作流 | 未使用 Rust |
| kotlin-build | Kotlin/Gradle build 修復 | 未使用 Kotlin |
| kotlin-review | Kotlin code review | 未使用 Kotlin |
| kotlin-test | Kotlin Kotest TDD | 未使用 Kotlin |
| gradle-build | Android/KMP Gradle 修復 | 未使用 Kotlin/Android |
| pytorch-patterns | PyTorch 訓練/推論模式 | 未使用 PyTorch |
