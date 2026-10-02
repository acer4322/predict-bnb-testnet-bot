# ETH5M Target 成交與歷史掛單推測

本補充包沿用上層 `SELECTION.json` 的全部 861 場，時間、順序與公開簿完全相同，沒有依 Target 行為或結果挑場。它提供官方已觀察成交，以及成交後重建的掛單候選，供離線教學／診斷使用。這是沿用 BTC 的歷史掛單推測用途，沒有訓練「下一次掛單」預測模型。

固定匯出 55,900 筆官方已觀察成交：22,964 筆 MAKER BID、32,936 筆 TAKER BID，全部在各自市場窗內。19,215 個 Maker parent 都有推測或 UNKNOWN 紀錄；支持充分 11,219、部分支持 1,033、UNKNOWN 6,963。這些是模型支持分類，沒有觀測到真實掛單時間來衡量準確率。舊 V2.1 凍結 18,256 個 parent，157 場的已成交份額覆蓋不完整。

## 讀取入口

- `target_fills_export.json.gz`：BTC 同樣的 JSON list，保存 `market_id/event_ms/observed_at_ms/side/price/shares/role`，另有 `quote_type/fill_seq/parent_seq`。Maker、Taker 都保留，不帶 winner/PnL。`fill_seq` 是本包匿名成交序號；`parent_seq` 是按市場、角色、買賣別、方向、價格與原委託識別分組後的匿名 parent。沒有 order identity 的 leg 單獨成 parent，不以價格／時間猜合併。
- `target_order_predictions.json.gz`：所有官方觀察到的 **MAKER BID filled parents**，包含第一次／最後成交、累計已成交量、支持增量、候選時間、資料缺口與 UNKNOWN 欄位。`fill_seqs` 可回連實際成交。
- `target_lifecycle_features.json.gz`：BTC `target_lifecycle_features` 的欄位名稱，含 `anon_seq/filled_qty/placement_first_ms/first_target_ms/resting_ms/placement_coverage` 及 source/receive 時鐘的 best bid／mid 欄位；額外欄位揭露推測語意。`anon_seq` 等於 `parent_seq`。未知值為 JSON null；本版本 `confidence`、`fill_allocation_coverage` 沒有校準或重新估算，均為 null，不能當 0 或勝率。
- `placement_allocations.json.gz`：每份候選支持量的來源 `update_seq/change_seq/source_ms/received_ms/availability_ms/native_side`、價格、公開正增量、模型中剩餘存量及本 parent 分配量。同一份正增量的容量跨 parent 共用，不能重複消耗。
- `public_depth_evidence/<id>.json.gz`：完整深度的匿名正負淨增量證據及所有更新時鐘。公開簿只含前五檔，此檔支持更深檔的候選推測；首幀 checkpoint 是初始存量，不當成新增掛單。後續 checkpoint 的 changes 是對上一份實際簿的變化，可作候選支持。
- `markets/<id>/target_fills.json.gz`、`target_order_predictions.json.gz`、`META.json`：逐場版本及成交／支持／既有推測覆蓋。
- `legacy_v21_reference.json.gz`、`legacy_v21_allocations.json.gz`：凍結已有 V2.1 推測及支持分配，保留原本的 confidence、support、post_action 與 `inferred_at_ms`。識別欄位改為 `legacy_seq`，可用 `official_parent_seq` 連到新包。原 V2.1 有 **TARGET_UNIT=18** 的模型假設；`expected_parent_shares`、`placement_supports_18`、`post_action` 都是模型欄位，不能當官方掛單量、實際撤單或實際掛單時間。本機滾動保留會造成舊場推測不全；每場 META 揭露實際份額覆蓋，不以「有 parent 列」宣稱完整。
- `INDEX.json`、`SOURCE_PROVENANCE.json`、`VALIDATION.json`、`INDEPENDENT_VALIDATION.json`、`SHA256SUMS.json`：固定清單引用、來源、核驗及 hash。

```python
import gzip, json
from pathlib import Path
root = Path("research_pack/eth5m/target")
def load(name):
    with gzip.open(root / name, "rt", encoding="utf-8") as f:
        return json.load(f)
fills = load("target_fills_export.json.gz")
predictions = load("target_order_predictions.json.gz")
features = load("target_lifecycle_features.json.gz")
# 支持充分仍是匿名簿候選，不是已確認的 Target 委託。
supported = [p for p in predictions if p["support_status"] == "FULL_LOWER_BOUND_SUPPORT"]
```

## 新版推測規則與證據界線

每個 Maker BID parent 的價格來自官方 match leg。UP 買單對應 native BID、價格 p；DOWN 買單對應 native ASK、價格 1-p。分配目標只有該 parent 的 **累計觀察到的已成交 shares**，是一個成交量下界；不假設固定 18 股或最低委託量，也不聲稱得到真實掛單總量。

