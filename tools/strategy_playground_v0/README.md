# BTC5M Strategy Playground V0 使用與交接

建置／驗證日期：2026-09-21，Asia/Taipei。

## 入口

已建立獨立的本機研究頁面：`http://127.0.0.1:4331`。

請在執行 BTC5M 專案的主機瀏覽器開啟。手機上的 `127.0.0.1` 指手機自身，不能連到這個主機。介面已做手機寬度排版檢查，但 V0 沒有開放區域網路或公網，也沒有修改既有 4320 Dashboard、交易服務或防火牆。

在專案根目錄重新開啟：

```powershell
.\start-strategy-playground-v0.ps1
```

狀態／停止（僅作用於身分與已保存 PID 相符的 Playground）：

```powershell
python -X utf8 tools/strategy_playground_v0/launch.py status
python -X utf8 tools/strategy_playground_v0/launch.py stop
```

此服務沒有啟動時自動啟動、排程研究、派送訓練或實單權限。

## 現在可以做什麼

1. 選擇一個真實原生研究快照。目前凍結 R78 的 7 個已消耗起點，涵蓋 6 個市場；它們不是 7 場新回測，也不是實單持倉。
2. 選擇六種假想後續情境之一：UP、DOWN、來回震盪、零成交／UNKNOWN、撤單競速、部分成交／價差擴大。
3. 調整上行、修復、待成交成本與支出成本的評分權重，或直接修改公式。點「檢查公式／更新動作」重新計算合法動作。
4. 點一個合法動作推進一個假想事件。動作集有 14 個候選，實際可用數量依當下合法性而定；灰色動作顯示拒絕原因。最多手動推進 12 步，可退回研究分支或回到不可變的真實起點。
5. 點「從目前狀態分支比較」，在同一份起點與同一情境下比較 KEEP 與你的公式控制器。每次延伸 1、3 或 8 個事件，顯示兩種 UP／DOWN 條件結算曲線、最差分支及含未終結掛單的下界。
6. 寫下想法，按「保存實驗 JSON」。檔案包含起點、公式、參數、手動動作序列、假想情境、分支結果、資料／kernel 雜湊與限制。

所有實驗保存於：

```text
data/research/strategy_playground_v0_20260921/experiments/
```

可將這個資料夾內的檔名或匯出的 JSON 提供給助手，接續正式化假說，不必只憑文字回憶當時的畫面。

## 第一個操作範例

先使用預設起點及來回震盪情境，直接比較一次。接著按「偏重修復」，再比較；觀察改善最差分支時，是否同時犧牲較佳分支、增加成本或留下更多未終結責任。此操作只是機制探索，不是策略績效驗收。

「偏重修復」是未驗證的示範配置，不是推薦實單策略。

滑桿調整的是評分權重，**不是下單量倍數**。新單表示、價格／份額刻度、Maker 15 與可變 Taker 候選沿用凍結的研究 kernel；不會變更 8781 的份額或任何實單限制。

## 公式

預設：

```python
repair_weight * d_floor + add_weight * d_best - risk_weight * pending_added - cost_weight * cash
```

- `d_floor`／`d_best`：候選若以其限價全額成交，最差／較佳結算分支的幾何變化。只供評分，不代表保證成交或未來收益。
- `d_up`／`d_down`：同一假設下各結算分支的變化。
- `cash`／`pending_added`：新候選全部委託的限價成本。撤單不會提前釋放這些成本。
- `floor`／`best`：目前已確認持倉的兩分支幾何。
- `gap`：已確認 UP／DOWN 份額差；`repair_gap` 是扣除修復側 pending 份額後的未覆蓋份額差，不是虧損金額。
- `is_add`、`is_repair`、`is_active`、`is_cancel`、`is_keep`：動作類型的 0／1 指標。

允許有限數字、四則運算、比較、and／or、min／max／abs，以及 `a if condition else b`。不使用 eval／exec；不接受任意 Python、匯入、檔案、網路、指數、迴圈或 Target／winner 變數。錯誤、除以零與非有限數值會明確拒絕，不默默替換成新策略。

自然語言想法目前**只保存，不自動翻譯成程式**。助手可在後續讀取這份實驗後，把想法轉成可檢查的公式。

## 結構停止

自動門檻預設值為較佳分支至少 +70、較差分支最多虧損 10；預設不開啟自動鎖定。

