# Binance Prediction Trading：BTC 5 分鐘模擬交易＋M0W 實單機器人

這一版直接讀取 Binance 正式 Prediction Trading API，來源是 Binance 包裝的 Predict.fun 主網市場。A～M 的研究帳本仍為模擬交易；另有一個完全分離的 M0W 正式實單執行器與監視頁。實單只有在明確啟用後才會呼叫取得報價及下單端點，不包含轉帳、提領、自動加大本金或不確定狀態重送。

## 重要：只讀仍需要 API Key

Binance 把 Prediction 市場列表、詳情與訂單簿歸類為 Market Data，但這些 SAPI 端點仍要求 `X-MBX-APIKEY`、`timestamp` 與 HMAC 簽名。匿名呼叫會收到 `-2014 API-key format invalid`。

市場資料建議在 Binance 建立一把專用 HMAC API Key：

- 只保留讀取權限
- 不啟用 Spot/Futures/Prediction 交易
- 不啟用提領
- 建議設定 IP 白名單
- 不要把 Key 或 Secret 傳到聊天、Git 或截圖

## 安裝

```powershell
cd "此資料夾"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest -q
```

## 設定金鑰

只在目前 PowerShell 工作階段設定：

```powershell
$env:BINANCE_API_KEY="你的只讀 HMAC API Key"
$env:BINANCE_API_SECRET="你的 HMAC Secret"
```

不要修改 `.env.example` 填入真實金鑰。

## 執行

```powershell
predict-bot discover
predict-bot run --duration 300 --interval 10
```

結束後清除環境變數：

```powershell
Remove-Item Env:BINANCE_API_KEY
Remove-Item Env:BINANCE_API_SECRET
```

CSV 預設寫入 `data/binance_production_observations.csv`。

## 使用的 Binance 端點

```text
GET /sapi/v1/w3w/wallet/prediction/market/list
GET /sapi/v1/w3w/wallet/prediction/market/detail
GET /sapi/v1/w3w/wallet/prediction/order-book
GET /api/v3/ticker/price
```

模擬路徑不會呼叫任何 `POST` Prediction 端點。每輪分別讀取兩個 outcome token 的真實訂單簿，不把顯示機率當成可成交價；模擬損益使用 Predict 的價格敏感 taker 費用公式：`shares × min(price, 1-price) × feeRate`。只有獨立的 M0W 實單執行器可呼叫下列正式端點：

```text
POST /sapi/v1/w3w/wallet/prediction/trade/get-quote
POST /sapi/v1/w3w/wallet/prediction/trade/place-order-bundle
GET  /sapi/v1/w3w/wallet/prediction/order/history
GET  /sapi/v1/w3w/wallet/prediction/pnl/portfolio
```

- 使用 `/api/v3/time` 自動校正 Binance 伺服器時間，避免本機時鐘誤差造成 `-1021`。
- 十五個策略可並行比較。除策略 L 以 UP／DOWN 各一筆表示兩條獨立腿外，其餘策略在每個 5 分鐘市場最多記錄一筆模擬交易。

## 目前驗證狀態

- 本機單元測試涵蓋 HMAC 簽名、UP/DOWN token 對應、兩側訂單簿、機率與淨優勢。
- 已確認匿名正式 API 呼叫會被 Binance 拒絕，因此沒有使用非官方或網頁內部端點繞過認證。
- 正式市場資料、跨市場輪替與重複交易風控均已完成端到端實測。

## 本地監控網站

執行：

```powershell
powershell -ExecutionPolicy Bypass -File .\start-local.ps1
```

若目前工作階段沒有 Binance 憑證，啟動器會安全提示輸入只讀 HMAC API Key 與 Secret；Secret 不會顯示，也不會寫入檔案。網站會開啟於 `http://localhost:4310`。

### 手機從同一個區域網路查看

啟動器會顯示類似 `PHONE (same Wi-Fi/LAN): http://192.168.1.25:4310` 的網址，並把網址寫入 `data/phone-url.txt`。手機連到和電腦相同的 Wi-Fi 後，在瀏覽器開啟該網址即可。第一次啟動或 API 連接埠更新後會要求一次 Windows 系統管理員確認，以建立只允許 `LocalSubnet` 存取 TCP 4310、8766 的防火牆規則。TCP 8765 不屬於本專案，啟停腳本不會終止占用它的其他應用。

