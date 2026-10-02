# 與本機原生 HFT 引擎對照規格（2026-10-02）

目的：量化「雲端簡化重播」「雲端 Linux 編譯的補丁版引擎」與「你本機的原生引擎／V49 策略堆疊」在同一批市場上的差距。不啟動 worker 以外的服務、不動 live。

## 資料（同一批 185 場）
research-data 已有事件檔與官方真標籤：`events/hft244_feed_v1_20261002_v1`（最新 70 場有標籤）、`hft244_fresh_after2807162_20261002_batch01/02/03`（15＋15＋85 場）。雲端結果（每場、每策略）在 `docs/research_specs/data/CLOUD_LAB_RESULTS_20261002.json`；5 分鐘現貨波動表在 `docs/research_specs/data/SPOT_RV5_TABLE_20261002.json`（215 場）。

## A. 平台對照（低工作量，先做）：同一份引擎碼、同一批事件，本機 vs 雲端
1. 準備 fixtures：`python tools/hft_repro/make_fixture_dirs.py <events 批次目錄> <fixtures 輸出目錄> --copy`（每批各一次，輸出到同一個 fixtures 目錄）。
2. 本機（原生引擎）執行：`python tools/hft_repro/strategy_lab.py <repro 包路徑> <fixtures 目錄> <labels.json> --only FAV_TAKER,UNDER_TAKER --out local.json`（labels 用 research_pack/labels 內各批 `*_LABELS.json` 合併；原生引擎需能 `import hftbacktest`，即補丁版）。
3. 比較：`python tools/hft_repro/compare_lab_results.py cloud.json local.json`（cloud.json 由 CLOUD_LAB_RESULTS 依策略取出，格式為 {策略: [每場列]}）。
4. 預期：PnL、股數、成本逐場一致（誤差 < 1e-6）。若有差：回報場次、時間軸、引擎版本與編譯條件。

## B. 策略堆疊對照（需要你們在 V49 堆疊上實作；不在雲端做）
把下列規則寫成 V49 堆疊可載入的覆蓋層（或同等的動作產生器），用與 A 相同的市場與事件檔重播，記錄每場的下單序列與損益。規則（凍結）：
- FAV：決策時 12 秒，熱門 F = 12 秒時中價 ≥ .5 的一邊；每 2 秒（12 ≤ t < 270）若「當前熱門中價」在 [.55, .70] 且 F 中價未跌到 ≤ .40（跌破即永久停止新買）且持股 < 300，則以賣價買 15 股；只有吃單；不賣出。
- 買冷門：每 2 秒（12 ≤ t < 290）若當前熱門中價 ≥ .75 且持股 < 300，買「非熱門」那邊 15 股，吃單；不賣出。
- v1（切換）：視窗開始前 5 分鐘現貨 rv_5m ≥ 3.1834e-05 → 買冷門，否則 FAV。v2：rv_5m ≥ 3.1834e-05 → 買冷門，否則不交易。
- 結算：贏的一邊每股 1；PnL = 持有勝方股數 − 總成本（零手續費；另行附 1%／2% 敏感度）。
需要記錄：每場的下單時間、方向、價格、股數、成交時間、成交價；總損益；與 A 相同的分類（未反轉／假反轉／真反轉）。
對照指標：每場損益相關、平均差與標準差、平均成交價差、下單數與平均下單秒數；接受標準（事先宣告）：平均差的絕對值 < 2、相關 > 0.95；若不符，請標出 V49 堆疊中造成差異的元件（例如被動轉主動、修補、風險上限、帳務時點）。

## 雲端已有的對照（供比較）
簡化 1 Hz 重播 vs 雲端引擎（170 場）：FAV 每場平均 +4.9 對 +4.7、相關 0.963；買冷門 −0.9 對 −2.5、相關 0.995（見最終報告）。