先按實際 received 順序重建同一 native 檔的匿名存量。負淨增量以 FIFO 模型扣除最舊存量，初始存量也參與扣除但不作掛單候選；若 before_size 與模型存量不一致，清除舊候選並把 after_size 當不可分配的未知基線，揭露 `level_chain_reset_count`。已分配部分在同一增量內先被扣除，後續 parent 可用量為 `min(模型中剩餘存量, 原增量-先前已分配量)`。這個匿名 FIFO 扣除是明確模型假設，無法知道實際撤單選擇或每個交易者隊列；負變化也沒有被標成確定成交或撤單。

在第一次成交的秒級 timestamp 前 60 秒內，尋找仍有模型存量的同檔正淨增量，按最近來源時間往前分配；所有參與重建的變化必須 source 在 first fill 之前、且在第一次成交被 ledger 收到之前已可觀測。parents 按 first fill、匿名序號排序；所有 parents 共用每份增量的剩餘容量。FIFO 存量模型、這個 parent 順序與「最近增量優先」都不是私有排隊位置證據。

`candidate_carrier_ready_ms`／相容欄位 `placement_first_ms` 是本次被分配支持增量中的最早 source 時間；`nearest_supporting_add_ms` 是最晚支持增量。多個增量可能來自不同交易者、補單、淨額抵銷或長時間資料間隙。即使支持量足夠，也不證明 Target 在那個時間掛單；`actual_placement_ms/order_quantity/unfilled_quantity` 為 null，`placement_ownership/unfilled_orders/cancel_state` 保持 UNKNOWN。`support_coverage`／`placement_coverage` 是分配量除以已成交量，**不是機率或預測準確率**。沒有支持仍保留 parent，標為 UNKNOWN。

只有已成交 Maker parents 有此推測；無成交委託、撤單、換價、隊列位置、未成交餘額及私人意圖均缺少直接生命周期資料。沒有成交不能推論沒有掛單。既有 V2.1 的 `CONFIRMED_NEXT_PARENT` 等 post_action 名稱表示其回顧式 parent 關聯，不是官方 order lifecycle confirmation。

## 時鐘與用途

`event_ms` 來自官方 `executedAt`，此批為秒級精度；`observed_at_ms` 是官方 collector 收到該頁的時間，不是委託 birth time，也不是個別 match 的真實網路到達時間。全部成交的 observed clock 均與 ledger context 核對。原始 API match 的 market、event、participant 身分、角色、方向、買賣別、價格、shares 全部逐筆核對後才匿名化。

模型的存量截止點是第一次成交所在秒的起點；該秒內新增、減量與真實成交的先後仍未知，不能宣稱候選在真實亞秒成交瞬間仍然有效。每筆的 `event_precision_ms=1000` 保存在成交檔；推測沒有捏造亞秒時間。

公開更新有真實 `received_ms`；`availability_ms` 是 source 順序重建所需的最晚收到時間。候選使用 `source_ms < first_fill_ms`，且 `availability_ms <= first_fill_observed_ms`；不能把 source timestamp 當收到時間。資料窗不足、最長 source gap、collector 原品質逐場揭露，未替補不完整場。

此批 3,222 個 parent 的來源簿未覆蓋完整前 60 秒，3 個 parent 的該檔鏈發生基線重設；詳見逐筆欄位與 `WINDOW_AND_CHAIN_AUDIT.json`。支持分類不會消除這些資料限制。

此推測使用 parent 後續成交總量，並以跨 parent 的回顧式分配重建，所以 **不是歷史當時可用的下一單預測**。`minimum_evidence_available_ms` 只表示這筆重建所用原始證據最早都已到齊的下界；`reconstructed_at_ms` 是本次離線重建批次起始時間，不能把候選 source 時間冒充模型輸出時間。相容特徵的 `mid_side_at_fill_plus5s*` 明確是未來離線診斷；不可作當時 runtime 輸入。Source/recv 特徵以各自時間取最後可用的真實前五檔，缺失／越界為 null。

已保存 ledger 的匯出覆蓋逐筆完整，官方 API 過去所有頁的絕對成交收集完整率仍是 UNKNOWN；無歷史全頁完整性證明。官方勝負請另外讀上層 `labels/OFFICIAL_WON_LABELS.json`，它仍是唯一 WON 規則，沒有引用官方 ledger 中較寬鬆的結果推斷程式。

## 重現與發布範圍

新推測可以僅用已發布的匿名成交、正負淨增量證據及上層公開簿重建，無須錢包識別或 DB：

```powershell
python research_pack/eth5m/target/export_target_eth5m.py reconstruct --public-root research_pack/eth5m --out research_pack/eth5m/target
```

工具會逐場及全檔核對新推測、BTC 相容特徵與支持分配的 gzip bytes。原始帳戶、wallet/order/transaction/settlement identifiers、API key、raw API payload 和 DB 不發布。只有 Target 在官方公開 matches 的匿名化成交；沒有私人委託 lifecycle、帳戶餘額或 OUR 私人成交。本次只做資料匯出／離線推測，沒有模型訓練、native/HFT 回放、下單、部署或服務與交易設定變更。
