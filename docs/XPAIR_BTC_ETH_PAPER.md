# XPAIR_BTC_ETH_PAPER

獨立、只讀、紙上測試的 BTC／ETH 五分鐘反向雙腿實驗。它不會呼叫任何 Prediction 下單、撤單、領取、轉帳或提領端點，也不會寫入既有 `simulation.db` 或 `live_m0w.db`。

## 測試的兩個實驗臂

- `BTC_UP_ETH_DOWN`
- `BTC_DOWN_ETH_UP`

每個對齊的 BTC／ETH 五分鐘市場只在指定剩餘秒數附近擷取一次。兩腿依真實 Ask 深度以**相同 shares**模擬成交，費用包含在總成本中。

兩腿結果分為：

- `winning_legs = 0`：雙輸
- `winning_legs = 1`：一勝一敗
- `winning_legs = 2`：雙贏

報表同時顯示：

1. 所有成功擷取並完成官方結算的市場結果；
2. 訂單簿新鮮度、時間偏差、深度與總成本都符合條件的 `eligible` 樣本；
3. `eligible` 雙輸率及 95% Wilson 信賴區間；
4. 一勝、雙贏、總 PnL 與 ROI。

## 執行

使用與主程式相同的唯讀 Binance HMAC API Key：

```powershell
$env:BINANCE_API_KEY="你的唯讀 API Key"
$env:BINANCE_API_SECRET="你的 HMAC Secret"
python -m predict_bot.xpair_btc_eth_paper
```

預設設定：

- 進場擷取：剩餘 180 秒，10 秒窗口
- 每個實驗臂紙上預算：10 USDT
- 含費總成本上限：每組 shares 0.98 USDT
- 最少等份成交：1 share
- DB：`data/xpair_btc_eth_paper.db`

較保守的一勝保護條件：

```powershell
python -m predict_bot.xpair_btc_eth_paper `
  --entry-seconds-left 180 `
  --entry-window-seconds 10 `
  --stake 10 `
  --max-total-cost 0.95
```

只看目前統計：

```powershell
python -m predict_bot.xpair_btc_eth_paper --summary-only
```

只擷取第一個符合窗口的市場後結束：

```powershell
python -m predict_bot.xpair_btc_eth_paper --once
```

## 判讀重點

`eligible_double_loss_rate` 才是接近「實際條件可形成兩腿時」的雙輸率。`all_double_loss_rate` 則包含價格或深度不合格的市場，只適合觀察 BTC／ETH 五分鐘方向分歧基準。

樣本很少時不要只看點估計。例如 1 次樣本剛好雙輸會顯示 100%，但 95% 信賴區間仍非常寬。至少先收集 300 組 eligible 已結算樣本，再考慮是否值得接實單執行器。
