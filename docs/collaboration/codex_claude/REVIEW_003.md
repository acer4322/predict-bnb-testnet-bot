# REVIEW 003｜接受機制對照證據；完整准入造成活動壓縮，不晉級

日期：2026-09-27。審查對象：[RETURN_003.md](RETURN_003.md)、v29 凍結協議與回收原件。結論：ACCEPT_WITH_CORRECTIONS，實驗結果為 DEGENERATE／NO_PROMOTION。接續 [TASK_004.md](TASK_004.md)，由使用者人工轉交，尚未派送。

## 已回答的問題

在 v28 相同選邊、修復及狀態語義下，開啟完整原生准入並未證明修復能力提高。它使原生路徑的活動與正收益再度縮小。部分虧損確實下降，但不是足以晉級的完整循環改善。

固定 10 場的數字由回收 ROWS 與 result/trace 交叉檢查，P/L 依兩個 signed 分支的正／負部分合計，不是私有帳戶利潤或可支用現金：

| 指標 | v28 OFF | v29 FULL |
| --- | ---: | ---: |
| 每場平均實際成本 | 1,998.32 | 692.10 |
| 正分支收益合計 ΣP | 193.20 | 111.89 |
| 負分支虧損幅度合計 ΣL | 3,244.43 | 2,327.81 |
| P>L 場數 | 0/10（0%） | 1/10（10%） |
| 官方勝方分支為正 | 5/10 | 6/10 |
| 雙正／正零 | 0／0 | 0／0 |

成本合計／平均的 ON÷OFF 為 **0.346343**，正收益合計保留 **57.91%**；相對 v24 PADD80 的 ΣP=1,711.07，FULL 僅保留約 6.54%。逐場正收益 5 場下降、2 場增加、3 場皆無正分支；L 改善 8 場、惡化 2 場。這些配對需一起看，不能只報勝方為正 50%→60%。

唯一 P>L 場 2628999：UP +5.0019、DOWN −3.8781、cost 36.6681；OFF cost 769.4278。它是成立的名目比例結果，但規模很小。全樣本平均成本亦低於 v24 PADD80 平均成本 1,707.53 的一半，觸發原先 DEGENERATE 活動診斷；不是新訂的收益保留門檻。

## 准入機制與覆蓋範圍

Codex 的只讀核查 [GATE_CHECK.json](review_003_evidence/GATE_CHECK.json)／[check_gate.py](review_003_evidence/check_gate.py) 直接讀固定 10 場的 restoration_trace，保存來源 hash。共 16,390 次呼叫，16,364 次拒絕，26 次放行。這是**評估嘗試次數**，不等於独立訂單、成交或16,364次不同決策機會。

| 入口 | 評估 | 拒絕 |
| --- | ---: | ---: |
| 原生被動 reserve 前 | 15,932 | 15,909 |
| 原生 active_opportunity | 452 | 452 |
| 原生 general_finite_active | 6 | 3 |

margin 失敗 7,410 次、reserve 失敗 8,954 次，本批沒有兩者同時失敗的呼叫。前者對應當時強邊買入，後者對應當時弱邊買入；這是角色／買入幾何，不能僅憑 side 推断私人方向信念或所有 producer 的 ADD/repair 意圖。

- 7,410 次 margin 失敗中，7,399 次 worst_margin 原已為負。依凍結公式，此時新買入須不再降低 margin。強邊買入的 delta 為 q×[1−(1+K)×price]；K=4.15 時常見強邊價格很難通過。這是既有規則的限制，並非本輪新發現的價格上限政策。
- reserve 拒絕按互斥順序分類：G 非正 1,213 次；G 仍正但已低於 .25×peak 3,513 次；其餘有現存餘量但候選支出超過餘量 4,228 次。沒有「G 本身高於線，只有 pending 單獨把 worst_G 壓過線」這個分類，不代表 pending 不影響其他呼叫。分類只描述既有軌跡，不能預測取消保留檢查後的成交與損益。

PADD overlay NEW 仍繞過這個入口，出生數 OFF 233→FULL 260。qualified active repair 亦走其 proposal，而非這個 Context.check；本批不能稱為「所有 ADD／repair 都被統一准入」。元件改變成交、排隊、後續持倉與角色，旁路也會間接受影響。

