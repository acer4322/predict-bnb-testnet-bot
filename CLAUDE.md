# Claude 專案接手入口

先讀 [AGENTS.md](AGENTS.md)，再讀 [Claude 中文交接](docs/handoffs/CLAUDE_HANDOFF_20260925_ZH.md)。最新的雲端研究交接（Target 逆向分析、現貨延遲吃單與影子模式）見 [CLAUDE_LOCAL_HANDOFF_20261003_ZH.md](docs/handoffs/CLAUDE_LOCAL_HANDOFF_20261003_ZH.md)。本檔只提供入口，不取代使用者指示或各目錄適用的 AGENTS.md。

目前工作是 BTC5M 離線 ADD／repair 循環研究。最新狀態以 [RESEARCH_CURRENT.md](docs/agents/RESEARCH_CURRENT.md) 第一項及其具名包為準；不要依檔案時間或 README 的舊執行指令選擇研究主線。

接手本身不啟動新實驗或實盤。先確認已完成結果、版本與唯一下一步。保留 dirty worktree、凍結實驗、V12 與 live 狀態；不要重送已完成 job。重 HFT／native／訓練僅能走核實的第二台 worker。

Claude 不一定具備 Codex 的 MCP／agent 工具。工具不可用時明確回報，使用已存在且經契約核對的介面；不能假裝已有遠端連線或執行權限。
