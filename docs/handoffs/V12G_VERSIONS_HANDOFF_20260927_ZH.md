# V12g 已測版本交接（給 Codex，2026-09-27）

本檔整理 9/26～9/27 在 V12g 路線測過的所有版本與輔助分析，以便接手時不重做、不重送。
- 數字取自各包的 CURRENT／SUMMARY 與 RESEARCH_CURRENT。
- 最新狀態以 [RESEARCH_CURRENT](../agents/RESEARCH_CURRENT.md) 第一項為準（目前是 v34）。
- 本檔只是索引與摘要，**不取代**各包的原件與 REVIEW 更正。

## 0. 共同設定與口徑
- **受測系統：** V12＋K415（K=4.15）在 V49 引擎上執行，只在第二台電腦 DESKTOP-JIERAGF 跑。不是簡化的 PTR 報價器。
- **引擎設定：** 延遲 250ms／250ms；排隊用 risk-averse（排最後，只因成交前進）；fee=0；capital_cap=null。
- **基礎策略（G300_FLIP40＋PADD80）：**
  - **G300：** 均衡開局，總份數到 300 時選中價較高的一邊。
  - **FLIP40：** 選邊邊中價 ≤0.40 時換邊。
  - **PADD80：** 選邊邊是熱門邊、分支 <0、ask ≤0.80 時，每 2 秒主動買 15 份；距收盤 10 秒內停止。
- **市場組：**
  - 自建 10／50 場（v1～v3）。
  - val100 的 100 場 base（`v12g_val100_20260926_v12/base`）；v16 挑出的 50 場（最虧 25 場＋分散 25 場）。
  - fresh30：9/26 最新 30 場，base 在 `v12g_fresh30_20260927_v24/base`；其中 10 場組是最虧 5 場＋獲利 5 場。
  - 這些樣本都已消費、依結果挑選、零費用，只能當離線診斷。
- **評分口徑演變：**
  - 早期用勝方分支為正的比例，畢業標準是 100 場超過 60%。
  - TASK_002 起加上結構比例：P=max(UP,0)+max(DOWN,0)、L=max(−UP,0)+max(−DOWN,0)，統計 P>L 且 P>0 的場數比例，並分列雙正、正零、混合、雙非正。
  - 官方勝方（target_markets.winner）只用於離線評分。
- **有效性：** 凡是含 PADD 或放寬主動修復的路徑，多數是 rc=2、safety_pass=false，唯一失敗是 legacy `active_matches_opportunity`（主動單來源登記與數量預算）。帳務有效、未結為 0 的，可做名目配對；**不是安全檢查全部通過**。

