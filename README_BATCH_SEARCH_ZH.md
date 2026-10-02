# BTC5M 批次公式／參數搜尋 V1

## 本次交付狀態

**實作包與本地元件測試完成；尚未寫入遠端專案、尚未派送新 job、尚無新市場成績。**

2026-09-21 已透過 5m bot 讀取 AGENTS、research/current/worker/development 路由、R87 程式與凍結 manifest，並查重 R59／R60／R65。第二台首次 SSH 逾時，後續既有 dispatcher probe 成功，回報 DESKTOP-JIERAGF／Python 3.13.15；R87 狀態為 succeeded。**遠端程式寫入隨後被工具安全檢查攔截**，再次查詢也確認原擬寫入的 `tools/research_batch_search_v1.py` 不存在。沒有以其他寫入通道繞過此攔截。

所以本包採獨立下載交付。它不是「已部署於你的主機」的證明。本地測試是在隔離環境以合成資料執行；Windows／R87 真正整合、native 撮合控制組與策略結果，仍必須由第二台實跑驗證。

專案中僅曾建立空目錄 `data/research/btc5m_batch_search_20260921_r88`；未將主線 CURRENT 改成 R88。最新已完成研究仍是 R87。

## 這版做什麼

`SEARCH_SPEC.json` 指定公式族群與數值範圍；搜尋器提出候選、取得回放分數後調整下一組、保留所有試驗、選出 Pareto 短名單，再用其餘執行情境檢查。

第一個 adapter 的研究問題是：**已確認的主動修復失敗，與尚未修復部位的真實成交年齡，能否改善後續被動加倉的成本判斷。** 這是延伸 R87 的歷史資訊缺口，不是只重掃原公共成本公式。

- 記憶：`attempts`、`age`、`attempts_age`。
- 曲線：`linear`、`sqrt`、`saturating`。
- 失敗／年齡權重：各在 **0.25～4.0** 之間作對數尺度搜尋；未使用的權重在 effective config 明確歸零。
- 起始九組涵蓋 3×3 的族群組合；之後從已完成候選的 Pareto 集合做鄰域變異，並保留全域隨機探索。第一個自適應點固定會走鄰域探索。

這是**不新增依賴的隨機＋Pareto 鄰域搜尋**，不是 Optuna／TPE／貝葉斯最佳化，也不是重新訓練神經網路。搜尋器與研究 adapter 分開，可接續使用；新增其他公式需要明確實作、測試及重新凍結，而不是把任意程式字串交給 `eval`。

### 公式的作用範圍

只對候選被動單中「新增未配對曝險」部分扣除一筆軟成本：

```
新增成本 = 新增風險份額 × 當前單位價格不確定性
         × [失敗權重 × 曲線(已確認失敗涵蓋量 / 尚未修復量)
           + 年齡權重 × 曲線(真實成交年齡 / 剩餘期限與物理延遲)]
```

失敗只計 **ACTIVE 已確認終止且累計零成交**，而且只歸屬送出嘗試時已存在的 FIFO 批次。UNKNOWN、取消請求和部分成交不標成零成交。部位修復完成，該部位的歷史壓力自然離開訊號；沒有固定冷卻或永久禁重試。部分成交逐批保存實際收到的時間，不用原始送單時間冒充成交年齡。

原主動修復演算法、交易合法性、pending 所有權、取消確認與帳務不變。這一版不直接調整主動修復份額／價格，不新增鎖盈模式，也不碰 R65/R84 的權重、Adam 或步數。

## 第一批的規模與讀法

總搜尋預算預先宣告 **128 組**，但第一批固定最多 **12 組**。

先重現一條 R85 observer-only control，十組關鍵欄位必須完全一致。每候選接著跑兩條完整 native 路徑：`early/L250/P0` 與 `late/L750/P500`。每四組輸出一份批次快照。

短名單最多三組。凍結短名單後，再補跑其餘十種執行情境。因此初批最多 **1 + 12×2 + 3×10 = 55 條新增 native 回放**；短名單不足三組或命中相同配置快取時，實際數量會較少。任何 native 拒絕、帳務不一致或控制組差異，都會停止並保存現場，不會繼續把分數當成有效結果。

**這些全部屬於已消耗市場 1977248，不是 12 個市場，也不是未見市場驗證。** 200bps 仍是條件費率；實際費率與來源時鐘認證仍未知。

三個搜尋目標同時保留：pending 最壞填單投影下的平均 floor、平均已配對盈餘、平均較佳結算分支。報告另外列出成交活動、支付、未完成 owner、兩個結算分支，以及每條路徑的改善／惡化，不只看平均收益。

