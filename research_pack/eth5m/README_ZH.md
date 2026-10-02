# Predict ETH 5 分鐘公開研究資料

本包固定 861 場時間連續市場：台北時間 2026-09-29 19:20 至 2026-10-02 19:05（UTC 2026-09-29 11:20 至 2026-10-02 11:05）。市場開窗相隔 300,000 ms，完整清單與凍結來源 hash 在 `SELECTION.json`。選取只依歸檔存在、時間連續與固定截點；截止市場已結束至少 15 分鐘。不按勝負、價格、績效、成交數或資料品質篩選，也沒有替補不完整場。

## 檔案與讀取

- `public_markets/<id>/public_<id>.json.gz`：根欄位 `market`、`books`；`market.window_start_ms`、`market.window_end_ms`；每幀 `source_ms`、實際回調 `received_ms`、`best_bid`、`best_ask`、`bids`、`asks`，每側最多五個真實 `[price, quantity]` 檔位。少於五檔或空側照實保留。
- `labels/OFFICIAL_WON_LABELS.json`：`{"records":[{"market_id":123,"winner":"UP"}]}`。860 個唯一二元標籤；市場 2745523 的官方 UP/DOWN 同為 WON，另存證據而不猜方向。最初連續 112 場都有唯一 WON。唯一依據為 Predict 官方 `GET /v1/markets/<id>` 的 `outcomes[].status="WON"`；市場必須 `RESOLVED/SETTLED`，而且唯一 WON 為 UP 或 DOWN。未使用最後中價、startPrice/endPrice、DB winner 或 isWinner fallback。
- `labels/OFFICIAL_NONBINARY_RESULTS.json`：官方同時有多個 WON、無法編成唯一 UP/DOWN 的市場另外保留原始證據，原市場不刪除、不替補；這些場在二元標籤檔沒有捏造方向。
- `labels/api/<id>.json`、`labels/OFFICIAL_WON_PROVENANCE.json`：官方公開欄位、API URL、查詢時間、狀態與 WON 證據；官方 `categorySlug` 的 ETH/5m/開始秒數與凍結市場窗逐場核對。
- `events/markets/<id>/events.npz`：與既有 BTC feed V1 相同的 `data`（64-byte HftBacktest event dtype）及 `local_times_ms`（int64）。`META.json` 含轉換方式、來源 hash、逐筆成交數、時間覆蓋、gap、事件 hash 與 NPZ readback。
- `INDEX.json`：時間排序的 public/event 路徑與數量；`VALIDATION.json`、`INDEPENDENT_VALIDATION.json`、`SHA256SUMS.json`：核驗及檔案 hash。

```python
import gzip, json, numpy as np
root = "research_pack/eth5m"
mid = 2728328
with gzip.open(f"{root}/public_markets/{mid}/public_{mid}.json.gz", "rt", encoding="utf-8") as f:
    public = json.load(f)
with np.load(f"{root}/events/markets/{mid}/events.npz", allow_pickle=False) as z:
    events, wakeups = z["data"], z["local_times_ms"]
labels = json.load(open(f"{root}/labels/OFFICIAL_WON_LABELS.json", encoding="utf-8"))["records"]
```

## 時鐘與覆蓋

公開簿保存 `window_start_ms-2000` 至 `window_end_ms+2000` 的來源幀，與 BTC exporter 相同；13 個超出此 padding 的來源更新只在完整事件檔保留，詳見 `PUBLIC_FRAME_BOUNDARY_AUDIT.json`。

簿按 BTC 公開檔方式，以 source_ms/received_ms 排序重建 checkpoint/delta。`received_ms` 保留原回調時間，另加 `chain_received_max_ms` 記錄該簿重建所需最晚收到時間。因果讀取時用 `max(received_ms, chain_received_max_ms)` 作 availability，不能把 source 時間當收到時間。兩個時間均為 Unix 毫秒；每市場 public META 記錄此差異的幀數。

事件使用未修改的 BTC feed V1 函式。深度 exchange/local 時鐘是實際 receivedMs × 1,000,000，單位 Unix ns。TRADE 只來自歸檔的真實 Predict 公開 matches，包含真實價量與 aggressor 方向；沒有把簿減量當成交。原成交 `executedAt` 只有秒級精度，沿用 BTC 的 `mid` 半秒 offset 加小序號，不代表真實亞秒成交時間或歷史成交到達時間。成交的完整收集率仍是 UNKNOWN，歸檔沒有全頁完整性證明；事件可含市場窗之前的真實成交，META 分別計數。引擎使用前請以 META 的市場窗限定評價範圍。

連續指市場窗連續，不代表每一毫秒都有收到簿。782 場 collector 品質為 `COMPLETE_FORWARD_V1`，79 場為 `INCOMPLETE_FORWARD`；兩者全部保留。每場 META 揭露開始/尾端缺口、最長 source/received gap、完整邊界是否覆蓋。詳細實際數量以 `VALIDATION.json` 為準。

## 可重現與範圍

`converter_source/` 保留既有 BTC Python feed、dtype 與匿名化 helper，`CONVERTER_SOURCE_SHA256.json` 記錄來源 hash；`export_eth5m.py` 是此包 export/官方標籤/核驗工具。只載入純 Python 轉換定義，未載入或執行 native 引擎。每場原始/匿名化事件與 wakeup bytes 必須完全相同，NPZ 寫入後全部 readback；另有三場已發布 BTC byte parity 與 ETH 原始來源抽查。

只發布公開簿、匿名價量事件與官方市場結果。未發布原始 tapes、錢包/帳戶/委託/transaction identifiers、私人成交、API key 或原始 DB。事件的 order_id/ival/fval 均為零。官方勝負只作離線評價標籤，不是 runtime 輸入。本次沒有訓練、native 回放、下單、部署、服務/collector/排程或 enabled/armed/stake 變更。

官方 API：[market by ID](https://dev.predict.fun/get-market-by-id-25552989e0)、[orderbook](https://dev.predict.fun/get-the-orderbook-for-a-market-25326908e0)。
