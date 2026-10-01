# HFT 2.4.4／V49 執行底座與 CG1AT 重現材料

本包提供既有原始碼、設定、完整 3 場公開輸入與既有 native 收據。2026-10-02 只做唯讀取回、匿名化、轉換與雜湊核對；沒有新 native 執行、建置、worker submit、fit 或 live 改動。標準答案是已完成的第二電腦執行結果，取回本機後原樣保存。

## 內容與來源

|需求|位置與證據|
|---|---|
|補丁版 HFT 完整建置原始碼|`engine/patched/`：99 檔，含 Rust、Python、3 個 Cargo workspace member、Cargo.lock 與 LICENSE；從 worker 的 V33 保留 source tree 唯讀取回，99/99 與歷史 `CANDIDATE_SOURCE_MANIFEST.json` 吻合。|
|原版與全部差異|`engine/upstream_build_sources/`：對應上游建置來源；`engine/all_changes_from_py_v2.4.4.patch`：全部 9 個異動檔（含新增 Cargo.lock）；`engine/PROVENANCE.json`：逐檔前後 SHA。|
|原版身分|上游 `nkaz001/hftbacktest` tag `py-v2.4.4`、commit `a244a14250b42d97fc305569c93c4117cd5e1dff`；Python 2.4.4、Rust crate 0.9.4。commit 由本機 git checkout 與 tag 核對，歷史 worker source receipt 本身沒有 `.git` 欄位，兩者的證據層級分列。|
|實際 native binary|歷史 candidate 與此次 worker 唯讀 Get-FileHash 同為 `033469835b44f94f1a022e419e79be61be72a9f2501ceea11b8e255b4824d145`；`engine/BACKEND_READBACK.json`。不附 DLL／pyd 或 SDK、Cargo cache；已取回的 candidate Python wrappers 7/7 與建置來源相同。|
|歷史 target_core_cycle_active_v8|`strategy/historical_v8/run_target_core_cycle_active_route_v8.py` 與 `run_target_core_cycle_rearm_v3.py`；另附凍結 adapter。這是舊 V8 直接 runner，保留旧 backend hash；不把它冒充 fresh100a 的最後 actor。|
|實際 V8 管理與後續 actor|`strategy/packages/CG1AT_fresh100a/base/frozen_runner.py` 的 `Policy`、`helper_rearm.py`；`clock_wrapper.py`、`money_runner.py` 及全部 base/overlay/root 模組構成實際轉換鏈。frozen_runner docstring 雖為 V9，工作目錄仍命名 `target_core_cycle_active_v8_*`，請依內容與雜湊辨識。|
|CG1AT／V58／CG1|`strategy/packages/{CG1AT_fresh100a,V58,CG1_historical}/` 原始碼與協定；`strategy/PROFILES.json` 保存臂別 env 與 argv。FULL 是 fresh100a 同 cohort 的 V58 參考，另附 V58 原始套件以區分版本。|
|執行層及所有修補|`strategy/runtime_template/` 43 個 Python 檔與 `strategy/runtime_scratch_CG1AT_2671717/` 44 個實際執行檔；含 owner-accounting、receipt adapter、pair accounting、legality、native system plan、quantity seam、open-funding runtime、完整 simulator inheritance chain。不限於 eof_runtime。|
|V33 EOF 修補與建置流程|`engine/repair_build/`；保留 Rust patch、Python patcher、scratch pins、contract、historical worker builder 與建置 helper；後者是來源參考，原路徑與 SDK 檔需由使用者配置。|
|引擎設定|`ENGINE_CONFIG.json` 與下節；每項連結到實際 scratch／Rust 來源。|
|公開簿到引擎輸入|`strategy/runtime_scratch_CG1AT_2671717/tools/hftbacktest_execution_tape_feed_v1.py`，其 event_row、normalize_match、archive loader 同目錄／src 全附；`utilities/rebuild_npz.py` 可在沒有 native engine 的環境重建提供的 NPZ。|
|驗收樣本|`fixtures/{2671717,2671719,2671768}/`：匿名 tape、完整 public books/frame payload、events.npz、公開市場逐筆成交、CG1AT 與 FULL 的 execution_clock/result/clock_trace/AUDIT/EXECUTION/PROCESS。|

