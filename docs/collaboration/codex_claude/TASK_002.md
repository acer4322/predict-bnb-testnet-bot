# TASK 002｜核對 v28 結果有效性與重新開啟准入規則的前提

狀態：PREPARED_FOR_MANUAL_HANDOFF。Codex 制定；使用者交給 Claude 執行。不是已派送或已完成的證據。

## 本輪要回答的問題

**目前是否有足夠可信的證據，值得進行「選邊後重新開啟防止再借准入規則」的最小比較？若不足，缺的是結果判讀、狀態語義，還是執行正確性？**

目前 RESEARCH_CURRENT 最新為 v28，父批 v27。主線目標仍是從空倉開局主動承擔風險，協調主動／被動 ADD 與主動／被動修復，保留有意義的正向收益。不能靠停掉開局擴張、消除所有 ADD 或把收益壓到近零取得漂亮的減虧數字。

這輪只分析既有工件，可新增有界的本地分析腳本及報告。**不修改策略／引擎／模型，不派送 worker，不重跑 HFT，不訓練，不改 live、收集器、服務或排程。** 不用電腦操控、不另建 Claude 對話、不啟動背景代理。完成後停下，等 Codex 驗收。

## 先讀與去重

1. 根 AGENTS.md、docs/agents/research.md、docs/agents/RESEARCH_CURRENT.md。
2. docs/collaboration/codex_claude/REVIEW_001.md。這是初步審查，不是所有推論都已證實；可用來源反駁。
3. data/research/v12g_active_repair4_small_20260927_v28/ 的 CURRENT.md、PROTOCOL.json、SUMMARY.json、worker.py、analyze.py，以及下列問題對應的 overlay 函式。
4. 僅按需要讀 v27、v25 與 v24/base 的相關來源、既有報告和相同市場工件；不要全庫掃描。

ACK_002.md 寫明最新指標、是否已有同題診斷、真正新增的證據，以及預計讀取的資料範圍。若已存在完整答案，引用並核對，勿換名稱重做。若主線已出現影響本題的後繼，先記錄差異；仍可完成適用的舊批審查，但不要把過期候選當成最新方案。

## 工作 A：14 條既有路徑的有效性分類（最高優先）

資料根：data/research/lan_worker_returns/btc5m-v12g-active-repair4-small-20260927-v28/。

已觀察到：10 條 AR4_R25、2 條 PADD80_CHECK 的 row 是 rc=2、safety_pass=false；兩條控制是 rc=0、true。SUMMARY 同時寫 errors=0。**這是待解釋的口徑差異，不能先認定資料全無效，也不能當成安全檢查通過。**

- 逐條讀 ROWS.json、對應 row.json、stdout/stderr 和 result.json.gz 的安全檢查欄位；用 Python gzip 在記憶體讀取，不改動壓縮原件。只輸出相關欄位。
- 從 worker.py 的 rc／safety_gate／errors 提取一路追到實際產生 exit code 與 safety_gate 的來源。標示精確檔案、函式、行號及條件。
- 分開說明：程序是否完成、資料是否齊全、執行是否合法、是否仍有未結責任、經濟／策略判準是否通過。若 safety_gate 混合不同類型要求，拆成個別檢查。
- 判斷是否屬真實執行失敗、研究組態不適用的舊斷言、經濟條件未達標、期末未結，或 UNKNOWN。不得僅憑欄位名稱分類，也不得把檢查改成通過。
- 用 2628553、2628769 的 v24 PADD80 既有結果，和 v28 的 PADD80_CHECK 對齊，確認問題是否基線已存在。依 manifest／現有引用找來源，找不到就記 UNKNOWN，不補跑。
- 給出每一類結果可支持什麼結論，以及哪些比較必須暫緩。真實執行失敗時，先保存最小反例；其餘可做的靜態分析仍可完成，但不宣稱有效績效。

## 工作 B：兩個固定案例的狀態與時鐘核對

只深入 2629199、2628553 的 AR4_R25，必要時讀相同市場的既有 PADD80／v27；不增加結果挑選的案例。

對應 arms/v12g28_AR4_R25_<market>/ 中已有 restoration_trace.json.gz、clock_trace.json.gz、result.json.gz、row.json。先檢查鍵值和大小，再抽取事件附近的片段；禁止將全檔灌入上下文。這輪不是 Read/Glob/Grep 工具限制，可在主機用小型腳本讀既有壓縮工件，不能執行 native runner。

建立事件表，至少含：market、原始時間／來源時鐘／相對市場起點時間、DECIDE／FLIP／first／修復提案／下單／成交事件、當時強弱邊對應 UP/DOWN、兩側持倉成本與 signed payoff、peak、last_H、pending、准入規則是否啟用及其理由。缺欄位留 UNKNOWN，不能用出生單量替代成交。

