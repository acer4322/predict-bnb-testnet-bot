# 現貨延遲吃單：影子模式規格（2026-10-03，預先宣告；不下單）

## 目的
在即時環境（直連 Predict.fun 原生盤口＋Binance 現貨 WebSocket）確認：現貨大幅移動後，Predict 賣價在我們可達的延遲內（直連實測 100–325 ms）仍然陳舊，且扣 2% 手續費後的短期價差為正。歷史重播（519 場）與一次性判定（219 場）已通過，影子模式是即時環境的獨立檢驗，也是任何實單探測的前置條件。

## 收集（tools/spot_latency_shadow.py）
- 只讀：Predict 讀取用 API key（`PREDICT_FUN_API_KEY`，與 `src/predict_bot/predict_fun_observer.py` 相同），**不載入任何下單／簽名／錢包程式**。
- 每個 5 分鐘桶自動找當前 BTC（預設）市場，訂閱 `predictOrderbook/<marketId>`；記錄每幀前 10 檔、`updateTimestampMs`、主機收到時間。
- Binance `btcusdt@aggTrade`：記錄 E、T、價格與主機收到時間。
- 每小時一個 `data/shadow_spot_latency/shadow_<ASSET>_<UTC小時>.ndjson.gz`；終端每 30 秒顯示幀數與「會觸發的訊號數」。
- 執行：`python tools/spot_latency_shadow.py --asset BTC --hours 6`（建議連續 ≥6 小時，目標 ≥60 個有 ≥3 bp 訊號的市場，約 ≥100 個 ≥3 bp 訊號）。

## 分析（tools/spot_latency_shadow_report.py）
- 訊號：近 500 ms 現貨對數報酬 ≥X bp（每 100 ms 檢查，市場第 15–285 秒，冷卻 2 秒），X ∈ {1,2,3}；買現貨方向那側。
- 執行價：訊號＋L 時該側賣方階梯吃 15 股的 VWAP（DOWN 賣價＝1 − UP 買價），L ∈ {0,100,165,250,325,500} ms；手續費＝名目 2%。
- 指標：+1 s／+5 s 側中價減成交價（扣費），市場自助 95% CI；到結算（給 `--labels` 官方標籤，否則以結束幀中價推斷並標記 INFERRED）；「執行時賣價仍等於訊號時賣價」的比例（陳舊持續率）。
- 兩個視角：
  - **EXCH**（主判定）：訊號用 Binance 成交時間 T，盤口用 Predict `updateTimestampMs`，與歷史重播完全同法（假設兩交易所時鐘一致）。
  - **LIVE**（輔助）：訊號用我方主機收到現貨的時間（含真實 Binance 行情延遲），主機時間以 o＝(收到−updateTimestampMs) 第 1 百分位換算成 Predict 時間。因 o 含 Predict 最小傳輸時間，LIVE 偏樂觀約該傳輸量；**絕不可用本機收到的盤口本身判斷**（本機盤口落後交易所，會製造假的陳舊報價）。
- 自我測試：以 70 場歷史公開簿＋aggTrades 轉成本格式，EXCH 結果與歷史重播一致（X=3、L=250：+4.25 分 [+0.65,+8.42]）。

## 判定（事先宣告，一次性）
- **主判定**：EXCH、X=3 bp、L=250 ms，有訊號市場 ≥60，扣費 +5 s 價差 CI 下限 > 0 ⇒ PASS。
- 另報（不改判定）：X=1/2/3 × 全部 L、LIVE 視角、+1 s、到結算、陳舊持續率、Predict 與 Binance 行情延遲分佈。
- PASS 後才進入最小金額實單探測（需使用者另行批准）：每訊號 1 USDT、保持連線、總上限例如 50 筆，記錄 ACK／orderAccepted／實際成交價與費用，對照影子同時刻的預期。FAIL 則停止此方向，不換參數重跑。