upstream_build_sources 與 patched 提供完整三個建置 crate 來源；未附上游不用於此建置的 collector/connector、文件、圖片、大型教學 notebook，以及 `.git`、編譯產物和下載快取。99 檔清單中的範例仍原樣保存。策略完整歷史 MANIFEST/PROTOCOL 也保留，會引用其他已凍結市場與舊絕對路徑；本包只供應列出的 3 場資料，不能拿 100 場 worker.py 直接當 3 場啟動器。

## 引擎設定與時間假設

`run_eth_dagger60_smoke_v1.Sim` 建立 `HashMapMarketDepthBacktest`，單一 UP/YES 資產，線性 contract_size=1、entry/response 各 250 ms、PartialFillExchange、RiskAdverseQueueModel、tick=0.01、lot=0.01。250/250 是固定模擬假設，未宣稱為場地量測值。

RiskAdverse 沒有額外機率參數。新單 front queue 為當價可見量；trade 扣除 front；depth 更新把 front 限制到新可見量；front 變負時依 lot 取整成交。這是 market-by-price 隊列近似，沒有每張真實市場單的排隊位置，也沒有我方下單對公開簿的市場衝擊回饋。Rust `models/queue.rs` 是確切語義，overlay 中「only advances on trades」的註解不足以描述 front cap 細節。

new_bt 不設定 fee，Rust `BacktestAsset::new` 的 TradingValueFeeModel maker/taker 均為 0。零費用是模型設定，不能推成真實場地成本。PASSIVE 為 GTX LIMIT，ACTIVE 為 GTC LIMIT。native 的 DOWN 買入以 SELL、price=1-DOWN 自己買價表示，owner/accounting 層還原兩邊股數與成本。

歷史收據真正使用的是 **feed V1**，不是後來的雙時間軸 feed V2。V1 把簿的 **receivedMs 同時用作 exch_ts 與 local_ts**；原 tape 的 sourceMs 留著供檢查，但引擎未用它作交易所時間軸。公開成交 executedAt 轉 ms，再按 `mid` 加 500,000,000 ns + min(同 timestamp 序號,999)×1,000 ns；兩個引擎時間欄同加。成交原始排序為 timestamp、transactionHash、native price、quantity。原值已換成匿名字典序 rank，保留同值群與排序。

這批公開成交時間主要為秒精度；半秒偏移及組內微秒只是指定排序規則，並非實測微秒成交或 received 時間。不能拿這些人工細分時間直接估真實網路延遲。executionMeta 的 private order/settlement dictionaries 已遮掉，但保留 source/received/count 及列數；converter 只用列數做診斷。3 場移除前後完整 event bytes 和 local wakeups 一致。

原 actor 是 `.data(events)` 直接吃記憶體 NumPy structured array；NPZ 是本次交付的保存容器，鍵 `data`、`local_times_ms`。event 是 64-byte aligned：ev:uint64，exch_ts/local_ts:int64（ns），px/qty:float64，order_id:uint64，ival:int64，fval:float64。市場 feed 的 order_id 全為 0；golden receipts 的 synthetic simulator order IDs 原樣保留供 owner 對照。

## 策略與來源轉換

CG1 的 low-conviction/freeze 邏輯在 governor，CG1AT 另加 passive_offset=0 及 sequential self-cross resolution；V58 與 FULL 不啟用 CG1 的 gate。`CG1_historical` 是原 fresh40d CG1；另外保存 `CG1_gate_diagnostic` 的全計畫 freeze_gate 診斷，後者不能冒充原 CG1 或 CG1AT。CG1AT env 沒有 FREEZE_GATE，因此不宣稱它包含這個額外全計畫 gate。各版 env 見原 PROTOCOL，不能把不同歷史版同名臂視為同一來源。

現存 template 與實際 scratch 有 6 個差異：新增凍結 adapter；3 個 EOF Python postimage；2 個 BTC passive 18→15 來源 postimage。EOF 的3個 SHA 已由原 `patches.python_clock` 重算一致；15 股兩檔由原 `ticket_condition.install`、result 的 sizing_installation 與實際 SHA 相互核對。`VERIFICATION.json` 列出全部 before/after。這些固定規則與凍結 theta/qref 是既有狀態；這次沒有重新 fit。

