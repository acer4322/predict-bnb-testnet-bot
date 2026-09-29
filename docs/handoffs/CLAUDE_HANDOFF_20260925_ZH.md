# Claude 專案交接｜2026-09-25

這是給沒有本對話歷史的 Claude 的接手文件。此次只建立交接，未啟動 Claude、未向外部服務傳送資料、未啟動新研究或 live 操作。以下依本次實際讀到的檔案整理；worker idle 是完成收件時的紀錄，未重新探測即不能視為現在狀態。

## 1. 專案位置與最短閱讀路線

Windows／PowerShell，專案根目錄：

```text
C:\Users\acer4\Documents\Codex\2026-07-16\chatgpt-conversation-6a580831-27e8-83ee-9994\outputs\predict-bnb-testnet-bot
```

本次讀到分支 `feature/dashboard-v2-polyhermes`，只作定位，不是要求切換分支。工作樹包含長期研究及其他任務，不能 reset、clean 或覆寫別人的修改。

按順序讀，無需全倉掃描：

1. [根 AGENTS.md](../../AGENTS.md)、[研究規則](../agents/research.md)。
2. [RESEARCH_CURRENT.md](../agents/RESEARCH_CURRENT.md) 第一個具名包；若已比本文件更新，以其可驗證產物接續。
3. 最新 [到期接收流修正 CURRENT](../../data/research/btc5m_prelive_execution_gate_20260925_v1/CURRENT.md)、[報告](../../data/research/btc5m_prelive_execution_gate_20260925_v1/REPORT_ZH.md)、[READINESS](../../data/research/btc5m_prelive_execution_gate_20260925_v1/READINESS.json)、[VERIFICATION](../../data/research/btc5m_prelive_execution_gate_20260925_v1/VERIFICATION.json)。
4. 父批 [100市場交接](../../data/research/btc5m_prelive_generalization_20260925_v1/HANDOFF_ZH.md)、[完整報告](../../data/research/btc5m_prelive_generalization_20260925_v1/REPORT_ZH.md)。需要具體數字時才讀 SUMMARY 或單條 trace。

根 README 介紹 Binance Prediction／Predict.fun 相關應用，倉庫內也有 live 執行器；其安裝、discover、run 指令不是本次接手需要執行的命令。`src/`、`tests/` 是應用與測試，`dashboard/` 和 `dashboard-v2/` 是不同前端，`data/research/` 才是當前研究主線。

## 2. 使用者真正要學的系統

使用者描述的 Target 規律：往一個方向逐步 ADD、承擔風險，同時在另一側建立 repair 倉位；ADD 和 repair 都各有主動／被動成交。repair 看的是兩個結算分支的金額收益，不是簡單把 UP/DOWN 份數補平。對私人動機、掛單及方向信念來源仍有不可觀察部分，不把這段理解當已證明的私有演算法。

目前目標是從空倉開局擴張、並行修復、再 ADD 的完整循環：允許犧牲部分正向收益來減虧，但不能把正收益全部壓掉。暂不要求3:1；也沒有使用者新設的50%保留率或成功率門檻。只留下接近零的正收益，不能稱穩健修復。

開局積極承擔風險不能消失。使用者曾以 V12 首分鐘約 +100/−100 作參考；這是歷史使用者描述，不是已與当前引擎同條件驗證的基準。不能因為配置寫210份，就宣稱每場建立了210份倉位。

V12 曾在**指定最後勝方**的微縮世界中形成結構，屬 oracle 診斷；真實自主策略不知道最後勝方。現在的凍結 V2 修復／協調模型不是「已接上V12」，也不是本輪對V12續訓。V12來源／配方未對齊前保持這個區分。

不要重新混淆：反側買入不等於方向信念翻轉；Target receipt 時間不一定是下決策時間；公開成交空窗不能證明私人掛單未成交。先前用「主動修復多的場是否被動修復更少」作公開代理檢驗，未支持簡單反向關係，因此沒有把該假說加為策略規則。

## 3. 已完成結果：版本不可混合

### A. 修復尺寸小批：已完成，未晉級

[金額尺寸報告](../../data/research/btc5m_repair_amount_screen_20260925_v1/REPORT_ZH.md)：9根×5臂45結果，18新native＋27重用。NEED 按當前修復需求、剩餘量與F可付金額縮量；STRUCTURE 再限制損害既有正收益大於虧損結構。兩者未穩定優於原策略，沒有新fit或全市場接入。

局部修復公式有效不代表完整後續ADD循環已學會；不要只換scalar權重或增加同套epochs重複試驗。

### B. 100市場泛化：已完成固定批次，整體NO_GO

唯一job：`btc5m-prelive-generalization-20260925-v1`，已回收，禁止重送。

