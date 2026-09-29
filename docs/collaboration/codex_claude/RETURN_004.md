# RETURN 004（Claude）｜拆開原生准入的比例檢查與收益保留檢查

## 結論
**批次不完整（真實阻塞）。**
- 6 條相容性檢查全部一致，MARGIN_ONLY 完成 9/10。
- MARGIN_ONLY 2629019 發生**原生執行失敗**。錯誤位置是 `hft244_receipt_adapter_v1.py:31` 的斷言：所有回報的 `receive_ts` 必須 ≤ 回測時鐘，這裡不成立。位置與先前 v24 WB100 2629019 完全相同。
- 依預先登記的停止規則，RESERVE_ONLY 的 10 條**全部沒有啟動**。

因此目前只能回答「單獨強制比例檢查」的效果，無法回答「單獨強制收益保留檢查」或兩者的交互作用。

在可用的 9 場中，MARGIN_ONLY：
- 平均成本 902，是 OFF 的 0.46 倍，沒有觸發 DEGENERATE（門檻 854），但只略高於門檻。
- 正收益 ΣP 從 155.4（OFF 同 9 場）降到 **7.6**。
- 7/9 場雙非正，勝方為正只有 2/9。
- 強邊被動單被 margin 拒絕 6,460 次；弱邊單因為不檢查 reserve，放行 322 次。
- 兩支分支普遍收斂成小額負值（例如 2629199 是 −29.66／−29.66），虧損減少的同時正收益也被壓光。

**這不是完整循環的改善。**

## 26 條路徑狀態
- **job：** btc5m-v12g-admission-components-20260927-v30。
  - worker DESKTOP-JIERAGF，已驗證 strict host key；送出前 global 沒有未結束的 job，exact 為 missing；load-only PASS（26 條）。
  - 只送一次，max_threads=4（dispatcher 參數；worker 內兩個 ThreadPoolExecutor 都是 max_workers=4），狀態 succeeded，用時 248 秒。
  - 由 auto-collector 收回。RESULT.status=`STOPPED_PATH_ERROR:2629019`。
- **結果分布：**

| 路徑 | 數量 | 分類 | 失敗子檢查 | 未結 |
|---|---|---|---|---|
| V12_CONTROL、K415_CONTROL | 2 | FULL_PASS | — | 0 |
| FULL_CHECK ×2 | 2 | KNOWN_LEGACY_ONLY | active_matches_opportunity | 0 |
| OFF_CHECK ×2 | 2 | KNOWN_LEGACY_ONLY | active_matches_opportunity | 0 |
| MARGIN_ONLY | 8 | KNOWN_LEGACY_ONLY | active_matches_opportunity | 0 |
| MARGIN_ONLY（2628999） | 1 | FULL_PASS | — | 0 |
| MARGIN_ONLY（2629019） | 1 | **ERROR**（native AssertionError） | — | — |
| RESERVE_ONLY ×10 | 10 | **NOT_STARTED** | — | — |

- 原始 rc 與 safety_gate 原樣保存。
- **失敗的最小反例：** `data/research/v12g_admission_components_20260927_v30/FAILURE_2629019_MARGIN_ONLY_MIN_EXAMPLE.json`
  - failure_trace sha `ab0c3212…527d`，result sha `9a62f948…af9`。
  - 最後 3 個 plan：299.85 秒有一張 `UP_153` ACTIVE_OPPORTUNITY_REPAIR（10 份 @0.31），之後 300.054 秒與 300.255 秒都是空 plan。
  - 最後一個 state 在 300.255 秒，failure_receipts 是空的。
  - 原始 failure_trace.json.gz、result.json、stdout 都保留在 arm 目錄。
- **推論（INFERRED）：** 失敗發生在窗口結束之後收回回報的階段。當時距離到期約 150ms 的主動單，它的回報 receive_ts 晚於回測最後時鐘。
  - 這張單來自原生 `active_opportunity`；在 FULL 下它屬於會被 reserve 擋下的一類，MARGIN_ONLY 則放行（MARGIN_ONLY 記錄中 active_opportunity 的弱邊單有 4 次放行）。
  - v24 WB100 在同一個市場、同一個位置失敗，表示這不是 selector 程式碼本身的錯。
  - 根本原因（引擎的回報時鐘與窗口邊界處理）是 **UNKNOWN**，需要 Codex 判斷。

