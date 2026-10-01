# 畢業研究：需要本機完成的兩件事（雲端 session 做不到）

目標候選機制：FAV 熱門收割（買價帶 .60–.80、上限 300 股）+ 翻轉當下對沖全部庫存（HEDGE 1.0）。
雲端結論與腳本：`tools/flip_response_check.py`、`flip_response_real.py`、`flip_hedge_scaled.py`、`huge_loss_check.py`、`user_def_check.py`。

## 任務 A：更多有真標籤的新市場（不重送已完成 job）
1. 取得「不在 `research_pack/labels/labels100a.json` 與目前 220 場內」的新市場，優先有 Polymarket/交易所結算標籤者，目標 ≥ 150 場。
2. 用 `tools/sync_research_pack.py` 同步到 `research-data` 分支（只需 `public_<market>.json.gz` 與標籤檔；不需 trace／訓練資料）：
   `python tools/sync_research_pack.py RETURNS_ROOT --markets <ids> --extra labels_new=PATH --out research_pack_out --git-push --branch research-data`
3. 標籤檔格式同 `labels100a.json`：`{"records":[{"market_id":..., "winner":"UP|DOWN"}]}`。真標籤，不要用最後中價推斷。

## 任務 B：真實成本核對（只讀、不啟動實盤）
- 場地是否允許「賣出」，與賣出／買入的手續費。
- 翻轉當下（FAV 持有 ≥150 股時）對面一側的可成交深度與價差；HEDGE 1.0 需要一次吃掉約等量庫存。
- 以上若不可行，HEDGE 1.0 作廢，回退到 SELL 版或 FREEZE（見雲端比較表）。
- 若可行，回報：每股手續費、對面深度（前 3 檔股數）、實際延遲 (ms)。

## 雲端收到後會做
- 用新標籤重算整體均值 CI（`flip_hedge_scaled.py`、`user_def_check.py`），預先宣告門檻：整體 CI 下限 > 0、未反轉 > 0、D1 通過、單場最大虧損 < 3×平均贏場。
- 不通過就明說不畢業，不改定義。
