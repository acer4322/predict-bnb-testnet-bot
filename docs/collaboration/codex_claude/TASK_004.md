# TASK 004｜拆開原生准入的比例檢查與收益保留檢查

狀態：PREPARED_FOR_MANUAL_HANDOFF。使用者將本文件交給 Claude 並要求執行後，完成下述一個批次，交 RETURN 後停止。Codex 尚未派送本輪 worker；不要因其他歷史連線文件啟動桌面操控、CLI 接入或新排程。

## 大目標與本輪唯一問題

先讀 [REVIEW_003.md](REVIEW_003.md)，再讀根 AGENTS、docs/agents/research.md 與其 current pointer、docs/agents/development.md、docs/agents/worker.md。沿用人工文件協作。

大目標仍是保留開局擴張，讓主動／被動 ADD、主動／被動 repair、修復後再 ADD 能接續運作；可以付出部分正收益修復，但不能靠消除活動把收益壓光。

v29 已證明完整原生准入使成本降至 OFF 的 34.63%，正收益合計降至 57.91%，拒絕 16,364／16,390 次評估。既有紀錄足以分出 margin 與 reserve 的拒絕，不必另跑一輪只有拒絕統計的任務。本輪回答：**比例檢查與收益保留檢查各自如何影響擴張、修復、收益保留，兩者合用是否形成活動壓縮？**

這是邏輯元件對照，不是調參、模型訓練、泛化或上線測試。K=4.15、RETAIN=.25 只是凍結對照的既有設定，並非重新要求某個固定收益比例或保留率。

## 去重與來源

Codex 已在當前 V12g 包的 CURRENT／PROTOCOL 與相關 tools 名稱中查過 MARGIN_ONLY／RESERVE_ONLY 及同義字，未找到這一組單獨元件對照。v25 是更早啟用等不同條件；v29 只有全開／全關。執行前仍須查同名包、exact/global worker 狀態與回收工件，已有等價結果就核對重用，不能重新取名重送。

- 全關 OFF：重用 v28 AR4_R25，`data/research/lan_worker_returns/btc5m-v12g-active-repair4-small-20260927-v28/`。
- 全開 FULL：重用 v29 NATIVE_GATE_ON，`data/research/lan_worker_returns/btc5m-v12g-native-admission-probe-20260927-v29/`。
- 背景開局基線：重用 v24 PADD80 同 10 場，不重跑全部基線。
- 新包：`data/research/v12g_admission_components_20260927_v30/`。
- 唯一 job：`btc5m-v12g-admission-components-20260927-v30`。

若包或 job 已存在先辨識擁有者與狀態，保留已寫檔案。若 worker 忙碌或契約衝突，回報具體阻塞，不發替代工作。

## 唯一行為變因

現有 `economics.admission` 計算保持不動，只在 `Context.check` 選擇要執行哪一項布林檢查：

| 邏輯格 | margin_ok | reserve_ok | 本轮來源 |
| --- | --- | --- | --- |
| OFF | 不執行 | 不執行 | v28，僅兩場重現檢查 |
| FULL | 執行 | 執行 | v29，僅兩場重現檢查 |
| MARGIN_ONLY | 執行 | 只記錄 | 新 10 場 |
| RESERVE_ONLY | 只記錄 | 執行 | 新 10 場 |

允許在新包 `overlay/run_variant.py` 增加 `V12G_ADMISSION_COMPONENT` 列舉，以及一個小型純函式 helper。合法值 FULL／MARGIN_ONLY／RESERVE_ONLY，缺省 FULL；未知值應在執行前失敗。OFF 仍用 `V12G_AR_NOGATE='1'`，其餘三格必須將 AR_NOGATE 從子程序環境移除，不可設字串 '0'。

保留原 `variant=='OFF'`、`first is None`、AR_NOGATE 的提前放行。到達原 admission 的檢查才套元件選擇；FULL 必須原樣回傳原 `row['allowed']`。MARGIN_ONLY 用 margin_ok，RESERVE_ONLY 用 reserve_ok。當前受測呼叫的 joint=True，強邊 reserve_ok 本來就是 True；不能另外發明強邊保留檢查。

記錄 component_mode、原 full_allowed、實际 selected_allowed，以及原有 G/H/peak、worst_G、pending、delta_margin、margin_ok、reserve_ok、price、quantity、side、origin、t/index。原 allowed 在新記錄中明確對應實際放行，另保留原值；不要把新增診斷欄位當成基線不一致。

`economics.py`、preparation 與其他策略檔 byte copy v29，SHA 不變；run_variant 的差異只限 selector、初始化與診斷欄位。新 helper 是允許的新來源，須納入 manifest、stage 與 hash。用小型純函式檢查四組 margin/reserve 真值、FULL 與舊 allowed 的相等性、實際弱／強邊 admission fixture、非法 mode。不要在主機 import 會啟動原生 runner 的整個 run_variant。

保留所有其他條件：v24 base/model/data、risk queue、250/250ms、fee=0、capital_cap=null、K=4.15、RETAIN=.25、GROSS=300、FLIP=.1、PADD=.80、AR=DP、AR_UNCAP=1、AR_AFTER_DECIDE=1、AR_TRIGGER=1、AR_STRANDED_MAX=.70。沿用 v29 環境清理，將新增元件變數納入白名單與清理／控制組規則；不要將主程序殘留值帶入 controls。

不改 peak、last_H／None、角色選擇、方向翻轉、數量、觸發、取消、時間邊界、active 登記、owner/reservation 或 self-cross。PADD 與 qualified active repair 原本的旁路保持原樣，量測而不改接線。不要加價格上限、冷卻、資金帽、縮量重試或新 reward。

