# BTC 5M Lab

本地 Binance Prediction 模擬交易監控網站。前端監聽 TCP 4310，並依照瀏覽器目前開啟的主機名稱自動連到相同主機的專用 TCP 8766 模擬 API，因此可同時使用 `localhost` 或電腦的區域網路 IPv4 位址。TCP 8765 保留給其他本機應用，不會被 BTC 5M 啟停腳本終止。

網站每 1 秒刷新監察畫面與模擬帳本。瀏覽器分頁隱藏時會暫停輪詢並取消未完成請求；持續開啟時每 30 分鐘自動重新載入以重建頁面 heap，並透過 `sessionStorage` 保留目前策略分頁、Observer 頁碼與捲動位置，未套用的實單規則草稿仍由既有 `localStorage` 保留。全部 23 個 M 系列 ID 與 A～L 舊策略仍保留，但已實現收益不高於 -500 USDT 的策略會集中到「暫時停止觀測」頁籤；目前停止 A、C、D、J、L、M、M2、M4、M5、M6，以及 M 出場分支 MX_T60、MX_T80、MX_T98。其餘 M 系列、M／M0 出場分支與舊策略分頁繼續觀測。M 系列的後端即時實驗引擎按收到的事件獨立判斷，網站的 1 秒刷新只影響畫面，不會把後端策略降成每秒執行。

## 歷史策略回測

專用回測器會以唯讀方式重播 `data/simulation.db` 的歷史 Prediction 雙邊最佳報價、可見深度、簿時差、簿年齡、剩餘時間及正式結算，不會寫入模擬或實單帳本。預設直接比較 M01 與禁止剩餘時間不高於 180 秒進場的 M01T180：

```powershell
python -m predict_bot.backtest
```

結果會寫入 `data/backtests/` 的 JSON 摘要與逐筆交易 CSV，並依時間順序分成 development／validation／holdout（60%／20%／20%）。若要測試新的 M01 類參數組，複製根目錄的 `backtest-strategies.example.json` 後執行：

```powershell
python -m predict_bot.backtest --strategies your-strategies.json
```

M01O Observer Replay 會依歷史快照時間順序重建每輪雙邊觸及、有效穿越、30s／60s ER 與已結算市場的滾動 RANGE／UNCERTAIN／TREND 分類，再比較未過濾 M01、F2、F1 與 LIVE：

```powershell
python -m predict_bot.backtest --observer-replay
```

Observer 的歷史輪只會在該市場最後一筆快照處理完後才加入，回放不會把當輪正式勝負洩漏給進場閘門。若正在運行的 API 暫時鎖住 `simulation.db`，回測器會最多等待 60 秒取得唯讀快照。

目前回放來源是採集器保存的 top-of-book 快照，因此結論只代表已記錄到的報價時點，不宣稱重建快照間遺漏的逐筆行情。

## 主頁：M 系列開盤方向與延遲實驗