回答三件事：

1. **准入規則是否真的等到選邊才可能生效？** 查 Context.observe 如何設定 first、AR_AFTER_DECIDE 實際約束哪個分支，列 first 與 DECIDE 的可比時間。沒有開 gate 的既有執行，不得把靜態可達性寫成已觀察到停擺。
2. **換邊時 peak／last_H 的物理分支身份是什麼？** 追蹤第一個換邊前後，以及新弱邊 H 與舊 last_H 的比較。先界定它是全程保留義務或單一分支基準；不得直接重設來消除風險。區分程式事實、已觀察影響與需反事實測試的影響。
3. **2629199 約 300.7 秒的 FLIP 代表什麼？** 先核實市場真正的到期時間與事件／接收／決策時鐘，不先把 300 秒當成同一時鐘下的硬界線。確認它僅改記錄／角色，或造成到期後 NEW、成交、費用或終局計分改變。取消與結清回報可晚於到期，不能一概當成非法交易。

「修了又加」目前是有支持的候選解釋，不是已識別的唯一因果。至少列另一個合理解釋及能推翻它的觀察。不能用「重設保護機制」這個實驗直接識別「換邊太早」。

## 工作 C：補上使用者要求的結構占比（重算現有資料）

固定使用 v28 同一 10 場，三列組別：v24 PADD80、v27 AR3_R25、v28 AR4_R25。AR3 與 AR4 相同仍保留來源標示，不算獨立樣本。官方勝方只作離線評估。

逐場列 UP、DOWN signed payoff、官方勝方收益、支出、主／被動成交筆數（若有）及數量、canonical 未結 owner 與責任金額（若有）、有效性分類、來源。主／被動與 ADD／repair 是不同維度；不從方向或份數直接猜角色。

以 epsilon=1e-8 處理浮點零：P=max(UP,0)+max(DOWN,0)，L=max(-UP,0)+max(-DOWN,0)。
- 主指標：完整有限數值的場次中，P>epsilon 且 P>L+epsilon 的 n/N 及百分比，即「正收益比虧損高」的描述性結構比例。
- 同時拆列：混合正負且正大於負、混合正負但未達、雙正、正零、雙非正；不要把雙正／正零隱藏在總比例。
- 另列僅混合正負場的達成率，以及官方勝方為正率；合計收益／合計虧損不是上述場次占比。
- 分母列計畫、工件完成、可算名目損益、經診斷可用、責任確認、UNKNOWN／缺失。名目全樣本與確認有效樣本分開；分母為零寫 N/A。
- 列同場改善與退步、正收益保留金額及減虧幅度、輸方分支變化。requested_qty-fill_qty 不等於未結責任，births 也不等於 fills。

這是零費用、依結果挑選且已消費 10 場的診斷，不是泛化或實單資格。不能新增 3:1、50%收益保留或成功率門檻。v28 已凍結的 50%成本退化判準可如實引用，但不是新的普遍策略限制。

## 交付與停點

允許新增／編輯的範圍只有：
- docs/collaboration/codex_claude/ACK_002.md
- docs/collaboration/codex_claude/RETURN_002.md
- docs/collaboration/codex_claude/task_002_evidence/（分析腳本、抽取 JSON/CSV、小型證據與來源 hash）

不要改 RESEARCH_CURRENT、任何原始／凍結報告、策略來源、共享 CURRENT.json 或協作契約。若發現報告錯誤，在 RETURN 列出原說法、證據、更正文字，由 Codex 審查後更新指標；保留既有歷史。

RETURN_002.md 應包括：
1. 一段直接結論：哪些資料可用、候選 gate 比較是否具備前提。
2. A 的 14 路徑檢查表、B 的兩場時間線、C 的三組結構比例與分母。
3. 每項關鍵結論的來源、欄位／事件索引、原件 SHA256；區分 OBSERVED／INFERRED／UNKNOWN。
4. 唯一建議下一步：先修判讀／先修具體語義／可作最小 gate 比較，三者以證據選擇。若仍需比較，只寫出可區分原因的最小方案和預期反證，**不要實作或派送**，不要展開 30 場或多參數矩陣。
5. 明列本輪沒有模型 fit、worker job、live 或收集器變更；如果意外發生，據實列明而不是填零。

驗收標準是問題被證據釐清，或缺失被精確界定，不是得出預設的「應打開 gate」。本地抽取程式只能驗證計數、來源對齊與公式；不能用它替代 HFT 行為驗證。完成 RETURN 後停止，交回使用者轉請 Codex 分析。
