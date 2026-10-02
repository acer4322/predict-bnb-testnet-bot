# BTC 5M Lab：多對話研究／工程協作啟動包 V2

日期：2026-09-11。狀態：**分工建議稿，未啟用任務、未派送worker、未改共享入口或runtime。**

## V2 新增：推理強度與第一輪配置

本版依後續對話建議加入角色推理分配，**不是更新專案研究進度，也沒有替既有對話切換設定、派出任務或修改 runtime**。原啟動包保留；原來源快照與研究規則不變。

| 角色 | 日常預設 | 第一輪建議 | 關鍵節點升級 |
|---|---|---|---|
| 00 總控／整合 | 高 | 極高：先對齊版本與依賴 | 跨支線矛盾、整合與升級裁決用極高；重大方向裁決才用 GPT-6 Pro |
| A 目標機理／經濟教學 | 極高 | GPT-6 Pro：集中審查完整教學目標 | 目標／reward 定義、競爭機制與刷分反例用 GPT-6 Pro |
| B 原生執行／主被動協作 | 高 | 極高定接口與狀態機，再用高實作 | 核心帳務、部分送出、晚到成交與共享責任用極高 |
| C 完整學生／狀態記憶 | 極高 | 極高：先診斷表達能力 | 涉及是否增加記憶或更換架構，再用 GPT-6 Pro 專項審查 |
| D 資料／獨立驗收 | 高 | 極高：先定資料與驗收契約 | 資料洩漏、尺度取巧、畢業考及重要否決用極高 |

完整說明見 [推理強度配置.md](推理強度配置.md)，可讀設定見 [REASONING_POLICY.json](REASONING_POLICY.json)。五份角色提示詞開頭都已內嵌各自設定，WORKBOARD 和 HANDOFF 增加建議／實際模型與推理檔位欄位，未知值不填成已確認。

極高／Pro 審查以「判斷、證據、最強反例、判別實驗、工程規格」為交付，完成後接實作，不反覆重做總分析。模型／模式與推理強度分欄，不把 GPT-6 Pro 當通用的「極高上一級」。這些都是本專案的配置建議，非官方效能排名或不同產品等價表。

新對話使用新版對應 PROMPT；既有对話只補貼新增的「推理設定」段落，不重跑原任務。已有工作板以新增欄位合併更新，勿用空白模板覆蓋正在執行的工作。變更清單見 [CHANGELOG_V2.md](CHANGELOG_V2.md)。

## 建議組織：一個總控＋四個專責對話

只指定一個對話為總控；現有研究串可改派角色，不必全部另開。總控統一版本、接口、資料使用與整輪驗收，專責對話依問題深挖；不是各自製作一套策略。

|對話|負責|第一個交付|不負責|
|---|---|---|---|
|00 總控／整合|版本與任務依賴、算力排程、唯一整合候選|主線與bridge相容性清單、工作板、整合版manifest|重新做每条專科研究|
|A 目標機理／整輪經濟教學|哪些Target證據能提供可學習的經濟目的與完整方案評分|教師可識別性表、尺度／交易量負對照、少量完整方案的評分見證|原生引擎、偷偷更改風控閘門|
|B 原生執行／主被動協作|同一目的的多通道、部分成交、拒絕、未知送出、終態|bridge對齊V3的接合清單，再分階段原生驗收與Active接線|發明Target私有目標、由介面fixture制定全域風險政策|
|C 完整學生／狀態記憶|policy的表達能力、stateful continuation、整輪共同訓練|目前12參數與輸入的能力缺口表、最小歷史依賴見證|私自改評分、另造撮合、訓四個互不相連的子策略|
|D 資料／獨立驗收|資料時鐘與標籤、跨時期、獨立評分、promotion資料保護|可用cohort及標籤清單、獨立整輪驗收草案|替研究者調模型或用考試結果挑參數|

## 為什麼現在需要串聯：本輪已核對的實際落差

學生主線的OpenFunding Recovery V3已記錄三個阻塞修复、11次原生執行（只有3個已消耗市場）、凍結訓練與audit完成，正式評估resource censor=0／terminal pending=0；但native Active仍缺，沒有策略promotion。

另一條AuthorizedOutcome bridge／新runner線已完成元件與拒絕／部分送出處理，却仍將V2的zero-fill mismatch、20pending、51resource censor列為未解。這是**版本引用落差**，不是可以直接宣稱「新bridge已被V3驗過」。需要比較import鏈、profile和hash，將既有修复移植/重用到該runner，才對新組合給驗收結果。橋接的原生工作曾因封裝缺依賴失敗，後續工具安全阻擋仍應遵守；不能換串、改名或換通道繞過。

Target機制支線另有144市場機制辨識結果與198市場跨尺度資料。它們比重做三場資料收集更值得先查；但舊／新BTC時鐘、共同支持、原單量未知與無成交不等HOLD限制，不能被新訓練忽略。