- 策略 M（原始策略的 WS 事件驅動版，開盤方向盲單）：每輪開盤後前 10 秒內，以即時引擎收到的第一個有效非零跨來源偏離決定方向；Binance BTCUSDT spot 高於正式 Prediction `startPrice` 買 UP，低於時買 DOWN，相等則等待下一筆 Spot trade 事件。Prediction 價格不參與方向判斷，選定側只用當下 ask 與可見深度模擬部分成交，買後持有到正式結算。舊版每秒策略交易由後端 `strategy_version` 分開保留。
- 策略 M0（固定種子隨機基準）：每個市場用固定種子產生一次可重現的 UP／DOWN，完全不讀現貨、永續或 Prediction 方向，是用來比較策略是否優於隨機的對照組。
- M0 卡片另外顯示目前連續命中、平均連續命中、目前連續失敗、平均連續失敗與「一勝一敗平均連續」。最後一項把 M0 結果投影成「上一盤 M0 勝才觀察下一盤」的 M0W 理論交易序列，再取其中連敗段的平均長度。只計目前測量起點後已結束且已有損益的 M0 交易；未平倉不納入也不切斷 streak，按「歸零已實現收益」後五項從新 cohort 重新計算。
- M0 的 24 小時分佈面板會在每個時段同時顯示勝率、平均連勝、平均連敗與一勝一敗率。平均 streak 只連接同一台北日期、同一小時且時間相鄰的 5 分鐘局；跨小時、跨日或資料缺口會切斷。一勝一敗率把本局歸入其進場小時，以「上一個相鄰 M0 勝、本局敗」除以「上一個相鄰 M0 勝」的可進場樣本數，代表方向層面的 M0W 理論敗率；畫面同時顯示分子／分母。
- 正式實單頁可選擇 M 系列與 live 白名單研究訊號、設定每策略金額，並調整 M0 每小時門檻。`R_MICROPRICE` 與 `R_OFI` 已列入 live 白名單；最多三個實單策略槽位各自持久化金額、Observer 開關與 `F1 / V2 / V3 / V4 / V6` 版本，因此 Futures Lead 系列、`R_MICROPRICE`、`R_OFI` 與 `R_CALIBRATED_VALUE` 可分別使用不同版本。每個策略槽位也可獨立開啟「兩連敗後冷卻一個市場」；它只使用該策略的新正式結算，第二次連敗後跳過下一個原本合格的市場並歸零，不採用被跳過市場的事後結果。此選項預設關閉且舊設定不會自動啟用。版本可在 Observer 關閉時預先選擇；啟用後只過濾該槽位的後續新訊號，缺少同市場 READY context 時 fail closed。頁面會先從輕量 `/api/live-rules` 載入真實規則，不再把預設 `M0W` 誤標為已同步。舊版共用 Observer 設定會在載入時遷移到每策略設定。選擇 `M01O_F1` 時仍使用原本的 F1 gate。設定持久化、只影響新訊號；主機本身與同一私有區域網路中經合法監察頁連線的手機都能儲存。手機仍不能恢復已暫停的實單。
- 最上方固定顯示目前市場的實單持倉區；沒有成交時顯示空持倉。任一正式實單策略有已成交且未結算的持倉時，市場卡會用每秒更新的 outcome 最佳買價估算目前可售價值、收益與 ROI。使用者可二次確認後選擇「現價 LIMIT GTC」或「MARKET FOK」賣出；後端會重新核對 Binance wallet shares、取得 SELL quote 並將出場獨立記帳。手動賣出可由本機或同一 private LAN 上、透過本機 `4310` 面板網址開啟的裝置操作；API 要求相符 Origin／Host 與專用確認標頭，Windows 防火牆仍限 `LocalSubnet`，公網或不相符來源一律拒絕。timeout／5xx 會標成不確定且不允許盲目重送。
- 前端核心統計使用獨立唯讀 SQLite/WAL 連線，不再取得即時策略 Store 的 Python 鎖。M／M0 出場與互補實驗改為進入對應分頁時才載入；毫秒延遲、微結構、M01 Observer／WS 診斷預設收合，按「顯示」才建立完整面板。這些開關只控制畫面，不會停止後端 F1 gate 或策略執行。
- 實單頁的「勝方結算領取」與「正式訂單、成交與損益」各自獨立翻頁，每頁最多顯示 10 筆；每秒同步新資料時保留目前頁碼，資料減少時自動回到仍有效的最後一頁。
- 正式 LIMIT 報價高於訊號限價時，一般策略後端最多重取一次 quote，重報 LIMIT 的硬上限是原訊號價 `+0.10`（最高 `0.99`）；第二次仍超限就拒絕，不會轉成市價單。M01／M01T180／M01O_F1／M01W 的最大進場價只負責觸發，重報價也可在該筆訊號價上方接受最多 `0.10`，實單滑價容許維持 1%。`R_MICROPRICE`、`R_FUTURES_LEAD`、`R_CALIBRATED_VALUE`、`R_OFI_EVENT_CUM` 可各自重取一次 quote，重報 LIMIT 的硬上限皆為原研究訊號價 `+0.05`（最高 `0.99`）；`R_CALIBRATED_VALUE` 改由模型機率、成交成本及含費淨優勢決定，不再套用舊的 `0.40–0.69` 固定價格區間，`R_OFI_EVENT_CUM` 則仍保留該區間。同市場的最多三個已選策略各自判斷、各自使用自己的金額上限，互不排除。
- 尚未按下「確認並套用」的實單規則會以本機瀏覽器草稿保存，切換頁籤或重新整理後仍會恢復，頁籤也會持續標示「有尚未套用的實單規則草稿」。成功寫入後端或主動放棄草稿時才會清除。
- 策略 M01（M0 低價等待進場）：先以與 M0 相同的固定種子方向鎖定 UP／DOWN，但不立刻買；整輪等待選定側 ask 降到 0.30 或以下，才依第一檔可見深度部分成交，買後持有到正式結算。未到價則該輪不交易。
- 策略 M01T180（時間門檻對照）：方向、價格上限與成交規則都與 M01 相同，但只在市場剩餘時間嚴格大於 180 秒時允許進場；剩餘時間等於或低於 180 秒一律禁止。它保留獨立模擬帳本，也可在正式實單規則中單獨選用；啟用模擬策略本身不會自動開啟實單。
- 策略 M01T180D（DOWN + T180 探索組）：共用 M01T180 的固定種子、最高買價、本金及訂單簿品質設定，但只在固定種子方向為 DOWN 且剩餘時間嚴格大於 180 秒時模擬進場。使用獨立帳本，固定為紙上交易。
- 策略 M01TASYM（非對稱時間門檻探索組）：共用 M01T180 的方向、最高買價、本金及訂單簿品質設定；UP 要求剩餘時間嚴格大於 210 秒，DOWN 要求嚴格大於 180 秒。使用獨立帳本，固定為紙上交易。
- M01 過濾強度對照的三組統計帳本都維持紙上交易：`M01O` 是 F2 嚴格組，要求歷史 `RANGE`、當輪震盪分至少 2 且沒有 TREND override；`M01O_F1` 是 F1 寬鬆組，歷史只要不是 `TREND`、當輪分數至少 1 且沒有 override；`M01O_LIVE` 是 LIVE 當輪優先組，要求至少 6 輪歷史樣本、歷史不是 `TREND`，並在當輪 UP／DOWN 直接 Ask 都曾不高於 0.30 時放行，不等待 ER 或穿越。三組共用同一 market ID、固定種子方向、價格快照、可見深度及模擬成交規則。只有 F1 gate 另經 fail-closed 實單轉接與二次驗證後，可由使用者明確選為正式實單訊號；F2 與 LIVE 仍不可選。
- F2／F1 當輪計分採分階段規則：開盤 0～60 秒使用雙邊觸及（+2）、有效穿越至少 2 次（+1）和 30 秒短窗 ER 不高於 0.35（+1）；滿 60 秒後才使用 Median 60s ER。TREND override 只在至少已有兩筆、時間跨度至少 1 秒的有效 60 秒 ER 觀測後生效，條件為 Median ER 至少 0.55 且穿越不超過 1 次。指標尚未成熟記為 `NOT_READY`，資料缺失或超過 5 秒未更新才記為 `MISSING_OR_STALE`。
- 對照面板累積候選、放行及成交結果，顯示交易覆蓋率、實際成交率、勝率、平均成交價、扣除模擬費用後平均損益、Profit Factor、最大回撤、零交易日比例，以及被擋原 M01 交易的反事實勝負與損益。至少累積 200 輪、最好 300 輪後再判讀。
- 策略 M01-Floor（價格下限對照，內部 ID `M01F`）：方向與 M0／M01 完全相同，但只在選定側 ask 落入 0.20～0.30（含邊界）時模擬進場；低於 0.20 不追低，仍會在整個窗口等待價格回到區間。它使用獨立紙上帳本、摘要及歸零起點，不接入正式下單。
- 策略 M01-Rebound（低價反彈，內部 ID `M01R`）：方向與 M0 完全相同。選定側 ask 首次到達 0.30 或以下後開始追蹤最低價，每次再創新低便下移基準；第一個 ask 達到「目前最低價 + 0.10」時依可見深度模擬買入，例如 0.10→0.20 或 0.30→0.40，之後持有至正式結算。它使用獨立紙上帳本，不接入正式下單。
- 策略 M0W（M0 命中續買）：上一盤 M0 已結算命中才放行，本盤依 M0 的固定種子方向於開盤窗口進場；上一盤失敗、未結算、無交易或沒有前一盤資料都不買。
- 策略 M01W（M0 命中低價等待）：上一盤 M0 已正式結算命中才放行，本盤鎖定 M0 方向後等待選定側 ask 不高於 0.30 才進場；不要求上一盤 M01 有成交，未到價不交易。
- 策略 M1（永遠 UP 基準）：每個市場固定選 UP，用來量測資料樣本及結算結果是否本身偏向 UP，不代表看漲判斷。
- 策略 M2（首次非零偏離）：原 M 的獨立複本；即時實驗引擎收到第一個有效且非零的 spot－`startPrice` 偏離後，正數鎖定 UP、負數鎖定 DOWN。交易、摘要與歸零 cohort 都和原 M 分開。
- 策略 M3（門檻偏離）：只有 spot 相對 `startPrice` 的絕對偏離達到設定 bps 門檻後才依正負選方向，用來過濾過小的符號變化；門檻不是已校準勝率。
- 策略 M4（連續方向確認）：要求指定筆數的不同 Binance Spot trade 事件具有相同偏離方向才鎖定 UP／DOWN；同一筆 trade 的重複評估不會被算成多次確認，也不假設上游每 1 ms 更新。
- 策略 M5（永續成交方向）：以 Binance USD-M BTCUSDT perpetual `aggTrade`／last traded price 相對正式 Prediction `startPrice` 的偏離決定方向，明確不使用 `markPrice`。這仍是跨來源比較，固定基差可能造成長期方向偏移。
- 策略 M6（歷史基差校正）：開盤 Spot－`startPrice` 基差樣本由與策略開關（toggle）無關的 canonical WS 資料持續收集。估計只使用已完成的前序市場，累積至少 20 個（或設定的更高門檻）後凍結跨輪 running mean；當輪扣除平均基差再依殘差正負選方向，不回寫自己的基準，也不讀未來資料。
- 策略 M7_1／M7_2／M7_3／M7_5（開盤絕對 deadline 子組）：deadline 分別固定為市場開盤後絕對 +1／+2／+3／+5 秒，不是首次 M2 或 Spot 訊號後再延遲。每組在自己的 deadline 取最後一筆 `received_monotonic_ns <= deadline` 且在 deadline 時年齡不超過 1 秒的 Binance Spot trade，以其相對正式 `startPrice` 的正負決定方向；因此四組方向可能不同。選定後只等待第一筆 deadline 後的有效 Prediction 訂單簿事件，並僅在共用 execution grace 內模擬成交。四組各自啟用、記帳、統計及歸零。

