# DEEP stage1 v1：首場回放完成，原 job 審計失敗；階段未完成

使用者固定十場只新增 DEEP，BASE 全部重用 fresh100a CG1AT。沒有等價的既有已否決模組；WL 為弱側多層、庫存比限制及 FLIP 退休，DEEP 為兩個實體邊各一張、bid−2 tick、15股、未成交 TTL2000ms。先前 consumed40 的 WL60約−30／WL90約−36、V58深價位及BTC15M階梯的負面結果仍保留；本報告沒有計算經濟指標或判準。

## 執行與失敗

- 分支先 pull 至 `1a684f1cc76fa6972da61d9bcb4ed3afcc14078e`；新套件 `btc5m_deep_layer_stage1_20261002_v1`，parent58檔、本機與遠端 frozen base 雜湊核對，CG1AT10組env逐字相同，唯一新增 `V12G_DEEP_LAYER=ON`。
- strict host key／`DESKTOP-JIERAGF` 身份、probe、exact/global status、stage/hash/load-only均通過。第一次 SSH連線逾時後再probe成功；不是submit逾時。load-only native_executed=0。
- 正式 job `btc5m-deep-layer-stage1-20261002-v1` **一次 submit**，max_threads4／每路native1；最終 **failed／rc2**，`STOPPED_FIRST_PATH_ERROR`。worker job27.898秒、worker.py27.626秒、首場subprocess26.707秒、引擎報告25.325秒。
- 首場2671717的 native `result.status=COMPLETE`／subprocess rc0。原 `deep_audit.py` 錯讀根目錄不存在的 `final_pending_cash_direct`，正確位置是 `clock_smoke.final_pending_cash_direct`。首場 gate 因這個審計例外停止，其他9場未啟動。這是本次實作錯誤，非native失敗或經濟停止。
- 原 source、MANIFEST、RESULT、AUDIT與failed狀態都保留。另存 `post_collection/deep_audit_v1r1.py`、`AUDIT_POST_COLLECTION.json`，只用已收集資料重新審计，沒有回放、重送或第二job。
- terminal後collect一次，25個原輸出遠端／本機sha256全相同，global nonterminal／其他重研究process空。

## 本機 inert 與各路徑 parity

12項元件檢查PASS：雙邊／固定價格票量、TTL2000、cancel pending責任、部分成交保留、凍結、STOP290、governor、risk floor、同價、native own-cross、V8维护跳過／owned扣除、post-install mutable globals。没有主機native HFT。

env OFF的 instrumentation 是原 frozen manager source 的byte identity，`on_plan`与`extend`均直接返回原ops物件。对十条既有BASE路径14,284个全场plan／1,940个operation逐字段重播PASS；原记录含854条receipt，receipt-producing source未改变。**这是本机停用identity与既有记录上的inert验证；没有新增env OFF native全场重播，也不把旧receipt计数当新receipt证据。**

| market_id | 本機 inert identity/replay | DEEP pre-NEW parity | native狀態 |
|---|---|---|---|
|2671717|PASS|PASS，空前綴|COMPLETE，事後審計PASS|
|2671719|PASS|NOT_RUN|NOT_RUN|
|2671768|PASS|NOT_RUN|NOT_RUN|
|2671808|PASS|NOT_RUN|NOT_RUN|
|2671979|PASS|NOT_RUN|NOT_RUN|
|2672208|PASS|NOT_RUN|NOT_RUN|
|2672226|PASS|NOT_RUN|NOT_RUN|
|2672244|PASS|NOT_RUN|NOT_RUN|
|2672250|PASS|NOT_RUN|NOT_RUN|
|2672431|PASS|NOT_RUN|NOT_RUN|

首場第一個DEEP NEW在第一個策略plan畫面，時間1790551800990ms；嚴格之前的plans、actions、states、observations、direction_rows、events、receipts與native calls都為0筆。規格的strict-before gate形式上PASS，但沒有非空前綴可驗證，不能宣稱已觀察非空pre-NEW路徑一致。其餘9場不是作廢替補，而是未執行／UNKNOWN；本輪不補送。

