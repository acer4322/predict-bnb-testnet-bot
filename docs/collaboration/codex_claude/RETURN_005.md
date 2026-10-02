# RETURN 005（Claude）｜單場回報時鐘診斷

## 結論
**REPRODUCED_CAPTURE_PARTIAL**（Claude 依證據判定）。
- 原錯誤在同一條件、同一呼叫堆疊下重現，失敗前的前綴與 v30 逐位元組相同。
- 違規回報的原始欄位、native clock、責任快照都已完整捕捉。
- **還缺失敗那一次時鐘推進的 elapse 回傳碼與推進後的時鐘**：實際的推進函式是 `strict_advance_to`，我的掛鉤掛錯了物件。
- 預先登記的 analyze.py 產出 `OTHER_ERROR`，是分析字串判定的缺陷（詳見「預先登記標籤」一節），不是另一種錯誤。

**已確定（OBSERVED）：**
- 令斷言失敗的是 UP_153 這張主動單的兩筆成交回報。這張單在 299.850 秒送出，10 份 @0.31，角色 ACTIVE_OPPORTUNITY_REPAIR。
  - exchange_ts＝300.100 秒，正好是送出時間＋250ms 下單延遲。
  - receive_ts＝300.350 秒，再＋250ms 回報延遲。
- 讀取回報時，native clock 只到 300.255 秒，比 receive_ts 早 95ms。
- native 帳務（state_values）**已經反映**這兩筆成交，但 OUR 帳本還沒有收到。
- canonical 責任有保留：carrier UP_153 仍是 SUBMITTED、filled 0，也沒有 ack。

