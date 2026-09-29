# REVIEW 005｜接受部分時鐘證據；299.850秒主動修復暴露到達時間與收益代價缺口

日期：2026-09-27。審查 [RETURN_005.md](RETURN_005.md)、v31原始capture／result、v30同場失敗前綴、凍結策略來源。結論：ACCEPT_WITH_CORRECTIONS／REPRODUCED_CAPTURE_PARTIAL。研究仍聚焦修復能力；下一步 [TASK_006.md](TASK_006.md) 已準備，人工轉交，未派送。

## v31實際完成了什麼

- v30／v31的failure_trace.json.gz逐位元組相同，已保存的1479個state／plan及動作前綴未變。原生結果仍ERROR，並非策略完成。
- UP_153的seq73／74兩筆回報已捕捉：exchange時間300.100秒，receive時間300.350秒；讀取時native clock為300.255秒，回報領先95ms。
- native帳務已多出10份／兩筆成交，OUR receipt sequence仍72；canonical carrier仍SUBMITTED、filled=0，沒有因未確認回報釋放責任。
- 時鐘觀測器advance.n=0。install將base.ex.advance_to換成strict_advance_to，原觀測器掛在更早的shift_audit函式上，未取得失敗那次elapse原始rc與推進前後記錄。

原ANALYSIS標OTHER_ERROR，是用舊檔名／行號辨認斷言，但加入觀測後斷言位置已變。相同斷言語義、呼叫路徑、實際違規時間與完全相同前綴支持事後更正為REPRODUCED_CAPTURE_PARTIAL；原件保留。下一轮不能再以測試通過或設定檔代替实际掛鉤呼叫證據。

來源釘選需按檔案讀：SOURCE_PIN中quantity_seam的v30/v24 hash相同，但不同於template；active_adapter有v30/v24 hash，沒有template對應hash。因此protocol概括「工具hash都與template相同」不準確。這份pin也不是src/tapes的完整hash清單；新輪需另驗實際載入資料。上述差異不否定本次原始capture與完全相同trace，亦不能以「副本相同」宣稱所有執行依賴已全驗。

## 使用者指出的尾盤主動單，確實值得優先處理

Codex直接核對凍結`base/active_opportunity.py`及failure_trace的opportunity_first，來源與算術見 [LATE_ORDER_CHECK.json](review_005_evidence/LATE_ORDER_CHECK.json)／[check_late_order.py](review_005_evidence/check_late_order.py)。

| 事件 | 相對時間 |
| --- | ---: |
| 送出UP_153，10份、限價0.31 | 299.850秒 |
| 研究窗口結束 | 300.000秒 |
| 模擬成交，送出後250ms | 300.100秒 |
| 模擬回報時間，再經250ms | 300.350秒 |

送出時僅剩150ms，小於固定250ms下單延遲；以本研究窗口作為可成交截止時，它在送出時便已無法於窗內到達。capture也確認模擬在窗後100ms成交。這不能推導真實交易所一定會接受該單；實際venue關閉規則與本地5M窗口的對應尚須證據。

觸發來源是繼承的`ActiveOpportunity`：

1. 尚未用過第一次符合條件的主動機會，且送出時間仍滿足start≤t<end。
2. 當時UP分支仍虧損，且尚有quantity_cap。
3. 固定15份被動候選價0.05，名目金額0.75，低於該凍結規則的1元條件，因此不再以「被動票仍可用」拒絕主動候選。
4. 主動ask=0.31、可見深度10份，經quantity/payoff cap與最小金額檢查後提出10份。

這個入口沒有按下單延遲檢查預計到達時間。它不是一個經本輪驗證的尾盤緊急修復控制器；固定15份／1元條件也不能直接說成目標帳戶或真實venue的完整規則。

## 同一張單也會耗盡所剩正收益

決策前signed分支：UP −11.52199、DOWN +1.60473。候選限價支出10×0.31=3.10；若全數按限價成交，UP會變−4.62199、DOWN變−1.49527，兩側皆負。

捕捉到的兩筆原生成交實際合計1.781322；只做離線增量算術，則UP −3.30331、DOWN −0.17659。這不是已確認終局損益：OUR未接納違規時間回報、carrier未結。不能為得到這組數字而繞過receipt斷言。

這是MARGIN_ONLY刻意移除reserve保護的對照，不能指稱FULL或所有版本都會做同一筆單。它仍提供清楚的修復反例：**減少反向虧損，可能同時把最後的正收益花完；送出前未到期，也不保證到達時來得及。** 後續修復教材與動作評估應明列這兩點。

成交截止與回報確認期限也需分開。到期前已成交、到期後才收到的有效回報仍有帳務責任；不能一律忽略所有窗後回報，或在到期瞬間自行釋放owner。

## 不接受直接切入drain作為已成立修復

实际函式是`strict_advance_to`：cur≥target放行，rc=0放行，rc=1回False，其他rc拋NATIVE_EXECUTION_INVALID。run_whole忽略False是候選控制流程問題；本次仍沒有直接捕捉rc=1。

RETURN建議「False後離開source loop、進end2／drain」尚不足以採用：

- end2再次advance後也會process；未證明這次可以到達目標。
- 既有drain在wait返回1時會計算query upper bound並呼叫process，但Reader看的是實際native clock。上界不是實際收到回報的時間，不能以此保證300.350秒回報已可交OUR。
- 中斷行情迴圈會改變source消費完整性；不能只放寬producer.calls斷言就宣稱修復成功。
- 「其他已完成路徑從未出現rc=1」沒有執行期證據，RETURN自己的限制亦標UNKNOWN；不得據此宣稱舊结果完全不受影響。

下一輪只補正有效strict函式的觀測、確認native feed尾端與期限來源，並做尾盤修復規則審查。根因證實後才制定執行修復／時間可行性變更及相容性矩陣。不得用新的任意尾盤禁單秒數遮掉原時鐘錯誤。

## 接續任務

[TASK_006.md](TASK_006.md)：一個新隔離包、最多一條同市場同MARGIN_ONLY的觀測路徑；在install後確認實際binding，使用strict语義測試，確保原rc及失敗前後時鐘被保存。同時只讀列清主動入口的期限／收益檢查，給出以實際延遲及可驗證截止時間為根據的最小設計，不實施新策略、不補RESERVE_ONLY、不改native binary或drain。

Codex此次新增審查與任務文件、只讀算術工件，未派native、未改live或收集器。v31原始結果與後補判讀保持分開。
