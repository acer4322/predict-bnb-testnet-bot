# BTC5M 新場批次03：完整85場

使用者授權交付剛盤點的全部85場（2810903-2814776），全數ID>2807162；與既有519、新70及先前已推30場不重疊。全85候選會員在查官方標籤前固定，本批完整重用清單，不依損益、勝方或confidence選擇／替補。第三批加先前30場，累計115場；原先至少100場的數量目標已滿足。本批只交付資料，不執行候選判定。

每場events.npz含data/local_times_ms，沿用凍結HFT244 feed V1。原始／匿名事件與wakeup位元組逐場相同，3場既有驗收樣本相同；未執行native引擎。盘口exchange/local時鐘均使用receivedMs；公開成交用executedAt加既有mid人工排序偏移，該偏移不是實測延遲。保留原轉換的開場前公開成交，不另裁切。事件ns、wakeup及metadata ms。

標籤在../../labels/FRESH_AFTER2807162_BATCH03_LABELS.json；同級LABEL_PROVENANCE.json附85場官方GET安全回覆與當時回覆hash。重用剛驗證的官方快照（2026-10-02 07:43 UTC），全85均RESOLVED/SETTLED、唯一明示WON UP/DOWN且與具名resolution一致；不以最後中價推斷。

META.json與BATCH_VALIDATION.json記錄每場source/received起尾缺口及最大內部gap，必須三者全部<=5000ms才算該時計品質通過。source84/85、received75/85、兩時計75/85；10場有空窗異常，最大received21746ms。所有85場均完整交付，異常未刪除／替補。舊盤點的品質booleans曾只算端點，本批以最大內部gap一併核對，不沿用錯誤boolean。這些是品質描述，不是公開成交完整收集證書；公開逐筆成交完整性仍UNKNOWN。

只發布數值陣列、公開metadata、轉換程式及hash，不發布原始tape、地址、帳戶、訂單或交易識別資訊。索引與SHA256SUMS可找齊及核對資料。