## 開局與修復後續

首次准入前的開局並未整段消失。15 秒的 gross／cost 10 場一致；30 秒仍有 7 場一致，60 秒只剩 1 場一致。中位 gross 約 1,478→901，中位 cost 約 798→549（60 秒），显示選邊／first 後的擴張被壓縮。這與「开局還能增加倉位」可以同時成立。

2629199 固定第一修復起點物理 F/L 觀察：L 從 −219.4 修到終局約 +1.8，F 卻結束在約 −307.5，不能當成保住正方向收益的循環。2628553 的 L 曾到約 +6.1，終局再掉到 −5.8，F 約 −34.5，也不能只取中途好看的時刻。兩例沿用原 trace 真實 inv/cost，沒有以 limit×fill 另造帳務。

## 執行有效性與報告更正

具名 job 為 `btc5m-v12g-native-admission-probe-20260927-v29`。14 條已回收，包含兩 controls、兩 BASE_OFF_CHECK、10 FULL。controls 完整通過；兩 OFF_CHECK 重現 v28。其他路徑中，2628999 FULL 為 FULL_PASS，其餘 11 條為已知 legacy active_matches_opportunity 單項失敗；原 rc=2 與 safety_pass=false 保留。全部 COMPLETE、帳務 valid、atomic pass、兩種 overfill=0、未結 owner=0，屬可用的名目帳務對照，不是安全全 PASS。

策略 overlay 與 v28 相同，主要 gate-on/off 行為差異是 AR_NOGATE 的缺席／'1'。SUBMIT.json 記錄 attempts=1，SUBMIT_STDOUT 的 accepted=true、同名 job 與 probe.hostname=DESKTOP-JIERAGF，回收日誌為 succeeded／collected。protocol 設 max_threads=4，worker.py 兩個順序階段各配置 ThreadPoolExecutor(max_workers=4)，數值庫環境設為1；這是提交／程式配置證據，沒有獨立量测作業系統的峰值執行緒數。Codex 本次只讀審查與文件工作，未派新 native job。

分工核查指出 v28 ROWS 本身缺逐項 safety_gate，無法僅靠表格證明失敗子檢查相等；Codex 再讀兩場 OFF 的 v28／v29 原始 result.json.gz，確認整個 safety_gate 字典一致、唯一失敗均為 active_matches_opportunity，來源 hash 已納入 GATE_CHECK.json。成本與P/L合計使用未四捨五入的原始 ROWS，與報告先逐場四捨五入再加總可能有微小差異。

必須保留以下更正而不覆寫凍結報告：

1. RETURN 的「平均成本 0.37 倍」應註明是 **逐場成本比的平均 0.368292**；平均成本之比／成本總和之比為 **0.346343**。兩者都成立，不能混稱。
2. 原 SUMMARY 的 `GATE_EVIDENCE=false` 是分析將預期 NOGATE='1' 的 BASE_OFF_CHECK 也納入 gate-on 檢查。SUPPLEMENT 是事後按 arm 修正的證據，不是預登記分析通過。原 false 保留，gate-on 環境與非零 admissions 支持實際啟用；下一輪分析須預先逐 arm 判定。
3. 拒絕次數是函式呼叫，主動 submits／birth 與有成交的 order 仍须分欄。99.84% 不能寫成「99.84% 的實際訂單被交易所拒絕」。

本批為相同已消費且按結果挑選的 10 場、零費用／既有延遲排隊假設，不建立多市場泛化、真實交易淨利或 Target 私人行為因果。全局 peak 與換邊後 reserve 義務仍可能有影響，但本輪沒有隔離它；不藉此直接修改 peak/last_H。

## 唯一下一步

完成四格邏輯對照：重用 v28 OFF 與 v29 FULL，只新增 MARGIN_ONLY、RESERVE_ONLY 各 10 場，加 6 條相容性檢查，共 26 條新路徑。先辨別是哪個元件／合用方式破壞活動，再决定是否需要更換協調機制。詳細授權、停點、來源與完整指標見 [TASK_004.md](TASK_004.md)。不調 K／RETAIN，不擴市場，不改旁路，不訓練或部署。