- 預定100不同BTC5M×2模型種子（20260920/21）×事先固定UP/DOWN＝400路徑。
- 實際99市場396回放；368條確認到期，7市場28條未確認到期；另1市場2576180的receive時間倒置1094ms，拒收不替補。
- 每日2026-09-21～24 UTC各25場，以公開品質metadata和時間順序抽樣，沒有依Target、winner或績效選樣。
- 全倉庫以前是否消費過這些市場仍UNKNOWN，不能稱完全未見holdout；本批如今已消費。
- 策略為凍結V2＋實際持倉交接＋條件式首批210主動轉換，沒有新fit。每配置僅8場套用210提案，提案也不等於全量成交。

| 配置 | 帳面正收益>虧損，含雙正 | 到期且訂單責任全清後確認 | 雙正 | 未結路徑 |
|---|---:|---:|---:|---:|
| 20／UP | 27/100 | 21/100 | 4 | 10 |
| 20／DOWN | 24/100 | 20/100 | 6 | 9 |
| 21／UP | 45/100 | 38/100 | 3 | 5 |
| 21／DOWN | 50/100 | 46/100 | 3 | 3 |

同一場兩個固定方向都確認達標：20為4/100，21為19/100。不能取每場較佳方向或最後勝方拼接績效。

公式：`max(UP, DOWN)>0 且 UP+DOWN>0`。UP/DOWN是同一持倉在互斥結算方向下的含費條件損益，不是同時實現的盈虧或市場勝率。帳面列含最後可觀測點；確認列排除未到期／未結責任的分子，仍保留100分母。缺失不等於實際虧損。

主核對396 trace、599 artifact hash、6716 receipt、12007 owner列次PASS。獨立覆核只重算行級計數、抽查兩條trace及拒收時間，不是第二次全帳本核對。證據分別為 [MAIN_VALIDATION](../../data/research/btc5m_prelive_generalization_20260925_v1/MAIN_VALIDATION.json) 和 [INDEPENDENT_RESULT_REVIEW](../../data/research/btc5m_prelive_generalization_20260925_v1/INDEPENDENT_RESULT_REVIEW.json)。

### C. 最新到期接收流修正：元件可保留，沒有改善收益

唯一job：`btc5m-prelive-execution-gate-20260925-v1`，已成功回收，禁止重送。

- 22項不同元件測試在本機、worker均通過，不稱44個不同測試。
- 3個已消費市場×雙seed×固定UP/DOWN＝12條；8新native、4輸入相同重用。
- 新增隔離 `expiry_feed.py`，只保留真實到期後LOCAL depth接收事件，不新增EXCH撮合、trade、heartbeat或合成terminal。
- 2577422、2577655共8條恢復到期確認並結清；2539607原始尾端缺失的4條仍未確認。
- 12/12既有觀測前綴、完整receipt、events、payoff相同；不是策略盈利改善。
- 狀態 `KEEP_BOUNDED_EXPIRY_COMPONENT`，整体仍 `NO_GO_FOR_LIVE`。
- 第三個cutoff市場2579312未測；不能將這12條與舊100場混版本重算總通過率。舊100場的368/396保持原口徑。

更早「任何EOF都排除」的確認0分是過度保守口徑；父批已分開到期後資料耗盡與未到期。不要恢復舊誤讀，也不要把所有EOF都改成通過。

## 4. 下一主線與已知阻擋

直接問題是**修回後再次ADD破壞收益／風險結構**。父批2498712、21/DOWN，約4分鐘F/L已達+8.96/−5.55，後續擴張至終局+11.72/−211.00，成本20.55→236.46且owner0。這場未套用210，不應把失敗全歸因於210大開局。[案例證據](../../data/research/btc5m_prelive_generalization_20260925_v1/WORST_L_CASE.json)。它是觀察反例，尚未做同根因果消融。

下一個有用的研究是：在修復後真實OUR狀態，以同根完整續行比較原ADD、其他合法ADD尺度／時機、主動／被動repair和等待／掛單維護，追蹤後續再ADD、F保留、L變化、支出與pending責任。先查重既有教材與消融、凍結具體假說和矩陣，再執行，不在這次接手時直接開跑。

開局210低覆蓋與尚未驗證的到期邊界另外記錄，不能為提高套用率直接放寬depth或擅改撮合。部件修正可保留也不等於策略晉級。

**資金口徑特別注意：** 研究契約明確沿用`capital_cap=null`。舊handoff提到「有資金約束的教材」是後續建議，不授權自行加100或任意上限。部署資金需求必須量測；實盤需要具名投入／損失額度與部署契約，不能繼承研究無上限。既有[research.md](../agents/research.md)已明確說明。

其他上線缺項：自主方向系統、場館實際費用／尺寸／延遲、API與帳戶訂單對帳、斷線恢復。200bps、被動15份、1092/273ms及秒級match中點都是研究條件；15份是研究策略限制，不是已證實的場館最小單量。小額測試也不能因此自動啟用。