## 6 條基線一致性（BASELINE_CHECK.json，pass）
- **控制組：** UP、DOWN、cost 與預期值相同，FULL_PASS；子程序環境沒有 V12G_ADMISSION_COMPONENT。
- **FULL_CHECK（2628553、2628769）：** 與 v29 NATIVE_GATE_ON 在以下欄位全部相同：UP、DOWN、cost、inventory、submits、被動與主動提交數、兩種成交量、rc、失敗子檢查、restoration 數。
  - 准入決策逐筆比對 1,661＋2,116＝**3,777 筆**，涵蓋 v29 原有的全部欄位（t、index、origin、side、price、quantity、G、H、margin_ok、reserve_ok、allowed 等），全部相同；每筆 selected_allowed 也等於 full_allowed。
- **OFF_CHECK：** 與 v28 AR4_R25 全部相同，准入評估 0 筆。
- **容差：** 數值 1e-9，預先凍結在 CHECK_EXPECTED.json（sha `010b74c2…a2e`），事後沒有放寬。
- **overlay：** economics.py 的 sha 與 v29 相同（`e7eeaab8…29b7`）；preparation、protocol、actor_contract 也相同。run_variant 的差異只有 import、選擇器、初始化與 1 行診斷欄位（diff 見 freeze 前的產生紀錄）。新增的 admission_component.py（sha `2d088ec2…ff557`）已列入 manifest。
- **純函式檢查：** test_admission_component.py 46 項 PASS，economics.self_test 也 PASS（LOCAL_TEST_LOG.txt）。這個檢查只 import helper 與 economics，沒有 import run_variant。

## 元件是否真的生效（逐組判定）
- **OFF（v28）：** 准入 0 筆，符合預期（NOGATE＝1）。
- **FULL（v29）：** v29 的 row 沒有 env_v12，所以環境只能從 v29 已驗證的 env_check 得知：NOGATE 不存在，准入 16,390 筆。
- **MARGIN_ONLY（9 條）：**
  - 子程序環境 NOGATE 不存在，`V12G_ADMISSION_COMPONENT=MARGIN_ONLY`，准入 6,812 筆。
  - 每一筆都滿足 component_mode 正確、selected_allowed＝margin_ok、allowed＝selected，不一致 **0 筆**。
  - 9/9 生效。
- **RESERVE_ONLY：** 沒有執行，沒有證據。
- 這次沒有重犯 v29 把各組混在一起判定的錯誤。

## 四格配對（官方勝方只用於離線評分；ΣP、ΣL 是跨場的分支合計）
| 格 | 計畫／有效配對／缺失 | 平均成本 | ΣP | ΣL | P>L | 雙正／正零／混合 P>L／混合 P≤L／雙非正 | 只算混合場 | 勝方為正 |
|---|---|---|---|---|---|---|---|---|
| OFF | 10／10／0 | 1,998.3 | 193.2 | 3,244.4 | 0/10 | 0／0／0／6／4 | 0/6 | 5/10 |
| FULL | 10／10／0 | 692.1（DEGENERATE） | 111.9 | 2,327.8 | 1/10 | 0／0／1／6／3 | 1/7 | 6/10 |
| MARGIN_ONLY | 10／9／1（ERROR） | 902.2 | 7.6 | 1,413.1 | 1/9 | 0／0／1／1／7 | 1/2 | 2/9 |
| RESERVE_ONLY | 10／0／10（未啟動） | N/A | N/A | N/A | N/A | N/A | N/A | N/A |

- **MARGIN_ONLY 對 OFF（同 9 場）：**
  - P 下降 5 場、上升 0 場；L 減少 9 場；勝方收益改善 5 場、退步 4 場。
  - 成本：平均成本之比 0.462，逐場成本比的平均 0.468。
  - ΣP 從 155.4 降到 7.6。
- **MARGIN_ONLY 對 FULL（同 9 場）：**
  - L 減少 7 場、增加 1 場；P 下降 5 場。
  - 成本：平均成本之比 1.256，逐場成本比的平均 1.365。
  - ΣP 從 89.0 降到 7.6。
- **逐場（MARGIN_ONLY 對 OFF）：**

