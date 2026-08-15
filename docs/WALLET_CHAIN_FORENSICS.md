# Predict Wallet Chain Forensics

這是一個完全唯讀的 BNB Smart Chain 錢包鑑識工具，不需要 Predict.fun API key，也不會碰實單下單程式。

目標範例：

```text
0x9ddbdc8bdf41ec79cd4f5c4dec14050f4b591a18
```

## 研究範圍

這個工具不限制 BTC 5 分鐘市場。它先從 Predict.fun 在 BNB Mainnet 的 Exchange 合約掃描目標錢包自己的 `OrderFilled`，保留所有能辨識出的 outcome token，再選擇性使用現有 Binance Prediction 唯讀 HMAC API 補上市場 metadata。

因此第一輪可以同時研究：

- 該錢包實際參與多少市場、多少 outcome token
- BUY / SELL 比例
- maker / taker 型態
- 進場成交價分布
- 每筆 collateral / position sizing 是否固定
- 同一市場是否分批加倉
- 是否有提前 SELL
- 相鄰成交間隔與毫秒/秒級 burst 程度
- UTC 活躍時段
- 能透過 Binance metadata 對上的市場中，Crypto / Other 佔比
- Crypto 市場的 symbol 分布，例如 BTC / ETH / SOL 等
- 若 metadata 有 start/end time，可直接計算 T-minus

未能對應到 Binance metadata 的鏈上交易不會被刪掉，而是保留 `market_family=UNKNOWN`。因此不會因為它不是 BTC 5M 就漏掉。

## 使用方式

先更新分支：

```powershell
git checkout feature/regime-guard
git pull
```

先跑最近 14 天：

```powershell
python .\tools\wallet_chain_forensics.py 0x9ddbdc8bdf41ec79cd4f5c4dec14050f4b591a18 --days 14
```

如果目前 PowerShell 已經有：

```powershell
$env:BINANCE_API_KEY="..."
$env:BINANCE_API_SECRET="..."
```

工具會自動使用 Binance Prediction Market List 做 metadata enrichment，但仍然不會送單。

若只想純鏈上掃描：

```powershell
python .\tools\wallet_chain_forensics.py 0x9ddbdc8bdf41ec79cd4f5c4dec14050f4b591a18 --days 14 --no-binance-enrich
```

## RPC

BNB Chain 官方列出的部分 public mainnet endpoints 不提供 `eth_getLogs`，所以本工具預設使用一個支援 logs 的 public BSC RPC。若 public RPC 限流、歷史深度不足或不穩，建議自行設定可使用 `eth_getLogs` 的 BSC RPC：

```powershell
$env:BSC_RPC_URL="https://YOUR_BSC_RPC"
python .\tools\wallet_chain_forensics.py 0x9ddbdc8bdf41ec79cd4f5c4dec14050f4b591a18 --days 14
```

若 RPC URL 含 API key，請只放環境變數，不要貼進聊天、Git 或截圖。

掃描器會自動把過大的 `eth_getLogs` block range 切小重試；若 provider 不接受多合約 address filter，也會退回逐一 Exchange 掃描。

## 掃描更久

確認 14 天正常後，可直接擴大：

```powershell
python .\tools\wallet_chain_forensics.py 0x9ddbdc8bdf41ec79cd4f5c4dec14050f4b591a18 --days 30
```

或指定 UTC 起點：

```powershell
python .\tools\wallet_chain_forensics.py 0x9ddbdc8bdf41ec79cd4f5c4dec14050f4b591a18 --since-utc 2026-07-01T00:00:00Z
```

也可以直接指定 block range：

```powershell
python .\tools\wallet_chain_forensics.py 0x9ddbdc8bdf41ec79cd4f5c4dec14050f4b591a18 --from-block 12345678 --to-block 12445678
```

## 輸出

預設輸出至：

```text
data/wallet_chain_forensics/0x9ddbdc8bdf41ec79cd4f5c4dec14050f4b591a18/
```

內容：

```text
order_filled_raw.json       原始鏈上 logs
binance_token_metadata.json 可解析到的 Binance Prediction metadata
fills_chain.csv             每筆鏈上成交
markets.csv                 每個市場/Token 的彙總
summary.json                統計與候選量化指紋
report.md                   人類可讀摘要
```

如果要丟回 ChatGPT 做第二階段分析，優先提供：

```text
fills_chain.csv
markets.csv
summary.json
```

原始資料有疑問時再補 `order_filled_raw.json`。

## 鏈上解碼

`OrderFilled` 包含 indexed `maker` 和 `taker`，以及：

```text
makerAssetId
takerAssetId
makerAmountFilled
takerAmountFilled
fee
```

工具只用 `maker = 目標錢包` 的事件來建立該錢包的 order fill history，避免同一 matched transaction 因 counterparty-facing event 被重複統計。

對一般 collateral ↔ outcome token 成交：

```text
makerAssetId = 0  => BUY outcome token
takerAssetId = 0  => SELL outcome token
```

成交價由 collateral amount / shares amount 重建。

`execution_role` 目前依 CTF Exchange matching event 結構推定：當 event 的 `taker` 等於 emitting Exchange contract 時標成 `TAKER`，否則標成 `MAKER`。這個欄位適合做研究假說，但等 Predict API key 核准後應再用官方 match event 做交叉驗證。

## Market metadata 與 Crypto 專門化

鏈上 `OrderFilled` 能確定 token、方向、價格、數量、時間與交易 hash，但不直接帶有人類可讀 market title。

如果 Binance HMAC 可用，工具會將 Binance Prediction Market List 中 outcome `tokenId` 對到鏈上 token，補上：

```text
market_id
market_title
chart_type
symbol
start_ms
end_ms
outcome
```

只有已解析的市場才進入 `crypto_share_of_resolved` 分母。未解析市場維持 UNKNOWN，不會被錯算成非 Crypto。

如果至少 10 個已解析市場中 Crypto 比例 >= 80%，summary 會列出：

```text
crypto-market specialization
```

這只是量化指紋候選，不代表已證明錢包背後的私人策略。

## 目前限制

- 鏈上只能看到實際成交，無法完整還原沒有成交就撤掉的 limit orders。
- Binance metadata 不是 Predict 全站資料庫，所以部分 Predict 市場可能一直是 UNKNOWN。
- public RPC 的歷史 logs 深度與限流依 provider 而異。
- collateral/shares 顯示數量目前依 Predict order units 的 18 decimals 處理；成交價格是兩者 ratio，因此不受這個顯示單位假設影響。
- maker/taker role 是依 matching event 結構推定，之後要用 Predict 官方 API 對照驗證。

## Tests

```powershell
python -m pytest -q tests/test_wallet_chain_forensics.py
```

測試覆蓋 address topic、BUY/SELL asset 解碼、maker/taker role 推定，以及 Crypto metadata enrichment / fingerprint summary。
