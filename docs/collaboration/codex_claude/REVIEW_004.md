# REVIEW 004｜部分結果可保留；回報時鐘錯誤先診斷，不補跑策略矩陣

日期：2026-09-27。來源：[RETURN_004.md](RETURN_004.md)、v30 回收原件、同市場 v24 WB100 舊失敗與凍結公開輸入。狀態：PARTIAL_REVIEWED_NATIVE_ERROR，沒有模型晉級。Codex 本次沒有重送 v30、補跑 RESERVE_ONLY 或修改原生引擎。

## 執行到哪裡

計畫 26 條；回收 RESULT 為 `STOPPED_PATH_ERROR:2629019`，15 條完成、1 條 ERROR、10 條 NOT_STARTED。

- 六條相容性檢查通過：兩個 controls、兩個 FULL_CHECK、兩個 OFF_CHECK。
- MARGIN_ONLY 完成 9/10；2629019 原生執行失敗。完成的9條可保留為部分名目帳務證據。
- RESERVE_ONLY 的10條全部未啟動，不是零交易，也不是零損益。
- dispatcher／wrapper 的 succeeded 不等於實驗完整成功；以 RESULT、ERRORS 與逐路徑工件為準。

這次在出現非預期原生失敗後停止，符合 TASK_004。不能依然套用「只忽略舊 active_matches_opportunity 斷言」來吞掉新的錯誤。

## 錯誤定位：已知、推論、未知

只讀證據與原件 SHA 見 [FAILURE_CHECK.json](review_004_evidence/FAILURE_CHECK.json)，產生方法見 [check_failure.py](review_004_evidence/check_failure.py)。它只讀檔，不 import runner，也不做 native replay。

**已確認：**

1. v30 原始 result 的堆疊是 `run_whole` 第290行 → `process` → `physical_process` → `Reader.peek` 第31行。失敗條件為某筆回報的 `receive_ts` 大於當時 `bt.current_timestamp`。這是因果順序保護，不能直接刪掉或截斷時間讓它通過。
2. 錯誤發生在**逐筆行情處理迴圈**，尚未走到 `drain_queued_responses`。RETURN 將它推論成「窗口結束後收回回報階段」過於具體；到期後仍可能處於帶延遲的行情迴圈，不等於已進入 drain 函式。
3. v30 最後一筆送出的主動單是 UP_153，299.850秒，10份@0.31，原生提交 rc=0。最後成功保存的 state／空 plan 在300.255秒。最後一筆凍結公開 book 的 source 時間是299.295秒、received時間是300.535秒；前面成功前綴對應1479筆，公開book共1480筆。因此下一次處理最後book是高度具體的定位候選，但失敗時的原生時鐘仍未被保存。
4. v24 WB100 2629019 的原始堆疊同樣在 run_whole／Reader.peek，最後成功 state 同樣300.255秒；它最後一筆 UP_200 也是299.850秒、0.31，數量8.33。支持「既有共用執行路徑問題被不同策略路徑碰到」，不是 v30 首次出現的故障。
5. `failure_receipts.json=[]` **不證明原生回報佇列是空的**。對應程式先清空 `_receipt_delta_rows`，再呼叫 peek；peek 斷言在回傳／consume／ack之前失敗。failure saver 保存的是已處理的 delta 列，沒有保存令斷言失敗的原始 journal。

**尚未確認：**實際失敗的 order ID／generation／sequence、receive_ts／exchange_ts、原生clock、目標行情時點、advance/elapse 回傳碼、native EOF 狀態，以及錯誤時canonical責任快照。299.850秒那筆單是合理嫌疑，但不是已證實的致因訂單。也不能因舊版同位置失敗就宣稱 selector 已被全面證明無錯。

本機 `advance_to` 將 elapse 結果轉為布林，`run_whole` 卻未检查該返回值便呼叫 process，值得優先核查。它是**程式碼層級的候選機制**：尚未證明本次真的遇到 EOF／推進未完成，更未核實故障 worker scratch 的全部工具hash與目前本機工具相同。修復前應釘選這些來源。

