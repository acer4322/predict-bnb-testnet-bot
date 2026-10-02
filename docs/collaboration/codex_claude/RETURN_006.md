# RETURN 006（Claude）｜strict 時鐘觀測，以及尾盤主動修復的時間與收益代價

## 結論
- **時鐘故障：REPRODUCED_WITH_CAPTURE**（依預先登記的分析）。
  - 觀測掛在 run_whole 真正使用的 `base.ex.advance_to`（install 之後的 strict_advance_to），直接捕捉到失敗那一次：target 300.535 秒、推進前時鐘 300.255 秒、`elapse(280ms)` 回傳 **rc＝1**、推進後時鐘**仍是 300.255 秒**、strict 回傳 False。
  - run_whole（minimal_student_native_system_plan_v1.py:290）沒有檢查回傳值，就在沒有前進的時鐘上呼叫 process。
  - 此時 journal 裡已經有 receive_ts 為 300.350 秒的 order 153 回報（seq 73／74），native 帳務也已經入帳，於是 peek 的因果斷言失敗。
- **尾盤主動單：** 這張 299.850 秒的主動單，來自繼承下來的 ActiveOpportunity「第一次合法主動機會」。它成立的原因是：固定 15 份的被動票（0.05×15＝0.75）未滿 1 元，而 MARGIN_ONLY 不檢查 reserve。這個入口：
  - 只檢查 start≤t<end，**沒有用下單延遲檢查能否在窗內到達**。
  - 收益上限只要求把弱邊修到 0，**不限制花掉強邊剩下的正收益**。
  - 依決策當時已知的 250ms 延遲，這張單預計在 300.100 秒才到達，晚於 300.000 秒的研究窗口。如果按限價全部成交，兩支分支都會變負。

## A．觀測是否生效、rc 與前後時鐘

**綁定證據（strict_clock_capture.json 的 `strict.binding`）：**

| 時點 | module.qualname | 檔案 SHA256 | 首行 |
|---|---|---|---|
| install 前 | tools.hftbacktest_execution_shift_audit_v0.advance_to | `4a430375…3741` | 163 |
| install 後 | tools.hft244_research_owner_accounting_v1.strict_advance_to | `519db8c4…aa510` | 106 |
| 觀測函式 | strict_clock_diag.make_strict_observer.\<locals\>.strict_advance_to_observed | `99463ed7…d3ed` | 42 |

- 綁定檢查：installed_function_is_strict＝True、observer_bound＝True。
- 每次呼叫都記錄 `bound_is_observer`，1,481 次全部為 True，後續初始化沒有再覆寫。
- `patched` 清單包含 install 與 Reader.peek；INSTRUMENTATION_INVALID 沒有觸發。

**失敗前後的呼叫（全部由 run_whole:290 呼叫，elapse 只呼叫一次，時間單位 ns）：**

| seq | source index | target（相對秒） | 推進前 | elapse 參數 | rc | 推進後 | 回傳 |
|---|---|---|---|---|---|---|---|
| 1477 | 1476 | 299.850 | 299.659 | 191 ms | 0 | 299.850 | True |
| 1478 | 1477 | 300.054 | 299.850 | 204 ms | 0 | 300.054 | True |
| 1479 | 1478 | 300.255 | 300.054 | 201 ms | 0 | 300.255 | True |
| **1480** | **1479** | **300.535** | **300.255** | **280 ms** | **1** | **300.255** | **False** |

- 更早的 1,476 次呼叫 rc 全部為 0，沒有其他 rc＝1，也沒有 raise。
- 1480 之後沒有 end2 或 drain 的呼叫：錯誤在該次 process 就已發生。

**引擎 feed 尾端**（首次呼叫時讀取 `self.events`／`meta`，唯讀）：
- 共 10,897 筆事件，欄位 ev、exch_ts、local_ts、px、qty、order_id、ival、fval。meta 註明 HFTBACKTEST_EXECUTION_TAPE_FEED_V1，updates 1,480，normalizedTrades 1,344。
- 最後 6 筆：4 筆位於 300.255 秒（depth 事件；ev 0xD0000001 賣方或 0xE0000001 買方），2 筆位於 **300.535 秒**（賣方 px 0.27 qty 0、買方 px 0.03 qty 16.56）。exch_ts 與 local_ts 相同。
- 另一條資料流（公開 book）的最後一筆：source 299.295 秒、received 300.535 秒。**兩條流不同**：native feed 的最後事件時間等於公開 book 的 received_ms，並不等於 source_ms。
- 轉換器是 tools/hftbacktest_execution_tape_feed_v1.py（`181a73b7…`）；tape 是 `38721e39…`，與 v24 base 的輸入相同。

