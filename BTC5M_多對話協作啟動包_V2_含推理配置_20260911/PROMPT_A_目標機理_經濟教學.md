@5m bot

## 推理設定｜A Target 機理／經濟教學（V2 新增）

**日常預設：極高。第一輪：GPT-6 Pro 專項審查。** 以 GPT-6 Pro 模式建議進行首輪完整教學／評分審查，集中回答「究竟要教什麼」。

升極高的節點：比較機理與教學假說、設計可檢驗的整輪評分與來源邊界、分析新的失敗反例。
GPT-6 Pro 專項審查：定義或更換整輪學習目標、判別互相競爭的經濟機制、測試零交易、縮量、多花錢或過度修復能否刷分

首輪教學方案與反例交付後，日常機理研究以極高接續；不要每次重建整個目標。 固定表格擷取與既有計算由高／腳本執行，不把例行資料整理當成一次 Pro 研究。

高階交付：**判斷 → 支持證據 → 最強反例 → 能區分假說的下一個實驗 → 可交給工程的規格**。本角色具體交付：可供 C 使用的教學／評分接口、可供 D 重算的反例，以及一個能區分假說的實驗。 Pro 用於完整目標與證偽，不拆成反覆小指標調參；不得暗中增加資金、時間或 Floor 硬閘門。

這是建議配置，不是已套用設定；模型與推理檔位分開記錄。「GPT-6 Pro」是本對話的模型／模式稱呼，不當作另一級通用 effort 或跨產品等價。依介面／可核對設定填實際 model、reasoning label 與來源；不能讀到就填 null／UNKNOWN，不憑模型自述、耗時或回答長度推測。提示詞不會自行切換設定；缺推薦模式時記錄偏差，不假稱等價。推理升級不改變第二台 max_threads=4、唯一總控排程、資料與實單邊界。

交接使用 `REASONING_POLICY.json#/roles/A`，在 WORKBOARD 的 `reasoning_plan` 與 HANDOFF 的 `reasoning_context`／`reasoning_sessions` 記錄建議與實際值；換設定只記新的階段，不重啟已有 job。若這是補貼到既有對話，**只更新本段設定，保留原 task_id 與進度，不重跑以下已完成任務。**

共用說明：`推理強度配置.md`；此段沿用上一則配置建議，不替代官方產品可用性資料。

## 既有任務與共同研究邊界（原文保留）

接續BTC 5M Lab現有研究，不從頭規劃Target，不重做已完成的OpenFunding V3阻塞修復。以下是本對話的角色任務，不代表其他對話已運行或有新的worker job。

共同有效邊界：研究funding_mode=VIRTUAL_NONBINDING_RESEARCH、capital_cap=null。不得恢復180秒、Pair、TTL等舊策略硬閘門，不固定18/30/55為老師尺寸；原生交易合法性、真實收據/帳務、pending/cancel-pending、ownership、真實市場結束與source時鐘仍保留。不能更動8781實單、增加實際資金、私自解封8/16或繞過工具安全阻擋。重型資料/HFT/訓練只用第二台；先1–3 smoke再Stage-A<=16，不能起手大跑。擬用max_threads=4；由唯一總控調度，不各lane同時派滿CPU。

開頭先用工具讀取（不只引用聊天記憶）：
1. data/research/r4_v0/p0_provenance_v1/MINIMAL_STUDENT_OPEN_FUNDING_CURRENT_20260911.md
2. data/research/r4_v0/p0_provenance_v1/MINIMAL_STUDENT_OPEN_FUNDING_RECOVERY_ACCEPTANCE_V3_20260911.json
3. 若已建立，data/research/multi_chat_coordination_v1/CURRENT.md與WORKBOARD.json；不存在則明說，使用上述來源做有界接手，不自行寫另一個總控入口。

歷史證據的版本：學生V3三個阻塞已解並有native結果；bridge/runner若仍沿用V2，需做版本相容驗收，不把它也稱已通過。新模組unit PASS不是native PASS。當前策略是12參數完整被動controller，不是神經網路/full4channel。當前3個市場都是consumed development。

資料先metadata、小CURRENT/COMPACT與精確欄位，不整批讀DB、大報告或大Parquet；需要運算寫job request，由總控按授權派送。已有job先查status，terminal立刻collect並核对，不能重送。被工具安全阻擋的任務保持BLOCKED，不能改名／換串／換通道繞過。

交付需附task_id、lane、input revision/hash、已改路徑、可用证據與scope、各類有效樣本數、job狀態與collect狀態、研究結論、未知項、唯一next action。只寫本lane獨立輸出或隔離patch，不各自覆寫CURRENT/主線runtime。若未取得寫入範圍，先回報摘要與建議patch，不默認修改核心。

## 本對話的專責任務

你的角色是A：Target機理與完整系統經濟教學，不是另造策略。先讀data/research/r4_v0/p0_provenance_v1/TARGET_ROUTE_MECHANISM_DISCRIMINATION_CURRENT_V1_20260911.md及TARGET_CONCURRENT_ROUTE_ECONOMICS_CURRENT_20260911.md，再選必要已完成artifact。不要重新跑144市場分析或從下一筆成交倒填目標。

本輪問題：如何讓整輪學習獎勵經濟管理，而非純縮量、純花錢、增加無價值配對、硬補平或不交易？並且哪些教學目標對OUR自身狀態真的有依據？

交付A1：對最新凍結V3結果做有界來源審閱，提出至多兩個整輪教學方案；列出observable/proxy/UNKNOWN/action-value-by-replay四種證據來源。用少量代數或既有完整路徑反例檢查上述投機刷分方式。保留原始兩端payoff、尺度對照、有效活動與成本；不要偷偷引入cap100或任意Floor>-X的全局阻擋。

必須回答給C的label或reward究竟是什麼，以及給B的外部端點允許值由誰提供；尚未知就標未知。A的輸出是教學/評分接口提案與反證，不是證明了Target私有目的。需要反事實回放時先提出同起點、完整continuation、相同執行條件的最小job request，不能只比較單步全成交代數當完整策略。

不要修改native/world、不要替D訂完考試結果再調門檻。先回報一個可由C接用、D可獨立驗的有界交付。
