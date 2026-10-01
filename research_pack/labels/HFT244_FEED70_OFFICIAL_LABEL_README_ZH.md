# 新 70 場 BTC5M 的官方真結算標籤

對應 research-data commit 8173c6c852574ff3445d03289b05cdc472dec180 的 research_pack/events/hft244_feed_v1_20261002_v1/ 中 selection_group=recent_gt_2685217 的固定 70 場（2802399–2809470；不是 ID 區間內所有市場）。選單無替換。

HFT244_FEED70_OFFICIAL_LABELS.json 的 records 僅含 market_id 與 winner（UP/DOWN），可直接與該包 INDEX.json 配對。70/70 已正式結算，0 pending、0 ambiguous/nonbinary、0 API failed；原先已有的 11 場與本次官方結果全同，本次補上其餘 59 場。

HFT244_FEED70_OFFICIAL_LABEL_PROVENANCE.json 保存每場官方 GET https://api.predict.fun/v1/markets/<id> 的 allowlist 欄位：terminal status、outcomes.name/status、winner flags、resolution.name/status、查詢時間 ms、HTTP status 與原始 response SHA256。只取市場 RESOLVED/SETTLED 且恰一個 UP/DOWN outcome 明確 WON 的結果，並核對官方 resolution；沒有用最後中價或標的 endPrice 推斷。

HFT244_FEED70_OFFICIAL_LABEL_VERIFICATION.json 記錄 exact cohort、既有11場比對與完整性核對。查詢時間是本機接收官方回應的時間，不是交易所實際結算時間。沒有重新生成／改動 events.npz、修補歷史 frozen labels、DB 或 live/collector/worker；所有欄位只供 offline evaluation，不作策略 runtime 輸入。

另：補齊真標籤不會修復已揭露的 8 場盤口覆蓋缺口，使用時仍須查看原事件包的 source_start_tail_and_gap_within_5000ms。
