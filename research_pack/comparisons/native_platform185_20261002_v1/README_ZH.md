# 平台對照 A：185 場 Windows 原生結果

`A/local.json` 是原樣的 `{FAV_TAKER: [185 筆], UNDER_TAKER: [185 筆]}`。兩策略共 370 筆，全部使用既有官方真標籤，沒有中價推斷、重抽樣或替補。job `native-engine-platform-185-20261002-v1` 單次提交，worker `DESKTOP-JIERAGF`，succeeded／rc0，worker elapsed 23.799 秒。四個回收檔案的跨機 SHA 全部相同，見 `A_COLLECTION_AUDIT.json`。

**平台一致性目前 UNKNOWN。** 已拉到規格 commit `1157f6f9`，但規格引用的 `docs/research_specs/data/CLOUD_LAB_RESULTS_20261002.json`、`SPOT_RV5_TABLE_20261002.json` 在本機、已取得的 Git refs 與 `research-data` 都沒有。缺少雲端逐場結果，因此尚未執行 cloud/local 比較，不能稱誤差小於 1e-6；未補造雲端結果或 rv。`MISSING_INPUTS.json` 記錄此界線。

資料身分固定為 `research-data` commit `0237417a`：feed70 官方標籤指定的 70 場，加 batch01／02／03 的 15／15／85 場。原 feed 的另 30 個 Target 市場不在本次 A。`INPUTS.json` 和 `INPUT_AUDIT.json` 提供事件、META、labels 的 hash 與批次；185 場全部吻合。36 場有既有 clock quality warning，仍原樣納入。公開逐筆成交收集完整性仍是 UNKNOWN。

依原 `make_fixture_dirs.py` 四次產生 fixtures，`strategy_lab.py`、引擎 helper 都逐 byte 不改。job wrapper 只固定 backend、檢查雜湊及觀察 run() 完成進度；沒有替換演算法、下單或成交函数。原工具的 bootstrap／經濟 flags stdout 不是這次的平台判定。

載入的是 V33 保留的 V49 candidate backend，而不是 worker 預設舊引擎。pyd SHA `033469835b44f94f1a022e419e79be61be72a9f2501ceea11b8e255b4824d145`；7 個 wrapper SHA 都符合既有重現包。舊預設 pyd 為 `74af885f...`，沒有用來回放。Python 3.13.15、numpy 2.5.2、numba 0.67.0、llvmlite 0.49.0 是本次實際 readback。既有 binary 建置紀錄為 Rust 1.93.1／Windows MSVC、`cargo build --locked --offline -p py-hftbacktest -j 4`；本次沒有重建。完整補丁 source 在 `research/hft244-v49-repro-20261002` commit `3bf09cf6` 的 `research/repro/hft244_v49_20261002_v1/engine/`。

引擎設定：250／250 ms 固定延遲，risk-adverse queue，PartialFillExchange，tick／lot 0.01，fee 0；单一 UP native asset，DOWN 買映射成 native SELL。保留 feed V1：簿 received 時間用作兩條引擎時間軸，公開成交保留既定人工排序偏移，不能當成實測網路延遲。

規格 B 與原 lab 的已知語義差異另保留：lab 的 1 Hz 時鐘從 firstReceivedMs+2000 起，不是精確 12+2k 秒；UNDER 分支原碼沒有 12 秒下限；FAV 額外檢查原始熱門 F 的 [.55,.70] 區間並在 flip 撤單。lab 用 firstReceivedMs 向下取 300 秒窗，未直接讀 META 的正式窗。A 全部原樣執行，以避免把策略語義改動混入平台比較。

取得 cloud JSON 後，在包內或配置路徑後執行原 `source/compare_lab_results.py cloud.json A/local.json`。另用 `source/audit_comparison.py cloud.json A/local.json LABELS.json --out A_COMPARISON.json` 核對完整 185 場、各別 UP／DOWN 股數及有限數值，避免原 print-only 工具的交集比較掩蓋缺場。

沒有改凍結包、資料庫、live、armed、stake、服務、排程或 collector，0 fit。A 為直接 native lab；未聲稱驗證 CG1AT 全部舊 owner／strategy gates，也未把既有 `active_matches_opportunity` 失敗改成 PASS。