## 1. 版本總表（v1～v34）
| 版本 | 包（data/research/） | 內容 | 主要結果 | 判定 |
|---|---|---|---|---|
| v1 | v12g_gross_decide_small_20260926_v1 | G300／G600／G300_FLIP40，10 場 | G300_FLIP40 平均 +1.1、勝方為正 7/10；G600 −126 | PROMISING（小樣本） |
| v2 | v12g_confirm_small_20260926_v2 | 另外 10 場確認 | G300_FLIP40 −7.9，換邊來回震盪 | NOT_CONFIRMED |
| v3 | v12g_expand_20260926_v3 | 50 場擴測 | G300_FLIP40 −15.5，G100_FLIP35 −71；換邊越多虧越多 | STOP |
| v4 | v12g_neutral_20260926_v4 | 反轉後改成均衡（NEUTRAL） | −33；尾部變小，但反轉 1 次的市場變差 | STOP |
| v5 | v12g_balanced_20260926_v5 | 永不選邊（BAL_ONLY） | 無利潤 | 否決 |
| v6 | v12g_fallguard_20260926_v6 | 下跌側封鎖（FALL2／FALL5） | 無改善 | 否決 |
| v7 | v12g_burst_small_20260926_v7 | 短期爆量偏向 | 無改善 | 否決 |
| v8 | v12g_latecheap_small_20260926_v8 | 180 秒後在 ≤0.30 買便宜弱邊（LCR30） | 輸方 −117→−21，但勝方為正 7→6 | STOP（診斷欄位有 bug，已補） |
| v9 | v12g_flipcap_small_20260926_v9 | 換邊後新邊加倉上限 15% | 修好一場震盪，卻擋住正確換邊後的追回 | STOP |
| v10 | v12g_leancap_small_20260926_v10 | 份數差上限 20% | 上限漏掉，實際約 50%；獲利市場變差 | STOP |
| v11 | v12g_payoff_small_20260926_v11 | PADD80（另有 PREP30） | 勝方為正 8/10、平均 +20 | PROMISING（PREP30 較差） |
| v12 | v12g_val100_20260926_v12 | PADD80 在 100 場驗證 | 勝方為正 68%，平均 −47.3，中位 +43.5；有 21 場 ≤−100（排除後平均 +78） | 基準 |
| v13 | v12g_maxflip_20260926_v13 | 最多換邊 1 次（FN／FH） | 多次換邊的市場仍約 −230 | STOP |
| v14 | v12g_addcap_20260926_v14 | 加倉價上限 0.55（ALL／POST） | ALL 沒有 ≤−500，但勝方為正只剩 45% | STOP |
| v15 | v12g_pace_20260926_v15 | PACE25：份數領先（含掛單）≤25% | 勝方為正 64%、平均 −20.1、沒有 ≤−500 | PROMISING（犧牲趨勢市場利潤） |
| v16 | v12g_pace_tune_20260926_v16 | PACE30／35，50 場 | 都比 PACE25 差 | NOT_PREFERRED |
| v17 | v12g_arep_20260926_v17 | 弱邊上漲時主動修復（疊在 PACE25 上） | 平均 −64.5→−94.9 | NOT_PREFERRED |
| v18 | v12g_defense_20260926_v18 | 反轉後防禦（疊在 PACE25 上） | 尾部變小，但反轉市場勝方為正 45%→10% | STOP |
| v19 | v12g_defense_base_20260926_v19 | 防禦放在基礎版本上 | 最差仍約 −1,270 | STOP |
| v20 | v12g_addgap_20260926_v20 | 選邊後加倉限速 1 秒／2 秒 | ADDGAP2 沒有 ≤−500、平均 −45，但 spread25 +77→+1 | STOP；使用者禁止以秒數限制 |
| v21 | v12g_wbudget_20260926_v21 | 弱邊分支虧損額度 −100／−200 | WB100 沒有 ≤−100、平均 −28，但趨勢市場打平，勝方為正 15/50 | STOP |
| v22／v22r1 | v12g_queue_log_20260927_v22r1 | 排隊模式 risk→log（v22 掛鉤有 bug，smoke 就停了；draft1 未送出） | PADD80 −168→−140，WB100 幾乎不變 | QUEUE_MINOR |
| v23 | v12g_price_lean_small_20260927_v23 | 偏向隨價格形成（PLEAN1）、中段掛單（DRV），10 場 | PLEAN1 −109、spread5 +16；DRV 有 4 場自我交叉失敗 | STOP |
| v24 | v12g_fresh30_20260927_v24 | 最新 30 場：PADD80 與 WB100（自建 base） | PADD80 勝方為正 57%、中位 +28、有 2 場 ≤−500；WB100 勝方為正 28%、最差 −100 | WB100 DOES_NOT_HOLD |
| v25 | v12g_active_repair_20260927_v25 | 放寬主動修復（RETAIN=0、部分修復、取消 5 單上限；AR_OPEN／AR_DP） | 開局 2～5 秒就修復，准入規則擋下 99.8% 的單，系統停擺 | 標為 IMPROVES，實際無經濟意義 |
| v26 | v12g_active_repair2_small_20260927_v26 | 選邊後才修復、關閉准入規則，10 場 | 最虧 5 場 −643→−228，但獲利 5 場 +136→−5 | STOP |
| v27 | v12g_active_repair3_small_20260927_v27 | 被動處理不了才出手（被甩開／虧損擴大／≤0.15），RETAIN .5／.25 | AR3_R25 平均 −253→−129 | R25 PROMISING（仍在取捨線上） |
| v28 | v12g_active_repair4_small_20260927_v28 | 被甩開條件限弱邊 ≤0.70 | 與 AR3_R25 逐場相同 | 重複 |
| v29 | v12g_native_admission_probe_20260927_v29 | 重新開啟原生准入（NATIVE_GATE_ON），10 場 | 評估拒絕 16,364/16,390；成本比 0.346；ΣP 193→112 | DEGENERATE |
| v30 | v12g_admission_components_20260927_v30 | 准入拆成 MARGIN_ONLY／RESERVE_ONLY | MARGIN 9 場完成、ΣP 只剩 7.6；2629019 發生原生 ERROR；RESERVE 未啟動 | STOPPED_PATH_ERROR |
| v31 | v12g_receipt_clock_diagnostic_20260927_v31 | 單場回報時鐘診斷 | 捕捉到 UP_153 的 seq73／74，但時鐘掛鉤綁錯 | REPRODUCED_CAPTURE_PARTIAL |
| v32 | v12g_strict_clock_deadline_20260927_v32 | 在 strict_advance_to 上觀測 | 末次 elapse rc=1、時鐘停在 300.255 秒、run_whole 忽略 False | REPRODUCED_WITH_CAPTURE |
| v33（r1～r3） | v12g_eof_execution_repair_20260927_v33r3 | **Codex：** EOF 執行修復（隔離 native） | 故障場完整入帳，終局 −3.303／−0.177；7 條正常路徑相容 | 修復驗收（非策略晉級） |
| v34 | v12g_reserve_completion_20260927_v34 | **Codex：** 補齊 RESERVE_ONLY 10 場 | 四格 P>L：OFF 0%、FULL 10%、MARGIN 10%、RESERVE 10%；RESERVE 的 L 比 OFF 多 3.1% | 沒有晉級 |

