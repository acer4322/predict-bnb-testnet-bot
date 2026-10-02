# REVIEW 006｜故障已直接捕捉，轉入獨立執行修復

結論：**ACCEPT_WITH_CORRECTIONS／REPRODUCED_WITH_CAPTURE**。接受本輪診斷完成，不代表native路徑完成、策略改善或可以上線。[TASK_007](TASK_007.md) 已準備，由使用者人工轉交；Codex此次沒有派送worker。

## 已獨立核對的證據

Codex直接讀取v32的capture、三輪原始failure_trace、來源pin與舊EOF測試。可重現核查：[CLOCK_CHECK.json](review_006_evidence/CLOCK_CHECK.json)，產生器：[check_clock_evidence.py](review_006_evidence/check_clock_evidence.py)。

- 1,481次實際strict呼叫全部`bound_is_observer=true`；install後掛鉤有效。最後一次source index=1479、目標300.535秒、起始300.255秒，`elapse(280ms)`直接回傳rc=1，結束仍300.255秒，strict回False。
- 呼叫端忽略False後進入process。原始seq73/74回報receive=300.350秒，比native clock晚95ms；交易時間300.100秒。UP_153仍SUBMITTED、filled/payment=0，未因原生帳務已更新而擅自確認責任。
- native feed共10,897事件，尾端兩筆位於300.535秒。不能把「rc=1」簡化成「這次呼叫沒處理任何事件」。
- v30/v31/v32的原始壓縮failure_trace直接hash均為`ab0c321220858f278bfed0eb58e8daf1ff3533550948a5b32d57894d8994527d`，不是僅採信ANALYSIS中的hash字串。
- SOURCE_PIN_POST記錄的三個scratch，41個tools、3個src、4個tapes逐組一致。這是回收的worker釘選證據；Codex本輪未重新連線查worker。native hash仍是`7de23355…8ebf`。

## RETURN中的兩項文字修正

1. 「弱邊買入margin恆為正向」過度概括。`q*(K-(K+1)*p)`在q>0、K=4.15時，只有p<4.15/5.15≈0.805825才為正；本案p=.31確實增加25.535，不影響此案判讀。FULL在**相同狀態假設評估**下會被reserve拒絕，不是FULL實際路徑必然走到同一狀態。
2. 與template的「三個差異」僅適用工具檔。`tapes/2629019.json.xz`在template pin中也沒有對應項；v30/v31/v32及v24 base則一致（`38721e39…171bf`）。這是template缺少該市場檔案，不能誤寫成它提供另一份不同tape，也不能概稱所有來源都等於template。原RETURN與pin不覆寫。

## 已有舊EOF證據，下一步不能再只是重現

[20260911 EOF probe結果](../../../data/research/lan_worker_returns/open-funding-native-eof-probe-20260911-v3/COMPACT.json)已在同一native版本記錄：NO_FILL的rc1後時鐘仍1.1秒，但訂單狀態已改；LATE_FILL_BEFORE_CANCEL在rc1後時鐘仍2.0秒，但fill及cancel狀態已更新。舊結論僅證明引擎狀態可更新，沒有證明receipt能按真實native時間安全接入OUR。不得重用其中「在舊時鐘送cancel」為修復方式。

Codex另讀本地`.tmp/hft244_accounting_source_v1/hftbacktest/src/backtest/mod.rs`：`goto`在下一事件超過目標時才設定`cur_ts`；事件集合變空時直接EndOfData返回，能解釋本案現象。但本地檔案SHA=`24695e64…545f5`，不同於v4回收SOURCE_MANIFEST中`3340cb40…e4977`。因此它只是定位線索，**尚不能宣稱已核對受測binary的精確原生根因**。TASK_007必須先釘選worker實際v4來源，再實作最小修復；不得拿這份本地舊樹整包替換。

## 尾盤單的策略結論保留

299.850秒只剩150ms，而entry latency=250ms；以本研究300秒窗口作截止，預計到達已在窗後。ActiveOpportunity只檢查送出時間，固定15份被動票未滿1元令主動候選成立，沒有arrival檢查；MARGIN_ONLY又移除reserve。它既不是已驗證的尾盤緊急修復能力，也不代表目標帳戶規律。

候選按限價全成交會把DOWN的+1.60473花成−1.49527；兩側皆負。原始回報的離線算術也會耗盡正收益，但仍不能冒充canonical終局。真實venue cutoff保持UNKNOWN。

這項策略問題要修，但下一輪先固定策略，修好測量路徑。不能靠禁掉299.850秒那張单讓時鐘bug消失；也不能把正常的到期後成交回報直接忽略。執行修復通過後，再另凍結共用arrival判斷與修復收益代價協調，並處理RESERVE_ONLY尚未完成的比較。

## 本輪審查狀態

v32一條native路徑仍ERROR，診斷成功；v30的RESERVE_ONLY十場及四格交互作用仍未執行／UNKNOWN。v32沒有新增經濟改善、泛化或模型學習證據。舊結果、V12、live及收集器保持原樣。

下一步是[TASK_007](TASK_007.md)：隔離的EOF事件時鐘與呼叫端修復，原生契約測試後最多8條固定市場路徑做故障恢復及相容性檢查。完成即交RETURN_007，不自動繼續策略實驗。