區域網路內能開啟這個網址的裝置也能看到模擬帳本並修改紙上策略參數；API Key 與 Secret 不會傳給前端。若不再需要手機存取，可執行：

```powershell
powershell -ExecutionPolicy Bypass -File .\disable-lan-access.ps1
```

後端資料與網站預設每 1 秒更新一次。程式會記錄 Binance 回應中的已用權重標頭；收到 HTTP 429 時，依照 `Retry-After`（至少 10 秒）自動暫停，避免持續請求造成 IP 暫時封鎖。進階調整可使用 `PREDICT_COLLECT_INTERVAL`，最低限制為 0.25 秒。

停止：

```powershell
powershell -ExecutionPolicy Bypass -File .\stop-local.ps1
```

模擬觀察、策略參數與交易帳本保存在 `data/simulation.db`；正式 M0W 訊號、送單狀態、成交、錯誤與 audit events 只寫入 `data/live_m0w.db`。

### 可調整的正式實單

實單頁籤只顯示 Binance 正式訂單與獨立實單帳本，不會混入紙上成交。頁面可儲存下列正式規則，設定會寫入 `data/live_m0w.db`，服務重啟後仍保留：

- 實單訊號策略可選 `M`、`M0`、`M01`、`M0W`、`M01W`、`M1`～`M6`、`M7_1／2／3／5`。這些都是事件驅動且買入後持有至結算的 M 系列；所選紙上策略必須保持啟用才會產生訊號。A～L 中需要盤中賣出、配對或止損的策略尚未接入實盤退出端點，因此不會出現在選單。
- `M0W` 與 `M01W` 仍額外要求上一個相鄰市場的 M0 已正式結算為命中；其他策略不套用這個前一盤閘門。
- 每市場最大下單金額可在 `0.01–100.00 USDT` 間設定。每筆訊號會凍結當時的設定值並換算成精確 wei；交易所回傳 quote 若超過該筆上限便拒絕。任何市場全域只允許一次實單紀錄，即使中途切換策略也不會在同市場送第二單。
- 每筆正式 quote／下單前會依訊號時間換算 `Asia/Taipei` 小時，讀取 M0 每小時統計。可分別設定「M0 最低勝率」與「一勝一敗率上限」：實際勝率嚴格低於最低值，或一勝一敗率嚴格高於上限，本時段便只記錄 `SKIPPED_M0_HOURLY_GUARD` 而不呼叫交易端點。剛好等於門檻仍允許；一勝一敗率沒有分母時不以該條件停單。下一個整點自動換桶。
- 若每小時統計資料庫或快照無法讀取，正式資金採 fail-closed：暫停該筆新單並留下錯誤原因。這個時段閘門不會關閉自動領取，也不處理或取消既有訂單。
- Binance MARKET 單最低約 `1.5 USDT` 且依深度變動，因此可調金額仍一律使用 `LIMIT + GTC`；初始限價取觸發時選定側的有效最佳賣價，程式不會為符合最低額而自動加大。若 Binance quote 的平均價高於初始限價，只會重取一次 quote，第二次的 LIMIT 硬上限為「原訊號價 `+0.10`」（且不高於 `0.99`）；第二次仍超限便拒絕，不改用市價單。M01／M01W 的最大進場價是觸發門檻，實際重報價同樣可在該筆訊號價上方接受最多 `0.10`。`slippageBps` 維持 `100`（1%），因為它和 LIMIT 的 `priceLimit` 是兩個獨立限制，放寬滑價不會取代限價檢查。
- 每個 market ID 只允許一次 place-order 嘗試。timeout、連線中斷、HTTP 5xx 或缺少 order ID 都標記為 `AMBIGUOUS`，不會盲目重送。
- 下單需要 Prediction Trade 權限與 SAS。取得 quote 成功不代表 SAS 已啟用；若正式下單收到 `-31003 SAS authorization required`，引擎立即解除武裝並在實單頁顯示原因。
- 頁面提供本地暫停／恢復控制；恢復前會再次確認，並重新執行 wallet、配額與可用餘額預檢。
- 自動領取與開倉開關彼此獨立。每 15 秒掃描 `PENDING_CLAIM`，只處理 `canClaim=true`、shares／value 大於零且結算滿 60 秒的勝方倉位。
- 每個 token 先寫入獨立帳本再呼叫一次 `batch-redeem`；timeout／HTTP 5xx 等不確定結果不會重送，而是用交易雜湊、redeem status 與單一倉位狀態持續核對。
- 實單頁的勝率、收益與 ROI 會依目前選定策略分開計算，只納入該策略已成交且由 token 持倉端點正式確認結算的訂單；歷史策略訂單仍完整保留。收益使用該筆精確 quote 成本與扣除 provider／network fee 後的 payout。
- `POST /api/live-rules` 接受本機 loopback，或由同一私有區域網路上、經目前 `4310` 監察頁送出的手機請求；手機可監看、暫停並改動正式策略、金額或門檻。來源不是合法監察頁、頁面主機與 API 主機不一致，或來自公開網路時仍會拒絕。恢復實單只接受本機 loopback。儲存規則本身不會送單，且只影響之後的新訊號。
- 編輯中的實單規則是「未套用草稿」：切換頁籤或重新整理時會保存在同一個瀏覽器的 local storage，回到實單頁仍會顯示；只有按下「確認並套用新規則」且後端回覆成功，才會清除草稿並改動正式規則。關閉或重新整理頁面時若仍有草稿，瀏覽器也會顯示離開警告。

