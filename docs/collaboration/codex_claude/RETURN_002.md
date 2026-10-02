# RETURN 002（Claude）｜v28 有效性、狀態語義與結構占比

證據腳本與輸出都在 `task_002_evidence/`：
- a_validity.py → A_VALIDITY.json
- b_timeline.py → B_TIMELINE.json
- c_structure.py → C_STRUCTURE.json

這些腳本只讀取既有工件，壓縮檔一律在記憶體中解碼，沒有改動任何原件。

## 1. 直接結論

- **資料可用，但只在「完整、帳務有效、責任已結」的範圍內。** 14 條路徑的狀態如下（OBSERVED）：
  - 全部 status=COMPLETE、unresolved_owners=0、execution_accounting_valid=true、原子責任守恆 pass、兩類 overfill 為 0。
  - 12 條 rc=2，唯一原因都是 V49 以 instrument 注入的舊結構斷言 `active_matches_opportunity`。
  - 這個斷言從 v11 加入 PADD 起就存在於基線：v24 PADD80 有 25/30 條 rc=2，沒有 PADD 的 G300_FLIP40 則 10/10 條 rc=0。
  - 因此它不是 v28 新增修復造成的，也不是帳務或未結責任的問題。
  - 但它指出一件真實的語義事實：overlay 產生的主動單繞過了 V49 已登記的主動模組與其准入路徑。
- **SUMMARY 的 errors=0 只代表 worker 收到 status=COMPLETE**，worker 沒有讀取 rc（worker.py:61）。它不代表 safety 通過。
- **候選的「選邊後重開准入規則」比較，目前前提還不足。** 缺的是**具體狀態語義**，不是結果判讀，也不是執行正確性：
  - last_H 會跨分支身份比較，而且已經觀察到它單獨促成一次主動修復（2628553 第 91.8 秒）。
  - peak 會跨分支身份累積。它決定 retained floor，也就決定了每次修復能用多少錢。
  - 准入規則在 v28 從未被評估（admissions=0），所以「修了又加」的另一個候選機制目前無法區分：強邊 ADD 會提高 G，也就提高了修復預算。
- **結構占比：** 「正收益大於虧損」的場數比例如下：
  - v24 PADD80：1/10。
  - v27 AR3_R25 與 v28 AR4_R25：0/10。兩者逐場完全相同，不是兩個獨立樣本。
  - 三組的官方勝方為正都是 5/10。
  - 同場配對：AR3/AR4 讓 8/10 場的虧損減少，但也讓 8/10 場的正收益減少。正收益合計只剩 PADD80 的 11%（193/1,711）。

## 2A. 14 條路徑的有效性（A_VALIDITY.json）

### rc 與 safety_gate 的產生鏈（OBSERVED，來自程式碼）
1. `frozen_runner.py:428` 建立 `safety_gate`，其中 `pass=all(values)`。
2. `frozen_runner.py:437` 在 `status!='COMPLETE' or not pass` 時 `raise SystemExit(2)`。
3. overlay `run_variant.py:444` 捕捉後記入 `economic_option.original_exit_code`，再在 `run_variant.py:474` 以同一個 code 退出。
4. worker `worker.py:55` 取得 `rc=pr.wait()`，`worker.py:61` 只檢查 `status`，`worker.py:66` 記錄 `safety_pass`。rc≠0 不會進入 errors（worker.py:93）。

原本的子檢查 `passive_only_has_no_active`，被 `active_opportunity.py:94` 的 instrument 替換成 `active_matches_opportunity`；之後 `coordination.py:118` 與 `general_finite_active.py:187-188` 各再改寫一次。最終條件是：

`len(active owners)==len(opportunity.submissions)+len(coordination.submissions)+len(general_finite_active.submissions)<=5 and opp<=1 and coord<=4 and gfa<=5 and (not active or mode=='ONE_ACTIVE')`

這個條件混合了兩種要求，應該拆開看：
- **(i) 來源登記：** 每一張主動單都必須來自已登記的主動模組。
- **(ii) 數量預算：** 主動單總數 ≤5，而且各模組各有上限。

### 逐條結果
active = 主動成交單數；opp/coord/gfa = 三個模組登記的主動單數。

