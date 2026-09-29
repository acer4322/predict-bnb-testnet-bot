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

你的角色是C：完整學生的表達能力與共同學習；禁止退回成交類型旁路classifier。先讀data/research/r4_v0/p0_provenance_v1/MINIMAL_STUDENT_OPEN_FUNDING_RECOVERY_RETURN_V3_20260911.md與acceptance，再核對其frozen policy/實際actor模組，只讀小型檔案。

本輪C1先回答：12參數固定公式能否表示我們要學的完整責任／延續？policy真正消費哪些own state、歷史、目標與pending訊息？哪些資訊雖在frame裡，卻沒有進入policy？不要把actor架構寫死的限制誤稱環境已無限制。

交付一份input→policy→action→receipt map，列出最小的有歷史差異之完整情境見證：當前相近book/持倉，但已做工作、未解責任或執行歷史不同，是否需要不同完整方案。這是研究假說與必要性測試，非Target原始專家答案。只在有具體缺口時增加最小持續狀態或記憶，不先上大網路。

後续由A提供教學與經濟評分、B提供有效native能力，C才訓練同一policy的全部管理輸出。A/B尚未交付時，可完成C1與小型合成可表達性見證，不把等待變成再調同三場loss。不能私改A/D評分，不能讓模型只控第一步、後面轉回不明舊策略。

實驗採一個整合候選與同執行條件基準，所有選模只在TRAIN；D的正式考試市場不可用來挑theta。Active未接好時清楚標PASSIVE_SCOPE，不宣稱完整Target系統能力。
