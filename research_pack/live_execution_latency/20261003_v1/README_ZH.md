# 歷史實單延遲資料：匿名匯出 20261003_v1

以前的實單紀錄仍在。本包從六個本機 SQLite 歷史資料庫建立唯讀快照，再匯出匿名的逐筆延遲、送單結果與成交狀態證據。沒有重新下單、啟動實測、改動服務或 live 設定。

這批測量走 **Binance Prediction REST**。`poly_gap_live*` 用 Polymarket 公開行情產生訊號，但向 Binance Prediction 讀盤口和送單。ETH 歷史紀錄不是目前 Predict.fun ETH5M 直連被動掛單的速度驗收；實單回傳、撮合接受與逐筆成交是不同端點。

BTC／ETH／BNB 是原歷史 DB／服務分組；`ASSET_BINDING.json` 保留 repository 的 DB↔symbol 對照及已保存的資產 runtime 事件證據。這裡未逐市場重查官方 symbol crosswalk，也不宣稱設定是目前生效的 runtime。

## 可以回答的實際毫秒數

全部數字是保留下來的實測紀錄，單位 ms。中位數與 P95 以完整合格資料計算，沒有按速度挑選。括號為 P95。

| 歷史鏈路／訂單 | 有完整時鐘的筆數 | 收到盤口到開始呼叫下單 | 收到盤口到下單函式回傳 |
| --- | ---: | ---: | ---: |
| ETH `poly_gap_live_eth`，BUY MARKET/FOK | 119 | **133.0（220.0）** | **424.0（587.4）** |
| BTC `poly_gap_live`，BUY MARKET/FOK | 183 | 132.0（241.4） | 403.0（598.5） |
| BNB `poly_gap_live_bnb`，BUY MARKET/FOK | 92 | 129.0（181.35） | 408.5（511.9） |
| `live_m0w` 普通訊號 LIMIT/GTC，保存訊號事件起點 | 456 | 231.1203（927.7581） | 501.50135（1198.975175） |

ETH 的回傳延遲最小 340、最大 891 ms；P99 699.04 ms。下單函式本身耗時中位數 282.1689、P95 397.3690 ms，quote 呼叫中位數 108.2179、P95 185.0018 ms。不同分段的中位數不能相加當成端到端中位數。

`live_m0w` 的 487 筆 SUBMITTED telemetry 都有 ledger 訂單 ID 與保存的 API response.orderId 一致。最終 API／ledger 狀態為 468 FILLED、14 EXPIRED、5 CANCELLED；FILLED 的 API filledShareQty 為正。31 筆 confirmation-add 的起點是在加倉訊號建立時重新取時鐘，另成一組，沒有混入 456 筆普通訊號主表。普通訊號的 456 筆包含 437 最終 FILLED，仍保留未成交訂單以避免只看成功成交。

**收到盤口到真正第一筆／完全成交：UNKNOWN。** 已保存成交狀態，不代表保存了相同時鐘上的逐筆成交時間。API createTime／modifyTime／terminalTime 存在於私有快照，但不能用它們直接減本機收到盤口的時間；本機與 API 時鐘未在每笔留下可核驗的同步證據。沒有用回傳時間、updated_at、持倉輪詢或最後中價代替成交時間。

## 時鐘與端點契約

| 欄位 | 實際測量邊界 | 限制 |
| --- | --- | --- |
| gap `book_receipt_to_place_call_start_ms` | direct orderbook GET 回傳後的本機 observedAtMs → 進入 place_market_order | time.time() 毫秒差；不含收到盤口前的 GET 耗時，也不是 socket 寫出時間 |
| gap `book_receipt_to_place_call_return_ms` | 同一盤口收到時點 → place_market_order 回傳／例外 finally | 必須按 outcome 分組；本包主表只用 BUY SUBMITTED 且有訂單 ID 與完整時鐘 |
| gap `place_rtt_ms` | 下單函式入口 → 回傳／例外 | monotonic 耗時，含簽名、client／HTTP 處理與 response parse；不是純網路 RTT |
| m0w `marketEventToPlaceStartMs` | 保存的 market_event_received_monotonic → 呼叫 place_limit_order 前 | 同程序 monotonic；普通事件源由目前 producing source 支持為可執行盤口事件，但 telemetry 未保存每筆 trigger stream／當時 binary hash |
| m0w `eventToPlaceResponseMs` | 保存事件起點 → 下單回傳或最後失敗 | quote reject／本地 block 的端點是失敗結束，不是下單回應 |
| m0w `totalMs` | live enqueue → 回傳／最後失敗 | 起點晚於盤口事件，不能當作盤口端到端 |
| engine `attempt_to_last_result_ms` | attempt → 保存的最後 result／reconciliation | 可包含稍後修復／輪詢；不是盤口到成交，也不列入速度主表 |