## 5. 接手時必守的工作邊界

- 使用者指示和適用AGENTS優先；保留dirty worktree及封存原件，修正另外開successor包。不要修改舊報告使其看起來通過。
- Target、最後winner、未來動作只可用於標明的離線教學／評估；OUR runtime只能用當時已可見公開資訊及已確認自身狀態。
- `UNKNOWN`、`CANCEL_PENDING`、同plan CANCEL都不釋放owner、預留資金、自成交責任；須canonical terminal。
- 本機只做輕量編排、分析和適當元件測試；重訓練／HFT/native僅在第二台。派送前讀[worker.md](../agents/worker.md)與相關[LAN手冊](../../BTC5M_LAN_WORKER_V1.md)。
- 已記錄SSH alias `btc5m-worker`、hostname `DESKTOP-JIERAGF`、根目錄 `C:\BTC5M-worker`、Python `C:\BTC5M-worker\.venv\Scripts\python.exe`。必須重新驗證身份、strict host key與資源，不假定IP或空閒；一個重job、max_threads4。
- 流程：exact/global status及已有returns→probe→stage/hash/load-only→一次submit→status/tail→terminal collect/核對。timeout不等於未提交，禁止重送或重建watcher。
- 不改live enabled/armed、策略、stakes、服務、排程或下研究實单；live維護另讀[runtime.md](../agents/runtime.md)。不把金鑰、.env、私人原始tape上傳外部服務。
- Codex工具／模型名稱不是Claude天然具備的能力。若專案要求GPT-6 Luna限定唯讀覆核但當前不可用，明示缺項；不要把主代理自核當獨立審查。
- 中文回報完成／部分／UNKNOWN，保留反例、雙側signed payoff、主被動成交、支出、owner與保留資金。用詞上區分測試PASS、策略收益、模型晉級及live資格。

## 6. 檔案與操作入口

| 內容 | 根目錄相對路徑／用途 |
|---|---|
| 最新候選與配對核對 | `data/research/btc5m_prelive_execution_gate_20260925_v1/` 的 `expiry_feed.py`、`worker.py`、`PROTOCOL.json`、`MANIFEST.json`、`verify_result.py` |
| 父批400格規格與模型hash | `data/research/btc5m_prelive_generalization_20260925_v1/PROTOCOL.json`、`MANIFEST.json` |
| 已回收native證據 | `data/research/lan_worker_returns/<job-id>/`；按精確ID讀ROWS、RESULT、INPUT_AUDIT及所需trace |
| 開局尺寸與凍結V2載入鏈 | `data/research/btc5m_opening_size_screen_20260924_v1/size_bridge.py`、`worker.py`；依載入鏈追權重，勿搜尋到任意V12檔就稱接入 |
| 舊原生事件建構／loop | `data/research/btc5m_batch_search_20260921_r88/native_reference.py`、`data/research/btc5m_pending_hft_market3_20260922_r93/hft_loop.py`；保留凍結來源 |
| 公開品質與tape | `data/wallet_maker_book_inference.db`、`data/execution_tape_v1/markets/<id>.json.xz`；唯讀、精確欄位及市場，勿全表／全tape灌入上下文 |
| 修改驗證規則 | [development.md](../agents/development.md)；文件更新只核路徑與保留狀態，不跑完整交易測試套件 |

不要重跑父批 `analyze.py` 覆蓋SUMMARY；它有防覆寫assert。來源要以MANIFEST/hash及實際receipt為準，不以檔名、exit0或宣告旗標代替證據。

## 7. 可直接貼給Claude的接手訊息

```text
請接手我的本機BTC5M專案。工作目錄：
C:\Users\acer4\Documents\Codex\2026-07-16\chatgpt-conversation-6a580831-27e8-83ee-9994\outputs\predict-bnb-testnet-bot

先讀根目錄CLAUDE.md、AGENTS.md、docs/handoffs/CLAUDE_HANDOFF_20260925_ZH.md，
再讀docs/agents/RESEARCH_CURRENT.md第一項指向的CURRENT、REPORT、READINESS。
不要重建、重送已完成job，不改live，不把當前凍結V2說成V12續訓。
先用繁體中文回覆你理解的研究目標、最新完成結果、仍未證明的事項，以及唯一下一研究主線。
此次先完成接手，不啟動新訓練／native／部署。後續依我指示繼續。
若你無法讀取本機專案，明確告訴我缺哪些檔案，不要假裝已讀或已連線worker。
```

這份文件配合本機repo使用；若是無檔案存取能力的Claude網頁對話，需由使用者提供本文件及所列報告。只傳聊天文字不能讓對方自動取得專案、模型、tape或worker。