| 組別 | 市場 | rc | safety | 失敗子檢查 | 未結 | 帳務 | active | opp/coord/gfa | PADD 單 | 違反 |
|---|---|---|---|---|---|---|---|---|---|---|
| V12_CONTROL | 2491311 | 0 | true | — | 0 | ✓ | 5 | 0/0/5 | — | — |
| K415_CONTROL | 2491311 | 0 | true | — | 0 | ✓ | 5 | 0/0/5 | — | — |
| AR4_R25 | 2628769 | 2 | false | active_matches_opportunity | 0 | ✓ | 70 | 0/0/28 | 42 | (i)+(ii) |
| AR4_R25 | 2628553 | 2 | false | 同上 | 0 | ✓ | 30 | 0/0/23 | 7 | (i)+(ii) |
| AR4_R25 | 2628653 | 2 | false | 同上 | 0 | ✓ | 80 | 0/0/7 | 73 | (i)+(ii) |
| AR4_R25 | 2628721 | 2 | false | 同上 | 0 | ✓ | 24 | 1/0/3 | 20 | (i) |
| AR4_R25 | 2628410 | 2 | false | 同上 | 0 | ✓ | 16 | 0/0/13 | 3 | (i)+(ii) |
| AR4_R25 | 2628999 | 2 | false | 同上 | 0 | ✓ | 10 | 1/0/9 | 0 | (ii) |
| AR4_R25 | 2628557 | 2 | false | 同上 | 0 | ✓ | 32 | 0/0/19 | 13 | (i)+(ii) |
| AR4_R25 | 2629133 | 2 | false | 同上 | 0 | ✓ | 89 | 0/0/31 | 58 | (i)+(ii) |
| AR4_R25 | 2629019 | 2 | false | 同上 | 0 | ✓ | 40 | 0/0/37 | 3 | (i)+(ii) |
| AR4_R25 | 2629199 | 2 | false | 同上 | 0 | ✓ | 32 | 0/0/18 | 14 | (i)+(ii) |
| PADD80_CHECK | 2628769 | 2 | false | 同上 | 0 | ✓ | 49 | 0/0/1 | 48 | (i) |
| PADD80_CHECK | 2628553 | 2 | false | 同上 | 0 | ✓ | 12 | 0/0/1 | 11 | (i) |

- 所有路徑的 stderr 都是 0 bytes，stdout 最後一行都是 status COMPLETE。
- **基線對齊：** v24 PADD80 與 v28 PADD80_CHECK 在 2628553、2628769 的 UP、DOWN、cost、rc、safety、失敗子檢查、主動單數、PADD 單數全部相同。
  - 所以這個狀況在基線就已經存在（OBSERVED）。
  - v24：PADD80 rc=2 有 25/30 條，WB100 有 24/29 條。
  - v11：G300_FLIP40_PADD80 有 7/10 條 rc=2，PREP30 有 10/10 條。
  - v1：G300、G300_FLIP40、G600 全部 rc=0。

### 分類與可支持的結論
- **類別：研究組態不適用的舊斷言。** 不是真實執行失敗，不是期末未結，也不是經濟判準未達標。
  - 這是 INFERRED：以上述 OBSERVED 子檢查為依據。
  - 依據一：(ii) 的上限 5 正是 v25 起刻意取消的預算（AR_UNCAP）。
  - 依據二：(i) 是 PADD 在 `QualifiedActive.apply` 直接附加主動 NEW，沒有登記到任何模組的 submissions。
- **這些路徑可支持：** 帳務有效、責任已結的名目終局損益，以及同場配對比較。
- **這些路徑不能支持：**
  - 不能說「安全檢查全部通過」。
  - 不能說 overlay 主動單經過 V49 的保留金保護。PADD 單繞過 `guarded_decide` 的 `ctx.check`，也繞過 `TailNewStop.veto`。
  - 不能作為實盤或正式前推的資格。
- **最小反例：** 沒有真實執行失敗，所以無需保存。
- **報告口徑需要更正：** 從 v11 起所有含 PADD 的結果都帶有 rc=2，這在之前沒有揭露。清單見第 5 節。

## 2B. 兩個固定案例的時間線（B_TIMELINE.json）

時鐘說明：
- 事件時間 `t` 是 V49 runner 的 frame 時鐘。
- 相對時間 = t − window_start_ms。
- 市場窗口為 [start, start+300,000ms)。
- 每一行的持倉與成本是用成交量乘以訂單價格重建的，所以最終值和 row 相差 0.2～2.7 元。row 使用實際付款。