## 五組研究策略 forward paper

`R_MICROPRICE`、`R_OFI`、`R_FUTURES_LEAD`、`R_CALIBRATED_VALUE`、`R_CONSENSUS` 是五組主策略；另有八組既有 Shadow、五組 Lead Observer Shadow（`R_FUTURES_LEAD_OBSERVER_F1/V2/V3/V4/V6`），以及 `R_OFI_OBSERVER_V3`、`R_MICROPRICE_OBSERVER_V3/V6`、`R_CALIBRATED_VALUE_OBSERVER_V6` 四組策略搭配。九組 Observer 都只能在同市場來源策略已實際開出模擬單後，以相同方向與相同可執行成交模型進場；各組資金獨立，門檻預先凍結，並各自累積 100–200 筆 chronological validation。共二十二組研究策略都先寫入自己的 paper 帳本；四組策略搭配固定為 paper only，不在 live 轉送白名單。`R_FUTURES_LEAD_REGIME_REVERSE_3L` 的正反控制與 `R_FUTURES_LEAD_REVERSE` 的依賴式兩腿安全規則維持不變。

三組新 Futures Lead shadow 共用預先凍結的訊號規則：spot／futures 最新成交 age 不得超過 500ms、Prediction book age 不得超過 1000ms；連續兩個 3 秒窗都必須是 spot 與 futures 同方向，且 `futures_return - spot_return` 的 signed residual 沿 futures 方向至少 0.25 bps。距離版用最近 60 秒 causal spot realized variance，將 `log(spot/startPrice)` 除以剩餘時間波動率後轉成終局機率，扣除模擬滑價、進場價與 taker fee 後 edge 至少 0.03。30 秒退出版只在進場後 30–45 秒的第一個新鮮 Prediction book 且第一檔 bid 深度足以覆蓋全部 shares 時退出，否則繼續持有至正式結算；不假設排隊位置。

