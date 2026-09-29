# TASK 005｜單場回報時鐘診斷，解除修復研究的測試阻塞

狀態：PREPARED_FOR_MANUAL_HANDOFF。使用者將本文件交给Claude並要求執行後，按本輪範圍完成並交回RETURN_005。Codex只準備任務，尚未派送；維持人工文件協作，不啟動桌面操控、CLI聊天接入或新排程。

## 使用者方向與本輪目標

使用者本輪要求繼續下一步，並將重心放在策略修復能力。Codex同意：保留開局擴張背景，優先學會以合理的正收益代價改善反向虧損，協調主動／被動修復，並避免後續ADD再次耗盡修復成果。不是把兩側份數補平，也不重新要求3:1、零代價修復或任意固定收益保留率。

這個方向不表示開局、方向來源或跨市場能力已獲完整驗證。本輪先解決妨礙可靠評估修復的觀測缺口：**2629019的原生回報為何晚於引擎當前時鐘？** 只做來源核對與行為不變的診斷，暫不修改執行語義或策略。

先讀根AGENTS、docs/agents/research.md與current pointer、docs/agents/development.md、docs/agents/worker.md，以及 [REVIEW_004.md](REVIEW_004.md)。RETURN_004的原始推論與review更正必須分開。

## 已知證據與去重

- 原v30：`data/research/lan_worker_returns/btc5m-v12g-admission-components-20260927-v30/arms/v12g30_MARGIN_ONLY_2629019/`。
- 舊同位置失敗：`data/research/lan_worker_returns/btc5m-v12g-fresh30-20260927-v24/arms/v12g24_WB100_2629019/`，僅作既有證據，本輪不重跑WB100。
- Codex只讀整理：[FAILURE_CHECK.json](review_004_evidence/FAILURE_CHECK.json)。
- v30六條相容性檢查及九條完成的MARGIN_ONLY已驗收；一條ERROR、十條RESERVE_ONLY未啟動。原結果不覆寫、不重新提交完整v30。
- 兩次堆疊均在run_whole行情迴圈的process→Reader.peek，尚未進入drain。最後主動單299.850秒，最後成功state300.255秒，最後公開book received為300.535秒。致因order/receipt/native clock仍UNKNOWN。
- `failure_receipts=[]` 是失敗前已清空的處理後delta，不是原始journal為空。

Codex已查當前V12g後繼包名稱、CURRENT/PROTOCOL/FAILURE候選與tools中的receipt/clock/capture工具，未找到等價的2629019原始違規journal捕捉。現有`run_hft244_residual_authority_capture_worker.py`針對其他authority/counter，不是本案根因證據；可參考其保存方式，不執行其舊job。執行者需再核對同名包、exact/global狀態及回收目錄，若已有本輪等價證據，重用並停止重複派工。

新包：`data/research/v12g_receipt_clock_diagnostic_20260927_v31/`。
唯一可提交job：`btc5m-v12g-receipt-clock-diagnostic-20260927-v31`。
最多一條native路徑：2629019、v30 MARGIN_ONLY原設定。若既有證據已足以回答問題，可以不提交。不得增加其他市場、WB100、RESERVE_ONLY、控制矩陣、訓練或參數搜尋。

## 先釘選實際來源

先寫ACK_005。只讀核對原故障worker scratch、既有staging及backend；保留v30原scratch，不覆寫、清理或搬走。由既有manifest/trace確認路徑，不盲用目前工作目錄版本替換。

記錄完整SHA256與來源：v30 overlay及helper、v24 base/model/public/tape、實際`hft244_receipt_adapter_v1.py`、`hft244_research_owner_accounting_v1.py`、`hftbacktest_execution_shift_audit_v0.py`、`minimal_student_native_system_plan_v1.py`、`open_funding_recovery_runtime_v3.py`、相關actuator，以及載入的native binary。確認Receipt ABI與欄位時間單位。只核對相關依賴，不掃私人帳號／憑證或整個工作站。

若舊scratch不存在，可查凍結封裝或已驗證hash相等副本；若仍不能連結實際失敗來源，回報SOURCE_PROVENANCE_UNRESOLVED並停，不自動採用最新工具重跑。新診斷使用新隔離scratch，實際載入路徑與hash須回收；不得就地patch共用template、base或live工具。

## 允許的唯一變更：觀測，不改控制流程

沿用v30 MARGIN_ONLY的全部行為、selector、env清理、K/RETAIN、數量、方向、PADD旁路、主動修復、取消與時間條件。保持fee=0、risk queue、250/250ms、capital_cap=null與同一資料／native binary。

只在新隔離副本或該子程序局部掛接觀測，保存下列證據：