### 2629199（AR4_R25，官方勝方 UP）
| 相對秒 | 事件 | 選邊 | 弱邊 | 價格／數量 | G（強邊）／H（弱邊） | peak／floor | 觸發條件 | last_H 前值／同分支？ | 當時 P_UP／P_DOWN |
|---|---|---|---|---|---|---|---|---|---|
| 15.921 | DECIDE→UP | UP | | | | | | | +135.9／−217.7 |
| 16.069 | FIRST（action=PREPARE，apply 分支） | UP | | | | | | | |
| 16.069 | 主動修復 | UP | DOWN | 0.43／60 | 137.2／−219.4 | 137.2／34.3 | deficit＋stranded | —／— | +137.2／−219.4 |
| 17.682 | FLIP UP→DOWN | DOWN | | | | | | | +145.7／−225.9 |
| 56.874 | 主動修復 | DOWN | UP | 0.33／6.2 | 43.0／−424.5 | **145.7（UP 分支的峰值）**／36.4 | deficit＋stranded | **−219.4（DOWN 分支）／否** | −424.5／+43.0 |
| 57.9～98.9 | 主動修復 ×9 | DOWN | UP | 0.33～0.37，合計約 295 | G 41～72／H −420～−481 | 145.7／36.4 | stranded（多數同時也是 deficit） | 同分支 | |
| 180.9～193.9 | 主動修復 ×6 | DOWN | UP | 0.17～0.21，合計約 611 | G 60～134／H −628～−923 | 145.7／36.4 | deficit 或 stranded；cheap 從未觸發（賣價都 >0.15） | 同分支 | −725→−629 |
| 300.685 | FLIP DOWN→UP | UP | | | | | | | −521.7／+37.2 |

- **換邊後的成交（17.68 秒之後，OBSERVED）：**
  - DOWN：被動 EXPAND 530、FRESH_FRONTIER 957.4、REPAIR 165、REPAIR_FRONTIER 120，PADD（主動）195。
  - UP：主動修復 760.5，被動 REPAIR 135.8、REPAIR_FRONTIER 105、EXPAND 30、FRONTIER 5.7。
- 17 筆修復全部是部分修復（partial=true）；雙正目標一次都沒有達到。

### 2628553（AR4_R25，官方勝方 UP）
| 相對秒 | 事件 | 選邊 | 弱邊 | 價格／數量 | G／H | peak／floor | 觸發條件 | last_H 前值／同分支？ |
|---|---|---|---|---|---|---|---|---|
| 30.020 | DECIDE→UP | UP | | | | | | |
| 48.849 | FIRST（PREPARE）＋主動修復 | UP | DOWN | 0.26／26.5 | 17.7／−33.8 | 29.0／7.2 | stranded | — |
| 52.8～67.9 | 主動修復 ×5 | UP | DOWN | 0.36～0.61，合計約 267 | | 42→97.2／10.5→24.3 | 多數只有 stranded（64.06 秒另有 deficit） | 同分支 |
| 74.810 | FLIP UP→DOWN | DOWN | | | | | | |
| **91.789** | 主動修復 | DOWN | UP | 0.45／90.2 | 71.4／−209.0 | 97.2（UP 分支的峰值）／24.3 | **只有 deficit（stranded=false）** | **−97.6（DOWN 分支）／否** |
| 94.8～202.8 | 主動修復 ×16 | DOWN | UP | 0.22～0.55，合計約 1,116 | | 137.4（此時是 DOWN 分支的峰值）／34.3 | stranded 或 deficit | 同分支 |
| 242.397 | FLIP DOWN→UP | UP | | | | | | |

- 換邊後：DOWN 被動 EXPAND 867、FRONTIER 974.7、REPAIR 共 90，PADD 75；UP 主動修復 1,205.8，被動 REPAIR 共 455.9，PADD 15。
- 結局 −242.2／−2.3（row）。

### 三個問題的回答
1. **准入規則是否真的要等選邊之後才可能生效？**
   - OBSERVED：
     - v28 的 10 場中，first 的時間都在 DECIDE 當下或之後。延遲 0～45 秒，2628999 是同一幀。
     - first 全部來自 apply 分支（action=PREPARE），不是 `Context.observe` 分支（run_variant.py:50 的 `PREPARE_CANCELLABLE_RISK`）。
     - v25 在沒有 AR_AFTER_DECIDE 時，有 28/30 場的 first 在 DECIDE 之前，也全部來自 apply 分支。
   - 程式事實：
     - AR_AFTER_DECIDE（run_variant.py:415）只約束 apply 分支。
     - `Context.observe`（run_variant.py:36-50）不受它約束，靜態上仍可能在選邊前設定 first。
     - v28 的 AR_NOGATE 讓 `Context.check` 直接回傳 True（run_variant.py:53），admissions 為 0，准入規則從未被評估。
   - UNKNOWN：開啟准入規則後會不會停擺，沒有任何開啟准入規則的 v28 型執行可以觀察，不能把靜態可達性寫成已觀察到的現象。