## 來源核對（SOURCE_PIN.json，唯讀，worker 上沒有寫入）
- v30 的故障 scratch `C:\BTC5M-worker\.tmp\target_core_cycle_active_v8_v12g30_MARGIN_ONLY_2629019\` 還在，保持原樣。v24 WB100 的 scratch 與 template 也都在。
- **來源與 hash 一致性：** 故障 scratch 的相關工具，與 v24 scratch、template、本地副本的 SHA256 都相同。唯一的例外是每次執行都會重新複製的 `open_funding_native_active_adapter_v1.py` 與 `minimal_student_quantity_seam_v1.py`；這兩個在 v30 與 v24 之間仍然相同。

| 檔案 | SHA256 |
|---|---|
| hft244_receipt_adapter_v1.py | `36e8747c0e84f0521734c0285931f3111166f022813a1f6c76929f7c8bb077e1` |
| hft244_research_owner_accounting_v1.py | `519db8c4625b3163ce5b1296f1c9f064e39a1908b03adead16636c8878510aae` |
| hftbacktest_execution_shift_audit_v0.py | `4a43037583d6ebf576cd65d7e2cb9176e3dd18171b0750301963b2e990173741` |
| minimal_student_native_system_plan_v1.py | `1137d6d3128bf0cc81b633ec87b45a5d56306d341fbd6fa45e9010d8e61a0931` |
| open_funding_recovery_runtime_v3.py | `2468fd631c277e114c3f943bfc9f4867c4f06bdab5f69a26b24d8a8685dd00f1` |
| hft244_minimal_pair_accounting_v1.py | `785a24591843acc869c9e4e3638dfe274d8b2bfc8e45b311f5ad3a9c9899bb79` |
| open_funding_native_active_adapter_v1.py | `1f058833b0868141271aee3695c79f2a857c73b13dfe51a7e372656f81d2c3c9` |
| native `_hftbacktest.cp313-win_amd64.pyd` | `7de2335528234bea9381bea4208a90594a7bec9795d09b913f430ff1f8cf8ebf`（與 v24 base manifest 的 native_sha256 相同） |

- **Receipt ABI：** 104 bytes（`hashmapbt_receipt_size_v1` 會斷言），欄位是 sequence、generation、order_id（u64），receive_ts、exchange_ts（i64，**ns**），side（i64），maker（u64），qty、price、fee、cumulative_qty、leaves_qty（f64），status（u64）。
- **v31 的新隔離 scratch：** `...\.tmp\target_core_cycle_active_v8_v12g31_MARGIN_ONLY_DIAG_2629019\`，由 frozen_runner 從同一個 template 複製；實際載入路徑見 traceback。共用的 template、base 與 live 工具都沒有修改。
- **需要更正的一點（REVIEW_004 與我先前的推論）：** 實際在 run_whole 裡推進時鐘的，不是 shift_audit 的 `advance_to`，而是 `install()`（hft244_minimal_pair_accounting_v1.py:24）替換上去的 **`strict_advance_to`**（owner_accounting.py:106-112）：
  - `cur>=target` 時回傳 True。
  - 否則呼叫 `rc=bt.elapse(target-cur)`：rc 為 0 回傳 True；**rc 為 1 回傳 False**；其他值則 raise `NATIVE_EXECUTION_INVALID`。
  - run_whole 第 290 行沒有檢查回傳值，就直接呼叫 `process(t)`。

## 單場狀態
- **job：** `btc5m-v12g-receipt-clock-diagnostic-20260927-v31`。
  - DESKTOP-JIERAGF，已驗證 strict host key；global 沒有未結束的 job，exact 為 missing；load-only PASS。
  - 只送一次，1 個 heavy job，max_threads=4，只有 1 條 native 子程序。由 auto-collector 收回。
- **RESULT：** status `DIAGNOSTIC_RUN_FINISHED`，native_outcome=`ERROR`。這只代表外層 wrapper 完成，並不改寫原始的 ERROR。
- **子程序：**
  - result.json 的 status 為 ERROR、error 為 `AssertionError:`，original_exit_code 為 2。
  - 所有工件都保留：result.json、failure_trace.json.gz、failure_receipts.json（`[]`，是處理後的 delta）、receipt_clock_capture.json、row.json、stdout／stderr。
- **實際生效的子程序環境**（row.json 的 env_v12）：與 v30 MARGIN_ONLY 相同（ADMISSION_COMPONENT=MARGIN_ONLY、AR=DP、AR_AFTER_DECIDE、AR_STRANDED_MAX=0.70、AR_TRIGGER、AR_UNCAP、FLIP=0.1、GROSS=300、PADD_PX=0.80、K=4.15、RETAIN=.25），另外只多了 `V12G_RECEIPT_CLOCK_DIAG=1`；沒有 AR_NOGATE。

## 時序表（2629019，window_start=1,790,440,500,000 ms）
| 相對秒 | 事件 | 來源 |
|---|---|---|
| 299.295 | 最後一筆公開 book 的 source_ms | public input（FAILURE_CHECK） |
| 299.850 | UP_153 NEW_ACTIVE 送出（原生 rc=0），10 份 @0.31 | native_actions |
| 300.000 | 窗口結束 | public market |
| 300.100 | 交易所處理並成交（兩筆回報的 exchange_ts） | 捕捉到的 receipt |
| 300.255 | 最後一個成功的 state／空 plan（第 1,479 幀） | failure_trace |
| 300.255 | **斷言時的 native clock**（bt.current_timestamp＝1,790,440,800,255,000,000 ns） | capture.native_clock_ns |
| 300.350 | 兩筆回報的 receive_ts | 捕捉到的 receipt |
| 300.535 | 最後一筆公開 book 的 received_ms，也就是下一次 run_whole 迴圈的推進目標 | public input |

- **INFERRED：** 失敗發生在第 1,480 次迴圈（最後一筆 book，target＝300.535 秒）的 `strict_advance_to` 之後。
  - 如果 elapse 的 rc 是 0，時鐘應該會推進到 300.535 秒，而且 300.350 秒的回報會是合法的。
  - 如果 rc 是其他值，會直接 raise `NATIVE_EXECUTION_INVALID`，而不是 AssertionError。
  - 實際觀測到的時鐘仍是 300.255 秒，所以最可能的是 **elapse 回傳 1（資料到頭／推進未完成）、strict_advance_to 回傳 False、run_whole 忽略這個回傳值，繼續在過時的時鐘上執行 process**。
  - 這是依程式分支排除得出的推論，不是直接捕捉到的 rc。

## 原始 journal 與責任證據（receipt_clock_capture.json）
- **Journal：** n＝2、capacity＝4096；violating_sequences＝[73, 74]；兩筆的 receive_minus_clock＝+95,000,000 ns，exchange_minus_clock＝−155,000,000 ns。
  - seq 73：generation 153、order 153、side +1（UP 買）、maker 0、qty 5.0849 @0.07、fee 0、cumulative 5.0849、leaves 4.9151、status 5。
  - seq 74：同一張單，qty 4.9151 @0.29、cumulative 10、leaves 0、status 3。
- **捕捉位置：** 在 `from_buffer_copy` 之後、斷言之前；同一次 get 取得的 owned rows。沒有額外呼叫 get、peek 或 ack。
- **native 與 OUR 的落差：**

| 項目 | native state_values | OUR 帳本 native | 差 |
|---|---|---|---|
| num_trades | 74 | 72 | +2 |
| position | −3.1267 | −13.1267 | +10.0 |
| trading_volume | 955.809 | 945.809 | +10.0 |
| trading_value | 332.270 | 330.489 | +1.781 |
| balance | −0.1766 | +1.6047 | −1.781 |

  兩筆成交已經在 native 帳務入帳，但還沒有交給 OUR。
- **OUR 與責任狀態：**
  - receipt ledger 的 sequence＝72、seen 72 筆；inv UP 466.341／DOWN 479.468；cost 477.863。與 OUR 的 inv／cost 相同。
  - orders[UP_153]：n＝153、status＝'NONE'、cum＝0、placed＝299.850 秒。key_role 是 ACTIVE_OPPORTUNITY_REPAIR。
  - canonical carrier UP_153：route ACTIVE、qty 10、limit 0.31、filled 0、payment 0、state **SUBMITTED**，而且是**唯一**一個非終結的 carrier。
  - 它的保留金額（pending）還在：責任有保留，沒有提前確認，也沒有釋放。
  - cancel 次數 114；UP_153 沒有 cancel 紀錄，在快照中看不到 transport 異常。
- **故障位置：** 堆疊依序是 `money_runner.py:203 main → <string>:839 main → minimal_student_native_system_plan_v1.py:290 run_whole（source loop）→ :220 process → … → hft244_research_owner_accounting_v1.py:14 physical_process → peek`。仍在**行情迴圈**裡，沒有進入 drain，這與 REVIEW_004 一致。

## 前綴對照（ANALYSIS.json）
- 新路徑的 failure_trace.json.gz SHA256＝`ab0c3212…527d`，**與 v30 的原件相同**。
- 逐項比較 states（1,479 筆）、plans（1,479 筆）、native_actions、observations，差異都是 0，容差 1e-9 為預先登記。
- 觀測器沒有改變受測路徑。

## 預先登記標籤
- analyze.py 以原始檔案的「`hft244_receipt_adapter_v1.py` 第 31 行 peek」字串判定是否為同一錯誤。但觀測副本把斷言移到了 `receipt_clock_diag.py:63`，檔名與行號都改變了，因此輸出 **OTHER_ERROR**。這是我的分析缺陷，原 ANALYSIS.json 保留不改。
- 依證據判定：斷言的敘述逐字相同（`assert all(r['receive_ts']<=int(self.bt.current_timestamp) ...)`），呼叫端 physical_process:14 相同，前綴也相同，屬於同一錯誤。advance 紀錄缺失，所以是 **REPRODUCED_CAPTURE_PARTIAL**。
- 這個標籤是事後判讀，需要 Codex 審查。

## 觀測沒有改變控制流程的檢查
- test_receipt_clock_diag.py 共 10 項 PASS（LOCAL_TEST_LOG.txt），沒有 DLL，也沒有 import runner 或 run_variant。
  - advance_to 的 5 種分支：cur>target、cur==target、rc=0、rc=1 且時鐘停在中途、rc=2。回傳值、elapse 呼叫與時鐘推進都和原始碼相同。
  - 合法回報與空 journal：回傳相同、get 只呼叫 1 次、ack 0 次。
  - 違規時先捕捉，原斷言照常 raise。writer 失敗時記錄 CAPTURE_ERROR，也仍 raise 原斷言。
  - snapshot 為唯讀。
- **限制：** advance 的測試對象是 shift_audit 的 `advance_to`，但實際執行時它已經被 `strict_advance_to` 取代，所以時鐘紀錄在真實執行中沒有作用（0 次呼叫）。peek 的捕捉則有作用。

## 最小修復設計（只是提案，未實作、未派送）
- **根因候選（INFERRED，待 rc 確認）：** run_whole 與 v2／v3 的各個迴圈，在 `strict_advance_to` 回傳 False（elapse rc＝1，推進未完成）後，仍在過時的 native clock 上執行 process。而 native 已經把「交易所時間已成交、本地還沒收到」的回報放進 journal，並更新了 state_values。
- **只做 peek 時間過濾（把未到 receive_ts 的回報留在 journal）不夠：**
  - 同一個 process 接下來的 `reconcile(state_values)` 會因為 native 已入帳而失敗。
  - 這會遮蔽問題，不能這樣做。
- **建議的最小修改：** 在呼叫 `advance_to` 的 source loop 中，**檢查回傳值**。一旦回傳 False（rc＝1），不要在過時的時鐘上執行 process 或產生 frame，而是離開 source loop，進入既有的結束流程（end2／drain），由 drain 負責讓 native clock 推進到所有待收回報的 receive_ts 之後，再依序交給 OUR。
- **會改變的語義：**
  1. 資料提前結束後，剩下的公開 update 不再產生 frame，所以 `producer.calls==len(updates)==frame_count` 的斷言需要改成允許「EOF 截斷」，並記錄截斷的幀數。
  2. 最後一張到期前的主動單，其成交會在 drain 階段確認。終局 inv／cost 會納入這筆成交，而原本 ERROR 時沒有終局值。
- **需要的相容性檢查：**
  - v28、v29、v30 的已完成路徑（至少 controls、2 條 OFF 與 2 條 FULL，以及 v30 的 9 條 MARGIN_ONLY）必須逐位元組重現。這些路徑從未出現 rc＝1，所以改動應該等於不動。
  - 2629019 要能完成：UP_153 的兩筆回報在 drain 被接收，reconcile 通過，未結為 0。
  - 另外記錄每條路徑是否發生 EOF 截斷。
- **在修改之前，必須先捕捉 `strict_advance_to` 的 rc，確認前提成立。**

## 唯一最關鍵的缺失
失敗那一次 `strict_advance_to(bt, 300535)` 的 **elapse 回傳碼與推進後的 native clock**，以及 feed 的最後事件時間（EOF 邊界）。下一步只需要把同樣的觀測掛在 install 之後的 `base.ex.advance_to`（也就是 strict_advance_to）上。依本 TASK 的規定，我不修正觀測器後自行送第二次，交由 Codex 決定是否授權。

## 限制
- 只有單場、單條路徑。「最後一張到期前主動單＋資料提前結束」的機制，目前只在 2629019 這一個市場看到（v24 WB100 與 v30 MARGIN_ONLY）。
- 其他市場是否也曾經靜默地遇到 rc＝1，但因為沒有待收回報而沒有觸發斷言，目前 **UNKNOWN**。
- 沒有做任何經濟評估。RESERVE_ONLY 與四格交互作用仍是 null。

## 本輪紀錄
- **新增的檔案：**
  - 新包 `data/research/v12g_receipt_clock_diagnostic_20260927_v31/`：source_pin.py、SOURCE_PIN.json、provenance/（worker 故障 scratch 的原始碼副本）、overlay（v30 的逐位元組複製＋掛鉤＋receipt_clock_diag.py）、worker、analyze、freeze、dispatch、MARKETS、PROTOCOL、IMPLEMENTATION_MANIFEST、test_receipt_clock_diag.py、LOCAL_TEST_LOG、ANALYSIS_SELFTEST、ANALYSIS、CURRENT、PREFLIGHT、SUBMIT 等。
  - worker 回收的 `lan_worker_returns/btc5m-v12g-receipt-clock-diagnostic-20260927-v31/`。
  - ACK_005.md、RETURN_005.md。
- **worker job：** 1 個（唯讀的 source pin 另有 1 次 SSH 讀取）。
- **沒有做的事：** 沒有模型 fit；主機沒有跑 HFT；沒有修改 live、收集器、服務或排程；沒有修改 v30 或更早的凍結包、TASK／REVIEW、CURRENT.json、RESEARCH_CURRENT；也沒有修改共用的 template 或 base。
- **主要 SHA256：**
  - receipt_clock_capture.json `cd2383e0…314b`、result.json `0cb35115…1b79`、failure_trace `ab0c3212…527d`（與 v30 相同）、row.json `ba763f6d…da0b`。
  - RESULT `1780a08b…48ea`、ERRORS `3743c272…f44b`、arms.zip `f47de9bf…7121`。
  - PROTOCOL `55c334a5…6b9`、MANIFEST `5485d3c7…c66f`、run_variant `7a257806…c44b6`、receipt_clock_diag.py `b6ffb525…771c`、SOURCE_PIN `ebe29795…fed85`、ANALYSIS `5164b406…2f26`。
