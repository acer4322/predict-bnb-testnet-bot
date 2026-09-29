# TASK 007｜修復EOF事件時鐘與回報接續，驗證後回到策略修復研究

狀態：PREPARED_FOR_MANUAL_HANDOFF。由使用者轉交Claude並要求執行後生效；Codex尚未派送。先寫ACK_007，完成本文件有限範圍後交RETURN_007並停止。沿用人工文件協作，不啟动桌面操控、CLI橋接、WSL或新排程。

## 目標與去重

先讀[REVIEW_006](REVIEW_006.md)、根AGENTS、research/current、development與worker路由。本輪大目標仍是保留開局擴張、學會減虧且不耗盡正收益的主被動修復。本輪直接工作是**修復阻斷該研究的執行時鐘，恢復可信的回報／責任測量**，不是再做一轮觀測器或調策略參數。

已完成證據：v32的strict觀測生效，1481次呼叫、末次rc1但clock仍300.255秒；同次journal含300.350秒回報。v30/v31/v32故障前綴完全相同。這些直接重用，不重跑舊diagnostic job。

去重必讀：

- `tools/probe_open_funding_native_eof_v3_20260911.py`及其[COMPACT](../../../data/research/lan_worker_returns/open-funding-native-eof-probe-20260911-v3/COMPACT.json)：同native早已出現rc1／clock不變／狀態更新，但沒有解決receipt真實時間。
- `data/research/hft_target_fidelity_current_20260923/probe_v2_order_receipt_eof.py`與`probe_v2_no_order_eof.py`：只作既有診斷參考，勿重跑Target資料或套用該研究的時鐘／成交模型修正。
- v32 SOURCE_PIN_V32_POST、strict capture及[主審核查](review_006_evidence/CLOCK_CHECK.json)。本輪新變因是**事件完成時間及其呼叫端語義**，不是另一個同義EOF probe。

新包：`data/research/v12g_eof_execution_repair_20260927_v33/`。
唯一worker job：`btc5m-v12g-eof-execution-repair-20260927-v33`。
一個隔離candidate binary、一個按依賴順序執行的build／契約／相容性批次，最多8條市場路徑。已有同名工件或工作先查exact/global與hash，不重建或重送；Codex未執行本輪。

## A．先證明受測原生來源，再凍結修復

受測native SHA256：`7de2335528234bea9381bea4208a90594a7bec9795d09b913f430ff1f8cf8ebf`。

worker已知來源位置：

- `C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_source_v1/`
- binary在同root的`.tmp/hft244_accounting_build_v1/candidate_python/hftbacktest/`。
- 本地參照manifest：`data/research/lan_worker_returns/hft244-receipts-v4-20260910-v1/SOURCE_MANIFEST.json`。
- 既有建置流程：`tools/run_hft244_receipts_v4_worker.py`。它是路徑與來源參考，不可原樣執行而覆蓋舊包。

核對worker身份、來源manifest、binary、wrapper與實際載入路徑；取回與EOF相關的`backtest/mod.rs`、事件選擇、local/exchange processor及Python rc映射的最小原始碼。保留hash與行號，解釋哪個返回路徑遺失了clock更新、哪些事件已真正處理，以及rc1代表的範圍。不能僅據Python capture宣稱內部實作已知。

Codex本地舊`backtest/mod.rs`的goto有「事件集合空→EndOfData直接返回而未更新cur_ts」線索，但其SHA與v4不同，**不能將本地舊source當成v4來源**。必須從已核對的worker v4樹複製到新隔離樹，不改舊樹。來源無法證明或根因不同時，交SOURCE_BLOCKED／ROOT_CAUSE_DIFFERS與具體證據，停止建置／市場回放，不能猜修法。

建置前凍結ROOT_CAUSE.md、最小diff、PROTOCOL、manifest、測試矩陣與分析器。只允許一個候選語義，不根據市場損益換修法。可修建置腳本的非語義錯誤並留痕；若原生契約或市場回放失敗，保留工件停止，不在同輪反覆換候選重跑。

## B．允許的修復及不變條件

### 原生事件時鐘

若A確認上述分支，最小方向是在EOF返回時，使對外clock反映本次**已實際完成處理的事件時間**。可在goto追蹤最後完成事件並於正常EOF出口提交；具體diff以受測來源為準，需說明local／exchange data、send／receive order四種事件及同時間排序。