三個 cohort 的 executable sample 順序在建立交易時即固定：1–60 development、61–80 validation、81–100 holdout、101–200 frozen confirmation。100 筆前只顯示 `COLLECTING_MINIMUM`，100–199 筆顯示 `MINIMUM_COMPLETE_CONFIRMING`，達 200 筆才標示 `ANALYZABLE`；不得利用 validation／holdout 表現回頭改門檻。

- 每組預設每筆 5 USDT；共同最低設定 2 USDT，用來高於本地 executor 記錄的 MARKET 約 1.5 USDT 門檻。
- 五組共用 100 USDT 尚未結算曝險上限；每次成交前重新查詢 SQLite 的 `OPEN` 本金，額度不足就不開新單。
- 必須由當下實際 Prediction 第一檔 Ask 完整承接，禁止部分成交；另計 50 bps 不利滑價、最大價差 0.03、簿齡 2000 ms、雙邊時間差 500 ms。
- `GET /api/state` 的 `researchForward` 會回報二十二組開關、每筆金額、最低額、五組主策略共享曝險、十七組 shadow 的獨立曝險，以及十二組固定 cohort 的 chronological validation 進度；各組勝敗與收益仍在 `summaries` 及 `trades`。
- 儀表板以獨立「5 主策略＋4 Shadow」分頁顯示這批 forward paper。原本的 M 出場分支與 M0 出場分支已由 `strategy_mx_enabled=false`、`strategy_m0x_enabled=false` 整組停止建立新 intent，兩組歷史面板移到「暫時停止觀測」；既有未平倉仍照原規則結算。
- Collector 會在定期官方結算檢查時找出因服務重啟而沒有 `market_settlements` 記錄的舊 `OPEN` 市場，排除當前市場後以 Binance 官方 `endPrice` 補結算。

M0～M6（含 M01、M01T180、M01O 三組、M01-Floor、M01-Rebound、M0W、M01W）的設定欄位均為實際 API key，不使用 `M*` 萬用字元：

