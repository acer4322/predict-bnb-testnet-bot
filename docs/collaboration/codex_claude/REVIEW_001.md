# Codex REVIEW 001

首輪直連成功。CLI 回傳 success、is_error=false、session_id 與請求一致，modelUsage 唯一模型為 claude-opus-5-5；31 turns，permission_denials 空。使用訂閱登入。此證據確認本次請求和回傳，不代表已連接既有桌面對話或已建立無人值守循環。

Claude 原始交付：runs/20260926T190535Z_996bea4a/RETURN_001.md。結論：接受為唯讀初步審查，不接受直接派發其建議的大批矩陣。

## 獨立核對通過

- v28 worker returns 的 ROWS.json：10 條 AR4_R25 及 2 條 PADD80_CHECK 均 rc=2、safety_pass=false，2 條控制為 rc=0、safety_pass=true。CURRENT 的零錯誤不能解讀為所有安全檢查通過。原因尚未診斷，不能直接推論是新增修復造成，因為 PADD80_CHECK 也有此狀態。
- overlay/run_variant.py、economics.py 及 v24/base/roles_runtime.py：peak 與 last_H 使用動態強弱分支且未按換邊重設。這是需要分支身份核對的狀態語義，不代表重設一定正確；peak 也可能被設計為全程保留義務。
- 2629199 row.json 記錄晚於 300 秒的第二次 FLIP；active_births 沒有 0.8 價格，並列有 DOWN 的 PADD。births 是下單出生記錄，不能稱為逐筆成交；需與成交及 restoration_trace 分開。

## 對 Claude 建議的修正

1. 優先解釋 rc=2 / safety_pass=false 的判定來源和基線情況，再決定是否新增實驗。不能把尚未解釋的檢查失敗當成正常成功資料。
2. 原因「過早換邊」與其所提「重設准入保護」比較不匹配；該比較混入保護機制改動，不能識別換邊時機的因果影響。
3. active_births 的價格不是成交價格；requested_qty 減 fill_qty 也不直接等於未結責任，還可能包含已取消、拒絕或終結的未成交量。需 canonical owner / terminal evidence。
4. 補上使用者指定的百分比：在具完整 UP/DOWN 終局分支資料的場次中，正分支收益大於負分支虧損絕對值的占比。混合正負、雙正、正零分開列，缺失不視為失敗或成功。另列官方勝方為正率；合計收益／合計虧損不是這個百分比。
5. 新增或重設 peak / last_H 前，先定義其物理分支身份與責任生命週期。不得因暫時清除歷史門檻而宣稱風險已修復。
6. 暫不採用 Claude 提出的 74～84 路徑矩陣；先作有界診斷及最小可識別比較，控制基線重用與重複實驗。

## 下一任務範圍（尚未派送）

唯讀定位 safety 判定來源、選邊前後 first / peak / last_H 的現有 trace 證據、晚於市場終點事件是否影響有效績效與行為。必要時由 Codex 解讀本地壓縮工件，再給 Claude 可核對的摘要。確認原因後才設計程式修改及 worker 比較。

本輪無研究作業派送、無訓練、無 live 改動；尚未建立排程或背景監看。後續執行功能尚未因本次讀取測試而獲得驗證。
