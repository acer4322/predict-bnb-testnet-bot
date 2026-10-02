# DEEP stage1：續跑剩餘9場完成，合計固定10場

使用者在原job停止後明確授權「直接完成其他9場」。本套件只包含原job的9條not_started，首場2671717與BASE全部重用，沒有替補或重跑。原 `btc5m_deep_layer_stage1_20261002_v1`、failed job、原AUDIT錯誤、凍結parent/base與原規格保留。

## 修正範圍與 worker

唯一審計修正是把pending cash讀取改為 `result.clock_smoke.final_pending_cash_direct`，並加本機fixture的public input路徑fallback；worker只調整9路清單與獨立scratch namespace。`deep_layer.py`、sizing、overlay、passive_offset、risk_floor、governor、hard_stop均與原套件byte相同，凍結價格／票量／TTL／CG1／STOP290／floor等規則未改。env與原DEEP9/9相同；與CG1AT相比唯一新增DEEP=ON。原首場真實結果的修正審計回歸PASS、9場BASE schema核對PASS，本機元件與disabled identity/replay PASS；0主機native HFT／fit。

續跑job **btc5m-deep-layer-stage1-rest9-20261002-v1**：strict host key／DESKTOP-JIERAGF身份、probe、exact/global status、stage／hash／load-only通過。一次submit，max_threads4／native每路1；最終 **succeeded／rc0**，worker elapsed **147.12秒**，9路全部 COMPLETE。load-only native_executed0。收集後218原輸出檔跨機sha256全相同、nonterminal空。終局readback曾SSH reset／一次連線逾時，後續probe喚醒成功並完成hash；沒有重送job。原首場未重跑，原failed job仍failed。

## 固定十場結果

全部10場機制AUDIT及嚴格pre-NEW parity為PASS，作廢0。首場採獨立事後AUDIT，原PATH_ERROR不覆寫；其他9場正常AUDIT。

|market|audit／parity|DEEP單|有成交單|成交股數|raw active_matches_opportunity|
|---|---|---:|---:|---:|---|
|2671717|PASS／PASS，重用首場|51|12|166.11|true|
|2671719|PASS／PASS|29|7|105|false|
|2671768|PASS／PASS|10|1|15|true|
|2671808|PASS／PASS|159|34|509.736792|false|
|2671979|PASS／PASS|10|4|60|true|
|2672208|PASS／PASS|29|4|60|true|
|2672226|PASS／PASS|23|8|120|true|
|2672244|PASS／PASS|5|0|0|true|
|2672250|PASS／PASS|49|15|225|false|
|2672431|PASS／PASS|109|22|330|true|

**parity限制：十場首個DEEP NEW皆在第一個策略plan；嚴格之前的前綴全為0筆。因此PASS全為空前綴，沒有非空pre-NEW逐筆一致證據。** 本機inert是env OFF的source identity與既有BASE全場計畫逐字段重播，不是新增native OFF回放，沒有把854条舊receipt當新驗證。原10場inert記錄與本續跑9場皆完整保留；不得擴大宣稱新增native inert全場已跑。

## 必過機制項目

|項目|10場結果|
|---|---|
|凍結中DEEP NEW＝0|10/10 PASS|
|290秒後DEEP NEW＝0|10/10 PASS，沿用STOP290全撤|
|risk floor違規＝0|10/10 PASS|
|owner／pending全部終局|10/10 PASS，unresolved0、carriers TERMINAL、pending cash0|
|V8誤撤深層單＝0|10/10 PASS|
|每邊最多一個nonterminal DEEP|10/10 PASS，含cancel pending及同畫面CANCEL|
|固定bid−2tick／15股|10/10 PASS|
|trace與canonical fills對帳|10/10 PASS|
|TTL與部分成交責任|10/10 PASS，cancel/fill race按canonical received時點判讀|
|capital_cap=null|10/10 PASS|
|無Target或winner runtime|10/10 PASS|

max-one僅限DEEP；既有BASE可以有同側其他單，且原risk／governor計算仍保留未terminal DEEP。不能把BASE NEW解讀成DEEP提前重掛，也不新增全體同側NEW等待DEEP終局的規則。trace提供最終terminal狀態，沒有單獨記錄精確cancel terminal transition時間。

獨立來源與逐路審查完成，9/9續跑路徑的11項必過條件與原AUDIT一致。無反轉路徑沒有啟用後翻轉floor檢查，該子集為空，不能視作測到floor生效。十場合計474單、179筆canonical fill，以每單NEW的唯一key及receipt sequence/order_id逐筆核對，時間、該邊價格、股數、maker與事件數均吻合；重複事件tuple保留，未跨單借用receipt。見 `INDEPENDENT_REVIEW.json` 與 `RECEIPT_READBACK.json`。

續跑19單、首場4單，共23單在TTL CANCEL後才收到canonical fill（續跑18單最終全成、1單最終部分成交）。沒有任何一筆在取消當下已收到，不能用最終成交量倒推取消時已知的partial fill。

舊 `active_matches_opportunity` 保留raw結果：DEEP三場false為2671719、2671808、2672250；BASE三場false為2671719、2672208、2672250。這不是所有舊門檻通過；沒有改寫safety_gate.pass。這些是完整結果中的原欄位，不作經濟判定。

## 嘗試／成交／被擋統計

十場合計28,506個side-frame嘗試，474張實際深層單、107張有成交，成交率22.57384%，成交股數 **1590.8467924528302**。每場股數見表。TTL撤單380次。這些只作機制描述。

|分類|次數|
|---|---:|
|成功NEW|474|
|CG1凍結|6900|
|STOP290|736|
|risk floor|12012|
|self-cross|20|
|維護／同價衝突|60|
|governor|0|
|已有nonterminal DEEP|5336|
|低於0.01|1152|
|venue validation|394|
|缺簿|1422|

trace每單一筆，匿名序號、實體邊自己的price／best bid、place/cancel ms、canonical fill exchange/receive ns與ms、成交價格股數及terminal；不包含地址、account id、order hash或Target私人資訊。

## 同步與研究界線

完整20路（10DEEP＋10CG1AT BASE）、20公開簿與原十場labels；不按FLIP選擇，4場無FLIP BASE已包含。dry-run **14.8MB**，FLAGGED0；121個source資料檔另查非空識別欄位與地址字串無命中。保留所有既有labels／Target INDEX metadata，透過獨立git worktree推到research-data commit **ef8e285038c958f4951f0c41e3a7613024c85ab4**。本輪142檔（141路徑資料檔＋1標籤）遠端sha256全吻合；原3份labels、target索引與其他entries保留，remote INDEX150路徑。逐檔遠端hash見 `SYNC_READBACK.json`。

重疊核對原報告維持NOT_EQUIVALENT；先前WL60約−30／WL90約−36、V58深價位與BTC15M階梯負面結果仍在先前報告，未因本批覆寫。250/250ms、risk queue、零費率與2秒TTL對掃單／反彈的真實成交相容性未驗證，不能外推可實盤。深層單也會透過共用OWN庫存、pending與own-cross改變原通道。

0 fit／live／armed／stake／服務／排程／collector／凍結parent/base變更。沒有計算經濟主指標、區間、判準或結論。完成固定十場的執行與機制核對，**不自行進stage2**；空前綴與native inert未重跑的證據限制保留。