V58 actor contract 指向 `v12g_fresh30_generalization_20260927_v45/base`。`strategy/shared_V45_base/` 另附本機該精確凍結 base 來源；其 manifest hashes 已核對，避免拿 fresh100a 的不同 metadata 冒充 V58 base。歷史 V8 的函式/路徑是來源參考，當代 CG1AT 應沿完整 overlay→sizing→money_runner→clock_wrapper→frozen_runner 的轉換鏈，而不是只執行舊 V8 文件名。

## 驗收與使用

選的是 fresh100a 協定的前 3 場，沒有以勝方／收益／成交量挑樣本。三場 event counts 為 20,018／18,974／11,842。每場兩臂各6個 golden 檔案，共36份，逐 byte 與既有 worker return 相同。標準答案未在本次重新生成。

在本目錄執行（只需 NumPy；驗證不載入 native 引擎）：

```text
python utilities/verify_bundle.py
python utilities/rebuild_npz.py --market 2671717 --output /your/new/path/2671717.npz
```

verify_bundle 同時檢查全部檔案 SHA、重新跑原 V1 Python 轉換、比較全部 event bytes 和 wakeups，再核對已有 clock SHA。NPZ ZIP 容器本身的 bytes 不保證不同工具版本每次相同，event payload 與 wakeup arrays 才是轉換驗收。

既有審計例外必須保留：CG1AT 的 2671719 以及 FULL 的三場，`failed_checks` 都含 `active_matches_opportunity`；即使 AUDIT.status 是 PASS，仍不能稱所有舊門檻通過。2671717、2671768 的 CG1AT 該檢查通過。本次驗證只證明來源/輸入/既有收據的完整性，沒有執行跨平台 native 重現，不能保證不同編譯器產出相同 pyd SHA 或相同 native 收據。

原建置紀錄為 Python 3.13.15、Rust 1.93.1 Windows MSVC，`cargo build --locked --offline -p py-hftbacktest -j 4`，Cargo.lock SHA `0d8fc5adf235b1f496e48ac04b0f23bec38111eae439168ee01ef147eca9fcdd`。2026-10-02 唯讀讀到 worker 環境為 numpy2.5.2、numba0.67.0、llvmlite0.49.0、httpx0.28.1；這是目前 readback，不把未留歷史 pip freeze 的事實補成當年保證。純轉換匯出在本機 NumPy2.2.6 完成。此包没有安裝工具鏈或呼叫 build。以原始碼重建時應使用 patched crate 與 lock；historical builder 的 prepare/reversal 是歷史對照流程，不能用它覆蓋此 patched 樹。

## 真實資料與隱私邊界

`observed_market_trades.json.gz` 是這 3 場的直接觀察公開成交（1,558／1,206／813 筆），沒有我方掛單/成交 linkage。未找到這 3 場可以配對的實盤 OUR 下單時間與完整成交收據，因此沒有用其他市場紀錄假充配對標準答案。過去實盤成本/延遲資料若另用，應分開 cohort 與數量規模。

沒有地址、帳戶、原訂單/transaction hash、raw evidence_json、Target 私人 tape、.env、DB、log 或私鑰加入 fixtures。原始碼內 API key 等字樣是環境變數讀取程式，不含實際值。唯一地址/tx hash 正規表示式命中位於上游原封不動的 Hyperliquid converter 公開 docstring 範例，patched/upstream 兩份 SHA 相同且來自上述上游 commit；不屬於本研究帳戶。`PRIVACY_REVIEW.json` 記錄此例外與白名單檢查。

檔案 SHA 清單見 `SHA256SUMS.json`；來源與建置元資料不是自主 live 輸入。本包沒有新增交易授權，也沒有把 winner/settlement/Target 路徑接入 actor。額外的 CG1_gate_diagnostic 所用凍結 base 另附於 `strategy/shared_CG4_base/`，不與原 CG1 base 混用。