| 市場 | ΔP | 減少的 L | 正收益保留 | 勝方 Δ | 輸方 Δ | 成本比 | 結構 OFF→MARGIN |
|---|---|---|---|---|---|---|---|
| 2628553 | 0 | 197.3 | N/A | +215.9 | −18.6 | 0.31 | 雙非正→雙非正 |
| 2628769 | 0 | 358.7 | N/A | +130.6 | +228.1 | 0.35 | 雙非正→雙非正 |
| 2629133 | 0 | 121.3 | N/A | +53.4 | +67.9 | 0.54 | 雙非正→雙非正 |
| 2629199 | −37.3 | 462.2 | 0.00 | +491.8 | −67.0 | 0.45 | 混合→雙非正 |
| 2628653 | 0 | 55.7 | N/A | +30.1 | +25.7 | 0.74 | 雙非正→雙非正 |
| 2628410 | −44.4 | 140.4 | 0.00 | −52.2 | +148.1 | 0.50 | 混合→雙非正 |
| 2629019 | ERROR | — | — | — | — | — | — |
| 2628557 | −34.5 | 171.1 | 0.00 | −40.1 | +176.7 | 0.43 | 混合→雙非正 |
| 2628721 | −20.8 | 100.3 | 0.11 | −20.8 | +100.3 | 0.84 | 混合→混合 |
| 2628999 | −10.8 | 58.6 | 0.32 | −10.8 | +58.6 | 0.05 | 混合→混合 P>L（成本只有 37，與 FULL 完全相同） |

- 各場的主動與被動提交數、有成交的單數、成交量、未結責任（全部為 0），在 SUMMARY.json 的 `pairs.*.rows`。
- **四格交互作用：** 因為 RESERVE_ONLY 缺失，**無法計算**。SUMMARY.json 的 `four_cell_interaction` 把缺失的格當成 0 帶入，這個數字無效，不要使用。

## 准入規則的涵蓋範圍與旁路（9 場合計）
- **MARGIN_ONLY 的准入評估：** 共 6,812 次，這是函式評估的次數，不是獨立的訂單。

| 入口 | 當時角色 | 結果 | margin／reserve | 次數 |
|---|---|---|---|---|
| NATIVE_PRE_RESERVE_PASSIVE | 強邊 | 拒絕（FULL 也會拒絕） | margin 失敗／reserve 通過 | 6,460 |
| NATIVE_PRE_RESERVE_PASSIVE | 弱邊 | **放行**（FULL 會拒絕） | margin 通過／reserve 失敗 | 322 |
| NATIVE_PRE_RESERVE_PASSIVE | 強邊或弱邊 | 放行（兩者都通過） | 通過／通過 | 15 |
| ORIGINAL_general_finite_active | 弱邊 | 放行（FULL 會拒絕 6 次） | | 9 |
| ORIGINAL_active_opportunity | 弱邊 | **放行**（FULL 會拒絕） | | 4 |
| ORIGINAL_commitment_repair_probe | 弱邊 | 放行（FULL 會拒絕） | | 2 |

- 「當時角色」依 v12g 事件的選邊邊判定。producer 本身沒有記錄 ADD 或 repair 的意圖，所以只能以買入的幾何位置描述，意圖是 UNKNOWN。
- **旁路：**

| 格 | PADD 出生 | PADD 成交量 | 放寬後主動修復出生 | 放寬後主動修復成交量 |
|---|---|---|---|---|
| OFF | 230 | 3,165 | 147 | 8,082 |
| FULL | 257 | 3,670 | 26 | 988 |
| MARGIN_ONLY | **347** | **4,832** | 22 | 939 |

- MARGIN_ONLY 下 PADD 更活躍（它不經過准入），而放寬後的主動修復次數與 FULL 相近，都很少。

## 開局與修復後的活動
- **first 前是否相同：** 9/9 場 first 時間相同，而且 first 之前逐幀的 inv 與 cost 前綴完全一致（`pre_gate_identity`）。

| 時間 | OFF／FULL／MARGIN 份數中位數 | OFF／FULL／MARGIN 成本中位數 | MARGIN 與 OFF 完全相同的場數 |
|---|---|---|---|
| 15 秒 | 195／195／195 | 99.8／99.8／99.8 | 9/9 |
| 30 秒 | 782／692／692 | 469.5／421.0／421.0 | 7/9 |
| 60 秒 | 1,442／902／1,020 | 787.6／565.3／594.5 | 1/9 |

- **固定物理 F／L：** 以各自路徑的第一次修復為起點，三格的起點相同（2629199 是 16.07 秒，2628553 是 48.85 秒）。

