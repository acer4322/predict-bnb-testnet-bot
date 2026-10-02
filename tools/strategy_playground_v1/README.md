# BTC5M 時間制模型實驗室 V1

2026-09-21 Asia/Taipei。主功能已實作、原生與瀏覽器流程已驗證；仍有一項亞微秒顯示／匯出時間精度限制，不能宣稱全數測試通過。

## 開啟

在 BTC5M 主機的瀏覽器開啟：

```text
http://127.0.0.1:4332
```

4331 保留舊 V0，不是新版。V1 沒有開放公網或區域網路；手機上的 localhost 不會連到主機。

重新啟動：

```powershell
.\start-strategy-playground-v1.ps1
```

查狀態／停止僅本 V1 服務：

```powershell
python -X utf8 tools/strategy_playground_v1/launch.py status
python -X utf8 tools/strategy_playground_v1/launch.py stop
```

## 使用方式

選市場與原版 R65 模型，按「載入原版回放」。頁面也會自動載入最近已完成的原生案例；本次最後完成的是市場 2312596、原版 seed 20260920 對照候選 seed 20260921。

按播放後，模型的既有原生操作會隨市場時間出現，不需要逐步選 HOLD／ADD／REPAIR。時間軸是整場 00:00–05:00；支援暫停、拖曳與 0.5×／1×／2×／5×／10×／30× 觀看速度。倍速只改觀看速度，沒有進入模型或撮合請求。

暫停後調整候選設定，選「從開盤套用」或「從目前市場時間套用」，再按「第二台重新回放」。後者從原版開盤重播到指定時間，時間點之後使用新設定及候選自己的原生成交。每個新候選都以選定原版為前綴，**不是累積上一次候選的多次修改**。

重算需要第二台可連線且資源允許。計算中保留先前圖表，標示實際載入的參數與新工作狀態；只有完整結果、帳務與前綴核對通過後才套用新曲線。斷線是 UNKNOWN，不當成工作失敗或自動重送；同請求使用固定工作編號去重。

## 可調設定

- R65 兩個凍結 checkpoint：seed 20260920、20260921。
- 加倉方向：DYNAMIC／FIXED_UP。
- 既有時鐘設定：P50／P90／STATIC3。
- 推論偏好：加倉、修復、主動修復、等待。這些是在模型輸出分數上的顯式偏移，**不是修改訓練權重、下單份額或成交概率**。預設 0 保留原分數；原生合法性 mask、ownership、預留成本與 receipt 流程不變。

小幅改參數可能仍選到相同動作，並非滑桿必然有明顯效果。畫面會保留實際使用的設定。V1 尚未提供任意 Maker/Taker sizing 公式、自然語言自動編譯或直接修改 NN 權重；文字想法會保存作為研究註記。

## 畫面與結果

市場報價取自當時記錄的 public book；持倉與成本來自原生已接收 receipt。顯示原版／候選的 UP、DOWN 條件結算、未完成掛單、含 pending 的保守下界、實際下單與成交，以及模型選擇的動作與分數。

條件結算不是已實現損益。最後結算後的回報保留其時間，不會直接搬到 05:00 前；本次案例最後回報在市場時間 05:00.700，整場核對欄另列。

可選市場索引來自 24 個已消耗的 R77 歷史市場，含 96 條已保存模型路徑；這不是本輪新增驗證了 24 或 96 場。

## 本輪實際原生驗證

全部在身分與 SSH host key 確認的第二台執行，max_threads=4，一次一個工作。三個成功回放都使用同一個已消耗市場 2312596，基線重用 R77，沒有重跑 R77／R78／R79 批次，也沒有訓練。

| 測試 | 已取得證據 |
|---|---|
| 零偏移 | 整場模型輸入、分數／动作、訂單、native actions、receipt、持倉與成本與凍結原版一致。 |
| 01:20 後套用 ADD −0.5、REPAIR +0.25 | 01:20 前的原生前綴完全一致；參數生效時間核對通過。小偏移未改變所選動作，終值相同。 |
| 切換 R65 checkpoint | seed 20260921 透過原生引擎重新計算，產生不同路徑；新單 44→31、receipt 37→21，帳務與 IOC transport 核對通過。 |

功能驗證的結算分支：

| 模型 | UP 條件結算 | DOWN 條件結算 |
|---|---:|---:|
| R65 seed 20260920 | -6.0704285714 | -5.9690000000 |
| R65 seed 20260921 | +3.8779848485 | -5.5020151515 |

這是接口／回放功能測試，**不代表 seed 20260921 普遍較佳、策略晉升或新市場泛化**。

工作編號：

```text
pgv1-c-966a7b87e2648062a16b   zero-offset / complete
pgv1-c-1547ab2801cafc8804f6   timed preferences / complete
pgv1-c-94b98e1c5e3d6461bbbb   checkpoint switch / complete
```

一次中途失敗 `pgv1-c-362aff11721841791c24` 是舊 runner 以輸出資料夾 basename 建 scratch 路徑，兩次通用 candidate 名稱衝突。現已改成 candidate_<job_id>；失敗紀錄與舊目錄都保留，沒有刪除後重送同一工作。

原生資料仍保留歷史 `active_matches_opportunity` 原始旗標；canonical accounting／receipt／ownership 等研究 gate 通過，不應將此描述成全部歷史 safety flags 都 PASS。

## 顯示精度的已知限制

圖表／匯出資料目前將大型 epoch timestamp 轉成浮點毫秒後再減開盤時間，會產生極小的舍入誤差。本次三個完成案例量得最大約 **0.00011328125 毫秒＝113.28125 奈秒**。

原始原生 tape、receipt、模型決策、訂單與結算帳務沒有使用這份顯示資料，因此上述原生驗證不受影響。另以目前資料所有變化邊界進行 **5,779 個整毫秒定位檢查，0 個持倉／成本不一致**。

但要求亞微秒精度的逐筆匯出 frame 測試仍有 1 項失敗：本地測試為 22 項中 21 項通過、1 項未通過。修正 timeline.py 的嘗試被工具安全檢查阻擋，**沒有套用**，也沒有偷偷改寫舊結果。不要宣稱全部驗收通過，亦不要把這些匯出浮點 frame 當成奈秒級 queue/latency ground truth。

下一個有界工程修正是先在整數 timestamp 中減 epoch，再轉成相對時間；對既有結果只需重匯出，不應因此重跑已完成的原生研究。

## 保存與交接

程式：`tools/strategy_playground_v1/`。

本地回放結果與參數：`data/research/strategy_playground_v1_20260921/jobs/<job_id>/`。

註記匯出：`data/research/strategy_playground_v1_20260921/notes/`。

正式核對摘要：`data/research/strategy_playground_v1_20260921/ACCEPTANCE.json`。

其他證據：`LOCAL_VALIDATION.json`、`UI_VALIDATION_WITH_SUBMIT.json`、`UI_MODEL_SWITCH_VALIDATION.json`、`UI_VALIDATION.json`、`WORKDIR_FIX.json`。已保存桌面／手機寬度實際 Edge headless 截圖；最新純 UI 檢查 9 項 PASS，兩次含原生派送按鈕的檢查各 10 項 PASS。

研究主線、R65+Adam parent、8781、4320 Dashboard、V0 4331、資金與實單設定皆未變更。此次訓練更新 0、實單變更 0、promotion 0；最後核對第二台 active_jobs=[]。
