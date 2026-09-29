# TASK 003｜原生准入規則開／關的單因素小批比較

狀態：PREPARED_FOR_MANUAL_HANDOFF。這是交給使用者轉交的任務，不是已派送。Claude 讀取本文件並被使用者要求執行後，按本輪範圍完成一個批次，再交回 Codex。

## Codex 的判斷與唯一問題

先讀 REVIEW_002.md，然後讀根 AGENTS、docs/agents/research.md、RESEARCH_CURRENT、docs/agents/development.md 與 docs/agents/worker.md。

RETURN_002 的名目損益與來源診斷有用，但不能由跨分支 last_H 比較直接推論「它多產生一筆修復」。Codex 在保留 None 首次觸發規則、只按物理側保存 last_H 的比較中，檢查 v28 已記錄的 951 個觸發入口、183 次修復提案，布林結果差異為 0。這不是 native 反事實執行，但不足以支持先把 last_H／peak 全改掉。

本輪只回答：**在 v28 相同的選邊、修復與狀態語義下，重新啟用既有原生路徑的 economics.admission，會改善完整 ADD／repair 循環，還是再次壓縮開局活動與正向收益？**

這個開關涵蓋原生被動與原生主動模組，並非純粹「禁止強邊 ADD」。PADD 的 overlay NEW 不經過同一入口，本輪保持這個差異並如實量測；不能宣稱已控制所有 ADD，更不能把結果外推為統一准入機制的成敗。

## 先檢查是否已有等價結果

- 查 RESEARCH_CURRENT、v25～v28 協議與既有 worker 狀態／回收工件。v25 是選邊前啟用、RETAIN=0 等不同設定；不是這次 AR4_R25 的等價格。
- 如果已出現完全相同的後繼或同名 job，核對來源並重用，不重送。若有其他人在修改同一候選或占用 worker，停在 READY／WAITING，交回具體狀態。
- 不需重新讀完整 Target 歷史或新增教師市場。固定沿用 v28 的 10 場，全部已消費且按結果挑選。

## 凍結範圍

新包：data/research/v12g_native_admission_probe_20260927_v29/。
唯一 job：btc5m-v12g-native-admission-probe-20260927-v29。
若路徑已存在，先核對；不得覆寫別人的工作或換名字偷偷重跑。

沿用 v28 的 base、model、data、queue、latency、fee、資金與所有 policy source。overlay 策略檔必須是 v28 原檔 byte copy，SHA256 相同；允許調整新包的 worker／protocol／分析與路徑引用。不要修改已凍結原包。

唯一行為變因：
- BASE_OFF：v28 AR4_R25 原樣，V12G_AR_NOGATE='1'。
- NATIVE_GATE_ON：只將 V12G_AR_NOGATE 從子程序環境**移除**。不可設成字串 '0'，因現行 bool(os.environ.get(...)) 會將非空字串視為 True。

其餘保留：K=4.15、RETAIN=.25、GROSS=300、FLIP=.1、PADD=.80、AR=DP、AR_UNCAP=1、AR_AFTER_DECIDE=1、AR_TRIGGER=1、AR_STRANDED_MAX=.70，及 v28 worker 的完整環境清理清單。

不改 peak、last_H、None 初始化、角色、觸發條件、取消機制、時間邊界、PADD 路由或 active 登記。不增加價格上限、冷卻秒數、持倉上限，不移植新 reward。K=4.15 只是此對照繼承的規則，不是使用者重新要求固定比值；若它導致強邊高價 ADD 被大量拒絕，要作為診斷結果揭露。

## 固定矩陣：共 14 條新路徑

- 10 場 NATIVE_GATE_ON：2628553、2628769、2629133、2629199、2628653、2628410、2629019、2628557、2628721、2628999。
- BASE_OFF_CHECK：2628553、2628769 兩場，重現 v28 AR4_R25；這是確認新 runner 的基線一致，不是新增獨立樣本。
- V12_CONTROL、K415_CONTROL：2491311，各一條，沿用 v28 的原設定及預期值。
- 其餘 BASE_OFF 配對直接使用 v28 的既有 AR4_R25，不重跑。PADD80 沿用 v24，同時作背景參考。

先跑四條 control／BASE_OFF_CHECK，通過後才啟動十條新 arm。四條檢查若不一致，保存差異並停止本批，不能邊修邊繼續。overlay、base hash 與決策參數相同的條件下，兩條 BASE_OFF 的 UP/DOWN、cost、inventory、submits、route fill quantities、rc／失敗子檢查必須和凍結結果一致。

