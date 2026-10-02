# Binance Prediction：一次 ETH5M BUY UP 延遲收集

準備範圍是固定 `ETHUSDT / 5 分鐘 / BUY UP / amountIn=1 USDT / MARKET / FOK`。
實際交易由帳戶持有人執行 `submit`；自動驗證只執行離線測試、只讀行情和 quote 預覽。
預設模式為 `observe`。工具與現有策略、服務、排程和 enabled/armed 設定獨立。

## 執行

在 bot repository 根目錄、已具有 `BINANCE_API_KEY` 和 `BINANCE_API_SECRET` 的 PowerShell 執行。
使用現有 Python 環境及 repository dependencies，不輸出金鑰或 signed URL。

只讀行情與主機時鐘測量：

```powershell
python tools/run_binance_prediction_one_shot_latency_v1.py --mode observe
```

只做一次 1 USDT quote 預覽，不建單、不消耗 submit 的一次性鎖：

```powershell
python tools/run_binance_prediction_one_shot_latency_v1.py --mode quote
```

帳戶持有人執行一次實單：

```powershell
python tools/run_binance_prediction_one_shot_latency_v1.py --mode submit --confirm-buy-up-1-usdt
```

固定讀取現有唯一 Prediction wallet；如果不是恰好一個就停止。
`fundingSource=MPC`，`accountType` 使用現有
`PREDICT_TARGET_TAKER_BINANCE_ACCOUNT_TYPE`，未設定時為 `SPOT`。
這與 repository 的 Binance MARKET 路徑一致。滑價維持現有 executor 的 `1 bps`。
`amountIn` 固定 1 USDT，費用由 venue quote/結算決定；沒有額外轉帳或自動入金。
1 bps 可能造成 FOK 不成交；工具保存失敗耗時，不修改滑價或換成 GTC。

工具只選當前精確 ETH5M window、官方明確命名 UP 的 token，要求 OPEN。
window 剩餘時間不足就停止，沒有自動切市場或後台等待下單。
此時尚未消耗一次性鎖，可以由操作者在下一 window 重新啟動。

## 整輪資料

默认先收集 20 秒行情，再等下一個 Prediction 盤口作為本地主機接收 anchor。
同一條 Binance Prediction WebSocket 訂閱盤口及 MARKET BUY 成功/失敗通知；
另一條 Binance Spot WebSocket 收集 ETH `aggTrade` 與 `depthUpdate`。
HTTP 使用現有 Binance Prediction quote/place 路由與 HMAC 簽名方式。

記錄內容：

1. 前後各五個 `/api/v3/time` 樣本、RTT、時鐘 offset 和 wall/monotonic 一致性。
2. 每幀 socket `recv` 返回立即記錄 wall/monotonic，之後才解析 JSON。
3. 現貨官方 E/T、Prediction `updateTimestampMs` 盤口版本、解析耗時。
4. anchor → 固定 BUY UP 決策 → 直接 REST UP 盤口查詢 → quote。
5. 每個 HTTP 請求的簽名、request build、連線/TLS、headers/body 寫入、回覆 headers、完整 body 及 JSON 解析 marker。
6. 一次性鎖與本地持久化 → 單次 place → Binance orderId 回覆。
7. 精確 orderId 的 active/history 查詢、vendor reference、正成交量首次觀測。
8. 官方 MARKET BUY 成功/失敗推播的本地接收時間。
9. 請求結束／被拒絕／逾時後，繼續收集 20 秒行情；有 orderId 時最多查詢 20 秒。

熱路徑只記憶體記錄，不在每個行情回呼寫 SQLite。實單前的持久化延遲屬於本輪實測，
不從端到端耗時扣除。這是獨立測試工具的耗時；現有 production strategy 的決策邏輯未執行。

## 一次性與結果界線

submit 在 quote 前，以 SQLite `synchronous=FULL` 寫入固定 contract 的唯一鍵。
鎖存於 `data/research/binance_prediction_one_shot_latency_v1/once.sqlite`。
包含 quote 拒絕、place 失敗、程序中斷和逾時，均不釋放鎖。重啟／重複執行不能再送。
HTTP transport retries=0，place 不跟隨 redirect，也沒有應用層重送。
此保證以這個 repository 的持久鎖為範圍；不要刪鎖、複製另一份 repository 後重跑，或跨主機啟動第二個 submit。

