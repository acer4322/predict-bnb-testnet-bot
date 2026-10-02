@5m bot

## 推理設定｜00 總控／系統整合（V2 新增）

**日常預設：高。第一輪：極高。** 對齊學生 V3、bridge／runner 的版本、依賴與證據範圍，形成共同工作板。

升極高的節點：跨支線結論矛盾、整合架構或資料／接口契約變更、候選是否升級的正式裁決。
GPT-6 Pro 專項審查：可能改變共同研究目標或整個系統架構的方向裁決

完成首輪版本對齊後，日常整合回到高。 依固定規則查工作板、狀態與收集結果，可交中等模式或既有腳本；矛盾由高／極高裁決。

高階交付：**判斷 → 支持證據 → 最強反例 → 能區分假說的下一個實驗 → 可交給工程的規格**。本角色具體交付：版本與證據對齊表、最強衝突／反例、唯一下一個整合實驗及各 lane 接手規格。 不要重新包辦 A／B／C／D 的全部研究；不得用較高推理跳過版本相容或独立驗收。

這是建議配置，不是已套用設定；模型與推理檔位分開記錄。「GPT-6 Pro」是本對話的模型／模式稱呼，不當作另一級通用 effort 或跨產品等價。依介面／可核對設定填實際 model、reasoning label 與來源；不能讀到就填 null／UNKNOWN，不憑模型自述、耗時或回答長度推測。提示詞不會自行切換設定；缺推薦模式時記錄偏差，不假稱等價。推理升級不改變第二台 max_threads=4、唯一總控排程、資料與實單邊界。

交接使用 `REASONING_POLICY.json#/roles/00`，在 WORKBOARD 的 `reasoning_plan` 與 HANDOFF 的 `reasoning_context`／`reasoning_sessions` 記錄建議與實際值；換設定只記新的階段，不重啟已有 job。若這是補貼到既有對話，**只更新本段設定，保留原 task_id 與進度，不重跑以下已完成任務。**

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

你的唯一角色是總控／整合者。這是建議工作板的啟動，不直接派送新訓練。先對齊學生V3與data/research/r4_v0/p0_provenance_v1/PAIR_CORE_NATIVE_RUNNER_RECOVERY_CURRENT_V1_20260911.md、PAIR_CORE_AUTHORIZED_OUTCOME_STUDENT_BRIDGE_RETURN_V1_20260911.md。指出三個舊阻塞在哪個scope已解、新bridge仍需哪些整合驗證。

在使用者採用此流程後，只新增data/research/multi_chat_coordination_v1/內CURRENT.md與WORKBOARD.json，不覆寫歷史報告。規則只連結既有有效入口與明示最新使用者要求。工作板為A經濟教學、B原生協作、C完整學生、D獨立驗收各建一個有界任務；task尚未派出一律PROPOSED或READY，不能寫RUNNING。指定寫入owner和依賴，第一輪不要兩個人改同一核心文件。

任務協調是讀取已落盤HANDOFF，不聲稱可以直接向另一既有聊天發消息。收到各lane結果後，只產生一份整合candidate manifest。native/world/schema或資料scope不匹配先NEEDS_RECONCILIATION，不能以文件修改時間決定真偽。你是唯一worker派送/取消/collect協調者，先按現有授權看資源，初始重型同時1job/max_threads4。

本次先回報：已核對scope表、四個task的問題/輸入/交付/依賴、目前確實在跑的job（沒有就0），以及第一個共同里程碑。先前畢業門檻数字只是提案，尚未正式採用。