1. **時鐘推進邊界：** source index、source_ms／received_ms、目標target_ms及換算ns、advance前後native clock、原生elapse回傳碼、advance原返回值、是否因cur≥target跳過elapse。每次保持原有呼叫次數與返回值；不得額外advance或wait。至少保留從最後主動NEW到失敗的完整邊界序列。
2. **原始違規回報：** 在既有Reader已完成`from_buffer_copy`、時間斷言之前，保存同一次get返回的owned rows與容量／筆數、觀測native clock。欄位含sequence、generation、order_id、receive_ts、exchange_ts、side、maker、qty、price、fee、cumulative_qty、leaves_qty、status；列出每筆與native clock及目標時點的差。不要在捕捉時再次呼叫peek/get/ack來改變觀測位置。
3. **未結責任與已確認前綴：** 原生ID→策略key/role/route映射、canonical carrier/owner、pending qty/cash、cancel/transport狀態、最近相關NEW/CANCEL、已確認receipt ledger sequence與可重建前綴、inv/cost。直接讀已存在的資料欄位，避免呼叫會內含peek的policy_state/preview/序列化入口。UNKNOWN原樣保留。
4. **故障定位：** 原exception traceback、實際module paths/hash、正在執行source loop或drain的標記。不得用上一次成功state的時間冒充故障native clock。

違規資料只進診斷檔，不得回傳策略或更新ledger。保存後仍保留原斷言／例外：**不ack違規回報、不調時間戳、不提前確認成交、不釋放pending、不推進時鐘補救、不延長／截短市場資料、不禁止尾盤主動修復、不跳過失敗市場。** 寫檔失敗需記錄CAPTURE_ERROR，同時保留原始錯誤，不能用新的序列化錯誤取代它。

可觀測原生返回碼及既有狀態，禁止改建native binary。本輪不修理任何經濟／執行邏輯，即使看起來是一行即可解決；先把根因與最小修改方案交Codex審查，避免污染四格對照。

## 證明觀測沒有改變受測路徑

提交前凍結PROTOCOL、manifest、儀器差異、分析與純函式檢查。小型測試至少驗證：

- 合法receipt與空journal保持原返回值、原get次數，沒有額外ack或狀態變更。
- 違規receipt會被捕捉，原時間斷言仍拋出；capture writer失敗不吞掉原始錯誤。
- clock recorder只記錄既有elapse結果，不多呼叫或改變返回值，包括cur已到目標及非零返回碼。

主機只跑不載入native引擎的fake/stub測試、hash與JSON檢查；不得在主機跑HFT。測試不要求DLL，不import會啟動完整runner的模組。

重現後，將v30已保存的states/plans/native_actions/observations與新路徑按既有欄位逐項比較，時間、side、key、動作完全一致，數值容差預登記1e-9；新診斷欄位獨立保存。若失敗前前綴不同，標PREFIX_DIVERGED，不能直接解讀為原故障重現；保留並停，不修完觀測器後自行提交第二次。

## 第二台電腦執行界線

當使用者轉交本TASK要求執行，授權上述必要的唯讀來源檢查、隔離診斷實作及最多一個具名第二台研究job。依worker文件核實DESKTOP-JIERAGF、strict host key，probe→exact/global status→stage/load-only→一次submit→回收。若worker忙，查明狀態並等待既有流程，不搶資源或停收集器。

一個heavy job，max_threads=4，僅一個native子程序，不另建scheduler/watcher；沿用既有收集器。逾時查狀態，不重送。原生子程序重現AssertionError是預期診斷情況，必須收回raw result/error與捕捉工件，不能因子程序非零就丟掉診斷檔；外層成功退出也不能改寫raw ERROR為策略PASS。

凍結v30結果與來源不改；live、armed、stakes、策略、服務、資料收集器不在本輪範圍。

## 判讀與交付

只允許以下診斷結論，均非經濟成功：

- REPRODUCED_WITH_CAPTURE：同一錯誤、前綴一致，必要raw clock／receipt／owner捕捉完成。
- REPRODUCED_CAPTURE_PARTIAL：同一錯誤但必要欄位仍缺失，逐欄UNKNOWN。
- NOT_REPRODUCED：未出現原錯誤，完整保留新路徑並停；不追加種子或市場「試到出錯」。
- PREFIX_DIVERGED／SOURCE_PROVENANCE_UNRESOLVED／OTHER_ERROR：明確交回阻塞與已知差異。

RETURN_005需回答：哪筆回報違規、三種時間的精確值與單位、advance實際回傳了什麼、receipt是否已在native帳務反映但未交OUR、責任如何保留、是否與最後主動單對得上。若EOF仍只是推論，標INFERRED；不要靠同時間巧合定根因。

若證據足夠，給出一個最小修復設計、會改變的語義及必需的相容性檢查。若根因未明，指出唯一最關鍵缺失欄位／來源。**不要直接補RESERVE_ONLY、延續v30、修改訓練或自動提交修復job。** 缺失格保持null/UNKNOWN，不能沿用SUMMARY的空集合補0交互計算。

可寫新v31包、其具名worker回收工件、ACK_005.md、RETURN_005.md及既有dispatch必需記錄。不得改TASK/REVIEW、CURRENT.json、RESEARCH_CURRENT或先前凍結包；由Codex驗收後更新。交付應含一段結論、來源核對、單場狀態、時序表、原始journal／責任證據、前綴對照、限制與唯一下一步。完成這個有限診斷或遇真實阻塞後停止。