## 固定矩陣：26 條新路徑

固定市場：2628553、2628769、2629133、2629199、2628653、2628410、2629019、2628557、2628721、2628999。這是已按結果挑選並已消費的 10 場，兩個新 arm 各跑一次，共 20 條。

另外 6 條相容性檢查，全部完成且一致才開始 20 條：

- FULL_CHECK：2628553、2628769，重現 v29 NATIVE_GATE_ON。
- OFF_CHECK：2628553、2628769，重現 v28 AR4_R25。
- V12_CONTROL、K415_CONTROL：2491311 各一條，沿用 v29 原設定與預期值。

比較 UP/DOWN、cost、inventory、submits、主被動成交量、rc、失敗子檢查。FULL_CHECK 另比較 admissions 的既有決策欄位與 t/index/origin，忽略新診斷欄位；OFF_CHECK 不應新增 admission 評估。既有允許的浮點誤差須預先凍結，不能看完結果再放寬。若不一致，收集並停止，不邊修邊跑新 arms。

## 執行與有效性

本輪允許第二台電腦一個具名研究批次。依 worker 文件核實 DESKTOP-JIERAGF 與 strict host key，probe → exact/global status → stage/load-only → 一次 submit → 收集；一個 heavy job，max_threads=4。沒有主機 HFT、fit、全 30 場擴測或 live／收集器修改。沿用既有回收機制，不另建 watcher；逾時查狀態，不重送。

先寫 ACK_004，凍結 PROTOCOL、manifest、baseline 預期、分析與可重現純函式檢查，再做 worker load-only 與唯一提交。記錄 source/model/data hash 與每個子程序實際生效的環境白名單，不以設定檔代替執行證據。

原 rc/safety_gate 原樣保存。controls 應完整通過；其他路徑只在 COMPLETE、唯一失敗為已知 active_matches_opportunity、帳務 valid、atomic conservation pass、兩種 overfill=0、canonical 未結=0 時列 KNOWN_LEGACY_ONLY 名目配對。其餘失敗、hash 差異、native rejection、pending/UNKNOWN 均停止未啟動路徑並保留證據。不能一概忽略 rc=2，不能把離線可計算當安全全 PASS。

## 必須回答的比較

1. 先逐 arm 核實 component 生效，gate-on arm 的 AR_NOGATE 缺席、評估非零、selected_allowed 符合該 mode；OFF 的 NOGATE='1' 是預期，不得重現 v29 將所有 arm 一起檢查造成假陰性的分析錯誤。逐路徑保存有效／缺失／失敗狀態。
2. 四個邏輯格各列 signed UP/DOWN、實際支出、ΣP/ΣL、P>L 比例與雙正／正零／混合／雙非正。沿用 P=max(UP,0)+max(DOWN,0)，L=max(-UP,0)+max(-DOWN,0)，eps=1e-8，P>L 且 P>eps；官方勝方只用於離線評分。完整列 planned、有效配對、UNKNOWN／missing 分母。
3. 對 OFF 與 FULL 分別列逐場 ΔP、減少的 L、支出、主被動提交數／有成交單數／成交量、未結責任。均值之比與每場比例的均值分開；0/0 留 N/A。全 10 場 mean cost < v24 PADD80 的一半，沿用已登記 DEGENERATE 活動診斷，不把它改成新收益保留門檻。
4. 15／30／60 秒 inv/cost、signed payoffs、主被動 fills、first/DECIDE/FLIP 時點。使用 clock_trace.states 的真實成本，保留同時間順序與觀測邊界，不以 limit×fill 冒充精確帳務。先檢查首次 gate 前路徑相同，再分析分叉後開局是否壓縮。
5. 對每場固定原第一修復起點的物理 F/L，報初值、改善幅度、修復所付出的正收益、改善後再流失、後續 ADD 與主被動 repair。若不同格的首次修復起點不同，另列各自事件時間，不能混用有利起點。2629199、2628553 必看；所有退步場都列，不只展示最佳例。
6. 按 origin、實際物理 side、當時強／弱角色，列評估／拒絕／放行；另列 PADD 和 qualified active repair 的出生／成交，揭露旁路。16,390 之類是函式評估次數，不是獨立订单，重複狀態不可解讀為大量私有意圖。producer 未記錄 ADD/repair 意圖就標 UNKNOWN，不靠 side 猜角色。
7. 用四格配對分清：放鬆哪個檢查恢復了何種活動，損失是否重新擴大，收益是否真正保住；兩格合用的變化可列四格差分，但只能解讀為這批完整路徑的交互影響。v29 既有拒絕紀錄的假設放行數不能當成新格的成交或終局結果。

無論哪格較好，均不直接標成新模型能力或上線候選。若只恢復交易卻擴大損失，照實報失敗；若都無法接續循環，停止門檻搜尋，回到缺失的動作／責任協調機制，由 Codex 審查後選下一步。不要自行增加 RETAIN／K 搜尋或新的價格規則。

## 交付與停點

Claude 可寫新 v30 包、其具名 worker 回收工件、ACK_004.md、RETURN_004.md，以及既有 dispatch 所需記錄。不可改 v29 及更早凍結結果、Codex TASK/REVIEW、共享 CURRENT.json 或 RESEARCH_CURRENT，由 Codex 驗收後更新。

RETURN_004 需包含：一段结論、26 條狀態、六條基線一致性、四格完整配對表、准入與旁路覆蓋、開局與修復後活動、改善及退步例、限制與唯一下一步。保存 result/row/clock/restoration、stdout/stderr、實際 env 與 hash。批次完成或真實阻塞後停，不追加工作。
