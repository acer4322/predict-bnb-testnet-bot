# 雲端（Linux）重現 HFT 2.4.4／V49 引擎：第一關驗收

- 來源：分支 `research/hft244-v49-repro-20261002`（commit 3bf09cf）的 `research/repro/hft244_v49_20261002_v1/`。
- 建置：Linux x86_64、Python 3.11、Rust 1.94.1，`maturin build --release --locked`（補丁版 crate，需先把 `README.md` 複製成 `README.rst`），numpy 2.2.6、numba 0.68.0。
- 驗收：`engine_gate.py R MID [ARM]` 把 golden `clock_trace.native_actions` 的下單/撤單依時間餵給 Linux 引擎（risk queue、250/250 ms、零費用、tick/lot 0.01），與 golden 的 per-order 成交量和 native state 比對。
- 結果（2026-10-02）：3 場 × 2 臂（CG1AT／FULL）共 6 組，全部 per-order 成交量 100% 相同（78/78、159/159、166/166、142/142、65/65、122/122），native position／balance／volume／trades 與 golden 一致（浮點誤差 <1e-4）。
- 限制：這是「同一組下單動作」的引擎層重現，策略層（V8／CG1AT 的決策鏈）尚未移植到 Linux（原始碼含 Windows 專屬的 hostname 斷言、`C:/BTC5M-worker` 路徑與 `creationflags`）。

## 被動配對機制（maker_pair.py，3 場、6 組參數）
- 規則：每邊最多 K 張 15 股被動 GTX 買單掛在該邊最佳買價，價格移動（≥2 秒）或超過 30 秒就撤改，領先一邊超過 D 股就不再掛該邊，270 秒後停止，持有到結算，不做吃單補腿。
- 結果（引擎：risk queue、250/250 ms、零費用）：兩邊持股接近平衡，但配對成本 0.98–1.05，只有 D 不設限的少數組合 <1；全部 6 組 3 場總損益 −64 至 −160。這個簡單被動配對在模擬器內沒有鎖住優勢。
- 限制：只有 3 場，只能說明機制在模擬器內運作的方式，不是統計結論。
- 本機重跑：`python tools/hft_repro/maker_pair.py <R=research/repro/hft244_v49_20261002_v1 的路徑> <labels.json>`，需要本包的 Linux/Windows 皆可編的補丁版引擎與更多市場的 events.npz／FIXTURE.json。
