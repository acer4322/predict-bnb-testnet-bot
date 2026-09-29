# Codex REVIEW 002｜接受資料核對，修正因果推論，準備單因素准入比較

結論：**ACCEPT_WITH_CORRECTIONS**。TASK_002 完成了核心來源診斷與結構指標；但不接受「last_H 已導致額外修復，所以先修改 last_H／peak」的因果推論，也不接受同時改語義再開 gate 的單組方案。下一步見 TASK_003.md；本次只準備任務，尚未派送任何 worker。

## 已核對的主要結果

獨立讀取 v28 14 條原始 result.json.gz 與固定10場 v24/v27/v28 ROWS：

- 14 條都是 COMPLETE、unresolved_owners=0、execution_accounting_valid=true、原子責任守恆通過、兩項 overfill=0。
- 12 條唯一失敗子檢查為 active_matches_opportunity；2 條控制全通過。可支持本引擎下帳務確認的名目損益比較，不能改稱安全全PASS，也不證明實盤或一般執行正確性。
- PADD 的來源登記及放寬主動修復數量與舊斷言不相容，這是可解釋的研究組態差異；它仍揭露 PADD 未走同一准入入口。原 rc/safety 不改寫。
- 主指標與 pooled 正收益如下，三组官方勝方為正均5/10。這是按結果挑選、已消費且零費用的診斷樣本，不是泛化。

| 組別 | 正收益大於虧損 | 雙正 | 雙非正 | 兩分支正收益合計 P |
|---|---:|---:|---:|---:|
| v24 PADD80 | 1/10（10%） | 0 | 2 | 1,711.0696 |
| v27 AR3_R25 | 0/10（0%） | 0 | 4 | 193.2031 |
| v28 AR4_R25 | 0/10（0%） | 0 | 4 | 193.2031 |

AR3/AR4逐場相同，不能算兩次獨立驗證。正收益合計保留11.292%，是跨場兩分支正值合計之比，不是每場都保留11%，也不是先前只看官方勝方正收益的約23%。虧損合計減少與正收益大幅縮小並存，不能只用舊PROMISING標籤晉級。

## 必要更正

1. **A表的 active 不是成交筆數。** a_validity.py:10取active_native_submits。2628721為24次提交／出生、23張有成交；PADD80_CHECK 2628553為12／11。C表使用active_filled_orders的方式正確。來源：base/frozen_runner.py:415-418及各原始result。
2. **舊斷言的數量判定須按實際程式解讀。** general_finite_active.py:187-188是 `len(active)==registered_sum<=5`，再加各模組上限。把(ii)描述成所有active≤5後，卻對active=24/12/49只列(i)，會自相矛盾；更精確是分列active數、登記總數、來源等式是否成立、登記預算是否超標。不要把原判定改寫成不存在的獨立raw-active條件。
3. **不能擴稱所有PADD路徑都rc=2。** 同一10場中v24 PADD80的2628999就是rc=0／safety=true。其他歷史批次未逐條審查，不批量修改v20～v28的說法。
4. **跨分支 last_H 被讀取，不等於改成分側記錄就少一筆修復。** 2628553在91.789秒確有UP H=-208.9766對舊DOWN H=-97.6276的比較，deficit是唯一觸發。但UP首次沒有歷史last_H，依原本None語義也會觸發。Codex新增唯讀核查腳本，在全部10場已記錄的951個觸發入口、183次修復提案上，分物理侧保存last_H且保留None規則，deficit／任一觸發差異均為0。見review_002_evidence/LAST_H_CHECK.json。此證據只涵蓋已觀察軌跡，沒有新native執行；足以否定把該案例當作「分側保存必然改善」的證據。
5. **peak的角色混合是已知設計語義，尚非已證實錯誤。** 可把它理解為歷任強邊收益高點的保留義務，也可提出分側方案；本輪不能憑更低floor就宣布修復改善。先保留原定義，以免同時改預算與gate。
6. **B時間線有更精確的帳務來源。** clock_trace.states提供逐幀inv/cost，兩案例最後狀態均與row一致。b_timeline.py的limit×fill重建成本有誤差，只可作近似；後續直接對齊states及事件順序，處理重複時間戳，不將近似當精確值。
7. **到期後角色更新的結論要限定。** 2629199最後source=299.498秒、received=300.685秒；既有工件未見該時點之後NEW／fill，物理終局inv/cost相同，支持沒有改變這次名目兩分支損益。但角色／最終方向標籤確有變化；不能擴稱所有角色相關分數均不受影響。
8. **替代解釋只能作機制線索。** 開gate後G/H怎麼變，不能單獨識別「修了又加」和「預算不足」，因動作、成本及後續路徑一起改變。「任何跟隨價格都會虧」也過強，本輪沒驗證所有此類策略。

## 下一步的選擇

採用 **現有狀態語義固定，原生准入開關單因素比較**。不先修改last_H、peak或PADD路由；因此OFF和ON的對照含義清楚。RETURN提出「只跑一組語義修改+gate ON」卻與不存在的同語義OFF對照，不能直接採用。

TASK_003規定14條新路徑：10個gate ON、2個v28 AR4_R25基線重現、2個固定控制；其餘OFF配對重用v28。原生gate涵蓋的實際路徑、PADD漏過、開局15/30/60秒、first/DECIDE以及收益被壓縮程度全部必報。K=4.15只作既有設定，不恢復為使用者策略目標。

先固定manifest／分析，再按worker契約在第二台電腦單job、max_threads=4執行。已知legacy斷言必須細分保留；任何新增執行失敗或UNKNOWN先停止診斷。這是使用者轉交後的下一輪範圍，本次Codex没有執行。

## 審查證據與範圍

- Claude原件：RETURN_002.md、task_002_evidence/{A_VALIDITY,B_TIMELINE,C_STRUCTURE}.json及其三個脚本；原件未修改。
- Codex直接核對：v28 overlay/run_variant.py:36-55、:258-270、:318-343、:415-437；v24/base/general_finite_active.py:187-188；兩案例原始restoration_trace／clock_trace及抽樣result。
- 額外有界核查覆蓋v28全部14個result與v24/v27/v28同10場ROWS，確認有效性欄位與P/L數值。
- 新增check_last_h.py只讀gzip、寫審查證據，沒有匯入policy或執行native。
- 本輪模型fit=0、worker提交=0、live／收集器變更=0。研究指標只新增本審查與更正指標，保留凍結歷史。
