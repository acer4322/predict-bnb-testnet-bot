# BTC5M 公開簿＋公開逐筆成交：100 場 feed V1 NPZ

本批只從本機既有 execution_tape_v1 archive 做 Python/NumPy 格式轉換；沒有新 native 執行、建置、worker 或 collector 啟動。檔案位於 research-data 的 research_pack/events/hft244_feed_v1_20261002_v1/。

## 選場與使用

- 70 場較新 BTC5M：market_id 2802399–2809470，全部 >2685217。
- 15 場 Target 成交匯出市場：2682168–2685217。
- 15 場 Target lifecycle 特徵匯出市場：1763611–1764475。
- 各組按 ID 由大到小選；需 BTC/Bitcoin 五分鐘標題、完整初始簿快照、正數且可正規化的公開成交、已結束的市場視窗。不按損益、勝方或 confidence 選。

SELECTION.json 凍結每場原始 tape SHA 與 offline cohort tag；INDEX.json 列出每場 NPZ 路徑、SHA、事件及公開成交數；markets/<market_id>/META.json 提供時間涵蓋、缺口、交易視窗分布與轉換來源。Target 的成交行為、結算或推估掛單不進事件陣列。

```python
import numpy as np
with np.load('markets/2809470/events.npz', allow_pickle=False) as z:
    events = z['data']
    local_times_ms = z['local_times_ms']
# 舊引擎直接 .data(events)，local_times_ms 是策略喚醒時刻，不是 event 欄位的 ns。
```

## 凍結 V1 語義與時間假設

沿用 source repro commit 3bf09cf645e1d83284825c32196e483294fcd182 的実際 CG1AT runtime scratch feed V1。NPZ 鍵為 data、local_times_ms；data 每筆 64 bytes，欄位 ev:u8、exch_ts:i8、local_ts:i8、px:f8、qty:f8、order_id:u8、ival:i8、fval:f8。事件時間為 Unix ns；喚醒時間為 Unix ms。市場 feed 的 order_id、ival、fval 全為 0。

V1 簿事件把 receivedMs 同時作 exchange/local 時間。sourceMs 僅供 META 檢查。公開成交 executedAt 正規化後，依 mid 政策加 500,000,000 ns + min(同 timestamp 序號,999)×1,000 ns。成交順序仍是原 timestamp、交易排序鍵、native UP/YES price、qty；匿名化前後全部事件與喚醒 bytes 必須相同。DOWN 公開成交沿凍結 normalize_match 換成 UP/YES 資產價格及相應 aggressor。半秒及組內微秒是模擬排序假設，不是實測 latency。

初始完整 UP/YES 簿之後逐筆重播 depth after-quantity；公開原始 priceExecuted、amountFilled 按 wei/1e18 正規化。所有可用公開成交依 V1 原樣保留，包括五分鐘視窗前後的成交；META 分列筆數，沒有自行裁切或換成 V2。

## 覆蓋與驗證限制

這是既有公開捕獲的轉換，不能聲稱全場完整。92/100 場符合既有 source 開頭、尾端與中間缺口均 ≤5000ms 的資料品質檢查；另外 8 場在 INDEX 與 CONVERSION_VALIDATION 明確標記，最大中間缺口 223004ms（market 2809074）。這個檢查僅供使用者篩選，不是完整捕獲證書。部分場次初始簿較晚、尾端較早或中間有缺口；META 的 observed_*_missing_ms、source_max_gap_ms 和 source_gaps_over_5000_ms 明列。RECORDING 是封存 metadata，並非此次執行的服務狀態。全部已通過選場當下的 window_end_ms 核對，但沒有公開成交完整分頁／場地對帳證書，public_trade_collection_completeness 一律 UNKNOWN。

CONVERSION_VALIDATION.json：既有 3 場驗收 NPZ 逐 byte parity PASS；本批 100 場原始／匿名化轉換及 NPZ 回讀均 PASS。SHA256SUMS.json 保護全部交付檔；verify_npz_batch.py 只用 NumPy 做回讀與完整性檢查，不載入引擎。

```text
python verify_npz_batch.py --root .
```

converter_source/ 保存未改動的 5 份 Python 來源與各檔 SHA，convert_batch.py 保存本次批量 wrapper。需要從原始 tape 重新轉換時，--bundle 指向上一個 source branch research/hft244-v49-repro-20261002 的 research/repro/hft244_v49_20261002_v1/；--selection 用本包 SELECTION.json，--tapes 指向本機原始 archives，--out 使用新目錄。原始含識別資料的 tapes 未隨本批發布。

本批只證明資料格式、來源與轉換一致性；沒有為這 100 場生成 execution_clock，也沒有驗證新 native 執行結果或經濟結論。