- M0：`strategy_m0_enabled`、`strategy_m0_entry_window_seconds`、`strategy_m0_stake`、`strategy_m0_max_book_skew_ms`、`strategy_m0_max_book_age_ms`、`strategy_m0_seed`。
- M01：`strategy_m01_enabled`、`strategy_m01_entry_window_seconds`、`strategy_m01_max_entry`、`strategy_m01_stake`、`strategy_m01_max_book_skew_ms`、`strategy_m01_max_book_age_ms`；方向共用 `strategy_m0_seed`。
- M01T180：`strategy_m01t180_enabled`、`strategy_m01t180_min_seconds_left`、`strategy_m01t180_max_entry`、`strategy_m01t180_stake`、`strategy_m01t180_max_book_skew_ms`、`strategy_m01t180_max_book_age_ms`；方向共用 `strategy_m0_seed`，預設門檻為 180 秒且比較採嚴格大於。正式實單需另外在實單規則選擇 `M01T180`。
- M01T180D／M01TASYM：獨立開關為 `strategy_m01t180d_enabled`、`strategy_m01tasym_enabled`；成交價、本金與訂單簿品質共用 M01T180 設定。兩組時間門檻固定，不列入實單可選策略。
- M01O 三組：F2、F1、LIVE 的獨立開關分別是 `strategy_m01o_enabled`、`strategy_m01o_f1_enabled`、`strategy_m01o_live_enabled`；三組共用 `strategy_m01o_entry_window_seconds`、`strategy_m01o_max_entry`、`strategy_m01o_stake`、`strategy_m01o_max_book_skew_ms`、`strategy_m01o_max_book_age_ms`、`strategy_m01o_min_observer_samples` 與 `strategy_m0_seed`。`strategy_m01o_min_current_range_score` 只保留舊設定相容性；F2／F1 的實驗門檻固定為 2／1 分。實單只開放 `M01O_F1`，而且主實驗的 `strategy_m01o_f1_enabled` 必須保持啟用；F2／LIVE 不列入實單選項。
- M01-Floor：`strategy_m01f_enabled`、`strategy_m01f_entry_window_seconds`、`strategy_m01f_min_entry`、`strategy_m01f_max_entry`、`strategy_m01f_stake`、`strategy_m01f_max_book_skew_ms`、`strategy_m01f_max_book_age_ms`；方向共用 `strategy_m0_seed`，僅供模擬。
- M01-Rebound：`strategy_m01r_enabled`、`strategy_m01r_entry_window_seconds`、`strategy_m01r_max_anchor`、`strategy_m01r_rebound`、`strategy_m01r_stake`、`strategy_m01r_max_book_skew_ms`、`strategy_m01r_max_book_age_ms`；方向共用 `strategy_m0_seed`，低點狀態會寫入資料庫，僅供模擬。
- M0W：`strategy_m0w_enabled`、`strategy_m0w_entry_window_seconds`、`strategy_m0w_stake`、`strategy_m0w_max_book_skew_ms`、`strategy_m0w_max_book_age_ms`；方向與放行結果取自 M0。
- M01W：`strategy_m01w_enabled`、`strategy_m01w_entry_window_seconds`、`strategy_m01w_max_entry`、`strategy_m01w_stake`、`strategy_m01w_max_book_skew_ms`、`strategy_m01w_max_book_age_ms`；方向共用 `strategy_m0_seed`，放行依上一盤 M0 正式結果。
- M1：`strategy_m1_enabled`、`strategy_m1_entry_window_seconds`、`strategy_m1_stake`、`strategy_m1_max_book_skew_ms`、`strategy_m1_max_book_age_ms`。
- M2：`strategy_m2_enabled`、`strategy_m2_entry_window_seconds`、`strategy_m2_stake`、`strategy_m2_max_book_skew_ms`、`strategy_m2_max_book_age_ms`。
- M3：`strategy_m3_enabled`、`strategy_m3_entry_window_seconds`、`strategy_m3_stake`、`strategy_m3_max_book_skew_ms`、`strategy_m3_max_book_age_ms`、`strategy_m3_min_abs_delta_bps`。
- M4：`strategy_m4_enabled`、`strategy_m4_entry_window_seconds`、`strategy_m4_stake`、`strategy_m4_max_book_skew_ms`、`strategy_m4_max_book_age_ms`、`strategy_m4_required_observations`。
- M5：`strategy_m5_enabled`、`strategy_m5_entry_window_seconds`、`strategy_m5_stake`、`strategy_m5_max_book_skew_ms`、`strategy_m5_max_book_age_ms`。
- M6：`strategy_m6_enabled`、`strategy_m6_entry_window_seconds`、`strategy_m6_stake`、`strategy_m6_max_book_skew_ms`、`strategy_m6_max_book_age_ms`、`strategy_m6_min_basis_samples`。

M7 四個子組的獨立開關是 `strategy_m7_1_enabled`、`strategy_m7_2_enabled`、`strategy_m7_3_enabled` 與 `strategy_m7_5_enabled`；共用欄位是 `strategy_m7_entry_window_seconds`、`strategy_m7_stake`、`strategy_m7_max_book_skew_ms`、`strategy_m7_max_book_age_ms` 與 `strategy_m7_execution_grace_seconds`。固定秒數寫在子組 ID，時間原點永遠是市場開盤，不是首次訊號；不另設 `strategy_m7_1_entry_window_seconds` 之類的分組參數。

所有 M 系列都只用 Prediction 訂單簿模擬執行與部分成交，Prediction 價格不作為 M 系列方向訊號；買後持有到正式結算。頁面上的毫秒數字是本機 queue、排程、age、timestamp 與 diagnostics 的量測，不能解讀為 Prediction 上游每 1 ms 更新，也不代表具備毫秒成交能力。

## M 出場分支實驗頁籤

這個頁籤不改變原 M 主實驗，而是以相同的原 M 開盤方向訊號建立八個獨立出場 cohort。所有分支只在市場開盤後前 10 秒建立進場 intent，進場限價上限固定為 0.50；ask 高於 0.50 時不追價。窗口結束時，完全未成交的 intent 標記為 `EXPIRED_UNFILLED`，只有部分成交則標記為 `EXPIRED_PARTIAL`。固定止盈、分批止盈及反轉退出都依可見深度模擬；深度不足時保留剩餘 shares，不把掛單直接當成完整成交。

目前 `MX_T60`、`MX_T80`、`MX_T98` 因累積已實現收益不高於 -500 USDT，已移到「暫時停止觀測」頁籤並關閉新進場；其開關分別為 `strategy_mx_t60_enabled`、`strategy_mx_t80_enabled`、`strategy_mx_t98_enabled`。關閉只阻止新 intent，既有未平倉部位仍會照原規則更新及結算，歷史資料也會保留。其餘五個 `MX_*` 分支仍在原頁籤觀測。

第四個「M0 出場分支實驗」頁籤使用完全相同的數量、進場限制、成交模型與八種退出規則，唯一差異是方向由 M0 的固定種子加 market ID 雜湊決定，因此可以重現。它使用 `M0X_T60`～`M0X_REV` 八個獨立 ID，資料與原本的 `MX_*` cohort 不會混合；API 以 `m0ExitExperiment` 個別輸出。

八個分支如下：

