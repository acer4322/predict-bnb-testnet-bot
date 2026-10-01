# BTC5M 新市場第一批：15 場事件＋官方真標籤

使用者於本輪指定「先推目前可用批次」。本批固定採先前已備好的15場，ID 2807193–2810203（完整ID見 INDEX/SELECTION），全部 >2807162，與現有519場及新70場均無重疊。這是原 >=100 任務的第一部分：15組完成、仍缺85；不宣稱100場完成，也沒有自行評估凍結候選參數。

research-data 路徑 research_pack/events/hft244_fresh_after2807162_20261002_batch01/markets/<id>/events.npz；標籤在 research_pack/labels/FRESH_AFTER2807162_BATCH01_LABELS.json，逐場官方證據在同目錄 FRESH_AFTER2807162_BATCH01_LABEL_PROVENANCE.json。標籤 records 僅含 market_id/winner，可逐ID配對。

同格式 NPZ 鍵 data、local_times_ms；事件是64-byte aligned固定8個數值欄位 ev/exch_ts/local_ts/px/qty/order_id/ival/fval，後3保留欄位全0；事件時間 ns、喚醒時間 ms。使用先前已公開且凍結的 feed V1 純Python定義，簿用 receivedMs 作exchange/local clock；公開成交 executedAt 用mid半秒偏移與同秒組內微秒排序。人工偏移不是實測延遲。所有正規化公開成交依V1保留，包括視窗前後成交，META列出筆數。

原始/匿名化轉換與NPZ回讀15/15 byte parity PASS，前包3場golden inputs parity PASS。唯一批量wrapper調整是沿已使用的graduation title parser接受起點省略AM/PM或:00的五分鐘BTC標題；事件建構函式與參數未改。converter_source/五份來源SHA保持原樣。沒有native replay、fit、worker、資料庫修改、collector或live變更。

官方15/15目前全已RESOLVED，UP/DOWN恰一個outcome明確WON，且與官方resolution一致，0 pending/ambiguous/API failures。provenance只含公開結算欄位allowlist、官方URL、本機fetched_at_ms與response SHA；查詢時間不是官方實際結算時間。沒有最後中價或價格推斷。標籤只供offline evaluation，不進事件/策略runtime。

資料限制：source開頭/尾端/中間缺口<=5000ms檢查15/15通過；received clock檢查14/15通過。2809834有13273ms received缺口，保留事件且在INDEX/META/BATCH_VALIDATION明列。公開成交捕獲完整性沒有分頁/場地對帳證書，仍為UNKNOWN。補真標籤不修復盤口缺口。

```text
python verify_npz_batch.py --root .
```

這是NumPy-only完整性回讀，不載入native引擎。SHA256SUMS保護本包檔案；原始含交易/訂單/帳戶識別資料的tape未發布。
