# TASK 006｜補正strict時鐘觀測，審查尾盤主動修復的時間與收益代價

狀態：PREPARED_FOR_MANUAL_HANDOFF。使用者轉交Claude要求執行後，完成本輪交RETURN_006並停止；Codex尚未派送。沿用人工文件協作。

## 目標與最新判斷

先讀 [REVIEW_005.md](REVIEW_005.md)、[TASK_005.md](TASK_005.md) 的觀測／責任保存界線，以及根AGENTS、research/current、development、worker文件。TASK_005已完成為部分捕捉，不能重送其job；本文件是修正已診斷的觀測器綁定錯誤的獨立後繼。

使用者繼續要求聚焦修復，並指出299秒多仍有主動單很奇怪。本輪要交付兩個相連但分開判讀的結果：

1. 捕捉真正生效的strict_advance_to原生返回碼、前後clock與feed尾端，完成時鐘故障定位。
2. 用已知2629019反例核對「為何此時還提出主動修復」「到達是否來得及」「是否花光正收益」，給出最小改進設計。

不在本輪改政策、EOF/drain語義或native binary，不執行新的期限守門策略。這輪也不補RESERVE_ONLY、不調K／RETAIN、不训练、不做全市場擴測。

## 已知證據與去重

- v31已確認order153的seq73／74：299.850秒送出、300.100秒模擬成交、300.350秒回報；native clock停在300.255秒。OUR未確認、carrier仍SUBMITTED。
- v30/v31 failure_trace相同，但advance.n=0；install在之後把base.ex.advance_to改成strict_advance_to，因此import時的shift_audit掛鉤失效。
- 原ANALYSIS的OTHER_ERROR由固定檔名／行號比對造成，保留原件；按語義与原始時間證據更正為REPRODUCED_CAPTURE_PARTIAL。
- [LATE_ORDER_CHECK.json](review_005_evidence/LATE_ORDER_CHECK.json) 已重算：送單剩150ms；被動15×0.05=0.75；決策前DOWN正收益1.60473，候選最大支出3.10，按限價全成交兩分支皆負。原始回報成本1.781322的算術也不能冒充已確認終局。

Codex已查v31與相鄰工具，未找到本案「install之後strict有效綁定＋原生rc」證據。執行前查exact/global及同名包／回收資料，已有等價證據就重用。名稱含deadline的其他Target/ETH研究不代表本V49入口已修復，需核對語義，不能直接套用。

新包：`data/research/v12g_strict_clock_deadline_20260927_v32/`。
唯一job：`btc5m-v12g-strict-clock-deadline-20260927-v32`。
最多一條native路徑：2629019、同v30 MARGIN_ONLY。重用v31來源釘選與capture；不重跑WB100、controls或其他成功市場。只讀檢查如已足夠，可不提交。

## A．修正觀測綁定並取得缺失欄位

先寫ACK_006。沿用v31 SOURCE_PIN／provenance，重新確認當前worker實際来源與hash未漂移。新隔離scratch，不改v30/v31、共用template、base、live工具。保留全部策略參數、資料、模型、native hash、fees、queue、250/250ms與原selector；允許的行為差異只有診斷觀測與其有效性检查。

不要把來源檢查簡化成「全部等於template」：quantity_seam在v30/v24相等但與template不同，active_adapter沒有template對應hash。按已驗證的安裝／轉換流程逐檔確認，並補驗實際src/tape與轉換後feed；SOURCE_PIN未涵蓋的項目不得自動標PASS。

已核對的實際安裝關係：frozen_runner呼叫`install(minimal.v2.base,binary)`，hft244_minimal_pair_accounting_v1.install將`base.ex.advance_to = strict_advance_to`。必須在這個安裝之後，對**run_whole真正使用的base.ex**掛接；僅觀察模組載入、或測試shift_audit.advance_to均不夠。

要求：

- 保存安裝前／後與首次真實呼叫時的有效函式module、qualname、來源path、code/source hash、原函式與觀測函式對應。確認後續初始化不會再次覆寫。
- 觀測實作保留strict全部分支：cur≥target→True；elapse rc0→True；rc1→False；其餘rc→原NATIVE_EXECUTION_INVALID。不能沿用v31將任意非零rc都轉False的shift版副本。
- 原elapse只呼叫一次，精確記錄原rc、傳入ns、target_ms/ns、before/after native clock、返回值或原exception、source-loop/end2/drain階段與source index。不得用推論填rc。
- 保存同次owned journal、違規receipt、canonical責任、已確認receipt prefix及native/OUR狀態，沿用TASK_005不額外peek/get/ack與不提前入帳界線。時鐘記錄即使之後異常也必須落盤；保留原ERROR。
- 生效證據不僅是patched清單。首次實際advance後必須看得到对应目標的觀測記錄；若有效綁定檢查未成立，終止診斷並標INSTRUMENTATION_INVALID，不跑完後才把0 calls當有效證據。
- 只讀輸出引擎實際使用的feed來源／轉換器hash、最後exchange/local事件時刻及event類型、公開book的最後source/receive時間。區分兩種stream，不把public最後received_ms直接當native最後event。若無可讀源，逐欄UNKNOWN，不能虛構EOF定義。