建議使用獨立的正式 Prediction key，並以 User 環境變數設定：

```powershell
[Environment]::SetEnvironmentVariable("BINANCE_LIVE_API_KEY", "你的正式 Prediction Trade Key", "User")
[Environment]::SetEnvironmentVariable("BINANCE_LIVE_API_SECRET", "你的正式 Secret", "User")
[Environment]::SetEnvironmentVariable("PREDICT_LIVE_ENABLED", "true", "User")
[Environment]::SetEnvironmentVariable("PREDICT_AUTO_REDEEM_ENABLED", "true", "User")
```

若沒有設定 `BINANCE_LIVE_*`，服務會重用 `BINANCE_API_KEY／SECRET` 連接 Prediction wallet；開倉仍只由 `PREDICT_LIVE_ENABLED` 控制，自動領取則由 `PREDICT_AUTO_REDEEM_ENABLED` 控制。前端會顯示 credential source，但永遠不回傳 Key、Secret、完整 wallet address 或 quote ID。正式送單使用已存在的 Prediction/MPC wallet 餘額，不執行自動轉帳。

### 個別策略重新測量

每張策略卡各自提供「歸零已實現收益／從現在重新計算」按鈕。操作前會再次確認；它只建立新的績效 cohort，不會刪除或修改 `trades`、觀察資料、策略參數或歷史損益。

- 重設時會原子記錄當下最大的 trade ID；該策略之後只用更大的 trade ID 計算交易數、勝負與已實現損益。
- 重設前已開倉但尚未結算的部位不混入新一輪績效，卡片會另外標示為「承接未平倉」；它們仍會正常結算並保留在歷史帳本。
- 每個策略有自己的 append-only 測量基準，重設 A 不會改變 B、B2 或其他策略，也不會解除既有的每市場去重限制；L 則維持同市場每一側最多一腿。
- 基準持久保存在 `strategy_measurement_resets`，服務或電腦重啟後仍有效。前端使用 `POST /api/strategy-reset`，只接受目前白名單中的策略 ID。

## 毫秒級微結構觀測器

啟動網站時會同時啟動一個完全只讀的微結構觀測器；它不會呼叫報價、下單或取消端點，也不會直接觸發任何紙上策略。觀測器使用四條 WebSocket 連線收集三組來源：

- Binance Spot：`BTCUSDT` trade、book ticker、10 檔深度（100 ms）。
- Binance USDⓈ-M Futures：book ticker／10 檔深度走 public route，`aggTrade` 走 market route。
- Binance Prediction：依目前 BTC 5 分鐘市場 ID 訂閱 dynamic orderbook topic，市場輪換時自動重新簽章與切換。