**根因的分級：**
- **OBSERVED：**
  - 最後一次推進 rc＝1，時鐘完全沒有前進，strict 回傳 False，但 run_whole 沒有檢查就呼叫 process。
  - 回報 receive_ts 為 300.350 秒，比當下時鐘晚 95 ms；native 已經入帳（num_trades 74 對比 OUR 72，position 相差 +10）；OUR 沒有收到；carrier 仍是 SUBMITTED、filled 0。
  - feed 在 300.535 秒仍有事件。
  - 上一次 process（300.255 秒）沒有觸發斷言。
- **INFERRED：**
  - 這兩筆回報，是在 rc＝1 的那次 elapse 期間才進入 journal 與 native state 的。依據是 300.255 秒那次 peek 沒有違規，但失敗時已經看到。
  - native 在資料尾端回傳 1 時，會處理或交付尚待收取的回應，卻不推進 local clock。
  - 為什麼 feed 在 300.535 秒還有事件，elapse 卻回傳 1 且不前進，需要看 hftbacktest 原生的 EOF 判定：可能是把 target 等於最後事件的情況當成資料結束。
- **UNKNOWN：**
  - native 內部 EOF 判定的精確條件（本輪不讀原生原始碼，也不重建 binary）。
  - 其他已完成路徑是否曾經遇到 rc＝1，只是當時沒有待收回報、因此沒有觸發斷言。

**原始 journal 與責任：** 與 v31 相同（本輪同樣捕捉，strict_clock_capture.json 的 `violations[0]`）。
- seq 73：order／generation 153，qty 5.0849 @0.07，status 5，leaves 4.9151。
- seq 74：qty 4.9151 @0.29，status 3，cumulative 10。
- 兩筆的 exchange_ts＝300.100 秒、receive_ts＝300.350 秒。
- native 與 OUR 的差：position +10、trading_value +1.781、num_trades +2。
- carrier UP_153 是 SUBMITTED、filled 0，是唯一一個非終結的 carrier，而且沒有 ack。

**前綴對照：** 新 failure_trace 的 SHA256 與 v30、v31 相同。states、plans、native_actions、observations 逐項比較，差異都是 0。

**來源（SOURCE_PIN_V32.json 與 _POST.json，逐檔記錄）：**
- v32、v31、v30 三份 scratch 的 tools（41 檔）、src（3 檔）、tapes（4 檔）完全相同。
- 與 template 不同的有三個檔案：`minimal_student_quantity_seam_v1.py`、`pair_core_asset_route_sizing_v2.py`，以及 template 沒有的 `open_funding_native_active_adapter_v1.py`。這些是 frozen_runner 每次執行時放進 scratch 的版本；v30、v31、v32 三份相同。
- native pyd 是 `7de23355…ebf`，與 v24 base manifest 相同。
- 本次沒有把「全部等於 template」當成通過條件。

## B．尾盤主動入口審查（只讀；受測設定為 MARGIN_ONLY）

### 入口表（這個策略可以產生主動 NEW 的入口）
| 入口（role） | 來源 | 送出時間條件 | 能否到達的檢查 | 收益代價限制 | 准入 | owner／pending |
|---|---|---|---|---|---|---|
| ActiveOpportunity（ACTIVE_OPPORTUNITY_REPAIR） | base/active_opportunity.py:51-77 | 第一次（first is None）且 start≤t<end；同一幀要有 demand row | **無** | 弱邊修到 0 為上限（payoff_cap＝−潛在弱邊收益/(1−ask)），同時受 min(份數平衡上限, 可見深度) 限制；需 ≥1 元；**沒有 cash／強邊保留檢查**（retention 斷言為 0） | overlay guarded_decide：共用主動計數 <5，以及 ctx.check。FULL 同時檢查 reserve 與 margin；**MARGIN_ONLY 只檢查 margin** | 走 draft.reserve（帶 market_end_ms，實作不在釘選範圍內，其作用 UNKNOWN）；未結前保留 |
| CoordinationProbe（ACTIVE_CONFIRMED_REEXPOSURE…） | base/coordination.py:18-107 | start≤t<end；需要確認的重新曝險事件 | 無 | 有限修復數量／深度；被動 15 份還可用時不下；≥1 元 | guarded_decide／ctx.check | 同上 |
| GeneralFiniteActive（ACTIVE_GENERAL_FINITE_WORK_SERVICE） | base/general_finite_active.py:25-173 | start≤t<end；每件工作限一次；全域主動上限 | 無 | 保留後的淨容量；≥1 元 | guarded_decide／ctx.check | 同上 |
| 放寬後的主動修復（ACTIVE_QUALIFIED_RATIO_RESTORATION） | v32 overlay run_variant.py:446 起 | start≤t<end；選邊之後；觸發條件（被甩開／虧損擴大／≤0.15） | 無 | proposal：強邊 G 最多花到 RETAIN×peak（.25）；DP 目標；允許部分修復 | **不走 ctx.check** | 同上 |
| PADD（V12G_PAYOFF_ADD） | overlay run_variant.py:366 | start≤t<end **且 end−t>10 秒** | 以固定 10 秒間接避開 | 選邊邊收益 <0，且 ask ≤0.80 | 不走 ctx.check | 同上 |
| V49 原生主動（frozen_runner.py:340-352） | base/frozen_runner.py | f['t']≥f['end'] 時只發 CANCEL（ACTUAL_MARKET_END，:212），不發 NEW | 無 | 原生候選與 validate | TailNewStop.veto → ctx.check（NATIVE_PRE_RESERVE_ACTIVE） | draft.reserve |
| CommitmentRepairProbe | base/commitment_repair.py | start≤t<end | — | — | — | **只發被動單**（PASSIVE_CURRENT_COMMITMENT_REPAIR），不算主動入口 |