失敗場最後保存的 inv/cost 只是部分前綴，不是終局收益；它的未結責任亦為 UNKNOWN。不能把它補0、排除後聲稱完整10場通過，或把未知回報事後餵回策略。

## 研究結果仍能說明什麼

九條完成路徑的原始result均為COMPLETE、帳務valid、atomic責任守恆通過、兩種overfill=0、未結owner=0。其中8條僅已知active_matches_opportunity失敗，2628999為FULL_PASS；不等於9條安全全PASS。以下三格全部排除同一失敗市場2629019，以相同9場原始ROWS重新加總：

| 相同9場 | 平均成本 | ΣP | ΣL | P>L |
| --- | ---: | ---: | ---: | ---: |
| OFF | 1,950.98 | 155.38 | 3,078.85 | 0/9 |
| FULL | 718.27 | 88.99 | 2,262.58 | 1/9 |
| MARGIN_ONLY | 902.20 | 7.59 | 1,413.12 | 1/9 |

MARGIN_ONLY 的正收益合計僅剩OFF的4.886%；7/9場雙非正。虧損幅度雖下降，這仍不符合保留有意義正收益的研究目標。唯一P>L場2628999仍是成本約36.67的同一小活動結果；另1場ERROR必須單列。

這支持「只保留比例檢查、解除收益保留檢查，在這9條已完成路徑會嚴重消耗正收益」。不能反過來聲稱「收益保留檢查才是活動壓縮的主因」；RESERVE_ONLY尚無結果，四格交互作用沒有證據。

需更正的報告／分析口徑：

- `SUMMARY.four_cell_interaction` 數值不可用。它把完全未執行的 RESERVE_ONLY 的ΣP/ΣL當成0，且將MARGIN的9場總量與OFF/FULL的10場總量相減；同時有缺值補0與配對集合不一致。REVIEW明確置為UNKNOWN／null，保留原SUMMARY，不覆寫凍結分析。
- 原 DEGENERATE 規則要求完整10場平均成本。9場均值902.20高於原10場參考門檻853.77，只能是部分資料描述，不能寫成整批已通過該診斷。完整10場的標籤目前UNKNOWN；無論是否過線，ΣP僅7.59都不能作為收益保留成功。
- `failure_receipts` 是處理後delta而非原始receipt，RETURN的「最小反例」目前是失敗前綴摘要，還不是已保存全部致因欄位、可單獨驗證根因的最小重現。

## 下一步：先補觀測，再決定修復

本段是後續診斷規格，尚未派送、未授權在本次只讀審查中執行新native批次。

1. 保留 v30、v24 舊失敗，核對 worker 故障 scratch 的 Reader、owner accounting、advance_to、run_whole、native binary 與輸入hash；查既有版本是否已有相同修復，避免重做。
2. 如既有工件仍不足，另建只加診斷的新包，先僅重現2629019的MARGIN_ONLY。第一次時序違反之前複製原始journal，以及advance前後clock、目標時點、elapse回傳碼、source index、order/generation、canonical owner/reservation、已確認receipt prefix。記錄後仍原樣raise，不能ack違規回報、改時間戳、放宽斷言、改late ADD規則、延長市場資料或繼續讀策略。
3. 用捕捉到的根因才選修復。如果調整共用執行／EOF語義，需另釘選修復包與基線相容性驗證；不可把新引擎結果直接混入舊四格，假裝唯一變因仍只有selector。不得靠禁止尾盤主動修復來掩蓋執行錯誤。
4. 修復範圍与配對一致性確認後，再決定如何補齊RESERVE_ONLY與失敗格；重用仍等價的成功結果。當前不重送完整v30，也不跳過2629019直接跑一批「看起來會成功」的案例。

本輪review與研究指標已更新，原始結果未改。沒有模型fit、live／收集器／服務變更。
