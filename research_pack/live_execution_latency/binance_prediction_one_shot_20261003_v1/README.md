# Binance Prediction ETH5M 一次性延遲收集

狀態：PREPARED_NOT_TRADED。固定 BUY UP、amountIn=1 USDT、MARKET/FOK；實單由帳戶持有人執行。
已通過 16 項離線測試，以及只讀行情與 1 USDT quote 預覽。實際建單數為 0。

在既有 bot repository 根目錄套用 `INSTALL.patch`（新增四個檔案），然後參照 `RUNBOOK.md`。
若四個檔案已存在，使用現有檔案與 `SOURCE_SHA256.json` 核對，不重複套用。

```powershell
git apply --check "<research-data checkout>/research_pack/live_execution_latency/binance_prediction_one_shot_20261003_v1/INSTALL.patch"
git apply "<research-data checkout>/research_pack/live_execution_latency/binance_prediction_one_shot_20261003_v1/INSTALL.patch"
python tools/run_binance_prediction_one_shot_latency_v1.py --mode observe
```

帳戶持有人自己執行實單的命令及只讀復查方法位於 `RUNBOOK.md`。
預設 observe 不建單。持久一次性鎖保留失敗／逾時，沒有自動重送、加碼或轉帳。

`READ_ONLY_PROBE.json` 是匿名預覽摘要，包含現貨事件到主機、Prediction 報價版本 age、quote 耗時及時鐘模型。
Prediction 報價版本 age 不代表純網路延遲。官方 API 未提供 Predict 內部 acceptedAt／精確 firstFillAt，保留 UNKNOWN。
這一場 1 USDT quote 成功不保證下一場也通過；官方最低約 1.5 USDT、依深度變動，拒絕時停止。

公開包不包含私人 reference、主機資訊、raw timeline、任何真實 wallet/order/quote/token ID 或憑證。
`INSTALL.patch` 中 PRIVATE 字樣是離線 MockTransport 測試常數。
此測試工具沒有掛入既有 strategy/service/scheduler，沒有修改 enabled/armed 狀態。

SOURCE_SHA256.json 的 new_files 雜湊以 CRLF 正規化為 LF 後核對；Windows Git 可能產生 CRLF。