Binance [官方 Trade API](https://developers.binance.com/en/docs/catalog/web3-wallet-prediction-trading/api/rest-api/trade)
目前列出 MARKET 最低約 1.5 USDT，隨流動性變化。
因此固定 1 USDT 可能收到 `-9000`。工具仍只請求 1 USDT，拒絕就完整記錄並停止，沒有加碼。
quote 必須保留 UP token、BUY、MARKET、PREDICT_FUN、同一 wallet、chain 56、1 bps，
回覆 amountIn 必須等於 1 USDT，且仍有效；不符合就不進入 place。

回覆 `orderId` 只證明已觀察到 Binance API 回覆。
`vendorOrderId` 是 vendor reference 的存在證據；其首次本地查詢時間不是 Predict 內部接受時刻。
active/history 的 `createTime / modifyTime / terminalTime` 保留為官方欄位，
不改名成 acceptedAt 或 firstFillAt。確切 Predict 接受時間及實際成交時刻保持 `null / UNKNOWN`。

[官方 Wallet Events](https://developers.binance.com/en/docs/products/w3w-prediction/websocket-api/wallet-events)
定義 MARKET BUY success 在 fully filled 後觸發；其 pushId 的 refId 為 OrderHistory.orderId。
工具只按這個精確 ID 配對，不按金額、市場標題或相近時間配對。
通知可能比 HTTP 回覆先到，因此允許相對 ack 的負延遲；不截成零。
通知到達時間包含業務處理／發布／網路延遲，不等於匹配引擎的成交時刻。
沒有 canonical ack ID 的逾時結果保持 UNKNOWN，不猜測哪筆新訂單是這次交易。

[官方盤口文件](https://developers.binance.com/en/docs/products/w3w-prediction/websocket-api/orderbook)
把 `updateTimestampMs` 定義為報價更新時間；這是版本到主機的 age，包含上游與發布等待。
它不是 Binance 發包時間，所以 `prediction_transport_latency_ms` 保持 UNKNOWN。
現貨 E 到主機是事件到接收估算，亦包含伺服器／發布延遲。
時鐘使用最短 RTT 樣本的 midpoint 模型，約 ±RTT/2 + 1 ms；這是模型誤差，沒有保證網路對稱。
不直接將本地 wall clock 減去 E，也不將估算當成精確單程網路延遲。

## 輸出與只讀復查

每次輸出在 `data/research/binance_prediction_one_shot_latency_v1/<run_uuid>/`：

| 檔案 | 用途 |
| --- | --- |
| `SUMMARY.json` | 行情 n/median/p95、時鐘模型、整輪及各 HTTP 階段毫秒、UNKNOWN 欄位 |
| `timeline.json` | 所有 marker 的 wall/monotonic 與 row sequence；僅本地，實單時間屬私人資料 |
| `PRIVATE_REFERENCE.json` | 僅本地：wallet、market/token、quote/order ID、目前 stage；不可上傳 |
| `HOST_PRIVATE.json` | 僅本地：實際執行主機、PID 和路徑；不可上傳 |

持有人在 place 後重啟，只能以原 run directory 做只讀復查：

```powershell
python tools/run_binance_prediction_one_shot_latency_v1.py --mode reconcile --run-dir "data/research/binance_prediction_one_shot_latency_v1/<run_uuid>"
```

復查不做 quote/place，不消耗或重設鎖。若主機重啟或時鐘連續性不成立，不跨 clock domain 計算毫秒。
沒有 orderId 時只回報無法精確綁定；原始 UNKNOWN 不會轉為成功。

公開分享只使用經核對的匿名摘要；不要整個 output directory 推上 research-data。
這次驗證沒有執行 submit，沒有實單成交結果。

## 離線驗證

```powershell
python -m pytest -q tests/test_binance_prediction_one_shot_latency_v1.py
```

測試以 MockTransport 驗證並行持久鎖、quote 金額/方向/路由約束、拒絕／逾時不重送、
只讀模式不可寫入、精確 canonical 配對、通知先於 ack，以及敏感資料不進入 timeline/摘要。