## 必過項目與深層統計

下列只描述首場2671717；其他9場全部NOT_RUN，**階段1整體尚未通過**。

|項目|首場事後結果|
|---|---|
|凍結中DEEP NEW|0，PASS|
|290秒後DEEP NEW|0，PASS，沿用STOP290全撤|
|risk floor違規|0，PASS，原ON guard全計畫檢查|
|全部owner／pending終局|PASS，unresolved0，所有carriers TERMINAL，兩邊reserved_cash／qty0|
|V8誤撤DEEP|0，PASS|
|每邊至多一個nonterminal深層owner|PASS，包括cancel pending與同畫面CANCEL|
|固定bid−2tick／15股、trace與canonical fills對帳|PASS|
|TTL、部分成交保留|PASS，41次DEEP_TTL撤單；未用cancel request釋放責任|
|capital_cap=null、無Target／winner runtime|PASS|

首場2974個side-frame嘗試，實際51張DEEP；12張有成交（23.53%），合166.11股。每單一筆trace，包含實體邊自己的價格／best bid、place/cancel ms、canonical fill exchange/receive ns及ms、成交股數價格與terminal狀態。

4張TTL撤單最終仍有成交（3張全成交、1張部分1.11股），均是cancel/fill延遲交錯：取消決策當時canonical fill尚未收到、owner remaining仍15；其中1張exchange fill已發生但receive晚於取消61ms。不能用最終成交量推定取消時已知部分成交。這4張owner最終都TERMINAL；有後續同側DEEP的2張，其新單也晚於canonical fill回報，逐計畫nonterminal最大一張檢查PASS。資料沒有單獨記錄cancel terminal transition的精確時間，不能補造。匿名時序另存`post_collection/CANCEL_RACE.json`。獨立只讀審查另存`INDEPENDENT_REVIEW.json`，51組order/carrier與20組canonical fill全部匹配。

|嘗試分類／被擋原因|次數|
|---|---:|
|成功NEW|51|
|CG1凍結|116|
|STOP290|98|
|risk floor|2131|
|own-cross|0|
|維護／同價衝突|5|
|governor|0|
|已有nonterminal DEEP|573|
|低於0.01／venue validation／缺簿|0／0／0|

首場raw `active_matches_opportunity=true`；十條重用BASE該舊欄位仍有3場false（2671719、2672208、2672250），原資料一字不改。沒有宣稱所有歷史安全門檻都通過，也不將這個舊欄位改寫。

## 同步與限制

同步可用的1 DEEP＋全部10 BASE（不依FLIP篩選），以及固定十場offline labels。dry-run為11路徑／7.9MB／11 public books，FLAGGED0，另查非空識別欄位及地址字串無命中。pack保留原AUDIT的PATH_ERROR，另附事後AUDIT；deep trace匿名order_seq，不含地址或訂單hash。

同步腳本加入deep trace／事後AUDIT、可重複`--arm`以選CG1AT及DEEP；在copy之前保存舊INDEX，保留既有labels與target metadata。隔離臨時git仓庫實際測試PASS，不推送測試到專案遠端；正式發布經獨立git worktree，不改主工作樹。research-data commit **23dc1fa7567b581d7c3fe18a4380bea5beb300ff**，70個本輪檔案遠端sha256全部吻合；既有2個labels、target索引與其他entries保持原樣。remote INDEX現有141路徑（原136＋4條原pack缺少的non-FLIP BASE＋1 DEEP）。BASE中無FLIP的2671808、2671979、2672244、2672431都已納入。詳細readback存`SYNC_READBACK.json`。

V49是250/250ms與risk queue、零費率；TTL是送出NEW的策略時間起算、到期請求CANCEL，回應未terminal不重掛。2秒單的快速掃單／反彈與真實場館成交相容性未驗證；不外推可實盤。深層單與原通道共用own-cross、pending及成交後庫存，會改變原BASE行為。

0 fit／live／armed／stake／服務／排程／collector／凍結parent或base變更。未做M1/M3、未下經濟判定，未進stage2。完成整個十場需要另有授權處理9條未啟動路徑；本輪不自行另開job。