- **可見範圍：** 只有 PADD 有「距收盤 10 秒內不下單」的固定緩衝，其他主動入口都只檢查 t<end。沒有任何入口用下單延遲判斷「到達時是否仍在窗內」。
- **表中的推論（INFERRED）：** 是依原始碼條件判讀，沒有逐一執行驗證。角色依原始 role 記錄，意圖不依買入方向猜測。

### 本案 299.850 秒的決策（failure_trace.opportunity_first 與公開輸入）
1. **可得資訊：**
   - 決策使用 299.850 秒收到的 book（source 298.652 秒），UP bid 0.06、ask 0.31；弱邊是 UP，選邊邊（強邊）是 DOWN。
   - own signed：UP −11.522、DOWN +1.605；inv UP 466.34／DOWN 479.47。
   - pending 為 0，live owner 為 0，同一 plan 沒有其他 NEW。
2. **被動候選為什麼沒過：**
   - demand row 顯示被動候選價 0.05、原始請求 15 份，eligibility 為 ECONOMIC_OR_MINIMUM_CONDITIONS_NOT_MET（money_capacity 12.13、quantity_capacity 13.13、minimum_reference 20）。
   - ActiveOpportunity 要求 `15×passive_price ≥ 1` 才拒絕主動；這裡 0.75 < 1，所以不以「被動票還可用」為由拒絕，主動候選第一次成立。
   - 這是固定 15 份／1 元規則的機械結果，**不是學會了尾盤緊急修復**。
3. **主動數量：**
   - quantity_cap＝479.47−466.34−0＝13.13；payoff_cap＝11.522/(1−0.31)＝16.70；可見深度 10。
   - 取最小值 10 份，10×0.31＝3.10 ≥1，沒有自我交叉。
4. **准入（MARGIN_ONLY，INFERRED，依凍結公式計算；ERROR 路徑沒有落盤 admissions）：**
   - 弱邊 margin 變化＝q×(K−(K+1)p)＝10×(4.15−5.15×0.31)＝**+25.5**，margin_ok 為真。
   - reserve 條件是 worst_G−p×q ≥ RETAIN×peak，也就是 1.605−3.10＝−1.50 < 0.25×peak（peak ≥ 1.605），reserve_ok 為**假**。
   - 所以 FULL 會拒絕這張單，MARGIN_ONLY 放行。
5. **能否到達：**
   - 送出時 t＝299.850 秒，窗口結束 300.000 秒，剩 150 ms。
   - 決策時已知的下單延遲是 250 ms（引擎設定 entry 250／response 250，見 TASK_003 的來源），預計**到達 300.100 秒**，也就是窗後 100 ms。
   - 預計**回報確認 300.350 秒**。
   - capture 的實際成交與回報（300.100／300.350 秒）只用來離線驗證，沒有當成決策輸入。
6. **window_end 的依據：**
   - frame['end'] 等於 public／tape 的 market.window_end_ms（1,790,440,800,000）。它來自品質表 maker_execution_market_quality_v1 的研究窗口，frozen_runner:59 斷言兩者一致。
   - 本地的語義有兩點：NEW 只在 start≤t<end 產生；t≥end 時策略只發 CANCEL（frozen_runner:212，ACTUAL_MARKET_END）。
   - native 撮合**沒有**在 window_end 截止：order 153 在 300.100 秒被撮合，feed 延續到 300.535 秒。
   - 真實 venue 的收單與撮合截止：**VENUE_CUTOFF_UNKNOWN**（本地沒有契約可以確認）。
7. **收益代價：**
   - 按限價全部成交：UP −4.622、DOWN −1.495，兩支都負；花掉 3.10，而 DOWN 的正收益只有 1.605。
   - 以原始成交做離線算術（1.781322）：UP −3.303、DOWN −0.177。這仍不是 canonical 確認的終局。
   - MARGIN_ONLY 移除 reserve 後，就沒有任何檢查限制「支出 ≤ 剩餘正收益」：ActiveOpportunity 本身不看強邊收益，margin 對弱邊買入又恆為正向。