## 2. 輔助分析（沒有 native 新 job，除非另外註明）
- **v12g_case_2491311_20260926_v1：** 最差場的解剖。加倉目標錨定在開局，修復以份數判斷缺口；之後有 190 秒不動。
- **btc5m_target_bigloss_analog_20260926_v1：** 目標在同型市場（開局領先邊後來反轉）的操作。反轉前兩邊都在 0.5 附近買，份數差約 2%；反轉後在 0.32 大量買入舊領先邊。
- **v12g_position_mix_20260927_v1：**
  - 倉位來源與場中活動：150 秒後成交占比，我們 23%、目標 46%；最後一筆成交中位，我們 214 秒、目標 271 秒。
  - 同口徑成交價：熱門邊均價目標 0.685、我們 0.663，**我們並不貴**。
  - 成交優勢：時間取整到秒時，目標 +0.95¢、我們 +0.38¢。
  - 內含 FLIP 方向欄位 bug 的更正版。
- **v12g_fresh30_bigloss_vs_target_20260927_v1：** 最新 30 場中 PADD80 虧超過 200 的 8 場，與目標同場比較。目標在這 8 場也只有 3/8 為正。CASE_2628553 顯示修復明明付得起兩支都正，卻只掛被動單。
- **docs/collaboration/codex_claude/：** TASK／RETURN／REVIEW 001～006，以及 task_002_evidence。這是 rc 與 safety 來源、peak／last_H 分支身份、結構比例的證據。

