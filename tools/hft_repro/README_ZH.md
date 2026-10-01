# 雲端（Linux）重現 HFT 2.4.4／V49 引擎：第一關驗收

- 來源：分支 `research/hft244-v49-repro-20261002`（commit 3bf09cf）的 `research/repro/hft244_v49_20261002_v1/`。
- 建置：Linux x86_64、Python 3.11、Rust 1.94.1，`maturin build --release --locked`（補丁版 crate，需先把 `README.md` 複製成 `README.rst`），numpy 2.2.6、numba 0.68.0。
- 驗收：`engine_gate.py R MID [ARM]` 把 golden `clock_trace.native_actions` 的下單/撤單依時間餵給 Linux 引擎（risk queue、250/250 ms、零費用、tick/lot 0.01），與 golden 的 per-order 成交量和 native state 比對。
- 結果（2026-10-02）：3 場 × 2 臂（CG1AT／FULL）共 6 組，全部 per-order 成交量 100% 相同（78/78、159/159、166/166、142/142、65/65、122/122），native position／balance／volume／trades 與 golden 一致（浮點誤差 <1e-4）。
- 限制：這是「同一組下單動作」的引擎層重現，策略層（V8／CG1AT 的決策鏈）尚未移植到 Linux（原始碼含 Windows 專屬的 hostname 斷言、`C:/BTC5M-worker` 路徑與 `creationflags`）。
