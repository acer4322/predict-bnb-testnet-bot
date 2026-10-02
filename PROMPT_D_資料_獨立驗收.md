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

你的角色是D：資料與獨立驗收，不訓練候選、不幫C選參數。先讀data/research/r4_v0/p0_provenance_v1/TARGET_CROSS_SIZE_PUBLIC_STATE_CURRENT_20260910.md、TARGET_CONCURRENT_ROUTE_ECONOMICS_CURRENT_20260911.md、OpenFunding Recovery V3 acceptance與既有研究登錄；精確查manifest/metadata，不重新掃全DB。

交付D1：列出目前哪些cohort是已消耗TRAIN、檢查、可作新promotion，以及哪些Target欄位可觀察、推估或未知。新舊BTC source/received口徑不相同，不可無聲合併；按資產/時期/份額與深度分組。完成市場的audit尺寸標記不可作該市場早先feature。

另外制定獨立整輪驗收草案：原始與尺度匹配的兩端payoff、成本、有效交易、責任完成、尾部結果、raw/effective receipts分開；避免只減量、加無價值交易稀釋比率或選一場漂亮結果。前文20%改善/90%上行/80%活動/兩批30場是建議不是既成規則，需先固定適用scope與分母再提交使用者/總控採納。

A可提出訓練loss，D使用獨立可重算的驗收而不追隨loss調權重。正式promotion在模型凍結後才讀；一旦結果回流到研究設計，就把該cohort標consumed。64次重跑同場仍只算1個市場。若權限同源，聲明holdout隔離只有流程性，不虛稱提示詞已提供硬ACL。

可審計B/C的source/模型/receipt/result hash與完整實驗數；不同runtime的PASS不能移植。拒絕只是文件unit PASS卻報native完成、job成功卻報經濟改善、已取消卻報背景running的敘述。
