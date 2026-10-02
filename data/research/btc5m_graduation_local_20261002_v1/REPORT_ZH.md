# 畢業研究本機資料交接（2026-10-02）

本輪提供真結算標籤、公開盤口與成本證據，供雲端依原定門檻重算。沒有計算損益、CI、畢業判準，沒有 fit、worker、實盤訂單或服務變更。任務 A 的資料數量達標；任務 B 的觀測與文件核對已交付，但精確部署費率公式及大額對沖成交延遲仍為 UNKNOWN，不能據此宣稱 HEDGE 1.0 可行。

## 結算標籤

四個本機凍結市場清單 fresh-100a、fresh-40f/g/h 共 220 場，互不重複。這是目前雲端 220 場的候選對照；雲端原始 BOOKS_ROOT 清單未提供，無法證明完全同一批。各市場與來源 hash 已保留。

重新唯讀查詢 Predict 官方市場 API，取得 519 場 UP／DOWN 真標籤：既有 219 場，加上不在上述 220 場內的 300 場新市場。另有兩場非二元結算，公開鏈上 payoutNumerators / payoutDenominator 在固定區塊 125160995 確認 UP、DOWN 每股各支付 0.5：2668062（fresh-40h）及 2782588（新增市場）。兩場另存結算與盤口，不強行指定勝方。

既有 120 場推斷標籤現在有 119 場二元真標籤及 1 場真實平分結算。按本機同批盤口最後有效中價重建的推斷，119 場可比較資料有 117 場相同、2 場不同：2658251（推斷 UP，官方 DOWN）與 2669348（推斷 DOWN，官方 UP）。這只是標籤一致性核對，沒有重算損益；仍須核對雲端原始 220 場清單。

不能單憑本機 DB 的 SETTLED 或 winner 視為真標籤：`target_wallet_official_v1.py::resolved_winner` 存在以起終價格推斷的後備路徑。此次標籤均以官方 API 明確且唯一 WON outcome 核對；兩場雙 WON 另以公開鏈上 payout 核對。DB 的 resolved_at_ms 是本機觀測時間，並非交易所結算時間。

新場先按 BTC、標題五分鐘時長、既有公開 tape、至少 50 筆有效簿及 t12–288 秒覆蓋篩選，再依 market_id 由大到小取場，不按勝負、損益或 confidence 篩選。最初選 300 場，其中一場平分；保留初選與平分場，再於追加時按同一排序和可用性條件取得當時最新符合條件的 2807162，達到 300 場新二元標籤。這是兩次可用性選取，並非重新覆蓋初選清單。盤口可用性篩選不保證樣本具代表性，也不將此批宣稱為完全未使用的獨立留置樣本。

## 翻轉時的公開深度

按雲端 `flip_hedge_scaled.sim` 的 t12–288、每 2 秒決策、初始 FAV 中價跌至 0.4 的規則定位事件，再篩選名義 FAV 庫存至少 150 股。庫存是雲端每次假設成交 15 股、總量上限 300 股的計算，沒有實際 OUR 成交證明。深度是已有公開 L2 的觀測值，兩者不可混稱。

共有 154 個事件。source 時間版本的對面前三檔股數 p10／中位數／p90 約為 174.9／728.2／1667.8；120/154 個事件前三檔足以容納名義 HEDGE 全庫存，另 34 個需要更深檔。完整觀測深度 154/154 可容納該名義股數；source 價差中位數為 0.01。完整對面賣盤價量 `opposite_asks_full`、VWAP、前三檔各價量、缺口與各事件時間均在附件；另以獨立 checkpoint/delta 重建核對兩種 clock 共 308 個快照的全深度數量、股數、前三檔與 VWAP，全部相同。這些數量不保證訂單到達時仍可成交。

146/154 個 source 時間快照在名義決策時間尚未收到。另提供 `received_available_clock`，要求 checkpoint 與 delta 的整條依賴鏈都已收到；此版本也是 154/154 有深度快照。它是同一 source-clock 翻轉事件的可用盤口對照，沒有另做 received-clock 策略重播，不能當成真實 live 翻轉時間。簿更新 received−source 中位數 517.5 ms 是資料觀測差，時鐘同步未證實，不能當成送單或成交延遲。

UP 使用 UP 簿；DOWN 自己的 ask 是 1−UP bid，DOWN bid 是 1−UP ask。每場發布最多五檔公開盤口，部分快照不足五檔；深度事件由既有完整 L2 重建。220 場原始凍結簿共有 316,055 行，其中 22,422 行至少一邊最佳價為 null，未納入輸出。其餘 293,633 行的時間、最佳价及最多五檔價量逐筆與重建結果相同（220/220 provenance 核對通過），沒有修改原始檔。這是保留有效簿的匯出，並非原始 books 列表不加過濾的完整複製。