- `MX_T60`、`MX_T70`、`MX_T80`、`MX_T90`、`MX_T98`：分別在持倉側匹配到 0.60、0.70、0.80、0.90、0.98 時，嘗試全倉止盈。
- `MX_P50`：以窗口結束或完整成交後的最終進場 VWAP 為準，價格到 1.5 倍時賣原始成交 shares 的 50%，到 2.0 倍時賣剩餘 50%。
- `MX_P10`：同樣以最終進場 VWAP 為準，從 1.1 倍到 2.0 倍每一階賣原始成交 shares 的 10%。
- `MX_REV`：標的價格反向穿越正式 `startPrice` 時退出；持倉側 bid 必須先站上 0.50，之後向下穿越到 0.50 以下才啟動價格停損，避免低於 0.50 的合法進場立即自我取消。

`/api/state` 可選擇性提供 `mExitExperiment`：

```text
mExitExperiment: {
  status, updatedAt,
  summaries: {
    MX_T60 | MX_T70 | MX_T80 | MX_T90 | MX_T98 | MX_P50 | MX_P10 | MX_REV: {
      realizedPnl, trades, wins, losses, openPositions, remainingShares,
      requestedEntryQty, filledEntryQty, entryFillRatio,
      requestedExitQty, filledExitQty, exitFillRatio,
      fullyUnfilledIntentRatio, partialFillIntentRatio
    }
  },
  positions: [],
  orders: [],
  fills: []
}
```

每張摘要卡除了已實現收益與未平倉數，也會顯示已成交交易數、勝／負、進場填單率、出場填單率、完全未成交 intent 比例、部分成交 intent 比例及 remaining shares。`trades` 只計至少有一筆進場 fill 的市場，完全未成交 intent 不算交易；尚未全部退出的交易只算進交易數與未平倉，不提前列入勝負，全部退出或正式結算後才依最終淨損益分類。這些指標不能互相替代：例如已實現收益為正，仍可能伴隨大量未成交 intent；出場填單率偏低則可能留下沒有反映在已實現收益內的曝險。

頁面下方另有三張可橫向捲動的明細表：未平倉部位、委託／未成交 intent、實際模擬 fills。欄位同時容錯常見的 camelCase 與 snake_case 名稱；後端尚未提供 optional 區塊時，頁面會顯示等待狀態與空表，不影響 M 主實驗或 A～L 舊策略。

## 互補測試頁籤

`PAIR_ARB_010` 與 `PAIR_ARB_020` 使用 Collector 並行取得的獨立 UP／DOWN outcome token 訂單簿，以相同 shares 同時模擬買入兩邊。M 即時流由單一 UP 簿推導的互補報價不會送入本測試。每 share 淨優勢定義為 `1 - UP ask - DOWN ask - UP taker fee - DOWN taker fee`，分別要求至少 0.010 與 0.020；可成交 shares 受雙側第一檔可見深度及共用總本金限制。同一市場、同一策略只記一筆，帳本獨立存於 `strategy_pair_arb_trades`，不會混入 M 系列或正式實單。

頁面顯示含費鎖定收益、ROI、交易數、平均淨優勢、簿時差／年齡與最大回撤，並另外計算每腿滑點 0.005 及 0.010 的壓力損益。診斷區會持續顯示雙簿評估次數、最佳淨優勢，以及未達門檻、簿過期或時差過大的排除次數。模型假設兩個同步快照的雙腿可同時按可見深度成交，因此只屬 paper-only 理論配對，不保證實際雙腿能原子成交。共用設定為 `strategy_pair_arb_stake`、`strategy_pair_arb_max_book_skew_ms`、`strategy_pair_arb_max_book_age_ms`；兩組開關為 `strategy_pair_arb_010_enabled` 與 `strategy_pair_arb_020_enabled`。

## 舊策略頁籤：A～L