後端保存本機接收 wall clock、monotonic clock、交易所事件時間、sequence ID、完整 WebSocket wire frame、正規化深度與解析器版本，並每 250 ms 建立一筆特徵快照。網頁仍每 1 秒取得一次 `/api/state`；這只影響畫面刷新，不會把底層 WebSocket 降成每秒取樣。

面板提供 event/s、校正傳輸延遲、Spot／永續 microprice、10 檔 queue imbalance、250 ms／1 s 主動成交不平衡、永續－現貨基差、Prediction UP 中價、流動性移除強度、波動警報與方向偏差。Prediction 的事件只標為 `LIQUIDITY_REMOVED`：單憑完整簿快照無法分辨成交與撤單，因此不會錯標成 cancellation。

高頻資料獨立寫入 `data/microstructure.db`（SQLite WAL），不會和 `simulation.db` 共用 writer。面板也顯示 writer 狀態／延遲、queue 深度、drop 數，queue overflow 或 writer error 會另存成帶來源及時間的持久化 gap row，後續分析可排除跨越缺口的視窗。清理採分批刪除，避免資料量變大後每分鐘全表掃描。預設保留原始事件 6 小時、250 ms 快照 72 小時、流動性事件與 gap 720 小時；可用 `PREDICT_MICRO_RAW_RETENTION_HOURS`、`PREDICT_MICRO_SNAPSHOT_RETENTION_HOURS`、`PREDICT_MICRO_LIQUIDITY_RETENTION_HOURS` 調整。若需要改資料庫位置，可設定 `PREDICT_MICRO_DB`。

