# TASK_001：接手確認與下一批研究設計覆核

狀態：PREPARED_NOT_DISPATCHED。由Codex編製；只有這次接手／唯讀設計被授權，本任務不包含新native、fit、worker派送或live操作。

## 目的

把現有V12g研究接入「Codex訂目標、Claude執行、Codex審查」循環。先確認最新結果，對固定R25擴大驗證及修復後再ADD的配對研究提出可執行計畫。研究目標與約束見README_ZH.md。

## 先讀範圍

從repo根目錄讀AGENTS.md、docs/agents/research.md，以及docs/agents/RESEARCH_CURRENT.md最新第一項及其指向的CURRENT／PROTOCOL／SUMMARY。2026-09-27建立任務時已更新到v12g_active_repair4_small_20260927_v28，父批是v12g_active_repair3_small_20260927_v27；若已有後繼，先報告差異，不能套用過時任務直接執行。

按需讀該包overlay/economics.py、overlay/run_variant.py，以及v12g_fresh30_20260927_v24的PROTOCOL／市場清單／摘要。僅核具體欄位，不全倉掃描大DB、raw tape或其他代理對話；需要超出範圍時先列明理由。

## 必須回答

1. 目前受測系統、費用情境、選樣方式、對照重现與已消費範圍是什麼？哪些只是宣告，哪些有執行證據？
2. R25所謂「被動修復處理不了」實際如何判斷？沒有掛單、掛單被甩開、cancel-pending、已送未成交能否被混為一談？
3. 弱邊角色換邊後，last_H與peak比較是否仍有一致的分支身份？找程式證據，不先假定有bug。
4. v27的10場與原30場如何對齊？要補固定R25，哪些格可重用、哪些未跑？禁止把其餘20場稱完全未見holdout。
5. v28已更正2629199：主動修復主要買便宜UP，換邊後繼續ADD DOWN抵銷修復；0.70限制一次未觸發。先以trace核對這個更正，不重複v27「修復在0.8追DOWN」的舊推論。對修回再ADD失守列兩個以上可推翻的解釋與最小同根比較；評估選邊後重開准入的候選是否又會造成停擺，不先假定它有效。
6. 報告格式怎樣同時保留兩側收益、官方勝方、正收益大於虧損比例、未結責任、費用與缺失分母？

## 交付與停止

桌面模式可只新增本目錄ACK_001.md與RETURN_001.md；其餘檔案不改。直接唯讀模式則將相同內容回傳，由Codex保存，無需寫檔。

ACK包括讀到的最新包、模型身份的可用證據／UNKNOWN、任务理解和任何冲突。RETURN包括上述六項、檔案行號／資料欄位證據、未知項及一個建議的下一批矩陣；明確哪些是觀察、推論或未執行計畫。

交付後停止，等Codex REVIEW。Codex可否決或調整研究設計；Claude可提出反證，不可自行宣稱模型晉級／live-ready。不得因本任務提及後續執行，就提前派job。