- 策略 A：市場開始後前 120 秒，UP/DOWN 價差至少 0.20，且便宜側賣價不高於 0.20；模擬買入後等待最佳買價到 0.40 出場。
- 策略 B：市場剩餘 20 秒內，只在高機率側賣價介於 0.90～0.95 時買入；每筆使用固定本金（預設 10 USDT），不隨剩餘時間加碼。進場後不再無條件持有到結算：系統每秒更新時若首次觀測到同側最佳買價嚴格低於 0.60，且最佳買量足以完整退出，便按實際最佳買價模擬止損；損益會同時扣除進場與出場費用。
- 策略 B2（實驗）：保留策略 B 的 0.90～0.95 尾盤進場區間，但僅在剩餘 20～3 秒開倉，本金依剩餘時間從最低本金逐步增加到最高本金；可調整加碼曲線，曲線越大代表資金越集中於接近 3 秒的位置。低於 3 秒不開新倉，但已有倉位仍會檢查止損。系統每秒更新時若首次觀測到同側最佳買價低於 0.60，且最佳買量足以完整退出，便按最佳買價模擬止損，不再持有到結算。這是在驗證「越接近結算，時間風險越低」的假設，不代表毫秒級成交保證。B 與 B2 獨立統計。
- 策略 C：沿用策略 A 的前段低價進場條件，出場目標為實際買入價格的 2 倍。
- 策略 D：分時買入兩側；第一腿先買前段低價側，第二腿只有在含費配對成本低於設定上限時才補齊，逾時則退出第一腿。
- 策略 E（實驗）：使用市場開始、剩餘至少 290 秒的同源 BTC 價格作為基準；剩餘 90～30 秒時，要求現貨至少移動 3 bps、預測市場方向相同、進場價介於 0.60～0.90，且買賣價差不超過 0.04。每筆預設模擬本金 10 USDT。
- 策略 F（實驗）：策略 D 的嚴格版；前 120 秒且兩側價差至少 0.20 時，第一腿最高買價為 0.30，雙邊含費配對成本上限收緊至 0.90，最多等待 120 秒，剩餘 20 秒強制退出。每筆預設模擬本金 5 USDT。
- 策略 E2（實驗）：策略 E 的獨立收窄版；以第一筆觀察至今的完整波動標準化現貨移動。剩餘 60～30 秒時至少需達 0.75σ，市場價介於 0.60～0.79、價差不超過 0.02，且報價年齡與兩側時差都合格。每筆預設模擬本金 10 USDT。
- 策略 G（實驗）：用正式 `startPrice`、剩餘時間與短期波動估計保守勝率，並扣除機率收縮、不確定性、延遲、Predict 費用與滑價。滑價會實際進入模擬成交損益；淨優勢還必須高於 `max(0.05, 3 × 買賣價差)`。每筆預設模擬本金 10 USDT。
- 策略 H（實驗）：逆轉狙擊偵測器先等待連續 2 局出現尾盤逆轉（原領先側曾達 0.90，卻在最後 5 秒翻成另一側勝出）。武裝後只在最後 20 秒買入價格不高於 0.01 的一側並持有到結算；若 H 連續失敗 2 次，便解除武裝並重新等待兩次連續逆轉。符合條件時承接最佳賣價上任何可見的正數數量，本金設定僅為上限。
- 策略 I（實驗，賭狗策略）：與其他策略獨立運行；只有 `seconds_left > 3` 時，任一側賣價不高於 0.01 才以最多 1 USDT 模擬買入並持有到正式結算，最後 3 秒一律跳過。符合條件時承接最佳賣價上任何可見的正數數量，本金設定僅為上限；這項高風險資料實驗不代表具有正期望值或交易優勢。
- 策略 J（實驗，尾盤賭狗反彈價差）：策略 C 的獨立尾盤版；只在最後 120 秒且 `seconds_left > 3` 時，兩側價差至少 0.20、便宜側賣價不高於 0.20 才模擬買入。出場目標是實際買價的 2 倍；未達目標則持有到正式結算。訂單簿必須新鮮，符合條件時承接最佳賣價上任何可見的正數數量，本金設定僅為上限。
- 策略 K（實驗，基差校正終局勝率）：在剩餘 180～20 秒內，以開盤基差校正後的現貨距離、剩餘時間與實現波動決定交易方向及模型勝率。UP／DOWN 訂單價格不會輸入方向或勝率模型，只用於費用、滑價、價差、淨優勢與實際可成交性檢查。每筆預設模擬本金 10 USDT。
- 策略 L（實驗，雙向前段反彈）：每輪開始後前 60 秒內，UP 與 DOWN 各自等待賣價不高於 0.50，兩腿可在不同時間成交；每腿最多使用總本金的一半，進場承接最佳賣價上的可見數量，有多少成交多少。兩側即使投入相同 USDT，實際 shares 也可能不同。任一腿最佳買價達 0.70 且同側第一檔買量足以完整退出時，以目標價 0.70 模擬賣出並計算雙邊費用；未達目標或深度不足的腿持有到正式結算。摘要交易數以腿數計，雙邊建倉不代表無風險或保證套利。

所有門檻與每筆模擬本金都可在網站調整，資料持久化在 `data/simulation.db`。

每張策略卡都會顯示目前的「測量起點」，並提供「歸零已實現收益／從現在重新計算」按鈕。確認後，前端會呼叫 `POST /api/strategy-reset`，以當下最大的交易 ID 建立該策略的新摘要 cohort；過去的交易與原始損益仍完整保留在本地帳本，不會刪除。若重設前的部位仍未平倉，卡片會另外顯示「承接未平倉」曝險，但不把它納入本輪績效。成功後頁面會重新載入；失敗時則在原卡片顯示原因，且不會影響參數表單。

策略 H、I、J、L 與全部 M 系列 ID 允許部分成交：設定本金是單筆上限，不要求最佳賣價深度填滿整筆。交易帳本的本金與損益會反映實際承接的可見數量。

策略 H 的設定欄位為 `strategy_h_enabled`、`strategy_h_leader_min_price`、`strategy_h_reversal_seconds`、`strategy_h_anchor_lookback_seconds`、`strategy_h_entry_window_seconds`、`strategy_h_max_entry`、`strategy_h_required_reversals`、`strategy_h_max_consecutive_losses`、`strategy_h_max_book_skew_ms`、`strategy_h_max_book_age_ms` 與 `strategy_h_stake`。`/api/state` 的 `strategyH` 會提供 `mode`、`reversalStreak`、`lossStreak` 與 `lastProcessedMarketId`，供卡片顯示偵測或武裝進度。

