# Codex × Claude 協作契約

使用者指定：Codex制定任務大目標，Claude Opus 5.5負責實作執行。建立日：2026-09-27。此文件不是已接通或已派工的證據；連線與任務狀態見CURRENT.json。

## 現行方式：Codex直接執行

最新已完成[V34四格對照](../../../data/research/v12g_reserve_completion_20260927_v34/REPORT_ZH.md)。本輪十場補齊，未晉級；後續由Codex直接處理收益分支與入口覆蓋，不需人工轉交Claude。

2026-09-27使用者認為人工往返效率不佳，明確改由Codex自行處理。TASK_007已由Codex直接完成：EOF修復八條整合路徑驗收通過，詳見[報告](../../../data/research/v12g_eof_execution_repair_20260927_v33r3/REPORT_ZH.md)；不再要求使用者將每輪TASK交給Claude。技術範圍沿用已審查的EOF執行修復，具體運行狀態見CURRENT.json與研究CURRENT。保留以下人工流程作歷史參考，不作當前派工條件。

## 歷史方式：使用者轉交文件

使用者已選擇停止電腦操控與自動派工，以節省 token。Codex讀最新 RESEARCH_CURRENT，建立具名 TASK；使用者請 Claude 讀取執行；Claude交付 RETURN，使用者再請 Codex分析。TASK_006已完成strict觀測，直接捕捉rc1／時鐘未前進，見[REVIEW_006.md](REVIEW_006.md)。[TASK_007.md](TASK_007.md)已準備：先釘選實際原生來源，隔離修復EOF事件時鐘與呼叫端，做契約及最多8條故障恢復／相容性路徑；策略期限與收益條件分開處理。尚未派送後繼，不重送v30～v32。準備好文件不代表已送給Claude。不要依下方歷史直接接入步驟重新啟動CLI、桌面操控、排程或WSL／DevChain。歷史連線測試紀錄保留，與目前人工轉交狀態分開。

## 工作分工

- **使用者**：決定研究目標與實盤授權。既有授權持續有效，不因交接要求重複確認。
- **Codex（研究設計與驗收）**：設定問題、反例、比較矩陣、成功／失敗判準及任務範圍；檢查方向是否偏離；核對關鍵證據後決定接受、返工或後續任務。
- **Claude Opus 5.5（執行者）**：先確認理解與資料覆蓋，實作、測試、收集證據，保留失敗與未知；可用證據反駁Codex的假說，不能為達標自行改評分或篩掉反例。

不是兩個代理輪流自由發想。每輪使用具名任務：Codex下達TASK → Claude回ACK／計畫 → 按本輪授權執行 → Claude交RETURN與artifact → Codex寫REVIEW → 下一任務。正常批次完成後停在REVIEW，避免無限追加實驗。

## 專案大目標

改善BTC5M空倉開局擴張、主動／被動ADD、主動／被動repair、修復後再ADD的完整循環：減少失控的反向風險，同時保留有意義的正向收益。方向、尺寸、成交可行性及未结責任均須因果處理。模型或元件先取得未消費市場證據，再談實單資格；小額實單也需要另外明確的部署和資金契約。

保持使用者允許犧牲部分正收益但不可全壓掉的要求；不自行恢復3:1或任意50%成功門檻。研究capital_cap仍依當前契約，不能默默把null改成任意上限。V12／V12g的具體受測版本以各次凍結來源和控制重現為準。

## 防止研究視角變窄

每份RETURN必須同時回答：

1. 這個結果支持哪個假說？仍不能排除哪些替代解釋？
2. 哪些市場／狀態改善、退步？是否只是轉移虧損、壓光收益或減少交易？
3. 本次局部改善有沒有在後續ADD或反轉時消失？
4. 樣本是否按結果挑選、已消費，或只是同市場重複路徑？
5. 是否納入費用、兩側signed payoff、支出、主被動活動、pending／EOF／缺失？
6. 有哪些結論需要下一個同根反事實比較，不能從相關性或單一案例推出？

Codex負責檢查以上項目，不能只看Claude給的PASS、平均值或文字結論。Claude可提出替代方案，但不得直接把新方案混入已凍結驗收。

## 檔案與執行責任

本目錄是新協作入口，不啟動或取代舊multi_chat_coordination_v1的00/A/B/C/D角色。

- Codex寫TASK、REVIEW、CURRENT.json；Claude只寫任務指定的ACK／RETURN及授權產物。
- 同一任務同時只有一位執行者。桌面既有Claude工作不會因本契約自動停止或轉移；不得用`--continue`盲接最近對話。
- 直接模式使用專用session ID，明確固定`claude-opus-5-5`，以實際回傳的model資訊驗證，不能只憑prompt宣稱模型設定生效。
- 每次指定可改檔案、是否可派worker、唯一job id及停點。版本／測試／樣本更改須由Codex重新記錄任務，不覆寫凍結來源。
- 遠端HFT、訓練、native一律依根AGENTS與docs/agents/worker.md，先核實身份與exact/global status。重用舊結果，不重送。
- live策略、armed、服務、stakes及私人憑證不在一般研究任務授權內。
- 失敗、timeout與UNKNOWN先保存狀態和證據，不能重複派單。新任務不得與另一位代理改同一檔案或搶同一worker。

## 兩種接入方式

**直接派工**：Codex用Claude Code非互動介面送任務、接JSON結果並保留session ID，驗收後發下一輪。桌面介面與CLI有各自的對話清單；這不是向使用者正在看的桌面聊天框注入訊息。CLI必須在自己的執行環境完成官方登入。不能複製桌面token或改動登入資料繞過它。

**桌面協作**：使用者將CLAUDE_BOOTSTRAP.txt貼給桌面Code工作階段；Claude讀TASK並寫RETURN，Codex讀檔驗收。純聊天若無專案檔案存取，需要使用者傳送文件。共用檔案本身不會喚醒閒置對話；尚未建立排程、watcher或背景自動喚醒機制。

官方參考：[Claude程式化執行](https://code.claude.com/docs/en/headless)、[桌面與CLI差異](https://code.claude.com/docs/en/desktop)、[模型設定](https://code.claude.com/docs/en/model-config)。

## 本機入口

在repo根目錄的PowerShell使用：

```powershell
python tools/claude_collaboration_bridge.py status
python tools/claude_collaboration_bridge.py login
```

`login`走Claude官方登入流程，需使用者完成帳號驗證；不讀取或搬運桌面憑證。登入後由Codex執行 `run-read-only` 做TASK_001第一輪，結果存本目錄runs及CURRENT.json。這個首版入口只提供Read／Glob／Grep，不授權shell、寫檔或native；後續實作任務需另列精確工具／檔案／worker範圍。尚未登入時會停止，不自動改用API key。請勿在桌面已承接TASK_001時又執行直接派工。

歷史測試：TASK_001 的 CLI 回傳模型已確認為 claude-opus-5-5，見 ROUNDTRIP_CHECK.json 及 REVIEW_001.md；原桌面對話的訊息往返另見 DESKTOP_ROUNDTRIP_CHECK.json。這些測試不代表任務自動派送或背景循環。現行一律依上方使用者轉交文件流程。