晉級至後續研究仍沿用 R87 接續門檻；即使門檻通過，也**不自動部署、不蒸餾、不訓練新網路**。目前沒有加入全新市場測試，也未實作專門的入選參數鄰域穩健性驗收；不得把單點高分宣稱為穩定區間。

## 檔案配置

- `tools/btc5m_batch_search_v1.py`：準備、第二台派送、接續、狀態與收件入口。
- `tools/btc5m_batch_search_lib/engine.py`：純搜尋、Pareto、原子保存與單寫入鎖。
- `attempt_memory.py`：逐收據 FIFO 年齡與終止失敗記憶。
- `controller_overlay.py`：只對新被動曝險加軟成本。
- `worker_template.py`：native 控制組、候選搜尋、凍結短名單與完整回放。
- `prepare.py`：從精確 R87 pin 組裝隔離工作包；不覆寫舊版本。
- `tests/test_btc5m_batch_search_v1.py`：本地元件測試。
- `ENGINEERING_VERIFICATION.json`：本次實際測試與未執行項目。

## 放入主機與執行

把 ZIP 解壓縮到專案根目錄下**新的** `BTC5M_batch_search_v1` 資料夾。不要覆寫既有同名檔案。以下命令均從原專案根目錄執行；只有下載包自身的 self-test 使用它自己的測試目錄。

```powershell
# 只做輕量元件測試，不連第二台，不做 native。
python -X utf8 .\BTC5M_batch_search_v1\tools\btc5m_batch_search_v1.py self-test

# 驗證 R87 雜湊、組裝隔離包、核對第二台與負載，然後只提交一次初批。
python -X utf8 .\BTC5M_batch_search_v1\tools\btc5m_batch_search_v1.py run --repo . --through 12

# 讀取同一個已知 job，不重新提交。
python -X utf8 .\BTC5M_batch_search_v1\tools\btc5m_batch_search_v1.py status --repo .

# 終止後收件並逐一核對 artifact 雜湊。
python -X utf8 .\BTC5M_batch_search_v1\tools\btc5m_batch_search_v1.py collect --repo .
```

第一次 SSH 沒回應時，依專案原有喚醒／連線程序查明狀況，再重新做 preflight；程式不會略過 strict host-key 或身分檢查。**提交逾時則只查同一 job，不重送**；提交前已落盤的 `*_SUBMIT.json` 會阻擋重複提交。

包裝準備會要求 R87 manifest SHA-256 為：

```
97f4c9032f32284e990253a2e3738f6590579fe3f84ff6978e05baf5e5cc7f40
```

若來源不同，程序會停止，而不是猜測相似檔案或修改舊版本。需先核對該差異，再決定是否建立新 adapter。

## 怎麼擴成 128 組，而不重跑前十二組

先檢查初批的 `REPORT_ZH.md`、`ROBUSTNESS.json`、失敗案例和活動變化，確認搜尋方向值得延續。再明確建立**另一個 job**：

```powershell
python -X utf8 .\BTC5M_batch_search_v1\tools\btc5m_batch_search_v1.py continue --repo . --from-job btc5m-batch-search-20260921-r88 --job-id btc5m-batch-search-20260921-r88-128 --through 128
```

續跑只接受已成功完成、來源 package 相同且所有產物雜湊通過的上一批。它將上一批 STUDY 與回放快取複製到新結果目錄，舊結果保持不變，已完成候選不再計算。新候選仍只根據原先兩個 screen 情境的分數提出，不把其他十條結果偷偷餵回搜尋。

中途崩潰的 native 回放沒有宣稱能從撮合器記憶體逐指令續接。那類 UNKNOWN／FAILED 必須先保存及分析，再明確授權修復；本工具不會自動把它重跑成一個看似乾淨的結果。

## 結果去哪裡看

新工作包預設為：

```
data/research/btc5m_batch_search_20260921_r88/
```

初批收件預設為：

```
data/research/lan_worker_returns/btc5m-batch-search-20260921-r88/
```

完成後優先讀 `REPORT_ZH.md`、`RESULT.json`、`FROZEN_SHORTLIST.json`、`ROBUSTNESS.json`。

`STUDY.json` 保存每組參數、提出方式、參考了哪些已完成候選及全部成績；`BATCH_004/008/012.json` 保存每四組快照；`cache/` 保存逐筆回放及校驗資料；`PROGRESS.json` 分開記錄目前情境、已完成候選與本 job 實際新增 native 數。

**不要因為資料夾叫 R88 就把它當成已完成主線。** 在真正跑完、收件與驗證前，專案 CURRENT 應維持 R87。