## 場地、費用與真實延遲

Predict 支援賣出已持有的 outcome 股數，需要持倉及相應授權；現有本機 cap100 taker 路徑只實作 BUY，本輪沒有改接單程式或送 SELL。[官方下單／取消說明](https://dev.predict.fun/how-to-create-or-cancel-orders-679306m0)

官方說明 maker 基礎費用為 0，taker 依價格調整；此次 521 個市場 API 的 feeRateBps 都是 200。這是查詢時的市場參數，並非歷史每筆交易有效費率，也不能解讀為固定 2% 成交額。部署合約的精確公式、帳戶折扣及精確每股費用仍 UNKNOWN；附件保留本機費用模型例子並明確標示未核對部署公式。[Predict 費用說明](https://predict.fun/zh-cn/learn/how-prediction-market-fees-work)

Binance 官方 quote 定義 BUY feeAmount 單位為股、SELL 為 USDT。歷史 order-history 的 marketProviderFee / networkFee 欄位未明定單位，不能無條件套用 quote 定義。LIMIT 只是掛單意圖，立即吃單仍可能收 taker 費；本機 MAKER 字樣不能證明交易所成交角色。歷史 cash/net-share 的差額可能含費用、滑價與四捨五入，沒有當成純每股手續費。[官方 prediction reference](https://github.com/binance/binance-skills-hub/blob/main/skills/binance-web3/binance-agentic-wallet/references/prediction.md#L469-L518)、[交易 REST schema](https://developers.binance.com/en/docs/catalog/web3-wallet-prediction-trading/api/rest-api/trade)

唯讀既有 `echtgeld_engine_v1.db::engine_cap100_orders`：368 筆有本機送單起終時間，其中 49 筆 MARKET BUY／本機 TAKER 意圖，送單 call 到 response 為 243–1645 ms、中位數 314 ms、p90 461.6 ms。樣本為 2026-08-20 至 08-29、9.51–18 股的小額 BUY，39 筆最後 FILLED、10 筆 REJECTED。時間不含先前 quote／盤口查詢或簽名，也不是交易所成交時間。150–300 股 HEDGE 或 SELL 的 decision→accept、submit→exchange-fill 仍 UNKNOWN。只發布聚合值，不发布個別訂單或帳戶資料。

## research-data 的檔案

- `research_pack/public_markets/<market_id>/public_<market_id>.json.gz`：519 場二元真標籤市場的公開簿。
- `research_pack/labels/GRADUATION_LABELS_ALL_TRUE.json`：519 場；`GRADUATION_LABELS_NEW300_TRUE.json`：新增 300 場。
- `GRADUATION_LABELS_EXISTING220_TRUE.json`：219 場；`GRADUATION_LABELS_REPAIRED120_TRUE.json`：119 場；`labels100a.json`：100 場。
- `GRADUATION_NONBINARY_SETTLEMENTS.json` 與 `GRADUATION_NONBINARY_BOOKS.json.gz`：兩場每邊 0.5 的真實結算與盤口。
- `GRADUATION_OFFICIAL_LABEL_PROVENANCE.json`、`GRADUATION_PUBLIC_PROVENANCE.json`：來源、API 狀態、時間、hash、凍結盤口逐筆核對。
- `GRADUATION_LABEL_REPAIR_COMPARISON.json`：120 場推斷對照。
- `GRADUATION_COST_FEATURES.json.gz`：154 個 source / received 可用深度事件及每場資料觀測延遲。
- `GRADUATION_VENUE_COST_EVIDENCE.json`：官方文件來源、歷史時間與費用聚合、UNKNOWN。
- `GRADUATION_LOCAL_README_ZH.md`：本交接說明。

以上附件皆在 `research_pack/labels/`，INDEX.json 列出路徑、大小與 sha256。同步走新 `--public-only` 模式，跳過 worker result／trace，只複製指定公開簿及附件；先 dry-run，大小在原 50 MB 上限內，FLAGGED=0，另做欄位／字串識別資訊檢查。原有資料分支內容及當前髒工作樹保留。二元 label 檔不能表達平分結算，所以平分簿刻意不用 public_* 檔名，避免雲端程式回退到最後中價猜勝方；雲端若要計入它們，必須按兩邊每股 0.5 處理。

本機工具的 prepare/fetch/export 與成本抽取只產生新匯出檔。資料庫以 mode=ro 與 query_only 開啟，沒有重建、collector、服務、排程、live/armed/stake 變更。沒有自行修改原定門檻或下經濟判定。
