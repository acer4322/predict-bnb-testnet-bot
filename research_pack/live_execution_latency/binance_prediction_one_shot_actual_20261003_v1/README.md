# ETH5M Binance Prediction：一次真人執行的 MARKET BUY UP

已收到完整資料：固定 amountIn=1 USDT、BUY UP、MARKET/FOK，只有一次 quote 和一次 place。
原執行紀錄含精確 orderId 的官方 history FILLED、正成交量與買入成功推播；
另外以只讀官方 API 再查同一 order，確認 UP、BUY、MARKET、PREDICT_FUN、FILLED 與 fillPercentage=1。
amountIn 是請求預算，實際淨成交 USDT/份數與費用屬私人帳戶欄位，不列入公開包。

| 階段 | 本次測量 |
| --- | --- |
| Binance Spot ETH aggTrade 官方 E 到本地主機接收 | 中位數 22.671 ms；p95 25.606 ms |
| Binance Spot depthUpdate 官方 E 到接收 | 中位數 21.400 ms；p95 25.714 ms |
| Prediction 報價版本到主機 | 中位數 105.249 ms；p95 199.309 ms |
| 接收 Prediction 盤口 → 開始 place | 226.410 ms |
| Quote 呼叫 | 92.100 ms |
| Place 呼叫 → Binance orderId 回覆 | 275.874 ms |
| 接收 Prediction 盤口 → Binance 回覆 | 502.284 ms |
| 接收盤口 → 收到官方買入成功通知 | 1526.846 ms |
| 接收盤口 → REST 首次觀察 FILLED | 2155.508 ms |

現貨 E 到主機是校時後的事件 age，包含發布／伺服器延遲，並非已證明的純單程網路延遲。
前後時計模型誤差約 ±22.2 / ±22.9 ms，offset 漂移 -1.515 ms；主機 wall clock 約快 1.21 秒。
本地主機內的端到端間隔用 monotonic clock，不套用上述 ±23 ms 的跨機校時誤差。
Prediction 的 updateTimestampMs 是報價版本時間，105 ms 包含上游／發布等待，不標成 transport latency。

本次 502 ms 包含直接 REST UP 查簿、quote、一次性鎖與 reference 持久化及 place 回覆，
是獨立測試工具的路徑；未執行現有 production strategy 的決策邏輯。
官方成功通知在 fully filled 後觸發，1527 ms 是成功通知抵达主機的觀測時間。
exact Predict acceptedAt 與真正撮合時刻沒有官方時間欄位，保持 UNKNOWN。
createTime/modifyTime/terminalTime 不改名為接受／第一筆成交時間。
只有一次訂單樣本；行情的 n/median/p95 不代表多筆交易的訂單延遲分布。

資料約 43.997 秒，1283 個記錄、198 幀 Prediction、270 個 aggTrade、419 個 depthUpdate，WebSocket 異常 0。
成交成功通知後仍保留約 21 秒收集。一次性鎖已消耗，不能再執行 submit，也沒有自動重送。

`SUMMARY.json` 是毫秒摘要；`TIMELINE_RELATIVE.json` 保留每個 event/HTTP trace 的相對時間；
`STAGES.json` 是關鍵順序；`CANONICAL_VERIFICATION.json` 是匿名官方回查證據；
`COLLECTION_INTEGRITY.json` 保存筆數、時間域及私人原件雜湊；`SHA256SUMS.json` 核對公開檔案。
原始實單 tape、wallet/order/quote/token ID、API credential、signed URL、主機身份及絕對實單時間不在公開包中。

來源：[官方 Trade API](https://developers.binance.com/en/docs/catalog/web3-wallet-prediction-trading/api/rest-api/trade)、
[官方 Wallet Events](https://developers.binance.com/en/docs/products/w3w-prediction/websocket-api/wallet-events)、
[官方 Prediction Orderbook](https://developers.binance.com/en/docs/products/w3w-prediction/websocket-api/orderbook)。
測量工具準備版本：05eb180e8002a7669080f53f005a980338b66f51。