- 時钟單調，journal已交付回報的receive_ts不得超過已公開clock；不得提前交付尚未發生的回報。
- 不把requested target／timeout上界直接設成clock，不從失敗receipt的最大時間倒填clock，不增加行情尾巴、重播book或更改tape。
- 完全沒有事件、目標之前仍有後續事件、最後事件恰等於目標、目標超過最後事件、只有未結掛單但没有待傳輸事件，要明確區分。沒有新事件時不能憑空推進；初始化sentinel不是有效時間。
- 保留原生rc含義與receipt ABI／sequence／ack／overflow等契約。非EOF錯誤仍停止，不因clock修復而繼續使用invalid engine。撮合、queue、fees、延遲、量價、訂單狀態轉移全部不改。

### Python呼叫端及drain

在新包隔離副本修正實際生效的advance／run_whole／end2／drain接線。不要只改未被使用的函式。

- 保留原rc並另記`target_reached`與實際native clock。正常rc0也應驗證到達；rc1不自動等於失敗或成功：只有有效native時間確實到達該source目標，才能處理該幀。到達與「還有沒有下一筆資料」是不同資訊。
- 每個process／frame入口都要有相符的時間證據。未到source目標時不繼續該幀，不把False直接導到同樣忽略回傳值的end2。安全保存已確認前綴、raw journal與責任，剩餘source標CENSORED／NOT_CONSUMED，不改完整性斷言成PASS。
- drain只按真正native事件時間接納回報；query upper bound保留為診斷欄位，不能當observed_ms、決策時鐘或撤單時間。時間轉換不得向未發生的未來取整。若EOF沒有進展且仍有未結責任，明確censor並保留，不能無限重試或用EOF釋放。
- 到期前已成交、到期後才回報仍接入原有receipt／canonical ledger。UNKNOWN、CANCEL_PENDING、同plan CANCEL都不釋放owner／cash／self-cross責任。只容許既有策略在真實時間上的closure，不加新撤單政策；不得把凍結失敗capture直接塞入新ledger。

允许新包內最小native patch、時鐘／呼叫端／drain副本、明確candidate路徑接線及測試。若必須更改狀態機、撮合或責任語義才能通過，超出本輪，保存並停止。不能重寫整個runner或放寬Reader因果斷言。

策略保持v30原樣：K4.15、RETAIN.25、GROSS300、FLIP.1、PADD.80、AR_DP／UNCAP／AFTER_DECIDE／TRIGGER／STRANDED_MAX.70；v24 base、risk queue、250/250ms、fee0、capital_cap=null、selector與所有旁路不變。開局、last_H／None、peak、size、方向、准入、主被動角色與最低票條件不改。

本輪**不加arrival cutoff或收益保留規則**，不以最後固定N秒禁單迴避故障。299.850秒訂單在故障恢復路徑仍應按原策略產生；它的經濟不合理性另行處理。V12／live／收集器及共用template維持原樣。

## C．先小型契約，再8條固定市場路徑

主機只做不載DLL的純測試、來源分析與封裝。編譯、native synthetic和市場回放全部在核實的第二台執行；build與回放不並行，max_threads=4。先load-only／ABI核對，再依序執行以下閘門；任何實質失敗停止後續未啟動格。

### C1．原生與呼叫端契約

凍結最小synthetic集合，舊binary与candidate配對驗證。可借用舊probe的fixture，但移除以舊clock送新cancel的錯誤預期；不要直接將舊`terminal_after_native_drain`當通過。

必涵蓋：

1. 目標在尾事件之前、恰等於尾事件、超過尾事件；空feed／初始化及重複EOF無進展。
2. 最後行情後仍有已排程成交／回報；部分成交→終結、取消與成交交錯。回報時間、quantity／price／status／sequence／ledger增量與原引擎相同，差異限定有效clock及因此可合法消費回報。
3. 未結resting order但無待傳輸事件，保持未結／censor；不能製造terminal。多asset至少覆蓋「一側feed先結束、另一側仍有事件」不提早全域EOF。
4. strict有效安裝後rc0到達、rc1到達、rc1未到達、其他錯誤；run_whole末幀／end2與drain皆涵蓋，不僅unit helper。未到目標不可產生該幀策略動作。
5. 既有receipt ABI、peek／ack exactly-once、overflow／非EOF錯誤停止、自交與責任保留契約。重用`tools/check_hft244_receipts_v4.py`及相關最小檢查，不降低舊預期。

完整列每個fixture舊／新rc、clock、raw journal、帳務與ownership。新binary路徑與hash須由每個子程序readback，不能只改設定而仍載入舊DLL。新hash只在這個隔離包採用，不全域安裝。