精確來源路徑、大小、sha256在SOURCE_SNAPSHOT.json。這些是本輪讀取的檔案快照，不是所有支線現場狀態的永久保證；開始任務前需重讀CURRENT與指定證據。

## 共用上下文與正式協作分開

同一ChatGPT Project可集中對話、檔案與規則；正式任務則透過所有對話已獲授權的5m bot讀寫同一份專案檔案。不能只靠聊天記憶、聊天分享鏈結或一個過期附件判定程式狀態。

第一版不建新平台。只需三類檔案（以下路徑是**建議路徑，尚未建立**）：

```text
data/research/multi_chat_coordination_v1/
  CURRENT.md                    # 只有總控寫；有效範圍、profile、基準、當前目標
  WORKBOARD.json                # 只有總控寫；task/dependency/job/collection/review
  lanes/<lane>/<task_id>/
    HANDOFF.json                # 該lane寫；小型交付、證據及整合要求
```

大型回放、訓練結果沿用既有lan_worker_returns與研究artifact；這裡只放路徑/hash，不複製DB或大檔。共用研究登錄沿用BTC5M_LAB_MONTHLY_TESTED_RESEARCH_REGISTRY_20260810_20260910_V1.md等既有來源，不重建另一個百科全書。

CURRENT保持短、直接描述目前狀態。歷史保留在各版本檔，不在CURRENT累積大量互相覆蓋的附註。新發現先回報給總控，專責對話不各自宣稱更新了全域正解。

## 兩個不同的版本規則

1. **使用者規則**：最新明確指示優先；cap=null、移除研究180/Pair/TTL等硬策略閘門，不因舊報告或專科fixture回復。
2. **工程證據**：必須匹配runtime、native、schema、profile、cohort與policy指紋。後寫的摘要不自動推翻較早但精確的原生結果；不同scope的證據保留分開，衝突標NEEDS_RECONCILIATION。

CURRENT與manifest至少含：baseline policy SHA、runtime SHA、native SHA、state/action/receipt schema版本、active capabilities、funding mode、資料快照及cohort角色。這不是新模型，只是防止拿不同系統比較。

## 工作流

總控發出一個有界任務 → lane讀CURRENT與依賴回報 → 確認讀寫範圍和task ID → 深挖並交付HANDOFF → 有實驗需要時提交計算申請 → 總控/唯一runner依授權排程 → lane處理結果 → D獨立覆核 → 總控整合一份candidate manifest。

每個HANDOFF至少回答：這次改變哪個可檢驗主張？用了哪個版本與資料？有哪些完整市場/有效行為支持？結果可接進哪個接口？仍未證明什麼？

`PROPOSED`、`IMPLEMENTED`、`COMPONENT_PASS`、`NATIVE_PASS`、`BEHAVIOR_SUPPORTED`、`ECONOMIC_SUPPORTED`、`FRESH_REPLICATED`分開。Job狀態（未提交／running／succeeded／failed／cancelled／blocked）與collect狀態、研究結論分欄。`succeeded`只代表程序成功，不代表策略成功。

作者不能以自己的unit PASS取代D的整輪評分；總控不得把不同scope的通過數加在一起當native場次。元件是模組化，訓練與主驗收仍是同一學生的完整閉環。

## 算力與程式權限

初期建議同時只有1個重型worker job，每job max_threads=4。這是協作調度建議，不是新策略限制；避免四條lane各跑4threads造成16threads。小型研究先1–3 smoke、再Stage-A<=16；開發與promotion不同。所有重型資料處理、HFT與訓練交第二台；主機做有界準備、狀態查詢與精簡結果。

只有總控/runner owner派送、取消與收回工作；其他lane提job request。阻塞先看status/heartbeat/tail，不重新submit。只有確定啟动且有job ID才說在跑；本包未啟動輪詢或背景服務。

核心帳本、world、native receipt/runner的mainline只有整合者寫；B用隔離module/patch，C用隔離policy或branch，A/D不修改生產控制器。Codex工程對話可各自用Git worktree；大型data/、模型cache與worker returns不要各複製一份，使用只讀資料引用或小型immutable bundle。AGENTS.md保持短且指向CURRENT。

共用5m bot的權限不會因提示詞自動變成檔案級隔離。第一版的single-writer是工作約定；真正鎖或存取隔離要由工具端實作。atomic write只避免半寫檔，不能自行防止兩個作者覆蓋。選一個總控並限定寫入者，比現在先寫大型排程器更合適。

## 四個當前難題的任務邊界

### A：什麼才算學會經濟管理，而不是學會刷分？

重新對照最新凍結整輪結果與Target機制辨識，不從零建Target理論。原始虧損減少可能只是縮量；每份比值也可能被額外低價、無價值交易稀釋。不能只優化一個可被交易量操控的分數。