已核對 `eventToPlaceResponseMs = marketEventToPlaceStartMs + placeNetworkMs`。client 下單入口後仍會組參數、取簽名時間、簽名，再呼叫 HTTP；缺少精確 wire-send、matching-engine accept、first-trade、full-fill 時鐘，這些欄位保持 null。

wall-clock 差有毫秒取整與時鐘調整限制；沒有把 wall-clock 差宣稱成 monotonic 精度。主表沒有把收到前的 feed／GET 傳輸延遲加進去。

## 檔案與覆蓋

保留各來源的所有原有紀錄，失敗、拒絕、缺時鐘與 UNKNOWN 也保留。只在計算指定端點的統計時限定必要證據；不同資料表可能描述同一訂單，不應把所有 row 數相加當成獨立實單數。

| 檔案 | 內容 |
| --- | --- |
| `entries.ndjson.gz` | 3,925 筆 `live_m0w` entry ledger，含未送單／本地阻擋／缺 telemetry |
| `attempts.ndjson.gz` | 上述 ledger 中 532 筆具有分段 telemetry 的紀錄，與 entries 的 order_ref 相連 |
| `manual_exits.ndjson.gz` | 41 筆歷史 exit，缺盤口到送單時鐘；33 筆 ledger FILLED |
| `gap_attempts.ndjson.gz` | BTC 461、ETH 232、BNB 192，共 885 次 BUY／SELL 嘗試 |
| `gap_rounds.ndjson.gz` | 2,629 筆匿名 round 狀態與時鐘 presence；round 不是 market 數 |
| `shotgun_orders.ndjson.gz` | 23 筆歷史分層訂單；沒有盤口起點，不作端到端統計 |
| `engine_orders.ndjson.gz` | 64 筆結果，31 SUBMITTED 有 vendor ID；保留各項 reconciliation 證據，SUBMITTED 不一概代表 FILLED |
| `canary_runs.ndjson.gz` | 301 次監視／quote／dry-run／live 分支，1 次 PLACEMENT_INCOMPLETE_MANUAL_RECONCILE 保持未解；不把其他監視記錄當實單 |
| `SUMMARY.json`、`ADDITIONAL_SUMMARY.json` | 全體、分策略、分 outcome 的 n／min／median／p90／p95／p99／max／mean |
| `COVERAGE.json`、`ADDITIONAL_COVERAGE.json` | 選樣、日期、缺時鐘與各狀態計數 |
| `PROVENANCE.json`、`ADDITIONAL_PROVENANCE.json` | 快照與 producing source 的 SHA256、來源與時鐘邊界 |
| `VALIDATION.json`、`INDEPENDENT_REVIEW.json` | 匯出驗證及獨立核對結果 |
| `SHA256SUMS.json` | 檔案 bytes／SHA256；此 manifest 自身不列入自身雜湊 |

日期範圍：m0w ledger 2026-07-18～08-20 UTC，telemetry 08-01～08-11；gap BTC 08-10～08-19、ETH 08-12～08-19、BNB 08-12。這是既有歷史紀錄覆蓋，不宣稱跨缺口的連續市場採樣。

cap100 drills／stress／replay、native HFT 模擬、paper xpair trials、空的 poly_fast_live 資料表與純行情 route probe 不充當實單延遲資料。只匯出白名單：匿名順序代號、UTC 日期、結果、證據旗標與耗時。精確壁鐘時間、帳戶／wallet／真實 order／token／market ID、價格、數量、資金、原始 API payload、自由文字及憑證留在私有快照。

## 重算

公開 NDJSON 已足以重算統計，分位數定義是 `(n-1)*p` 線性插值。若持有六個私有唯讀快照，可用附帶的兩個離線 exporter 重建匿名檔案；程式不匯入交易 client、不連 API、不寫 live DB。

```powershell
python export_live_execution_latency.py --snapshot <private-live-m0w-snapshot> --output <new-output-dir> --source-root <source-checkout>
python export_additional_live_latency.py --snapshot-dir <private-snapshot-dir> --output <new-output-dir> --source-root <source-checkout>
```

API 欄位參考：[Binance 官方 Prediction Trade API](https://developers.binance.com/en/docs/catalog/web3-wallet-prediction-trading/api/rest-api/trade)。官方文件提供 order status、createTime、modifyTime、terminalTime；本包的毫秒結論来自歷史紀錄，沒有把文件範例當成測量。