主機純測試要涵蓋「先安裝會替換函式的install、再掛觀測、從base.ex呼叫」這個整合次序，並測cur到達／未到達、rc0、rc1、其他rc的原語義、logger失敗保留原例外。fake/stub不得載入DLL或import完整runner。分析器亦需測同一斷言移到新檔名仍可辨識、無關AssertionError不誤認，以及無advance證據時只給PARTIAL。

worker執行前凍結PROTOCOL、manifest、預期前綴与分析。新路徑按states/plans/native_actions/observations逐項與v30/v31比較，數值容差1e-9、時點／動作／key完全一致；新欄位另存。前綴分叉或不同錯誤即保存停止，不在本輪調整後再次送出。

## B．尾盤修復只讀審查與最小設計

限同一市場、目前受測策略来源與既有v30/v31資料，不擴大Target研究。重用Codex已核對的ActiveOpportunity條件，再核實實際呼叫與scope／selector影響，不能把MARGIN_ONLY的行為說成所有版本的行為。

交一張可追溯的入口表：目前策略會產生主動單的入口／producer、送出時間條件、到達時間條件、收益代價限制、owner與pending處理。至少涵蓋本案ActiveOpportunity、qualified active repair與同策略可達的其他原生active入口；欄位未知留UNKNOWN，按原始role記錄而不是靠買入側猜ADD／repair。

對本案列出：

1. 299.850秒前的公開報價可得時間、被動候選價、主動ask／depth、量上限、own signed payoff、同plan／pending、實際准入mode。逐步說明15份被動票未滿1元如何讓主動候選第一次成立；不能說是學會了尾盤緊急修復。
2. 以決策時已知下單延遲計算預計到達時點；另列回報確認時間。capture中的實際成交／回報只作離線驗證，不得成為新的決策輸入。
3. 查清本地window_end是什麼依據：研究窗口、native模擬撮合截止與venue實際關閉並非自動等價。已有本地契約足以確認的寫來源；沒有證據就保留VENUE_CUTOFF_UNKNOWN，不擅自宣告真實市場可接受窗後單。
4. 同時報候選按限價全成交的收益代價，及原始成交的離線增量算術；後者仍不是canonical確認終局。說明MARGIN_ONLY移除reserve後，何以允許支出大於剩餘正收益。

只提出一個最小後續設計，方向是「以可驗證截止時間與實際延遲判斷到達可行性，並和修復收益代價協調」。不要改成最後1／5／10秒一律禁單，不引入任意新收益保留率。

設計須分開驗收以下情況：
- 剩150ms、下單延遲250ms：在截止為300秒的前提下，無法窗內到達。
- 剩300ms、下單延遲250ms、回報延遲250ms：可能窗內成交、窗後確認，不能僅因總來回延遲超過剩餘時間就忽略其責任。
- 已送出／已成交但回報延遲的owner持續保留；不以截止或CANCEL_PENDING釋放。
- 修復改善虧損但花光正收益，應和基本合格修復分開報；不以兩側份數趨同替代收益保留。

## C．本輪不採用的修復與執行界線

RETURN_005提議False後直接進end2/drain，尚未證明可行。現有end2也忽略advance返回值；drain的EOF上界也不是native真實clock。不得移除receive_ts斷言、把未到時回報过滤後硬做reconcile、以query上界當時鐘、丟public更新／放寬frame完整性檢查來取得PASS。

即使本輪捕捉到rc1，仍先報根因與最小執行修復方案、語義影響及必要相容性矩陣，由Codex驗收後釘選下一修復包；本輪不自動套用。其他成功路徑是否曾遇rc1是UNKNOWN，不可假定全部沒有。

使用者轉交後，授權一個第二台具名診斷job：核實DESKTOP-JIERAGF／strict host key，probe→exact/global status→stage/load-only→一次submit→回收；一個heavy job、max_threads=4、一個native子程序。逾時只查狀態，不重送，不新建watcher或修改live／收集器。主機限小型純測試、只讀分析與封裝。

## 交付與停點

可寫新v32包、其具名回收目錄、ACK_006.md、RETURN_006.md及既有dispatcher必需記錄。不得改原v30/v31、Codex TASK/REVIEW、CURRENT.json或RESEARCH_CURRENT。

RETURN_006給出：有效binding證據、真正rc與前後clock、feed截止、原始journal／責任、前綴對照、尾盤主動入口表、決策時收益／時間可行性分析、根因的OBSERVED/INFERRED/UNKNOWN區分，以及一個下一步。未執行四格維持null，沒有本輪經濟晉級。

診斷成功是取得可靠原因，不是強行讓策略跑完。保留REPRODUCED_WITH_CAPTURE／PARTIAL、NOT_REPRODUCED、PREFIX_DIVERGED、INSTRUMENTATION_INVALID或OTHER_ERROR等真實狀態。完成有限診斷或遇阻塞後停止，不自動續跑第二次。