策略 I 的設定欄位為 `strategy_i_enabled`、`strategy_i_max_entry`、`strategy_i_stake`、`strategy_i_min_seconds_left`、`strategy_i_max_book_skew_ms` 與 `strategy_i_max_book_age_ms`。

策略 J 的設定欄位為 `strategy_j_enabled`、`strategy_j_window_seconds`、`strategy_j_min_seconds_left`、`strategy_j_min_gap`、`strategy_j_max_entry`、`strategy_j_target_multiplier`、`strategy_j_max_book_skew_ms`、`strategy_j_max_book_age_ms` 與 `strategy_j_stake`。

策略 K 的設定欄位為 `strategy_k_enabled`、`strategy_k_max_seconds_left`、`strategy_k_min_seconds_left`、`strategy_k_min_baseline_seconds_left`、`strategy_k_lookback_observations`、`strategy_k_min_return_samples`、`strategy_k_sigma_floor`、`strategy_k_basis_uncertainty_bps`、`strategy_k_calibration_slope`、`strategy_k_min_probability`、`strategy_k_latency_seconds`、`strategy_k_min_net_edge`、`strategy_k_max_spread`、`strategy_k_spread_edge_multiplier`、`strategy_k_slippage_bps`、`strategy_k_max_book_skew_ms`、`strategy_k_max_book_age_ms` 與 `strategy_k_stake`。

策略 L 的設定欄位為 `strategy_l_enabled`、`strategy_l_window_seconds`、`strategy_l_max_entry`、`strategy_l_target`、`strategy_l_total_stake`、`strategy_l_max_book_skew_ms` 與 `strategy_l_max_book_age_ms`。若要從完整一輪開始比較 L，最好在兩輪之間按下歸零；否則同一輪可分時成交的兩腿可能跨越新舊摘要 cohort。

策略 M 的設定欄位為 `strategy_m_enabled`、`strategy_m_entry_window_seconds`、`strategy_m_stake`、`strategy_m_max_book_skew_ms` 與 `strategy_m_max_book_age_ms`。

## M 系列即時引擎與時間欄位

`/api/state` 的 optional `mRealtime` 狀態列顯示 `status`、`mode`、`paperOnly`、`marketId`、`eventRate`、`queueDepth`、`processedEvents`、`droppedEvents`、`evaluations`、`lastEventAt`、`lastDecisionAt`、`lastQueueDelayMs`、`lastDecisionDurationMs`、`spotAgeMs`、`futuresAgeMs`、`predictionBookAgeMs`、`schedulerTickMs`、`m7EventReorderGraceMs`、資料完整性與 replay/drop 診斷，以及 `error`。M7 目前保留 10 ms 的本機跨 socket 排序寬限，避免 deadline 前已 timestamp、但晚數毫秒排入 worker 的 Spot trade 被漏掉；這個寬限會如實記入 scheduler lateness。後端按事件驅動 M 系列判斷；網站每 1 秒刷新狀態列和帳本，不會改變後端事件處理頻率。

市場時間面板只在 API 有值時顯示 `observed_timestamp_ms`、`up_book_timestamp_ms`、`down_book_timestamp_ms`、`book_age_ms`、`book_skew_ms`、`collection_latency_ms`、`futures_price`、`futures_timestamp_ms`、`futures_age_ms` 與 `futures_agg_trade_id`。M7 交易若有 diagnostics，帳本會顯示 `signal_timestamp`、`execution_timestamp`、`requested_delay_seconds`、`actual_delay_seconds` 與 `execution_lag_seconds`；其中 delay 是相對市場開盤的絕對 deadline，不是相對首次訊號。這些 timestamp、age、queue delay 與 execution diagnostics 是 API 或本機引擎提供的觀測值，不是對 Prediction 上游毫秒精度或實際成交延遲的保證。

## 微結構即時觀測器

首頁會容錯讀取 `/api/state` 的 optional `microstructure` 區塊，顯示 Binance 現貨、USDT 永續與 Prediction 訂單簿三條事件串流，以及 event/s、延遲、microprice、queue imbalance、250 ms／1 s taker imbalance、永續－現貨基差、預測盤流動性移除、波動警報及方向偏差。觀測器只收集與顯示資料，不會觸發任何模擬或真實訂單。

API 有提供時，資料層會以毫秒 timestamp／latency 欄位記錄收到的事件；各來源實際更新頻率不同，不能因此宣稱上游行情每 1 ms 更新。網頁仍沿用 `/api/state` 每 1 秒刷新，這只影響監察畫面，不會降低後端事件引擎的判斷頻率。串流若提供 `transportLatencyMs`，介面會優先顯示為「校正延遲」；只有 `latencyMs` 時則明確標成「表觀延遲」。

`microstructure.storage` 同時相容精簡欄位 `events`、`snapshots`、`retentionHours`，以及 `dbBytes`、`eventsRows`、`snapshotsRows`、`liquidityRows`、`gapRows`、`droppedEvents`、`queueDepth`、`writerStatus`、`writerError`、`writerLagMs`、`rawRetentionHours`、`snapshotRetentionHours`。Prediction stream 另顯示目前的 `bookMapping` 驗證結果。後端尚未啟動、缺少個別串流或欄位時，面板會顯示等待狀態與破折號，不會讓既有策略頁面失效。