2. **換邊前後，peak 與 last_H 指的是哪一支分支？**
   - 程式事實：
     - `peak=max(peak, inv[roles.strong]−cost)`（run_variant.py:37）。
     - roles.strong 是動態的：選邊前預設 UP（roles_runtime.py:17-19），之後是目前的選邊邊。
     - 換邊時不會重設，所以 peak 是「歷任強邊的最高分支收益」，而不是單一分支的基準。
     - last_H（run_variant.py:422、433）記錄的是上一次主動修復時的弱邊 H，換邊時同樣不重設。
   - OBSERVED 影響：
     - 2629199 在 56.874 秒拿 UP 分支的 −424.5 和 DOWN 分支的 −219.4 比較。當時 stranded 也成立，所以這次比較不改變結果。
     - 2628553 在 91.789 秒，**deficit 是唯一的觸發條件**，而且它來自跨分支比較（UP 的 −209.0 對 DOWN 的 −97.6），促成一筆 90.2 份 @0.45 的主動修復。
     - peak 方面：2629199 換邊後 floor 36.4 的來源是 UP 分支的峰值 145.7，卻套用在 DOWN 分支上。
   - 需要反事實測試才能確認的部分：如果改成依分支身份分開保存，觸發次數、修復金額與結局會怎麼變。
   - 本輪不重設，也不宣稱哪一種才是正確語義。它應該是「全程保留義務」還是「單一分支基準」，需要先定義。
3. **2629199 在 300.7 秒的 FLIP 代表什麼？**（OBSERVED）
   - window_end 在 300.000 秒；最後一筆盤口的 source_ms 是 299.498 秒，received_ms 是 300.685 秒。
   - FLIP 的時間等於最後一筆盤口的接收時間。frame 時鐘在這一點上與接收時間一致，所以不能把 300 秒當成同一時鐘下的硬界線。
   - 到期之後：NEW 0 筆、成交 0 筆；到期前後由成交重建的持倉與成本完全相同。
   - 結論：只改了角色與紀錄，對下單、成交、費用或終局計分沒有影響。

### 「修了又加」與另一個候選解釋
- **支持「修了又加」的觀察（OBSERVED）：**
  - 換邊後，強邊 DOWN 的被動 EXPAND／FRONTIER 成交量高於弱邊的主動修復量：2629199 是 1,487 對 760，2628553 是 1,842 對 1,206。
- **另一個候選：修復預算受限於 G 與 floor。** 主動修復每次都是部分修復，數量上限是 (G−floor)/價格。G 大多只有 40～135，floor 是 24～36，所以每筆只有數十到兩百多份。
  - 關鍵在於：強邊 ADD 以低於 1 的價格買進，會**提高** G，也就是在增加修復預算。
  - 所以暫停 ADD 也可能縮小修復能力。兩個機制的方向相反。
- **能推翻「修了又加」的觀察：**
  - 開啟准入規則之後，如果強邊 ADD 被擋下，但弱邊 H 的改善反而變小，G 也停在 floor 附近，就代表主要限制是預算，不是「又加」。
  - 反過來，如果 H 改善變大、G 仍然足夠，就支持「修了又加」。
- **第三個解釋（最後幾秒反轉的市場路徑）：** 2629199 到 295 秒時 UP 中價仍然偏低，勝方在最後幾秒才反轉，任何跟隨價格的做法都會虧。這可以用「同場 PADD80 在 240 秒時的分支狀態」檢驗，不需要新實驗。

## 2C. 結構占比（C_STRUCTURE.json；零費用、依結果挑選、已消費的 10 場，只作診斷）

分母（三組相同）：
- 計畫 10 場、工件完成 10 場、名目損益可計算 10 場。
- 經診斷可用 10 場：分類為「帳務有效、只有舊斷言失敗」或全部通過。
- 責任確認（未結為 0）10 場、UNKNOWN 0 場。

| 組別 | P>L 且 P>0（名目＝確認） | 雙正 | 正零 | 混合且 P>L | 混合但 P≤L | 雙非正 | 只算混合場的達成率 | 官方勝方為正 | 合計 P／合計 L（總額，不是場數比例） |
|---|---|---|---|---|---|---|---|---|---|
| v24 PADD80 | **1/10（10%）** | 0 | 0 | 1 | 7 | 2 | 1/8 | 5/10 | 1,711／5,640 |
| v27 AR3_R25 | 0/10（0%） | 0 | 0 | 0 | 6 | 4 | 0/6 | 5/10 | 193／3,244 |
| v28 AR4_R25（與 v27 逐場相同） | 0/10（0%） | 0 | 0 | 0 | 6 | 4 | 0/6 | 5/10 | 193／3,244 |