### 最小後續設計（只是提案，未實作）
**到達可行性加收益代價協調：** 所有主動 NEW 入口共用一個純函式判斷：
- 輸入：決策時的 t、已知的下單延遲 L_entry、**可驗證的成交截止時間** T_cut（契約化；目前只有研究窗口可用，venue 截止須另外確認）、候選價與數量、當下的 signed 分支。
- 規則：
  1. 只有 `t + L_entry < T_cut` 才允許**新的** NEW（依實際延遲判斷，不設固定秒數）。
  2. 收益代價要與修復改善協調：候選全部成交後，同時報告弱邊的改善與強邊的剩餘；若強邊由正轉負，就標記為「以正收益換取減虧」，與基本合格修復分開計分（依 V6 契約揭露，不引入固定的保留率）。
- 回報確認時間（t+L_entry+L_resp）只影響責任的保留時間，**不能**用來拒絕已送出的單或釋放 owner。

**驗收情況（分開驗收）：**
1. 剩 150 ms、下單延遲 250 ms、T_cut＝300 秒：預計無法在窗內到達，所以不送新單（本案應被擋下）。
2. 剩 300 ms、下單 250 ms、回報 250 ms：可能在窗內成交、窗後才確認，允許送出，而且 owner 要保留到回報確認為止，不能因為「來回延遲超過剩餘時間」就忽略它的責任。
3. 已送出或已成交但回報延遲的 owner：到截止時間或 CANCEL_PENDING 時都繼續保留，不釋放；需要有測試覆蓋。
4. 修復改善了虧損但花光正收益：單獨標記，與基本合格修復分開報告；不能用兩邊份數趨近相等來代替收益保留。

**本輪不處理：** 時鐘執行修復。rc＝1 之後正確的處理方式（在 process 之前推進到回報時間、end2 的回傳檢查、drain 的時鐘語義）需要 Codex 另外釘選修復包。不能移除斷言、不能過濾回報後硬做 reconcile、不能用查詢上界當時鐘，也不能丟棄公開更新。

## 唯一的下一步
先由 Codex 審查兩件事，再決定是否另開修復包：
1. **時鐘修復：** 根因是 rc＝1 之後時鐘沒有前進，run_whole（以及 end2、v2／v3 的迴圈）仍照常執行 process。最小的執行修復方向是：strict 回傳 False 時，不在舊時鐘上呼叫 process；由明確的語義決定如何推進到待收回報的時間。相容性矩陣需要包括 v28～v30 已完成路徑的逐位元組重現，以及執行期間是否曾出現 rc＝1 的記錄。
2. **尾盤入口：** 上述到達可行性與收益代價協調的設計。

這兩件事應分開釘選、分開驗收，以免同時改兩處、混淆因果。本輪不實作、不派送。

## 本輪紀錄
- **新增的檔案：**
  - 新包 `data/research/v12g_strict_clock_deadline_20260927_v32/`：strict_clock_diag.py、run_variant 掛鉤（v31 的 byte copy 加上 SCD 區塊）、test_strict_clock_diag.py（12 項 PASS，沒有 DLL）、analyze.py、ANALYSIS_SELFTEST（v31 自我測試得到 PARTIAL）、ANALYSIS、SOURCE_PIN_V32 與 _POST、source_pin_v32.py、worker、freeze、dispatch、PROTOCOL、MANIFEST、CURRENT、PREFLIGHT、SUBMIT 等。
  - worker 回收的 `lan_worker_returns/btc5m-v12g-strict-clock-deadline-20260927-v32/`。
  - ACK_006.md、RETURN_006.md。
- **worker job：** 1 個（另有 2 次唯讀的 source pin SSH 讀取）。
- **沒有做的事：** 沒有模型 fit；主機沒有跑 HFT；沒有修改 v30／v31、共用 template、base、native binary、live、收集器；沒有修改 TASK／REVIEW、CURRENT.json、RESEARCH_CURRENT。RESERVE_ONLY 與四格交互作用仍是 null；沒有經濟晉級。
- **主要 SHA256：**
  - strict_clock_capture `4bf336ca…12fd`、result `0cb82c98…59b0`、failure_trace `ab0c3212…527d`、row `5cee548b…657a`。
  - RESULT `3f2e413c…c1bc`、ERRORS `2e6523f0…d244`、arms.zip `c0d38e85…b871`。
  - PROTOCOL `ac02960d…8007`、MANIFEST `ef77eabc…59dd`、run_variant `31bfd8b0…e9b`、strict_clock_diag `99463ed7…d3ed`、ANALYSIS `be824191…b626`。
  - SOURCE_PIN_V32 `e7ca39f5…825f`、SOURCE_PIN_V32_POST `b08347ec…f33e`。
