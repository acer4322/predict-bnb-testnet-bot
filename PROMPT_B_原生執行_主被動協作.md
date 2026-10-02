@5m bot
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

你的角色是B：原生工程與同目標下的主被動協作。沿用已有修复，不再重做V2 zero-fill/20pending/51censor。先讀data/research/r4_v0/p0_provenance_v1/PAIR_CORE_NATIVE_RUNNER_RECOVERY_CURRENT_V1_20260911.md、PAIR_CORE_AUTHORIZED_OUTCOME_STUDENT_BRIDGE_RETURN_V1_20260911.md與OpenFunding Recovery V3；需要原碼再只讀精確相關函式。

本輪第一個交付B1是相容性清單：V3 runtime/receipt/EOF/resource修復與opt-in bridge/runner的import鏈、schema、profile與known failures如何接合。把已解於V3、尚未驗於新bridge分開。既有安全阻擋不以換對話視為授權。不可直接重送被拒工作；先回報可合法執行範圍與明確blocked事項。

後續B2才是native Active能力：同一完整目標/責任上送Active與Passive child，保留共同剩餘責任、部分成交、晚到收據、unknown send、費用、滑價、撤單與terminal；不得重複消費quota，不把pending Repair當已付，不做夢幻成交。Active不可套舊被動18/12限制，但實際route合法性需來源。

只做隔離patch，不修改Target研究結論或教學reward。外部UP/DOWN風險端點是具名fixture/政策輸入，不是新世界限額。交付實際可調用的能力/schema與reject/feedback語義，元件後須有最小native完整路徑驗收才說接線完成；不因多個unit PASS自動宣布full4channel或收益PASS。