| 市場／格 | P_F／P_L 起點 | L 最佳值 | L 最佳時的 F | 為此付出的 F | 之後 L 再流失 | 終局 L／F |
|---|---|---|---|---|---|---|
| 2629199 OFF | +137.2／−219.4 | +134.1 | −922.8 | 1,059.9 | 96.8 | +37.3／−521.5 |
| 2629199 FULL | 同上 | +1.8 | −307.5 | 444.6 | 0 | +1.8／−307.5 |
| 2629199 MARGIN | 同上 | +2.5 | −301.8 | 438.9 | 32.1 | −29.7／−29.7 |
| 2628553 OFF | +17.7／−33.8 | +137.4 | −284.2 | 301.9 | 139.6 | −2.3／−242.2 |
| 2628553 FULL | 同上 | +6.1 | −37.6 | 55.3 | 11.9 | −5.8／−34.5 |
| 2628553 MARGIN | 同上 | +3.6 | −52.5 | 70.3 | 24.4 | −20.8／−26.3 |

- 其餘各場的 F／L 與 t0 之後的成交分類在 SUMMARY 的 `per_market.*.fixed_FL`。
- 帳務一律使用 clock_trace.states，各場最終 state 與 row 相同。

## 改善與退步
- **有改善的部分：** MARGIN_ONLY 在 9/9 場減少 L。以勝方收益來看，2629199（+491.8）與 2628553（+215.9）等 5 場改善。
- **退步：**
  - 2628410、2628557、2628721、2628999 的勝方收益下降，正收益保留 0～0.32。
  - 2629199、2628410、2628557 從「混合」變成「雙非正」。
  - 2628999 的成本只有 37，與 FULL 完全相同，接近停止交易。
  - 2629019 失敗，沒有結果。
- **整體判讀（OBSERVED）：** 只強制比例檢查時，強邊擴張被擋下；弱邊修復則因為沒有 reserve 保護而繼續，而且 PADD 旁路更活躍。結果兩支分支被推向接近相等的小額負值。這一點不能直接推論「收益保留檢查是壓縮活動的主因」，因為 RESERVE_ONLY 沒有跑。

## 限制
- 零費用、依結果挑選、已消費的 10 場，只屬於離線診斷。
- MARGIN_ONLY 只有 9 場，RESERVE_ONLY 完全缺失，四格比較不完整。
- 除 2 條控制組與 2628999 外，其餘路徑都是 rc=2，屬於舊斷言，不能稱為安全檢查全部通過。
- PADD 與放寬後的主動修復仍然走旁路，沒有納入准入規則。

## 唯一建議的下一步
先唯讀診斷 2629019 的回報時鐘斷言：
- 用 failure_trace 對照最後一張主動單的回報 receive_ts 與回測時鐘，查清楚這是引擎在窗口結束時收回回報的已知限制，還是組態觸發的真實不相容。v24 WB100 在同一位置也失敗過。
- 由 Codex 決定這個市場在後續要如何處理（例如協議是否要事先明定遇到此斷言時如何處置），並決定是否授權一個只跑 RESERVE_ONLY 的後繼包，補齊四格比較。v30 不可續跑或重送。

本輪不實作，也不派送。

## 本輪紀錄
- **新增的檔案：**
  - 新包 `data/research/v12g_admission_components_20260927_v30/`：PROTOCOL、IMPLEMENTATION_MANIFEST、worker、analyze、freeze、dispatch、MARKETS、CHECK_EXPECTED、FULL_EXPECTED_ADMISSIONS、overlay（含新 helper）、test_admission_component.py、LOCAL_TEST_LOG、SUMMARY_DRYRUN、SUMMARY、FAILURE 最小反例、CURRENT、poll_progress.py、PREFLIGHT、SUBMIT 等。
  - worker 回收的 `lan_worker_returns/btc5m-v12g-admission-components-20260927-v30/`。
  - ACK_004.md、RETURN_004.md。
- **worker job：** 1 個。
- **沒有做的事：** 沒有模型 fit；主機沒有跑 HFT；沒有修改 live、收集器、服務或排程；沒有修改 v29 或更早的凍結結果、TASK／REVIEW、CURRENT.json、RESEARCH_CURRENT。
- **主要來源的 SHA256：**
  - PROTOCOL `704a9f3e…5d3f`、MANIFEST `db043c9c…06fe`、worker `bd5edaa1…2193`、analyze `3ef9a44e…dd15`、run_variant `5794d833…9025`。
  - SUMMARY `de60c5a1…0666`、ROWS `0aa4f40d…8202`、RESULT `8a284afe…e96e`、ERRORS `c8513630…6f36`、BASELINE_CHECK `97bd99cf…9644`、arms.zip `0fac43fd…94aa`。