### C2．固定8條市場回放

通過C1後，先跑故障恢復一條，通過其因果／完整性檢查才跑其餘七條：

| 用途 | arm／市場 | 舊結果來源 |
| --- | --- | --- |
| 故障恢復 | MARGIN_ONLY／2629019 | v30失敗、v32有效capture |
| 正常修復相容性 | MARGIN_ONLY／2628999 | v30既有完成格 |
| 全開相容性×2 | FULL_CHECK／2628553、2628769 | v30 checks、v29 FULL |
| 全關相容性×2 | OFF_CHECK／2628553、2628769 | v30 checks、v28 OFF |
| 凍結控制×2 | V12_CONTROL、K415_CONTROL／2491311 | v30及v29controls |

總數8，每格一次。舊binary的市場結果全部重用，**不再另跑8條舊引擎市場路徑**。本輪不補v24 WB100、不跑完整9／10／30市場、不跑RESERVE_ONLY或訓練。

故障格驗收：直到舊source index1478完成的state／plan／actions／receipt prefix不變，299.850秒訂單仍相同。原index1479應在真实已到達clock下處理；seq73/74由native→reader→canonical路徑各入帳一次，記錄費用、份數、支出與owner終結。全部公開source frame按順序消費，producer/frame/update數量一致。不能用丟末幀、改tape、手工ledger／資金平帳完成；native、OUR帳務與責任不一致就停止。

七個相容性格：比較state／plan／native_actions／receipt既有欄位及signed UP/DOWN、cost、inventory、主被動活動、rc與失敗子檢查；新增診斷欄位分開。事件、動作、序號與份數語義精確相等，原浮點數比較沿用既有預先凍結容差（不大於1e-9）。

允許且必須單列的差異只有修正的EOF clock、證據化的drain時間標籤與新增診斷；經濟結果、訂單量價、取消決策／時點或責任結果一旦改變，不以「修clock所以正常」略過。記錄首個分叉及原因，標PARITY_DIVERGED並停止未啟動格，交Codex判斷是否需要新的基線。全trace字節不可能在新增欄位後一致時，要比較既有欄位，不能刪除實質差異來通過。

controls應完整通過。其他格只可沿用原已證實的單一legacy active_matches_opportunity標籤；原始rc／safety_pass保留，不能一概忽略rc2。帳務、canonical責任、atomic conservation、overfill、完整消費必須分開驗收。

## D．執行與交付

遵循worker路由：核實DESKTOP-JIERAGF與strict host key，probe→exact/global status→stage／load-only→同一具名job提交一次→回收。逾時查狀態不重送，一個heavy job、max_threads=4，不新增watcher、不更動收集器或live。SOURCE_PIN可使用必要的唯讀連線，記錄與native job分開。

只可寫新v33包、新隔離worker source/build/scratch、其具名回收目錄、ACK_007、RETURN_007及既有dispatch必需記錄。不得改舊v24～v32、原binary、共用template／主repo runtime工具、Codex TASK／REVIEW／CURRENT／RESEARCH_CURRENT。

RETURN_007至少交：

- 受測原生根因與來源hash，最小diff及新binary依賴／載入證據；已確認、推論、未知分開。
- synthetic全表、8格planned／complete／failed／not-started狀態、每格首次EOF與有無時計差異、相容性首分叉。
- 2629019的末段時間序列、seq73/74入帳唯一性、canonical owner、完整source消費、native／OUR對帳；若仍未結就留UNKNOWN，不稱終局。
- signed UP/DOWN、P=max(UP,0)+max(DOWN,0)、L=max(-UP,0)+max(-DOWN,0)、P>L且P>eps（eps=1e-8）、双正／正零／混合／雙非正，成本及主被動活動。此處只是修復後記帳診斷；8條含不同arm／重複市場，**不能把8條混成策略成功率**。凍結原v30結果不覆寫，未執行四格保持null。
- 判定EXECUTION_REPAIR_VALIDATED、SOURCE_BLOCKED、CONTRACT_FAILED、CENSORED、PARITY_DIVERGED或PATH_ERROR等真實狀態；即使全通過，也只是有限執行修復驗證，不是收益提升／模型晉級／上線通過。

本輪停在審查。通過後的研究方向是共用到達可行性與修復收益代價協調，再決定如何完成未完成元件比較；不在這一輪混入。到期前送達但窗後確認的訂單仍保有責任，真實venue cutoff在無證據前保持UNKNOWN，不能靠任意尾盤禁單秒數取代。