第一個交付：原始兩端盈虧、按交易尺度品質、有效活動、責任成本與資金需求的分離報告；scale-only與no-action控制；哪些教師資訊可觀察、哪些只有推測、哪些可由合法完整方案反事實回放形成。不要把效果名Repair/ADD當私有意圖，不把Target下一筆當OUR的專家答案。

若提出reward/授權目標，先用已消耗資料的完整路徑或代數反例展示不會被純花錢、純縮量、硬補平、零交易和增加無價值配對刷分。這是學習目標建議，不是世界新增金額/Floor硬門檻。初步畢業的20%/90%/80%與兩批30場仍是先前建議，未正式採納，不能偷偷變成規則。

### B：整套能力是否真的能執行？

先對齊open_funding_recovery_runtime_v3與已有AuthorizedOutcome bridge／execution runner，不重做已解的舊阻塞。逐項比較source/import/profile/clock與測試範圍。新bridge要保留其reject→next-real-frame與unknown-send fail-stop，不能用主線成功替代新組合驗收。

再補native Active端的submit/ack/fill/partial/cancel-or-terminal、費用及滑價回饋；接到同一目標與責任帳本。當Maker與Taker先後或並行成交時，不能雙重支付同一責任，未完成Repair不能當已收款去支持ADD。實際流動性與排隊限制保留。

外部指定UP/DOWN允許值只能是具名實驗授權，不得當整個學習學生的預設risk gate。Active可用不等於強制使用，更不是另建一個Taker策略。

### C：現在的學生能否表達它應該學的行為？

現有12參數controller是有用的可執行基準，但其預先寫好的公式可能限制了系統學習。先列出policy真正讀取哪些狀態、哪些責任/歷史未使用，哪些動作是先被架構定死；不要先換更大網路。

第一個交付：用同一當前報價／相近持倉但不同自身責任、近期成交／未成交歷史的成對情境，測試是否需要不同完整方案。這些是必要性/接口見證，不冒充真Target私有答案。若現有輸入把重要情境壓成同一狀態，才增加最小持續記憶或目標狀態；若已可表達，先修學習方法或教學信號。

A提供教學/評分定義、B提供能力與返回語義後，C才共同訓練整個policy；不分別訓四個actor再硬拼。主結果是完整回放與反方向損失，不是旁路AUC。

### D：改善是否真實、可遷移而且沒有污染考試？

盤點已被其他lane看過的cohort與模型選擇次數；三個9/7市場都是development，不重叫fresh。近期198市場資料可以先讀現有小表/manifest，卻不能未查時計就和舊BTC混合。事件時點coverage與全時點coverage分母不同，必須分開。

報告原始委託量/成交量/累計有效前進、無收據/零成交/unknown與terminal的定義；不同資產、時期、份額与深度分開。初步新市場門檻只可在採用前固定，不能發現結果不佳後下修。D不調A的loss權重，也不幫C挑參數；真holdout只交凍結candidate評分，結果一旦回饋開發便轉為已消耗。

在共同權限的repo中，「D獨占holdout」只有流程效力；若需要硬隔離，另行設計工具級權限，不能聲稱提示詞已實現。

## 第一個整合里程碑

先完成一次版本對齊與四條lane的最小交付，不要求每條各跑100場。A回傳可學目標與防刷分例；B回傳可執行/待驗能力與兼容版本；C回傳學生表達能力缺口；D回傳資料支援與凍結評估方案。總控組成**一個**可辨識變更的系統候選，跑相同小cohort的整輪驗收。

Active工程尚未通過時，C可繼續有價值的被動stateful訓練，但標PASSIVE_SCOPE，不能把它宣布full system畢業。反過來A/C不應因等待B就原地重複資料稽核，先完成各自可獨立交付的工作。

## 暫不另開的對話

不再新增泛用Maker Side、獨立Taker配方、固定延遲調參、巨牆/PIN alpha、零成交統計或期末收尾重做串。舊Target規律文檔是重要證據索引，但其中固定18份、180/60硬階段、先shadow沒有action權等舊建議不能覆蓋使用者最新要求。跨市場普通資料可做分析；8/16 SEALED仍保持封存，除非新的明確授權。

## 啟動方法

将對應PROMPT_00/A/B/C/D文字貼進選定的五個對話；第一條總控先建工作板。專責對話開始時先讀共享CURRENT（若尚未建立，按提示中的當前來源只做有界接手，不擅自發起大型實驗）。用戶只需在總控說「收取A/B/C/D最新HANDOFF並更新工作板」，不需貼整段歷史。

本包不是一個已安裝的自動多代理系統。普通對話不會因為命名就互相發消息；發言中的「下一步」也不代表已有worker job。若未來要自動排程，應另做有權限、持久化job registry與真正並發鎖的執行層，不以常駐shell或繞過工具限制代替。

## 參考

專案精確證據見SOURCE_SNAPSHOT.json。外部僅查官方Projects、Git worktrees、AGENTS.md文檔，用於介面使用方法；上述角色與流程是針對本專案提出的設計建議，並非官方保證。
