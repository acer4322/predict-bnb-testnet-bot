# 雲端 → 本機 Claude 總交接（2026-10-03）

本檔整理雲端工作階段（Claude Code on the web）的全部研究結論、工具與待辦。雲端只能讀 GitHub；所有實盤、原生引擎與即時資料由本機執行。**接手時先讀本檔，再依 CLAUDE.md／AGENTS.md 既有規則；不要重送已完成的 job 或一次性實單。**

## 0. 分支與資料
- 工作分支：`feature/dashboard-v2-polyhermes`（本檔、所有雲端腳本 `tools/`、規格 `docs/research_specs/`）。
- 資料分支：`research-data`。重要 commit：
  - `2d5c5972` ETH5M 861 場公開簿；`7cde0e2b` 本機原生 185 場平台／V49 對照；
  - `0e43c9cd` 歷史實單延遲（Binance Prediction REST）；`ca8b2869` Binance Prediction 一次性實單；`18cfa109` **直連 Predict.fun 一次性實單**。
- HFT 重現：`research/hft244-v49-repro-20261002`（Linux 重建的 hftbacktest 2.4.4）。
- 總報告：`docs/research_specs/FINAL_REPORT_20261002_ZH.md`（逐段累積，所有數字與失敗皆在內）。

## 1. 使用者的標準（必須遵守）
- 畢業標準（EACH）：NO_FLIP／FALSE_FLIP／TRUE_FLIP 三類市場平均損益，全部為正，或只有一類為負且其絕對值 < 另兩類各自（不可相加）；另需整體均值 CI 下限 > 0。巨大單筆虧損＝虧損 ≥ 3 倍平均獲利市場。
- 實單只在候選達畢業／預登記標準後使用；最小下單 1 USDT（名目）。
- 方法：預登記、探索／確認／最終分割、市場層級自助 CI、揭露已用（燒過）的樣本、不得事後挑選或用未來資訊篩選。

## 2. 已結案（不要重做）
- FAV（買 favourite）、UNDERDOG、v1／v2（現貨波動切換）、各種對沖／保險／DIP／停損／閘門／價格帶／買量遞減、便宜側收割、跨市場（BTC↔ETH、Polymarket）、ETH 專屬規則 —— **皆未通過 EACH**。共同卡點：平靜場成本 > 反轉場獲利；真假反轉在買入當下無法區分（AUC 0.4–0.57）。
- 真反轉虧損解剖：虧損＝買入成本，75% 在 12–60 s 平穩時買進，不是追跌；改變買量曲線只會同比例縮放三類。
- 本機原生對照（7cde0e2b）：A 平台完全一致；B 中 UNDER／v1／v2 通過、FAV corr 0.948 未過但不改變結論。
- 簡化雙邊被動配對：在任何佇列模型／延遲下皆為負（−31～−71／場），即使假設永遠排第一仍 −49。

## 3. Target 逆向分析的核心結論（docs 報告第「Target 為什麼這樣交易」節）
1. 每場收尾**兩邊股數相等**（UP 佔比 0.503，sd 0.043）⇒ 結果中性，損益＝股數×(1−配對成本)，因此沒有真反轉風險。
2. 被動單：30 股、掛最佳買價或下方 1–5 檔、從不改善價；被動腿每場 **−48.2**（被延遲交易者撿走）。
3. **主動單才是利潤來源**：TAKER +5 s 標記值 +2.0 分／股（兩半段穩定），每場 **+64.7**；現貨先動、Predict 未更新時吃單。
4. Predict 對現貨的反應延遲：相關峰值 +200～300 ms（交易所時間）；≥700 ms 消失。