## 3. 已確立的結論
1. **大虧在第一次反轉之前就形成了。** 選邊後 V49 高價單邊加倉（約 0.62），修復追不上。反轉後再修，成本太高。
2. **各種限制偏向的做法都落在同一條取捨線上：** PACE、ADDCAP、ADDGAP、WB、DEFENSE、PLEAN、准入規則。尾部變小，趨勢市場的利潤也跟著被壓掉，平均從來沒有轉正。
3. **瓶頸不是單價，也不是排隊。** 目標的利潤大約等於成交量×小幅成交優勢，而且它整場都在交易；我們後段幾乎停下，量又集中押在單邊。
4. **主動修復原本的規則幾乎不會出手**（全有或全無、RETAIN 0.5、比例目標、每場 5 單上限）。放開之後會把強邊的正收益花光；加上出手條件，也只是在取捨線上換個位置。
5. **原生准入：** margin 擋住強邊擴張，reserve 擋住弱邊修復；兩者合用就會壓縮活動。v34 發現 `reserve_ok` 在 side==strong 時直接放行，這不等於保護了真正的正收益分支；PADD 也會繞過准入入口。
6. **執行正確性：** 2629019 的 EOF 故障已由 v33 修復驗收（rc=1 時時鐘沒有前進，而呼叫端忽略了這個回傳值）。

## 4. 已知陷阱與更正（不要重犯）
- **FLIP 事件的新方向存在 `to` 欄位，不是 `side`**；要寫成 `e.get('side') or e.get('to')`。舊的 position mix 因此一度算錯。
- **worker 只檢查 status=COMPLETE、不讀 rc。** errors=0 不代表 safety 通過。
- **v29 的 GATE_EVIDENCE=false 是分析錯誤：** 環境檢查誤把 NOGATE=1 的 BASE_OFF_CHECK 也算進去。元件是否生效必須逐組判定。
- **v30 的 SUMMARY.four_cell_interaction 無效：** 它把缺失的格補成 0，而且不同格的場數集合不一致。
- **v31 的 OTHER_ERROR：** 是用檔名與行號比對同一錯誤；觀測副本改變了斷言位置，應改用斷言文字加上呼叫者判定。
- **v31 的時鐘掛鉤綁錯：** install 之後會被換成 strict_advance_to，掛鉤必須放在 install 之後的 base.ex 上。
- **不能拿「目標熱門邊 0.55」和我們的加倉價比較：** 那是開局前 60 秒的領先邊價格。和目標比成交時間，要先把我們的時間取整到秒。
- **v27 說 2629199 是在 0.8 追高，這是錯的：** 實際上是在 0.17～0.37 買入 UP，然後又繼續加倉 DOWN。
- **v25 的「IMPROVES」其實是系統停擺。** 之後的預先登記都加了防呆：成本低於 PADD80 的 50% 就判定 DEGENERATE。
- **active 提交數不等於成交單數；births 不等於 fills；requested−fill 不等於未結責任。**

## 5. 使用者偏好（已寫入記憶）
- 小批量測試：10 場（或 50 場篩選）；只有要看泛化時才用 100 場。
- 先拿掉限制，再考慮新增；新想法放在基礎版本（G300_FLIP40／PADD80）上測，不要疊加。
- **不用秒數限制或延遲。** 節奏要和價格、成交、分支收益綁在一起。
- 開局大量建倉、之後再修復，是偏好的路徑。修復可以付出部分正收益，但不能壓光；不能用兩邊份數平衡代替收益保留。
- 每次都要說明受測系統；平均被少數大虧拉低時，要報告大虧場數與排除後的平均。

## 6. 目前狀態與下一步
- **最新一版是 v34**，沒有進行中的 job。
- 下一步見 [v34 NEXT_STEP](../../data/research/v12g_reserve_completion_20260927_v34/NEXT_STEP.md)：在 8 個收益耗盡事件與既有的雙正正例上做固定狀態的反事實，分開驗證兩件事：
  - 保護對象應該是「選定方向」還是「實際的正收益分支」。
  - PADD 與各路徑是否都被准入覆蓋。
  之後再獨立驗證「修復後又加倉」。
- **尚未處理：** 尾盤主動入口沒有「能否到達」檢查（見 RETURN_006 的入口表與最小設計），以及真實 venue 截止時間（UNKNOWN）。
- **所有結果都是已消費、零費用的 BTC5M 診斷。** 沒有任何版本晉級或具備上線資格；live、收集器、V12 原件都沒有修改。
