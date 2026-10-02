# BTC5M 新場批次 02：15 場

第二批固定 15 場，ID >2807162；與既有519、新70及批次01的15場不重疊。清單在查詢官方勝方前固定，依已結束BTC5M場的market_id由小到大選場，不依損益或勝方。兩批累計30場，原定至少100場仍差70場。本批不執行候選判定。

每場 events.npz 含 data/local_times_ms，沿用凍結 HFT244 feed V1。原始／匿名事件與wakeup位元組逐場相同，3場既有驗收樣本相同；未執行native引擎。盤口 exchange/local 時鐘均使用 receivedMs；公開成交使用 executedAt 加既有 mid 人工排序偏移。這個偏移不是實測延遲。保留原轉換的開場前公開成交，不另裁切。事件ns、wakeup及來源metadata ms。

標籤位於 ../../labels/FRESH_AFTER2807162_BATCH02_LABELS.json；來源證據位於同級 LABEL_PROVENANCE.json。15場均來自官方GET市場結果，RESOLVED/SETTLED、唯一明示WON UP/DOWN且與具名resolution一致；不用最後中價推斷。

META.json 與 BATCH_VALIDATION.json 逐場記錄source和received覆蓋／空窗。5000ms檢查為描述，不是完整逐筆成交證書。公開成交收集完整性為UNKNOWN，不能宣稱無漏單或真實延遲已量測。索引可找到全部輸入與標籤。只发布數值陣列、公開metadata、轉換程式及hash，不發布原始tape、地址、帳戶、訂單或交易識別資訊。