可選 STOP_ALL 或 REPAIR_ONLY，門檻可看 CONFIRMED 或 PENDING_SAFE。PENDING_SAFE 按各結算方向計算所有未終結掛單的保守成本下界；不是把 pending 當作已成交。

手動「停止新單並等撤單」不受門檻達標限制，會在同一研究分支保持 STOP。它不會立即消除掛單；一個事件可以請求撤銷一側可取消責任，後續繼續等待回報。UNKNOWN／cancel-pending 不會因按鈕或時間到而消失，部分成交仍更新帳務。已手動 STOP 後，分支兩組都延續 STOP。

REPAIR_ONLY 只限制後續新候選，並不宣稱原先 ADD 掛單已被撤除；它們仍可能成交。+70／−10 是條件結算形狀，不是已實現或保證獲利。

## 必須保留的證據邊界

V0 是 **真實原生研究快照＋短程假想 receipt-stress 世界**。不是完整市場 replay、不是已校準的成交機率、不是 HFT queue 撮合或原生結算成績。

沿用 R78 kernel 的簡化包括：零手續費、無市場衝擊、以公開 UP 報價推導互補側報價，以及由指定壓力事件控制成交份額。Passive 可成交判斷是情境簡化，不是原生排隊重播。保留帳務、部分成交、UNKNOWN 與撤單競速，**不代表已經驗證市場成交真实性**。

圖中的 KEEP 是「不新增委託但保留既有掛單」對照，**不是 V49／R65 的神經控制器**。你的公式也是新研究控制器，不是已升級的主模型。

所有起點為已消耗／部分事後挑選的研究資料；情境可由人知道。介面為 Analyst Lab，不宣稱 blind test，所有人類操作與匯出資料標示 `training_eligible=false`。不能將微縮世界結果當成 unseen 泛化、勝率、平均實際 PnL 或實單準備度。

## 第二台與原生 HFT

「保存第二台原生驗證需求」只寫出 `SAVED_NOT_SUBMITTED` 的需求檔，**不派送、不排程、不重跑 R77／R78**。

V0 尚未接通完整市場原生 HFT adapter、同起點 R65 配對回放、真正 native 任意 checkpoint fork 或 10／30 市場批次驗證。匯出明寫 `native_adapter_status=NOT_CONNECTED_IN_V0`，不是一個假裝會執行的按鈕。

正式驗證仍須另外凍結候選、確認第二台身分與容量、配對真實回放及檢查費用／receipt／ownership。介面不會因單一假想端點變漂亮而自動 promotion。

## 實作與驗證

來源：`data/research/v49_incremental_cycle_value_micro_20260921_r78/` 的六個 kernel／encoding 檔，僅複製至獨立 vendor 資料夾，保留 SHA256。`ROOTS.json` 也做不可變副本與雜湊驗證。原研究資料與主線 CURRENT 不覆寫。

2026-09-21 的實際檢查：

- 23 項 unittest／HTTP 檢查 PASS；其中 84 組配對情境涵蓋 924 次分支狀態轉移的 receipt／成本核對。不是 84 場原生回測。
- 10 項實際 Edge headless UI 檢查 PASS，包括 7 個起點／14 個動作、4 條分支曲線、參數變更後失效處理、手動一步／退回、危險公式拒絕、STOP 鎖定、1440px 桌面與 390px 手機寬度無水平溢出、無 JavaScript runtime exception。
- JavaScript `node --check` 通過。
- 本輪 native HFT 0、worker job 0、模型更新 0、實單變更 0。

驗證與畫面檔：

```text
data/research/strategy_playground_v0_20260921/VALIDATION.json
data/research/strategy_playground_v0_20260921/UI_VALIDATION.json
data/research/strategy_playground_v0_20260921/UI_DESKTOP.png
data/research/strategy_playground_v0_20260921/UI_MOBILE.png
```

重驗證：

```powershell
python -X utf8 tools/strategy_playground_v0/server.py --check
python -X utf8 tools/strategy_playground_v0/test_playground.py
node --check tools/strategy_playground_v0/static/app.js
# 先啟動 Playground；使用独立 headless profile，不接觸個人瀏覽器 profile。
python -X utf8 tools/strategy_playground_v0/browser_check.py
```

本次刻意不更新主線研究指標，也不以 UI 完成度代替策略品質。下一個工程範圍是原生回放 adapter；須明確測試它，不得把這個情境 kernel 的數字改標成 HFT。