## 執行邊界與已知斷言

本輪允許一個第二台電腦的具名研究批次；不允許主機 HFT、模型 fit、全30場擴測、重新調參、部署、live 或收集器變更。

依 docs/agents/worker.md 核實 worker 身份與 host key，probe → exact/global status → stage/load-only → 一次 submit → 收集。使用既有 dispatcher，**一個 heavy job、max_threads=4**，不得沿用 v28 的12並行設定。不要另造 scheduler／watcher；傳輸逾時只查狀態，不重送。

原始 rc、safety_gate 及所有子檢查原樣保存。新 runner 另加分類，不能刪除失敗檢查或強制 safety_pass=true：
- controls 應重現原本完整通過。
- 實驗路徑可能只因 legacy active_matches_opportunity 失敗而 rc=2，須保留來源登記差異與數量預算差異，標成離線診斷的已知差異。
- 只有在 status COMPLETE、唯一失敗為上述已核對舊斷言、帳務valid、責任守恆通過、兩種 overfill 為0、canonical未結為0時，才納入「帳務確認的名目配對」，仍不是安全全PASS或live資格。
- 任意其他失敗、pending/UNKNOWN、hash不符、native rejection 均停止尚未啟動的路徑並保留部分證據。不要假定 rc=2 全部可忽略。執行已排入的並行路徑如需處置，依既有 worker 安全停止／收集流程，不殺資料收集服務。

## 收集與分析

保留 row/result/clock_trace/restoration_trace、stdout/stderr、來源與模型hash、實際子程序環境白名單、job/collect狀態。先將無效／UNKNOWN與有效樣本分開，再算指標。

1. 證明 gate 確實啟用：AR_NOGATE不存在、Context.check 的 admissions 非零（如沒有啟用，解釋 first/路徑原因，不能寫成有效 gate 對照）；按 origin、物理side、route列評估／拒絕數，分開 margin_ok 與 reserve_ok。PADD 可繞過的範圍必須列出。
2. 保留開局：15／30／60秒的UP、DOWN份數、實際成本、signed兩側收益、主被動成交。first／DECIDE／FLIP時間並列，若 gate 提早啟用如實記錄，不臨時增加選邊守門改變實驗。
3. 用 clock_trace.states 的實際 inv/cost 與事件順序，對齊 G/H/peak/floor、修復提案與成交。不要再以 limit price × fill 重建「精確成本」；同時間多狀態／回報有歧義時標示前後界線。
4. 兩個案例2629199、2628553必看，但不只報成功案例。所有10場列從原第一修復起點固定物理F/L的恢復／再流失，以及後續ADD與主被動repair活動；若新路徑分叉，不能把舊路徑未發生的成交當成新路徑結果。
5. 依TASK_002公式列P>L場數比例、雙正／正零／混合／雙非正、官方勝方為正率，完整分母。列每場正收益保留、減虧及輸方分支變化，另列spent與未結責任。0/0不當作收益保留。
6. 全10場平均成本若低於v24 PADD80的一半，沿用v28預登記標DEGENERATE；同時連續報告對BASE_OFF的活動／成本變化。即使沒觸發此標記，收益近零也不能稱為穩健能力。不得只憑均值改善寫PROMISING或模型晉級。

解釋時不要用單一G/H現象認定唯一原因。強邊ADD可增加G但會降低H，弱邊repair可改善H但消耗G；這是兩方向分支損益，不是可無限再投入的現金。gate同時改變成本、排隊與後續路徑，需按配對觀察區分「減少再加倉」「缺乏修復預算」「整體活動被壓制」，不足處留UNKNOWN。

## 交付與停點

Claude可寫新v29包、其具名worker回收工件，以及本目錄 ACK_003.md、RETURN_003.md。既有技能／dispatch必需記錄依其規範保存。禁止改v28及更早原件、共享CURRENT.json、TASK/REVIEW、其他任務工作或RESEARCH_CURRENT；由Codex驗收後更新研究指標。

先寫ACK，凍結PROTOCOL/manifest/analysis，跑有限本地純分析檢查及worker load-only，之後才提交唯一批次。遇到缺少必要API或需變更策略來源，停下列具體差異，不自行擴大。

RETURN_003：一段結論、job/collect與14路徑狀態、基線一致性、10場配對表、gate覆蓋及漏過的PADD、開局與修復後活動、退步案例、限制、唯一下一步。完整批次或真實阻塞後停止；不追加另一組參數、不做新訓練，不因本文件而自動開始後續任務。