- **同場配對（AR3＝AR4 對 v24 PADD80）：**
  - 勝方收益：改善 4 場、退步 6 場。
  - 虧損 L：減少 8 場、增加 2 場，合計減虧 2,396。
  - 正收益 P：減少 8 場，合計從 1,711 降到 193（保留 11%）。
  - 逐場數字在 C_STRUCTURE.json 的 `paired_vs_v24.rows`，包含輸方分支的變化。
- **注意：** 規則判定的 PROMISING 只依據平均、勝方為正與 ≤−200 的場數。這張表顯示它的代價是正收益幾乎被壓光，這一點應依 V6 契約（可以犧牲部分正收益，但不能壓光）揭露。本輪不新增任何門檻。
- 主動與被動成交筆數、未結 owner 在 C_STRUCTURE.json 的 `per_market`。未結 owner 全部為 0。requested−fill 與 births 沒有被當成未結或成交使用。

## 3. 來源與雜湊（SHA256）
- v28 worker.py `30a3c95a…aa79`、overlay/run_variant.py `6b3c2f33…3d99`、economics.py `e7eeaab8…29b7`、SUMMARY.json `c480bdee…ebd5`、PROTOCOL.json `801d2731…ebd5`。
- v24 base：frozen_runner.py `1cbcce2d…f853`、general_finite_active.py `a1a86ca2…0690`、roles_runtime.py `267aa8fb…742c`。
- ROWS：v28 `347cb0fe…c9d4`、v24 `398ad4bd…ccf`、v27 `4224ea12…a415`；v28 RESULT.json `4d8160fd…5f86`。
- 每條路徑的 result、clock_trace、restoration_trace、row、public 的 hash 都在 A_VALIDITY.json 與 B_TIMELINE.json 的 `sources`。

## 4. 唯一建議的下一步：先修具體語義
依據：資料可用性已經釐清（屬於舊斷言，帳務與責任都有效），所以不需要「先修判讀」。但以下兩點已觀察到會影響修復觸發與預算，而准入規則的比較會和它們混在一起：
- last_H 的跨分支比較（2628553 在 91.8 秒因此觸發修復）。
- peak 與 floor 跨分支身份累積。

先把這兩個狀態的物理分支身份與生命週期定義清楚，並寫進協議：
- **last_H：** 依弱邊的分支身份分開記錄，還是換邊後把前一支的最後值保留為全程義務？
- **peak：** 是全程保留義務，還是依分支記錄？

之後才適合做准入規則的比較。

語義定下來之後，最小可區分方案（本輪不實作、不派送）：
- 同樣 10 場，只跑一組：AR4_R25＋語義定義版＋准入規則開啟，並和 AR4_R25（准入關閉、同一語義）配對。
- 需要記錄每次被擋的強邊單、G 的變化路徑與修復數量。
- 預期反證：
  - 如果准入規則開啟後，G 停在 floor 附近、H 的改善變小，就否定「修了又加是主要原因」，支持預算限制。
  - 如果成本低於 PADD80 的 50%（沿用 v28 已凍結的判準），就是停擺。

**需要 Codex 審查後更新的報告說法（原件未改）：**
1. v20～v28 各 CURRENT 的「0 個錯誤」：需要補充「含 PADD／放寬主動修復的路徑 rc=2、safety_pass=false，唯一原因是 legacy `active_matches_opportunity`（主動單來源登記＋數量 ≤5）；帳務有效、未結為 0」。
2. v27 CURRENT 與 RESEARCH_CURRENT v27 條目中「2629199 修復在 0.8 追上漲弱邊」這句是錯的：
   - 證據：17 筆修復中，16 筆 UP @0.17～0.37，1 筆 DOWN @0.43（B_TIMELINE）。
   - v28 CURRENT 已經更正，建議在 v27 條目加上更正指標。
3. v27／v28 的「PROMISING」旁應補上：正收益保留 11%、P>L 0/10（PADD80 為 1/10）。

## 5. 本輪沒有做的事
- 沒有模型 fit、沒有 worker job、沒有 HFT 或 native 重跑。
- 沒有修改策略、引擎、模型、live、收集器、服務或排程。
- 沒有修改 RESEARCH_CURRENT、凍結報告或共享契約。
- 新增或寫入的檔案只有：ACK_002.md、RETURN_002.md，以及 task_002_evidence/ 下的 3 個腳本與 3 個 JSON。