## 4. 現行唯一通過預登記的候選：現貨延遲吃單
規格：`docs/research_specs/SPOT_LATENCY_TAKER_SPEC_20261003_ZH.md`（含所有結果）。
- 規則：Binance 近 500 ms 對數報酬 ≥X bp → 買該方向側，15 股，冷卻 2 s，市場第 15–285 秒。
- 一次性判定（219 場未用）：X=3、L=400 ms，+5 s 價差 +3.81 分 [+2.01,+5.55] → PASS。
- 手續費：使用者截圖確認 **名目 2%**（以份額扣）；p>0.5 時是 2%×p 或 2%×(1−p) 尚未確認（目前以 2%×p 計，較保守）。
- 延遲：Binance Prediction 通路接單後約 0.8–1.0 s 才成交 → **不可用**；**直連 Predict.fun**：簽名 8 ms、寫出→ACK 126 ms（冷連線另加 TCP/TLS 132 ms）、ACK→orderAccepted 約 160 ms；現貨訊號→到達估 100–325 ms。
- 以直連延遲重算（`tools/spot_latency_direct.py`，含深度 VWAP＋2% 費），確認段：X=1/L=165 +3.05 分；X=2/L=165 +6.37；X=3/L=250 +5.84；X=3/L=325 +3.97（CI 皆 >0）；X=1/L=325 已為負。**到結算的期望值大多仍含 0（未證實）**。
- 尚未解決：了結方式（持有到結算 vs 5 秒後賣出需再付費＋價差）、真實撮合時刻、p>0.5 的費率公式、規模（每場約 2–11 筆 ×15 股）。

## 5. 唯一的下一步：影子模式（不下單）
- 規格：`docs/research_specs/SPOT_LATENCY_SHADOW_SPEC_20261003_ZH.md`。
- 收集：`python tools/spot_latency_shadow.py --asset BTC --hours 6`（需 `PREDICT_FUN_API_KEY` 唯讀 key；只讀，不含下單程式）。
- 分析：`python tools/spot_latency_shadow_report.py data/shadow_spot_latency --labels <官方標籤.json>`。
- 主判定：EXCH 視角 X=3、L=250、≥60 有訊號市場、扣費 +5 s CI 下限 > 0。PASS 後才可提最小金額實單探測（需使用者批准）。
- 注意：腳本在雲端只用歷史資料自我測試過（結果與重播一致），**未在真實 WebSocket 上跑過**；首次執行請先跑 10 分鐘確認兩個串流都有幀、市場切換正常。訂閱／取消訂閱格式沿用 `predict_fun_observer.py`。

## 6. 雲端工具索引（tools/）
- Target：`target_inventory_logic.py`、`target_dollar_balance.py`、`target_hedge_timing.py`、`target_quote_rules.py`、`target_queue_priority.py`、`target_role_edge.py`、`target_pnl_decomp.py`、`target_edge_by_price.py`、`target_half_compare.py`、`target_break_scan.py`、`target_economics.py`、`target_false_vs_true_test.py`、`target_regime_drivers.py`。
- 延遲：`spot_predict_lag.py`（1 Hz）、`spot_latency_arb.py`（毫秒、可 JUDGE/MINID）、`spot_latency_direct.py`（直連延遲＋深度＋費）、`fetch_aggtrades.py`（Binance aggTrades 下載）、`spot_latency_shadow.py`／`spot_latency_shadow_report.py`（影子）。
- 被動／配對：`ladder_front_queue_bound.py`、`ladder_spot_guard.py`、`hft_repro/strategy_lab.py`（QM／LAT 環境變數、TL_* 階梯策略）。
- ETH／跨資產：`eth_frozen_transfer.py`、`eth_specific_search.py`、`eth_flip_features.py`、`pooled_cross_asset_search.py`、`cross_market_lead.py`、`true_flip_loss_anatomy.py`、`fav_taper_variants.py`、`cheap_side_harvest.py`。
- 判定：`early_fade_judge.py`（拒絕已燒市場；BTC 新場次 ≥100 才判）。
- 雲端快取（`*_cache.pkl`、aggTrades gz）在雲端 scratchpad，**沒有推上 GitHub**；本機需要時用 `fetch_aggtrades.py` 重抓（公開 API，519 場約 2–3 小時）。