Prediction 串流遵循官方的 signed SAPI WSS 規格；實盤目前 dynamic topic 外層可能回傳 `DATA`，文件範例則是 `TOPIC`，解析器會同時接受兩者並排除 `COMMAND` 回覆。每次連線／換盤都會清除舊特徵，等第一筆完整簿建立新 baseline，避免跨市場假撤單；並用既有 REST UP 簿驗證 canonical WSS book 的方向，必要時轉換成 UP，尚未驗證前不發布 Prediction 衍生特徵。靜默、過期或亂序資料不會繼續污染方向分數。官方契約：[Prediction orderbook WSS](https://developers.binance.com/en/docs/products/w3w-prediction/websocket-api/orderbook)、[Spot market streams](https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams)、[USDⓈ-M market streams](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Connect)。

網站可同時執行 A～L 與 M 系列共 26 個彼此獨立的紙上策略 ID。依照資料蒐集目的，目前不套用跨策略總資金、每日停損或單市場合併曝險限制：

- A：前 120 秒買入不高於 `0.20` 的便宜側，最佳買價到 `0.40` 時出場。
- B：最後 20 秒買入價格 `0.90`～`0.95` 的高機率側，固定本金預設為 `10 USDT`。進場時同側最佳買價不得已低於止損；持倉後若每秒觀測到同側最佳買價嚴格低於 `0.60`，且該價位深度足以完整退出，便按實際買價止損並計入雙邊 taker fee，否則持有到結算。新交易版本標記為 `B_v2_stop_loss`，既有歷史列不會被改寫。
- B2（實驗）：保留 B 作為固定本金控制組；B2 只在剩餘 `20`～`3` 秒、領先側賣價 `0.90`～`0.95` 時進場。要求本金依 `progress=((20-seconds_left)/(20-3))` 的可調次方曲線，從 `5 USDT` 增至 `20 USDT`，並受最佳賣價可見量限制。B2 使用與 B 相同的完整深度止損成交語義；剩餘時間低於 3 秒不開新倉，但既有倉位仍會檢查止損。
- C：沿用 A 的進場條件，目標價固定為實際買價的 `2` 倍。
- D：前段先買低價側，含雙邊費用的等量配對成本不高於 `0.95` 才補第二腿；逾時或剩 20 秒時退出未配對部位。
- E（實驗）：剩餘 90～30 秒時，Binance 現貨相對同市場第一筆現貨觀察至少移動 `3 bps`，且預測市場同方向確認、價格 `0.60`～`0.90`、價差不超過 `0.04` 才進場。
- E2（實驗）：剩餘 60～30 秒、價格 `0.60`～`0.79`、價差不超過 `0.02`；使用第一筆觀察至今的完整整合波動標準化現貨移動，強度至少 `0.75` 且報價新鮮才進場。
- F（實驗）：D 的嚴格版本，配對成本上限降為 `0.90`，第一腿本金降為 `5 USDT`，用來測試降低尾部風險後的結果。
- G（實驗）：使用正式 `startPrice`、短期波動及剩餘時間估算保守勝率，扣除不確定性、價格敏感費用與滑價後，淨優勢仍高於固定門檻和三倍價差才進場；滑價會直接計入模擬成交與損益。
- H（實驗）：先等待連續兩局出現經正式結算確認的尾盤逆轉；也就是原領先側在最後 5 秒曾達 `0.90`，最後卻由另一側勝出。第二次成立後進入武裝狀態，之後只在最後 20 秒買入唯一一個不高於 `0.01` 的低價側並持有到結算。`10 USDT` 是單筆上限；符合價位的可見掛單有多少就成交多少。H 連續兩筆正式結算失敗後解除武裝，重新等待兩次連續逆轉；中間沒有 H 成交的市場不算失敗，H 獲勝則把連敗歸零。
- I（賭狗實驗）：完全不等待 H 的逆轉訊號；只要剩餘時間超過 `3` 秒，且唯一一側的最佳賣價不高於 `0.01`，就按當下可見掛單量模擬買入並持有到結算，單筆本金上限為 `1 USDT`。倒數 3 秒內不追價；這條策略可作為 H 的低價對照組。
- J（尾盤賭狗反彈價差）：沿用 C 的低價反彈概念，但只在剩餘 `120`～`3` 秒之間尋找機會；兩側價差至少 `0.20`、便宜側不高於 `0.20` 時，按當下可見掛單量買入，單筆本金上限為 `10 USDT`。最佳買價到實際進場價的 `2` 倍便出場，否則持有到結算。
- K（實驗，基差校正終局勝率）：先用市場最早一筆 Binance 現貨與官方 `startPrice` 校正兩個價格源的初始基差，再用目前現貨離校正目標的距離、剩餘時間、短期波動與基差不確定性估計終局勝率。方向與勝率完全不由預測盤報價決定；報價只用來檢查手續費、滑價、價差、新鮮度與完整成交深度，避免高勝率卻買得過貴。
- L（實驗，雙向前段反彈）：每輪開始後前 `60` 秒內，UP／DOWN 各自第一次出現賣價不高於 `0.50` 時，用總本金的一半作為該腿預算；兩腿可在不同秒成交，並按最佳賣價可見量部分成交。任一腿的最佳買價達 `0.70` 且第一檔深度足以完整退出時，才以目標價 `0.70` 模擬賣出並計入雙邊 taker fee；未達標的腿持有到正式結算。相同 USDT 不代表相同 shares，雙向建倉也不是無風險套利。
- M（原策略的 WS 版）：開盤前 `10` 秒內，以第一筆非零 Spot－正式 `startPrice` 偏離決定方向；正數買 UP、負數買 DOWN，Prediction 簿只負責紙上成交，持有到結算。
- M0／M1：分別是固定種子的可重現隨機方向，以及永遠買 UP 的基準組。
- M0 卡片會按目前測量 cohort 顯示當前／平均連勝、當前／平均連敗，以及「一勝一敗平均連續」；最後一項先把 M0 結果套用「上一盤勝才觀察下一盤」的 M0W 放行規則，再計算所得連敗段的平均長度。只計已結束交易，未平倉不參與 streak。
- M0 的 24 小時分佈面板會在每個台北時間小時另外顯示平均連勝、平均連敗及一勝一敗率。連勝／連敗只串接同日、同小時且相鄰的 5 分鐘局；一勝一敗率以本局進場小時分桶，計算上一個相鄰 M0 勝後本局轉敗的比例，作為方向層面的 M0W 理論敗率。
- M01：先沿用 M0 的固定種子方向但不立即買入；整輪等待選定側 ask 不高於 `0.30` 才依可見深度部分成交，未到價不交易，成交後持有至結算。
- M01-Floor（內部 ID `M01F`）：方向與 M0／M01 完全相同，只在選定側 ask 落入 `0.20–0.30`（含邊界）時模擬進場；低於 `0.20` 也不買，但會在整輪窗口繼續等待回到區間。它有獨立模擬帳本與歸零起點，刻意不列入實單策略選單。
- M01-Rebound（內部 ID `M01R`）：方向與 M0 完全相同；選定側 ask 到達 `0.30` 或以下後追蹤最低價，第一個 ask 從目前低點反彈至少 `0.10` 時模擬買入（例如 `0.10→0.20` 或 `0.30→0.40`），再創新低時觸發基準同步下移。成交後持有至結算，使用獨立模擬帳本且不列入實單策略選單。
- M0W：只有上一盤 M0 已結算命中，本盤才跟隨本盤 M0 的固定種子方向進場；上一盤失敗或結果未知時跳過。
- M01W：只有上一盤 M0 已正式結算命中，本盤才鎖定本盤 M0 方向並等待選定側 ask 不高於 `0.30` 進場；不再依賴上一盤 M01 是否成交，成交後持有至結算。
- M2／M3／M4：分別測試首次非零偏離、至少 `1 bps` 偏離，以及兩筆不同 Spot trade 連續同方向後才進場。
- M5：用 Binance USD-M BTCUSDT perpetual `aggTrade` 相對正式 `startPrice` 判斷方向，不使用 mark price。
- M6：以不受策略開關影響的 canonical WS 樣本估算前序市場平均 Spot－`startPrice` 基差；至少累積 20 輪後，扣除因果凍結的平均基差再判斷方向。
- M7_1／M7_2／M7_3／M7_5：在市場開盤後絕對 `+1／+2／+3／+5` 秒各自取 deadline 前最後一筆、當時年齡不超過 1 秒的 Spot trade；再等待 deadline 後第一筆已驗證 Prediction 簿，且只在設定的 execution grace 內模擬成交。四組方向、交易、統計與歸零彼此獨立。

第三個「M 出場分支實驗」頁籤另外執行 `MX_T60／T70／T80／T90／T98／P50／P10／REV` 八組 cohort。它們共用原 M 的首次非零 Spot 偏離方向，只在開盤後前 10 秒、持倉側 ask 不高於 `0.50` 時依第一檔可見量模擬進場。固定組分別在 bid 到 `0.60／0.70／0.80／0.90／0.98` 時出場；P50 以最終進場 VWAP 的 `1.5x／2.0x` 各賣原始成交量 50%，P10 從 `1.1x` 到 `2.0x` 每階賣 10%；REV 在 Spot 反向穿越正式 `startPrice` 時退出，或等持倉側 bid 先站上 `0.50`、再跌回 `0.50` 時退出。第四個「M0 出場分支實驗」以 `M0X_*` 八個獨立 ID 複製同樣數量與退出條件，只把進場方向改成 M0 的固定種子隨機方向。兩頁分開顯示交易數、勝／負、進／出場填單率、完全未成交與部分成交 intent 比例；完全未成交不算交易，未平倉不提前判勝負，正式結算的剩餘部位只計入最終損益，不會冒充限價出場成交。

時間套利的第二腿必須是等量 shares；E、E2、G、K 仍要求最優價深度足以完整成交。B2、H、I、J、L 與 M 系列的進場本金會受可見賣量限制；B 與 B2 止損、L 的目標出場都要求最佳買價深度足以退出全部持倉，避免假設整筆都能成交在第一檔。A～L 仍由每秒 REST 快照觀察；M 系列則由 WebSocket 事件與 1 ms 本地 deadline scheduler 驅動，記錄 nanosecond receive clock、queue delay 與 decision duration。這些是本機量測與排程能力，不代表 Prediction 上游每 1 ms 發送資料，也不代表真實訂單具備毫秒成交。M 系列的原始研究帳本仍全部是紙上成交；只有新設的 M0W 實單分頁會接收同一個因果訊號並由隔離的背景執行器送出正式訂單。若市場結束時正式 `endPrice` 尚未提供，既有策略可先以現貨 proxy 顯示損益，但 H 的逆轉與連敗狀態會等待背景補查正式結果後才推進。所有紙上門檻都能在網站調整；M 系列的精確定義與欄位見 [dashboard/README.md](./dashboard/README.md)。
#   p r e d i c t - b n b - t e s t n e t - b o t  
 