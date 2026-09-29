## 2026-09-30 總交接：Codex V58 交接之後到現在（接手先讀這份）

[CLAUDE_RESEARCH_HANDOFF_20260928_20260930_ZH.md](../handoffs/CLAUDE_RESEARCH_HANDOFF_20260928_20260930_ZH.md)：整段期間的時間軸、系統定義、關鍵結果、否決清單、結構性結論、資料位置和待決事項；細節見 CLAUDE_CG1AT_HANDOFF_20260929_ZH.md 第1～13節。現況：5m CG1AT 為減虧版本（期望值約0）；15m FAV15 為高風險（用戶不採用）；目標的低風險報酬依賴我們無法複製的成交時機。另完成15m掛單生命週期與快速重掛測試（重掛−0.014，目標+0.004）。0實盤／服務／collector變更。

## 2026-09-30 跨幣種與BTC 15分鐘：FAV15為目前唯一扣費後為正的候選；掛單路線不可行

交接第13節（[CLAUDE_CG1AT_HANDOFF_20260929_ZH.md](../handoffs/CLAUDE_CG1AT_HANDOFF_20260929_ZH.md)）；[跨幣種CURRENT](../../data/research/btc5m_crossasset_target_20260930/CURRENT.md)；[15m掛單回放CURRENT](../../data/research/btc15m_maker_replay_20260930/CURRENT.md)。BTC15M資料在 data/wallet_maker_book_inference_btc15m.db（滾動約288場，約一週）。15m熱門方0.6～0.8低估約+7～12個百分點（兩半皆成立，同期5m僅+2～3）。FAV15（每10秒、價格0.60～0.80時主動吃單買15股，持有到結算）：每場+22.7±8.4，報酬率7%，成本325，不受延遲影響；虧≥150約13%，每日平均+39→+4.5遞減。目標式主動吃單修補能消除大虧但吃掉收益。掛單回放：追價−2分、掛單梯−3.5分，不可行（目標掛單約中性）。下一步：估算FAV15的資金風險，下週用新資料重驗。0實盤／服務／collector變更。

## 2026-09-30 反轉後處理PFC／PFC-T（否決）：反轉後政策前沿是平的

[PFC-T 100場](../../data/research/btc5m_pfct_used100_20260930/CURRENT.md)；[PFC-T 10場](../../data/research/btc5m_pfct_small10_20260930/CURRENT.md)；[PFC 10場](../../data/research/btc5m_pfc_small10_20260930/CURRENT.md)。唯讀：反轉前選定方建倉在實際組合下+84.6/場（假反轉也賺），反轉後新方價格到0.70：假33%、真93%。PFC（只擋新單）無效：V8缺口累積，一到門檻就補回。PFC-T（改V8目標＋低於門檻撤單）機制有效，fresh-100a 100場200/200 PASS：假反轉−71→+11／+44，真反轉−93→−154／−175，整體−6.1→−6.5／−7.5，大虧4→8／11場，否決。所有反轉後規則都只在真假反轉間搬錢。沒反轉比例決定損益（各批42～67%，打平約49%）。CG1AT仍為目前版本。0實盤／服務／collector變更。

## 2026-09-30 目標雙邊買入建模、市場定價偏差、UR50（否決）

[目標建模 CURRENT](../../data/research/btc5m_target_policy_20260930/CURRENT.md)；[UR50 CURRENT](../../data/research/btc5m_underrepair_used100_20260930/CURRENT.md)。唯讀104場：目標每窗兩邊股數約各半（熱門方0.41～0.59），資金跟隨價格主要因熱門方單價高；目標+20.4/場＝成交優勢+52.6＋部位−32.3，CG1AT −1.5＝−5.7＋4.2。照抄分配不會賺錢。CG1AT掛單5秒後−13.9±2.1/場，各子類皆負。9月下旬熱門方0.5～0.7被低估約8個百分點（104＋獨立224場皆成立；8月未重驗）。冷門方修補是真反轉保險。UR50（DECIDE後V8冷門方缺口×0.5）10場+2.4→+6.5（雜訊）；100場用過場（fresh-100a）100/100 PASS：−6.1→−7.3，差−1.3±2.2；無反轉+12.6、真反轉−18.8，否決。CG1AT仍為目前版本。0實盤／服務／collector變更。

## 2026-09-30 V8分配與資金跟隨價格：資金比例閘門否決；待用戶決定是否重新設計建倉控制器

交接第12節。V8管理比例只看自身淨持倉（w3=−0.8自我平衡、無價格輸入），方向性來自V12層。目標資金比例與價格相關係數240秒+0.80（真反轉亦跟隨），CG1AT +0.45、真反轉−0.55。資金比例閘門：v1刪單→NONCONTIGUOUS_BIRTH；r1 α0.4 平均+2.4→−1.4；α0.8/1.2 qualified_audit失敗（單號重用）；v2改否決點30/30 PASS：α0.4/0.8/1.2 平均−13.1/−6.9/−33.0（CG1AT+2.4）。閘門只能擋不會補，仍為真假反轉取捨。0實盤／服務／collector變更。

## 2026-09-30 勝敗結構與反轉處理：各規則皆為真假反轉取捨；下一步研究V8建倉分配

交接第11節。CG1AT 217場：勝率52%／賺賠比0.89（打平需53%）；獲利66%來自23%的高明確度無反轉場，65%虧損來自有反轉場；反轉時持倉價值約−95（虧損在反轉前形成）。否決：反轉後凍結（10場+2.4→−4.0）、只修復（估+1～2）、確認區加碼（取代V8自有買單）、提早反轉（40場18.3→16.7／10.7，製造新假反轉）；後段金額修復需數千USDT不可行。目標在反轉時持倉價值約0（兩邊買、隨價格調整），反轉後在真反轉也虧（−143）。下一步：唯讀研究V8管理desired/share分配能否隨價格調整。0實盤／服務／collector變更。

## 2026-09-30 CG1AT 100場新場：長期期望值約0（未達預登記>0）

[CURRENT](../../data/research/btc5m_cg1at_fresh100a_20260930/CURRENT.md)；交接第10節。fresh-100a（2671717–2685217）200/200 PASS：CG1AT平均−6.1（95%[−22,+10]），V58−23.8；CG1AT−V58 +17.8（95%[−4,+39.5]），58好41差，≤−100 28→15，峰值974→584。新場合計217場CG1AT約−0.9/場（V58約−23.2）。CG1AT穩定減虧但本身非盈利系統；300～400版暫緩；長期方向待用戶決定（重新設計建倉）。0實盤／服務／collector變更。

## 2026-09-29 深夜 追加：CG1凍結漏洞極小，補上無益；CG1AT仍為最佳

[CURRENT](../../data/research/btc5m_cg1gate_insample40_20260929/CURRENT.md)；交接第9.5節。凍結閘門（CG1凍結期間丟棄全部NEW）40場：CG1AT 18.3→17.4，僅5場各漏1單（主動機會修補3、一般有限工作2），高明確度場完全相同。CG1效果未被低估。下一步待用戶決定方向：重新設計建倉，或以CG1AT做更大規模新場統計。0實盤／服務／collector變更。

## 2026-09-29 夜 追加：開局震盪訊號與CG4（否決）；下一步補CG1凍結漏洞

交接追加見 [CLAUDE_CG1AT_HANDOFF_20260929_ZH.md 第9節](../handoffs/CLAUDE_CG1AT_HANDOFF_20260929_ZH.md)。唯讀：開頭60秒UP中間價穿越0.5次數≥2在8批311場V58穩定較差（新批−47.5 vs −2.8），20～30秒即可見；但在CG1放行的高明確度場內分批不一致（40/40d/40e反向），非穩健規則依據。CG4（穿越≥K則60秒起凍結至下一次FLIP）在40f+40g 40場：CG1AT 18.3、K2 16.6、K3 16.0，否決；虧損在前60秒已形成。發現凍結漏洞：ACTIVE_GENERAL_FINITE_WORK_SERVICE、ACTIVE_OPPORTUNITY_REPAIR、PASSIVE_REPAIR_CAPACITY_FRONTIER、V12G_PAYOFF_ADD 不經CG1否決點（CG1同樣受影響）。唯一下一步：補漏洞後40場小測，再新場驗證。0實盤／服務／collector變更。

## 2026-09-29 CG1AT：五批新場皆勝V58，但絕對損益隨批次，尚未穩定正收益

完整交接：[CLAUDE_CG1AT_HANDOFF_20260929_ZH.md](../handoffs/CLAUDE_CG1AT_HANDOFF_20260929_ZH.md)（系統定義、五批新場結果、否決方向、重要發現、基礎設施、唯一下一步）；產生器／模組保存於 `data/research/btc5m_cg1at_handoff_20260929/`。不構成新派送。

CG1＝V58＋選邊明確度閘門（m0<0.56 第一次FLIP前不下單）；CG1AT＝CG1＋V8管理被動價改最佳買價（含自我成交保護）。新場每場平均 V58→CG1系列：40d −76→−46、40e −30→−16、40f −6→+10.7、40g 0→+21.0、40h −60→−20.3；相對改善+14～+40穩定，絕對值隨批次（40f–h合計約+3.4）。fresh-40h穩定性門檻FAIL；縮半−5.7、三分之一−4.5不合格，300～400版暫緩（三分之一峰值≤400占88～95%）。否決：CG2保險／不追價、CG3弱勢階梯、縮半＋400上限。重要發現：V8維護迴圈會撤外加被動單（V67被動結論失效）；目標時間戳早約1秒但成交品質差距真實。下一步：小規模唯讀比較V58大虧批次（40d/40h）與賺錢批次（40f/40g）的市場狀態差異。0實盤／服務／collector變更，已完成job未重送。

## 2026-09-28 V58完成：原規模STOP290基準成立，但經濟略退步

累計交接：從Claude原V34交接至V58的[完整續接檔](../handoffs/CODEX_TO_CLAUDE_V12G_V35_V58_HANDOFF_20260928_ZH.md)，含版本表、已完成job與下一步；不構成新派送。

[報告](../../data/research/v12g_original_scale_stop290_20260928_v58/REPORT_ZH.md)、[CURRENT](../../data/research/v12g_original_scale_stop290_20260928_v58/CURRENT.md)、[比較](../../data/research/v12g_original_scale_stop290_20260928_v58/COMPARISON.json)、[核驗](../../data/research/v12g_original_scale_stop290_20260928_v58/VERIFIED.json)。原V50 cap=null/被動15/PADD15/需求不縮，唯一策略變更STOP290；300版保留暫停調參。OFF1＋ON29完成，0重跑/fit/live/collector改動；290前prefix29/29相同，NEW>=290為0、exchange>=300為0、owner0。

平均-30.04→-30.54，3改善1退步25相同；未FLIP9平均+117.40→+124.80仍9/9正，有FLIP20 -96.39→-100.44；最差-400.73／最差2和5不變，P>L48.28%→41.38%。2634488移除五筆既有＋一筆晚新增UP0.10修復，少90份及9支出，UP-5.24→-86.24；六筆原成交都在291.5秒附近，非到期後假成交。正常組三場勝方多賺也伴隨反向損益惡化，不當風險改善。用戶290規則保留，未擅自開例外。

四場13筆實際CANCEL、45次cancel-pending owner觀察預留核對，終局全terminal；兩張截止前舊單在290後仍成交（4筆fill receipt）皆計帳，未在290後NEW。首截止幀2～510ms延遲。原V50有2633749一筆到期後成交，原值保留效力限制；新版無。25/30保留legacy active_matches_opportunity失敗，不稱所有門檻PASS。

單job一次submit、4路單執行緒10.58分鐘，45凍結檔/484跨機hash一致，父版保留。worker空閒無下一job，不重送。已消費BTC零費率29場，2629444維持UNKNOWN，非新泛化或上線驗收。V58為290規則下原規模研究對照，非經濟晉級；下一步見[有限部分修復設計](../../data/research/v12g_original_scale_stop290_20260928_v58/NEXT_MECHANISM_DESIGN_ZH.md)，先隔離舊全域啟用，再研究主被動修復及修復後再承擔，未實施新修復公式。

## 2026-09-28 V57完成：290秒停新單及全撤機制加入研究候選

[報告](../../data/research/v12g_stop290_cancel_all_20260928_v57/REPORT_ZH.md)、[CURRENT](../../data/research/v12g_stop290_cancel_all_20260928_v57/CURRENT.md)、[比較](../../data/research/v12g_stop290_cancel_all_20260928_v57/COMPARISON.json)、[核驗](../../data/research/v12g_stop290_cancel_all_20260928_v57/VERIFIED.json)。沿用V56 POST20、cap300/被動10/PADD15；290000ms起所有ACTIVE/PASSIVE、ADD/repair禁止NEW，首個策略幀全撤可撤owner，未確認與CANCEL_PENDING不釋放責任。事件驅動首幀延遲2～213ms，沒有新增獨立計時器。

11唯一native＝INERT1＋同十場STOP290；290前prefix10/10一致，290後NEW0、實際exchange到期後成交0、owner0。平均勝方-6.24→-5.42，4改善0退步6相同；平均成本277.02→276.21，最差-18.92不變，盈利1→2場，P>L仍10%、雙負9→8。**十場截止時及之後均無未結掛單，實際CANCEL0；非空全撤僅元件測試通過，native覆蓋尚缺。** 9/11仍legacy active_matches_opportunity失敗，非所有門檻PASS。

單job一次submit、4路單執行緒2.85分鐘；44凍結檔、180跨機檔核對，父版證據保留。0重跑/fit/live/collector變更；worker已空閒，無下一job，不重送。保留STOP290研究候選，仍為已消費BTC零費率十場，不作泛化或實單認可；下一步補非空撤單情境與300版PADD／修復協調。

## 2026-09-28 V56完成：POST較少虧，尚未恢復盈利；新發現到期後模擬成交

[REPORT](../../data/research/v12g_small300_demand_scale_audit_resume_20260928_v56r1/REPORT_ZH.md)、[CURRENT](../../data/research/v12g_small300_demand_scale_audit_resume_20260928_v56r1/CURRENT.md)、[COMPARISON](../../data/research/v12g_small300_demand_scale_audit_resume_20260928_v56r1/COMPARISON.json)、[VERIFIED](../../data/research/v12g_small300_demand_scale_audit_resume_20260928_v56r1/VERIFIED.json)、[到期成交審核](../../data/research/v12g_small300_demand_scale_audit_resume_20260928_v56r1/EXPIRY_FILL_AUDIT.json)。38唯一native完成：原6＋r1未跑32，3既有BASE重用；兩job各只送一次、0重跑/fit/live/collector變更。576跨機hash、40+44凍結輸入與V53/V55證據核對，worker已空閒，沒有下一job。四路單執行緒，執行與worker審核合計7.29分鐘（不含準備/審核修正/傳輸），最低可用18.266GB、CPU峰32.7%。

- 固定原V50高收益5＋低收益5十場，被動10、cap300；raw需求係數0.2。BASE/OPEN/POST/BOTH平均勝方−9.30/−9.71/−6.24/−9.36，最差−22.25/−26.03/−18.92/−32.45；P>L20/10/10/20%、盈利1/2/1/2場。POST7改善3退步，虧損均值少32.97%，仍負且9場雙負，不晉級。
- POST保留原開局，只縮選邊後目標；原高收益5平均−8.20→−2.97、低收益5−10.41→−9.50；BOTH高收益−1.07但低收益−17.65，開局也縮不更好。首60較高分支均值BASE−0.42→POST+10.02，尚非原+100量級。POST仍有3925個finite goal補高raw需求幀，既有責任/pending不縮；但主動PADD49→65有成交訂單、支出229.19→450.89，需協調獨立主動加倉。
- **新增執行限制**：POST兩場299.981/299.999秒主動修復，exchange300.231/300.249秒；BASE/OPEN也各1場，原V50有1場被動expiry後成交。舊frame no_postexpiry_acquisition僅查closure不送NEW，不查exchange cutoff。50路皆另審，原始PnL保留，僅帳務扣除敏感度BASE−9.09/POST−5.98，非native反事實；帳務terminal不代表到期成交有效，不作實單證據。
- 原4BASE失敗源於新增scale audit忽略growth固定物理錨點；r1只改逐步核對raw→growth→hold→demand，六條重核與六負例PASS，actor/native/clock/sizing完全不改。原錯誤保留，33/38仍legacy active_matches_opportunity失敗。40小額路徑cap/receipt/owner與10大額receipt終局重建，owner0，不能宣稱所有策略／執行效力門檻PASS。
- 下一步先修／驗證到期執行邊界，再以POST研究主動PADD接力與3退步場修復。十場全已消費結果選樣BTC/零費用，非新泛化；不自動追加native，不重送原job。下方執行中條目均為歷史過程記錄。

## 2026-09-28 V56r1續接中：只修審核，保留6条native、接未跑32条

V56原job6条native完整，4条BASE因新增scale audit假設growth方向隨roles更新而誤判；93檔跨機核對，原失敗保留。舊AdditionGrowth錨定後維持物理strong，反轉後可能恰是current弱側。r1改為核對raw→growth→hold→demand各步，六条重核／六個壞樣本檢查PASS，actor/scale/clock/native/參數完全未改。見[契約](../../data/research/v12g_small300_demand_scale_audit_resume_20260928_v56r1/CONTRACT.md)、[RECHECKED6](../../data/research/v12g_small300_demand_scale_audit_resume_20260928_v56r1/RECHECKED6.json)、[續接矩陣](../../data/research/v12g_small300_demand_scale_audit_resume_20260928_v56r1/PROTOCOL.json)。

r1 job：btc5m-v12g-small300-demand-scale-audit-resume-20260928-v56r1；單次派送，最多4路，僅32未執行；不重跑原6，不fit/live/collector變更。整輪38仍待完成，不宣告經濟成功。V56下方為過程記錄，原job已STOPPED，不重送。

## 2026-09-28 V56執行中：300版持倉需求分階段縮放

[契約](../../data/research/v12g_small300_demand_scale_20260928_v56/CONTRACT.md)、[矩陣](../../data/research/v12g_small300_demand_scale_20260928_v56/PROTOCOL.json)。沿用V54原高收益5＋低收益5十場，原cap300被動10重用3／補7，OPEN／POST／BOTH各10，加INERT1共38新native上限。固定raw demand係數0.2，OPEN/BOTH選邊gross60，POST/OFF300；票10、PADD15、財務修復、既有有限goal與pending責任不縮。已過純元件測試與第二機load-only；主機/host-key/來源hash核對，空閒約19.97GB；已單次submit，INERT完整parity與BOTH smoke均PASS，進行四路矩陣；新finite repair啟動時點可隨raw deficit改變，財務容量與既有goal/pending保留。既有V53/V55不改不重跑，未fit/live/collector變更。

具體job：btc5m-v12g-small300-demand-scale-20260928-v56；單job最多4路各單執行緒。先完整INERT再BOTH smoke，失敗停止保留，不自動重送。這十場是已消費結果選樣診斷，非泛化或上線認可。

## 2026-09-28 V55完成：10份無300上限，六場平均成本1436.81USDT

[REPORT](../../data/research/v12g_passive10_uncapped_20260928_v55/REPORT_ZH.md)、[CURRENT](../../data/research/v12g_passive10_uncapped_20260928_v55/CURRENT.md)、[COMPARISON](../../data/research/v12g_passive10_uncapped_20260928_v55/COMPARISON.json)、[VERIFIED](../../data/research/v12g_passive10_uncapped_20260928_v55/VERIFIED.json)。INERT1＋NOCAP6共7native完成，單job一次送出、0重跑/fit/live/collector變更；101檔跨機hash、36凍結輸入與V53證據不改。4路單執行緒4.13分鐘，最低可用18.451GB，worker已空閒，無下一job。

- 同六場被動10份僅移除300cap：平均成本1436.81、中位1297.72、最低583.85、最高2230.50 USDT；cap300平均293.53、約4.90倍。原無cap15份1463.52，10份僅少1.83%，持倉量級未隨單票縮至2/3。
- 無cap10平均勝方−57.94、最差−383.17、4/6盈利、P>L3/6＝50%、雙正1／雙負1／正零0。原好場2629892 +97.86、2633749 +185.20恢復，但2630303−383.17、2630045−342.10尾虧也恢復；不能以取消cap當風險改善。
- runtime cap=null/BASE/裁量rows0核對6/6、實際PASSIVE10、active提案15/gross300保留；INERT完整parity，18參照與新路徑receipt重建成本／持倉，owner0，新6條299秒後ACTIVE NEW0；legacy active_matches_opportunity保留5/7。
- 花費＋pending名目峰值2232.57；保守未配對修復預留公式峰值4147.15是另項觀察，不是實際支出或保證實單本金。六個已消費BTC／零費率，非自然樣本平均、未見或上線前泛化。保留10研究候選，V54十場小額架構設計仍未派送，不重跑V55。

## 2026-09-28 V54唯讀完成：保留10份，開局與選邊後需求仍是大額量級

[REPORT](../../data/research/v12g_small300_opening_diagnostic_20260928_v54/REPORT_ZH.md)、[CURRENT](../../data/research/v12g_small300_opening_diagnostic_20260928_v54/CURRENT.md)、[VERIFIED](../../data/research/v12g_small300_opening_diagnostic_20260928_v54/VERIFIED.json)、[下一輪十場](../../data/research/v12g_small300_opening_diagnostic_20260928_v54/NEXT_TEN_COHORT.json)。使用者確認保留被動10份研究候選，未晉級／未改live。重用六場×V50大額／300-15／300-10共18路，88來源hash、終局receipt/owner核對；0新native/fit/worker/live/collector變更。V53全部凍結與VERIFIED證據不改。

- 開局初始desired每側448.22、gross300或30秒選邊、PADD提案15／間隔2秒／ask≤.80沿用；選邊後desired仍600～900份。六場10份首次資金裁量全在選邊前5.382～7.804秒，不等同確認支出已用完300。
- 原高收益2629892 +107.24→小10 +5.24；2633749 +206.27→+5.45；原最差2630045 −341.19→−12.19、2630303 −400.73→−6.24皆覆蓋。高收益兩場三版PADD成交支出全0，所以不能只縮主動PADD；需分開開局與選邊後被動擴張。
- 2631069原+27.49→小10−41.55：選邊19.745→30.103秒；小額選DOWN時UP約198.87/DOWN0、cost91.67、資金上界290.53；60秒已雙負。屬機制診斷，未證明縮目標必然解決。
- 下一輪固定V50最高5＋最低5十場（包含高收益但FLIP2場），3場已有10份可重用、缺7場；全已消費診斷，非泛化。開局量級與gross、選邊後新增風險需求須分離再聯合，修復責任與pending不能一起裁掉。候選係數未凍結、沒有下一個job，不重跑V53。

## 2026-09-28 V53完成：被動10份小幅少虧，單縮票量未修好300版

[報告](../../data/research/v12g_passive_ticket_small300_20260928_v53/REPORT_ZH.md)、[CURRENT](../../data/research/v12g_passive_ticket_small300_20260928_v53/CURRENT.md)、[完整比較](../../data/research/v12g_passive_ticket_small300_20260928_v53/COMPARISON.json)、[下一步設計](../../data/research/v12g_passive_ticket_small300_20260928_v53/NEXT_STEP.md)。25新native＝15份INERT1＋10/7.5/5/3各6場，15份六場重用V52；單job只送一次、0重跑/fit/live/collector變更。353檔跨機hash一致，另以独立腳本30路／3313收據／5092NEW／90來源hash全核對；worker空閒，沒有下一個待派job。4路單執行緒7.02分鐘，最低可用18.558GB、CPU峰34.7%。

- 固定6場（大額參照未FLIP3＋有FLIP3），15/10/7.5/5/3份平均−11.60/−10.83/−12.39/−13.24/−12.84；最差−40.30/−41.55/−42.06/−43.05/−40.51。P>L除5份1/6＝16.67%，其餘2/6＝33.33%。10份2改善4退步；正常3均值−8.57→−10.29，FLIP3−14.63→−11.37。10只作研究候選，全仍負、不晉級、不稱全局最佳。
- 15→3份被動NEW421→1707、平均成本289.40→289.43；小單仍追原倉位量級，未恢复盈利開局。被動有成交206→960、主動27→38（按route獨立訂單數）。主動PADD量／方向gross300／價格與財務修復目標未改，僅被動票量及相依可掛性一致縮小；低價修復受研究最低名目1限制。
- 新25實際PASSIVE尺寸、來源、frame/receipt/carrier、pending-aware300上限通過；owner0，16/25保留legacy active_matches_opportunity失敗。INERT全路徑相同。新四尺寸299秒後主動NEW0，舊15份2629892仍有299.544秒一筆。原V5234凍結檔不改。
- 下一步先用既有資料拆解2631069與10份改善場的首分鐘資金／修復循環，再設計開局及持倉需求的一致小額縮放；保留10與15對照，不自動追加native。6個已消費BTC市場／零費率，不是未見或實單前泛化；2629444仍UNKNOWN。

## 2026-09-28 小額版設計修正：先縮每張被動單

使用者指出300版應先縮小每張被動單。已核對V52仍固定被動15份，只加資金上限；資金縮量後不等於15即整筆拒絕，故未測到真正的小票循環。下一步先做一致可配置被動票量（含ADD／repair／接續／餘量）與300上限的對照，再分開核對主動票與絕對門檻是否相容。新票量及venue最小金額尚未凍結，未改actor／live、未派新job；V52原始結果與核驗不改。詳見[修正說明](../../data/research/v12g_postflip_add_and_smallstake_20260928_v52/SMALLSTAKE_SIZING_CORRECTION_20260928_ZH.md)。

## 2026-09-28 V52完成：反轉後停ADD效果有限，300USDT限額版收益保留失敗

[報告](../../data/research/v12g_postflip_add_and_smallstake_20260928_v52/REPORT_ZH.md)、[完整29場結果](../../data/research/v12g_postflip_add_and_smallstake_20260928_v52/RESULTS_ZH.md)、[CURRENT](../../data/research/v12g_postflip_add_and_smallstake_20260928_v52/CURRENT.md)、[核對](../../data/research/v12g_postflip_add_and_smallstake_20260928_v52/VERIFIED.json)、[下一步](../../data/research/v12g_postflip_add_and_smallstake_20260928_v52/NEXT_STEP.md)。INERT1＋HALF29＋REPAIR_ONLY29＋SMALL30029共88唯一native完成；單job只送一次，0重跑／fit／live／collector變更。1,235檔跨機hash一致，worker已空閒，沒有下一個待派job。34個凍結輸入不改，不重建重送。

- 重用V50同29場：勝方平均V50−30.04→HALF−28.83／REPAIR_ONLY−29.05，最差均−400.73，P>L均14/29＝48.28%，勝方正16→15，雙正6、雙負11→12、正零0、owner0。两方案皆9改善4退步16相同；未FLIP9收益100%保留。首分鐘28/29一致，2632293已在首分鐘FLIP；介入前完整一致。
- 原FLIP後新生偏倉支出367.67→115.54／0，修復支出5226.69→5248.67／5299.15；修復直接淨改善2765.49→2730.48／2733.67。反轉前出生、反轉後成交104.70仍在；新增撤舊偏倉owner條件0觸發。2630303仍−400.73，主瓶頸是確認FLIP前的負債，不能把停ADD視為已解決。
- 使用者確認300USDT。SMALL300保留大額門檻另加pending-aware預留，資金核對29/29通過，上界≤300（1e−7容差），最大實際支出299.5541。但全29均值−12.18／最差−46.12／P>L6/29＝20.69%、雙正0雙負19；正常9均值+117.40→−3.45，每100支出損益+10.78→−1.24，首分鐘較高分支均值+144.00→+1.26。不是合格小額版，設定disabled/unarmed/not-deployable。
- 大額兩組299秒後ACTIVE NEW0；SMALL3002629892有299.544秒主動修復DOWN13@.20，交易所299.794、receipt300.044，全部terminal，需保留尾盤延遲驗收。72/88保留legacy active_matches_opportunity失敗；no_extension_full_parity非V52獨立證明。完整來源／帳務／owner／資金／介入前parity重核通過。
- native與worker審核22.47分鐘，最多4單執行緒，最低可用18.625GB／CPU峰39.3%。全部已消費BTC約2.5h／零費用，2629444仍EOF UNKNOWN，非新泛化或實單通過。下一步設計：銜接V51獨立有限部分修復；小額另做開局／單量／工單／絕對門檻整體縮放與真實費率／最低量核驗。三組不晉級，不自動組合或追加native。

## 2026-09-27 V51唯讀診斷完成：有部分修復空間，但修復後風險再累積與舊入口耦合需分開處理

[報告](../../data/research/v12g_affordability_decomposition_20260927_v51/REPORT_ZH.md)、[CURRENT](../../data/research/v12g_affordability_decomposition_20260927_v51/CURRENT.md)、[核對](../../data/research/v12g_affordability_decomposition_20260927_v51/VERIFIED.json)、[下一步設計](../../data/research/v12g_affordability_decomposition_20260927_v51/NEXT_STEP.md)。重用V50已消費29場；0新native／fit／派送／actor／live／collector變更。V50仍是實測參照，未新增盈利或泛化結論，沒有待派新job。

- 完整修復拒絕18,564幀：18,560精確重建、4同毫秒提案phase UNKNOWN。9,842幀通過局部部分容量檢查，分布28/29場；27場首次在60秒內，包含全部9個未FLIP盈利對照。不是獨立成交機會，也不能用作反轉辨識訊號。
- 實際receipt/carrier/終局核帳29/29、區間端點58/58通過，145現金流來源hash一致；全研究208來源hash核對。2630303的W買入淨改善395.92、同期F支出690.23；2630045為110.70對763.55，且F支出656.87／722.00來自拒絕後才出生的委託。修復確有成交，後續承擔仍超過改善；F/W是固定物理分支，不把所有F買入當語義ADD。
- 28場第一個局部候選時ctx.first均未啟動；直接沿用舊入口會提前啟動全域限制、改寫後續被動／ADD。下一步先做有限部分修復與舊啟用狀態分離的元件驗證，釐清有限工單的因果准入／續作，再決定整場候選；不重做V40、不固定成基準啟動時間、不無限逐幀重生預算。
- 額外發現16場繼承早期另一物理方向收益高點，保存peak29/29可重建。改用同物理方向高點的離線敏感度僅解鎖另2場49個重複完整准入幀，兩重點大虧場均0；不混入主修復變更。2629444仍EOF UNKNOWN；已消費BTC／零費用樣本，未通過上線前泛化。

## 2026-09-27 V50全29配對完成：最大尾虧修回，正常收益保留；平均仍負

[報告](../../data/research/v12g_qualified_work_continuation_20260927_v50/REPORT_ZH.md)、[COMPARISON](../../data/research/v12g_qualified_work_continuation_20260927_v50/COMPARISON.json)、[CURRENT](../../data/research/v12g_qualified_work_continuation_20260927_v50/CURRENT.md)、[下一步](../../data/research/v12g_qualified_work_continuation_20260927_v50/NEXT_STEP.md)。1INERT+29ON唯一native完成，重用V43基準；0重跑/fit/live/collector改動。两job397檔跨機hash一致，worker已空閒，沒有新待派任務。主審所有帳務/來源/owner/finite-goal/prefix檢查通過；另行逐筆receipt重建29/29通過、174來源hash，見[VERIFIED](../../data/research/v12g_qualified_work_continuation_20260927_v50/VERIFIED.json)與[RECEIPT_AUDIT](../../data/research/v12g_qualified_work_continuation_20260927_v50/RECEIPT_AUDIT.json)；基準29另有獨立覆核。

- 勝方平均−65.12→−30.04，最差−1036.13→−400.73，最差2平均−718.43→−370.96；P>L13/29=44.83%→14/29=48.28%，勝方正15→16，雙正5→6，雙負11不變，正零0，owner0。3改善3退步23相同。
- 原最差2629327：UP−1036.13/DOWN+371.27→UP+5.52/DOWN+318.03。新增25張續修、1094.895371份、支出79.493076；goal差0.002425但弱側非負，標WITHDRAWN而非假完成。排除本場，其餘28場平均配對−0.867273，改善集中單一災難場。
- 基準未FLIP9場平均+117.39→+117.40、仍9/9正；收益保留100.01%。有FLIP20均值−147.25→−96.39。29/29開局一分鐘持倉/支出/雙側payoff不變。分組是策略FLIP，非獨立價格反轉標籤；保留率相對V43，不是新測原PADD80。
- 全29續修42張/1635.890260份/支出211.644194。ACTIVE有成交訂單332→366、PASSIVE3693→3693；route和MAKER/TAKER分列。299秒後ACTIVE NEW0，最晚285.466秒。25/30保留legacy active_matches_opportunity失敗，不冒稱所有舊門檻通過。
- 原V50於22路徑後因舊work_audit final-plan同側单NEW假設停止；2632273 frame510被動15+後續主動20都有預留且合法。原raw錯誤保留，V50r1只修審核，22重核＋7負例通過，只接未跑8場。actor/clock/native/參數不改，最多4單執行緒，兩批job執行合計9.56分鐘（不含診斷），最低可用18.65GB。
- V50保留候選但未晉級。下一步先唯讀分解2630303/2630045等完整修復不可負擔，含2632221等盈利但高條件風險對照；避免重做V40換邊後部分修復。這29是已消費BTC/約2.5h/零費用；2629444仍來源EOF UNKNOWN、不補場，並非新泛化或實單上線通過。

## 2026-09-27 V49大虧診斷完成：有限修復被深度拆單耗盡額度，下一步測同工單續作

[報告](../../data/research/v12g_preflip_debt_diagnostic_20260927_v49/REPORT_ZH.md)、[CURRENT](../../data/research/v12g_preflip_debt_diagnostic_20260927_v49/CURRENT.md)、[核對](../../data/research/v12g_preflip_debt_diagnostic_20260927_v49/VERIFIED.json)、[下一步](../../data/research/v12g_preflip_debt_diagnostic_20260927_v49/NEXT_STEP.md)。V49是唯讀診斷，非新模型；重用V48已消費新30中的29完整V43＋6差異V44，2629444兩臂仍UNKNOWN。0新native／fit／worker派送／live／collector變更。

- 35/35獨立receipt/carrier/終局/切點核帳通過；主審26,231 preFLIP來源幀、174項區間現金流交叉核對、236來源hash一致。最差三場firstFLIP時最終勝方已−1050.38／−446.11／−700.87，F支出96.45%／100%／98.14%為PASSIVE。最差2629327在210秒後已無F成交，不能只歸因尾盤繼續ADD。
- 2629327在254.954–255.869秒五張qualified深度拆單申請268.53、成交230.333244、支出26.018928、W淨改善204.314316；首筆當時算出需求1430.231。共享五張提交上限後仍有111幀原金額條件與局部後續gate可行。全部29共7場／1108重複幀同樣重現，包括6終局盈利、3未FLIP；不是111／1108個獨立可成交機會，也不是上限移除後的反事實收益。
- 另兩差場沒有cap拒絕，主要是FULL_RESTORATION_NOT_AFFORDABLE，需另做部分修復診斷。盈利場也有W負債超1000／修復空窗F支出644.40，不能直接用大負債或未成交作停ADD／方向預測。
- 下一項僅設計：原qualified已進場工作的有限量跨深度續作，維持原F收益保留、pending、方向與ADD／開局／首筆准入，不直接增加次數常數、不提前ctx.first、不混入V40部分修復。尚未實作／凍結／派送；先核生命周期與不變性，再全29配對，單job最多4單執行緒路徑。V43/V44原平均−65.12／−65.24、P>L44.83%、legacy失敗不變，未晉級。

## 2026-09-27 V44＋新30測試完成：增量改善未成立，轉查換邊前修復與風險累積

[新30報告](../../data/research/v12g_fresh30_parallel_resume_20260927_v48/REPORT_ZH.md)、[CURRENT](../../data/research/v12g_fresh30_parallel_resume_20260927_v48/CURRENT.md)、[COMPARISON](../../data/research/v12g_fresh30_parallel_resume_20260927_v48/COMPARISON.json)、[下一步](../../data/research/v12g_fresh30_parallel_resume_20260927_v48/NEXT_STEP.md)。V43/V44凍結後按時間選新30，排除V12G已用130；62不同native（60測試＋2控制）、0重跑/fit/live/collector更動。29完整配對＋2629444兩臂期後空來源EOF保持UNKNOWN，不換場。

- 原十場V43→V44均值−39.70→−40.06；新29完整配對−65.12→−65.24，3改善3退步23相同，最差−1036.13不變，V44不晉級。未換邊9場+117.39→+116.48且兩版9/9勝方為正；換邊20場−147.25→−147.02。固定V43策略FLIP分組，非獨立價格反轉標籤。V44未換邊收益保留99.23%，不是相對原PADD80新30的實測。
- 勝方為正15/29=51.72%，P>L13/29=44.83%，双正5／雙負11、正零0、owner0；保留未知的全30 P>L範圍43.33%–46.67%。全30嚴格平均UNKNOWN；補充期後持倉快照平均V43−64.98／V44−65.10不當完整PASS。50/60完整路徑保留legacy active_matches_opportunity失敗；299秒後主動NEW0，最晚289.969秒。
- 最差2629327在285.954秒換邊時UP已−1050.38，剩14.046秒，終局−1036.13；前三差皆以receipt prefix重建。下一步查換邊前repair淨改善、ADD支出、pending責任與修復失速，涵蓋29場＋V44差異6場；尚未執行，不再重做小餘量工單與desired維護。這30已消費，單一BTC/2.5h/零費用，不是跨日期跨幣種或上線前通過。
- V45/V46/V47原STOPPED保留：依序修同毫秒審核join、標記真實期後source EOF、修終局drain producer/source計數，actor/clock/native不改。四工作合計756檔跨機hash核對。原33重核31完整＋2截斷；V48接原剩29完成，無重跑。
- 使用者要求提高並行，V48已實測同job最多4個單執行緒路徑，29條540.303秒，最低可用18.571GB、CPU峰34.5%；流程已記於research_batch_tests.md。最後worker無非終局工作、無其他research process，沒有待派新job。

## 2026-09-27 V43完成：接續修復改善尾虧，但新需求與撤單維護仍不一致

[V43 CURRENT](../../data/research/v12g_physical_work_continuation_20260927_v43/CURRENT.md)、[完整報告](../../data/research/v12g_physical_work_continuation_20260927_v43/REPORT_ZH.md)、[核對](../../data/research/v12g_physical_work_continuation_20260927_v43/VERIFIED.json)、[下一步](../../data/research/v12g_physical_work_continuation_20260927_v43/NEXT_STEP.md)。21條native已完整回收；OFF1+BIND10+SERVICE10，重用V41 RATCHET10。0fit/live/收集器改動，沒有新job。

- BIND固定物理工單方向，五筆錯標完成改為方向撤回；十場實際持倉、計畫、receipt與V41相同，修語義不等於新增收益。SERVICE另加独立被動需求接續：十場勝方均值−50.99→−39.70、最差−453.54→−306.06、最差兩場平均−331.85→−258.11；FLIP6−183.68→−158.65，未FLIP4+148.04→+138.73（保留93.71%）。兩改善、兩退步、六相同，P>L仍1/10=10%，胜方正值4/10、雙負6；平均仍負且維護有缺口，未晉級。
- 2629133少虧147.48；原停滯74.268→131.900秒UP成交0→120份，帶來66.60淨修復，但DOWN仍花409.05（原407.70），FLIP前UP負債−863.83→−798.58，後續再多改善82.23。未FLIP2628410收益−36.33、2629019−0.92；2628557仍−149.96。
- SERVICE19張接續票，5張有成交／75份／支出25.05，旧target不擴大、owner0。2629133十張都有surplus撤單標記，九張非stale：新准入承認独立需求，舊desired/surplus仍只認原小餘量。下一步先把独立需求接入維護生命週期，保留正常撤單與pending責任，不能禁撤單遮住問題；之後再處理repair改善追不上ADD支出。未派新輪。
- 單job `btc5m-v12g-physical-work-continuation-20260927-v43`，891.054秒；256檔跨機hash、30784 guards／4808 receipts／6057 NEW核對，19/21仍legacy active_matches_opportunity未過。SERVICE首分鐘九場相同、總份數99.9534%（相對V41）；不得稱原PADD80開局全面恢復。worker無非終局工作。
- 固定十場已消費、結果挑選、零費用BTC5M；非未見／跨幣種／實單前泛化。保留V41、BIND語義參照與SERVICE有限機制候選。

## 2026-09-27 V42診斷完成：找到工單換邊錯標完成與小餘量服務中斷

[V42 CURRENT](../../data/research/v12g_debt_and_stall_diagnostic_20260927_v42/CURRENT.md)、[完整報告](../../data/research/v12g_debt_and_stall_diagnostic_20260927_v42/REPORT_ZH.md)、[下一步](../../data/research/v12g_debt_and_stall_diagnostic_20260927_v42/NEXT_STEP.md)、[核對](../../data/research/v12g_debt_and_stall_diagnostic_20260927_v42/VERIFIED.json)。同固定十場PADD80／V41既有路徑診斷；0新native／worker／fit／live，最後native仍是V41。

- 2629133第一FLIP前UP已−863.83。8.905→131.900秒DOWN支出1128.15、UP購買淨改善124.48；DOWN支出94.14%為被動route。不是只由主動PADD造成，也不宜只靠全面壓開局。
- work12剩UP1.666667，低於凍結被動15 ticket，當時主動金額也不足；74.268→131.900秒UP無NEW／成交，DOWN再花407.70，UP−456.13→−863.83。407.70為觀察到的惡化，不是已證明可避免的虧損。
- 原FiniteGoal沒有固定physical side，換邊時拿另一側持倉核對scalar target。PADD80十筆、V41五筆未達原物理目標卻標完成，15/15用原純類別與實際狀態重現；不是底層owner／現金提前釋放證據。餘量條件也出現在未FLIP的2628410／2629019，後續不能承諾正常收益完全相容。
- 六場V41共5430次拒絕評估，155次單筆假設全成可改善physical worst；保留其他pending後0次維持floor。2629133的169次中改善為0；主審與Luna獨立逐場一致。重複幀、雙腿假設價和<1均不當可實現獲利機會。
- 111來源hash、V41凍結25檔、十場receipt終局／20工作軌跡核對。下一步先固定work物理方向、區分withdraw與complete，再獨立測試有當下新需求的餘量服務銜接；保留精確舊target、canonical pending和V41 floor。設計尚未實作／凍結／派送；完整十場分離兩項改動，既有收益/P>L/legacy失敗不變。

## 2026-09-27 V41完成：反轉後共用風險限制壓下原尾虧，仍留深負債與鎖虧問題

[V41 CURRENT](../../data/research/v12g_postflip_risk_floor_20260927_v41/CURRENT.md)、[完整報告](../../data/research/v12g_postflip_risk_floor_20260927_v41/REPORT_ZH.md)、[核對](../../data/research/v12g_postflip_risk_floor_20260927_v41/VERIFIED.json)。原PADD80底座，V40新部分修復OFF；首次runtime FLIP後，全入口NEW共用含pending的最壞損益下限，只隨已確認改善提高。

- 同完整十場PADD80→V41：官方勝方均值−253.31→−50.99、最差−1114.05→−453.54、最差兩場平均−986.44→−331.85；未FLIP4均值+148.04全路徑相容，FLIP6−520.87→−183.68。5改善、1退步、4相同，P>L仍10%、雙負6場；不以雙負單獨淘汰，但平均仍負，不晉級。
- 原最差兩場降至−96.42／−69.70。2629133仍−453.54，firstFLIP前已有UP−863.83；2628557原+89.24→−149.96。兩側修到接近平衡後易鎖住虧損；不能宣稱已會盈利修復。下一步先查FLIP前負債／修復失速及平衡後低成本修復受阻，同十場配對，不全面壓開局、不直接重跑或擴大批次。
- 一次job `btc5m-v12g-postflip-risk-floor-20260927-v41`，484.768秒，1 OFF＋10 ON完成，重用V40 INERT10。124檔hash、16136 clock guards／2753 receipts、2917 NEW／6700 protected frames核對，owner0。10/11仍legacy active_matches_opportunity未通過；額外審核的draft/canonical混比已依來源修正，候選及結果未改、0重跑。worker無非終局工作，0 fit/live/收集器改動。
- 首60秒份數保留94.51%，8/10相同；第一次FLIP在首分鐘內的場會提早受限。固定十場為已消費最虧5＋盈利5、零費用BTC5M診斷，不是自然反轉占比或實單前泛化證據。原PADD80、V40、BLOCK都保留。

## 2026-09-27 使用者更新目標：保留未反轉盈利，反轉後控制虧損以改善整體平均

[目標更新與下一步](../../data/research/v12g_postflip_money_repair_20260927_v40/OBJECTIVE_UPDATE_ZH.md)、[均值／頻率敏感度](../../data/research/v12g_postflip_money_repair_20260927_v40/MIXTURE_SENSITIVITY.json)。使用者明確允許反轉後犧牲收益，重點是虧損不能太大，靠未反轉場盈利提高整體平均。本次只重整目標與現有數據算術，0新job/native/fit/live。

- 往後不再以反轉場終局為負、雙負或P>L未改善單獨否決；主要看未反轉收益保留、反轉平均／尾部虧損及代表性市場整體均值。這修正V40前輪文字評價，不改凍結合約或結果。V40仍未晉級：未FLIP+148.04保住，但FLIP−327.50、最差−797.11、同十場平均−137.28。
- 用現有條件均值作零費用算術，V40需未FLIP占比約68.87%才打平，原PADD約77.87%。若假設正常場均值148.04且占比40%／50%／60%，反轉平均虧損的打平界線為98.69／148.04／222.06。這些不是新交易門檻或自然市場占比；固定十場是結果挑選過的診斷組。
- 主線為只在runtime已觀察反轉後的損失控制，保持觸發前／未觸發PADD80。先區分繼承負債／pending與觸發後新增惡化，協調PADD、被動與主動修復。V40「分離新修復和舊准入啟用」仍是機制候選，須看尾損而非僅恢復盈利，尚未派送。不得把兩版子組均值拼成已驗證策略，不要求未來方向輸入。

## 2026-09-27 V40完整十場完成：保住未換邊收益，但換邊修復仍失敗，不採用

[V40 CURRENT](../../data/research/v12g_postflip_money_repair_20260927_v40/CURRENT.md)、[完整報告](../../data/research/v12g_postflip_money_repair_20260927_v40/REPORT_ZH.md)、[下一步](../../data/research/v12g_postflip_money_repair_20260927_v40/NEXT_STEP.md)、[V39入口診斷](../../data/research/v12g_repair_entry_audit_20260927_v39/REPORT_ZH.md)。使用者授權繼續研究並測改善PADD80反轉虧損；V39唯讀診斷完成後，以原PADD80凍結「runtime已FLIP後金額部分修復」，保留.5／原共享gate／ADD，不沿用R25底座。

- V40 10 INERT全trace相容＋10 POSTFLIP，完整固定十場。PADD80→V40官方勝方均值−253.31→−137.28、正值5/10→4/10、P>L皆1/10（10%）、雙負2→5；ΣP1711.07→594.40、ΣL5640.34→3998.80。未FLIP4場均值+148.04全保留；FLIP6場−520.87→−327.50但ΣP僅2.24，不能採用。
- 2628557由+89.24→−87.55，2629133−465.52→−797.11；原FLIP6場三改善三退步。13新修復送單／12有成交／1零成交terminal，並非未出手；主動route有成交271→389，PADD增加113張，被動1701→1049。
- 新修復提早設定原ctx.first准入，但PADD另走入口。2628557原first=None、新129.438秒啟用；同切點後UP被動1564→90份、PADD300→855份。下一步先同狀態核查各入口／分支語義，再隔離「新修復」與「提前啟用舊准入」；不宣稱關掉gate一定解決，也不直接重跑PACE/WB/BLOCK。
- 一次job `btc5m-v12g-postflip-money-repair-20260927-v40`，922.088秒完成。204檔跨機hash、29,296 guards／5,552 receipts核對、owner0；18/20仍legacy active_matches_opportunity失敗。worker無非終局工作，0 fit/live/收集器改動，未派下一批。
- V39既有20路徑1321個PADD可重建錯置frame，僅換金額角色0個完整比例可行，1000個不可負擔；原.5 floor下455個實際FLIP後部分算術時點分布六場。不是獨立機會數。V40驗證幾何可行有成交仍不足以形成循環。
- 四/六只是固定十場分層，原最虧5＋盈利5的已消費、零費用BTC5M樣本；非未見／跨幣種／實單前通過。原PADD80收益基線與BLOCK風險參照均保留。尾盤期限未改，本十場沒有299秒後主動單，不等於期限問題已解。

## 2026-09-27 V38唯讀重評完成：修正比較基線，保留PADD80收益參照

[最新CURRENT](../../data/research/v12g_lineage_regime_review_20260927_v38/CURRENT.md)、[完整報告](../../data/research/v12g_lineage_regime_review_20260927_v38/REPORT_ZH.md)、[新下一步](../../data/research/v12g_lineage_regime_review_20260927_v38/NEXT_STEP.md)。使用者提醒早版未反轉收益高、目前可能繼承壓收益的减虧線；本輪只重評既有結果，0新job/native/fit/策略/live修改。最後新實驗仍是V37。

- 確認現在是AR3_R25／AR4_R25→RESERVE_ONLY→BLOCK修復線，未啟用PACE25或WB100；20個候選EXECUTION環境及worker清除V12環境來源已查。
- 固定同十場原PADD80 FLIP分組：未換邊4場官方勝方均值PADD80+148.04、R25+30.35、V34+38.47、BLOCK+38.47；有換邊6場−520.87、−234.47、−234.74、−87.51。收益能力在R25以前／當時已大幅被壓縮，V37相對V34增P不能代表保留早期收益。
- 原獲利5場均值+136.28→BLOCK−1.17，包含2628557原一次FLIP、勝方+89.24→−159.75。完整十場BLOCK勝方正值7/10，但P>L仍1/10，兩者不可混用。PADD80→BLOCK ΣP1711.07→378.25、ΣL5640.34→1717.91。
- 30歷史列與終局inv-cost核對、R25／AR4十場一致、V34／V37基準十列一致；未重算全體歷史receipt或重跑舊native。舊94場PACE分層也支持未換邊收益約+99→+43，惟是末價推定winner，不混同当前官方評分。
- 下一步以PADD80作收益參照、BLOCK作風險參照，先查同十場入口及修復後ADD；不直接沿R25疊加限制，不把事後FLIP當runtime訊號，不重跑已有格。V37入口錯置發現保留，但下一候選底座須重新明確。小組4/6是同十場拆解，非獨立晉級；已消費零費用診斷，無版本上線。

## 歷史：2026-09-27 V37完成：以完整十場判斷，P>L仍僅10%，下一步查修復分支入口

[最新CURRENT](../../data/research/v12g_ten_market_extension_20260927_v37/CURRENT.md)、[完整報告](../../data/research/v12g_ten_market_extension_20260927_v37/REPORT_ZH.md)、[停滯診斷](../../data/research/v12g_ten_market_extension_20260927_v37/STALL_REPORT_ZH.md)、[下一步](../../data/research/v12g_ten_market_extension_20260927_v37/NEXT_STEP.md)。使用者要求10場，重用V36四場雙候選與V34十場基準，只補6場×2共12路徑，未改策略／參數。

- 原策略／BLOCK／CLIP完整十場P>L皆1/10（10%）；原4場25%、新6場0%。ΣP259.7049／378.2541／369.8233，ΣL3345.2759／1717.9138／1728.4046。BLOCK減虧48.65%、P增45.65%，平均成本也降42.25%；CLIP較BLOCK P少8.4308、L多10.4908，不晉級。
- 首60秒總份數保留91.42%／91.58%，8場相同；2629199約48%、2628769約77%，開局仍未完整保住。每候選7個耗盡皆activation前成交，無啟用後漏接。雙正2628999保留，沒有新增P>L場。
- 2629199九個停滯抽樣的同frame修復weak=UP，實際負收益=DOWN，入口錯置；不是frozen first activation資料。剩餘可花正收益7.9717，只支持小額主動報價可行量；遠價被動15份亦有可行掛價，成交／等待價值UNKNOWN。不可把可行當立即主動較佳，或外推十場同因。
- 單次job `btc5m-v12g-ten-market-extension-20260927-v37`，480.376秒完整收回；111新工件跨機hash、20候選29296 clock guards／5187 NEW核對通過，owner0。保留legacy active_matches_opportunity失敗，非安全全PASS。最後無非終局worker工作，0訓練／live／收集器變更，未派下一批。
- 下一步先核對同十場既有BLOCK修復角色與金額分支，再凍結入口修正與主動／被動／等待後續循環；ADD方向與開局保持對照。不重跑本輪，不放寬保留門檻。已消費、零費用、BTC5M；非未見／跨幣種／上線前通過，299秒期限未改。

## 歷史：2026-09-27 V36完成：整批收益保護減少失血，主動縮量未改善持續修復

[最新CURRENT](../../data/research/v12g_whole_plan_repair_20260927_v36/CURRENT.md)、[完整報告](../../data/research/v12g_whole_plan_repair_20260927_v36/REPORT_ZH.md)、[下一步](../../data/research/v12g_whole_plan_repair_20260927_v36/NEXT_STEP.md)。Codex完成4個已消費BTC5M × BLOCK/CLIP_ACTIVE，另1 inert，共9路徑。

- 原策略／BLOCK／CLIP的P>L皆1/4（25%）；原ΣP80.6015/ΣL1258.7988，BLOCK115.2419/549.7539，CLIP106.8074/552.3484。雙正正例2628999保留。少交易也是減虧來源，不晉級。
- 3筆PADD縮量均實際成交，但2629133的BLOCK稍後修復成本更低、終局兩分支更佳；CLIP沒有優於BLOCK，不採用可負擔就立即主動修復。
- activation前及當毫秒前綴全部一致；3場首60秒持倉相同，2629199首分鐘份數只保留47.72%／48.47%，31.750秒後沒有成交。60秒後1178次舊qualified入口均NO_RESTORABLE_NEGATIVE_BRANCH，同時1102次新guard拒絕。下一步只查matched confirmed states下修復機會與ADD方向/正负收益分支身份。
- 一次job `btc5m-v12g-whole-plan-repair-20260927-v36`，354.089秒完成收集；12984 clock guards、2024候選NEW、84檔跨機hash通過，owner0。9條保留legacy active_matches_opportunity失敗，非安全全PASS。無進行中job、0訓練、0live/收集器改動，未派下一批。
- 四場零費用消費樣本，非未見/跨幣種/實單前通過。299秒期限仍未改。

## 歷史：2026-09-27 V35完成：固定狀態核對支持分支／全入口保護，但既有掛單與整批NEW仍須協調

[最新CURRENT](../../data/research/v12g_branch_coverage_shadow_20260927_v35/CURRENT.md)、[完整報告](../../data/research/v12g_branch_coverage_shadow_20260927_v35/REPORT_ZH.md)、[核對](../../data/research/v12g_branch_coverage_shadow_20260927_v35/VERIFIED.json)。Codex完成八個耗盡事件＋2628999雙正正例的既有工件計算，0新worker/native/fit/live修改。

- 同狀態四格拒絕：選定方向×原入口0/8，選定方向×全入口0/8，實際正收益分支×原入口5/8，實際正收益分支×全入口8/8。各物理peak的獨立敏感性同樣8/8；這不是策略勝率或新終局P>L。
- 4/8在新單出生前，既有相反側pending支出已超過正收益；2629199有201元CANCEL_PENDING不能釋放。計入整批原始NEW後6/8有耗盡正收益風險。
- 保留同批其他NEW時八筆均無可行ask替代；2629133整批改成單笔主動修復，可12.37份@.74，條件減虧3.2162，DOWN保留23.4882。被動可負擔12.71份但不合法於凍結exact15條件。
- 雙正正例558張NEW全核對，啟用前／當批54張不變；啟用後504張會改77張被動單，其中5張已成交62.61份／成本60.1056。既有15筆qualified主動修復保留；新閉環終局UNKNOWN，不能宣稱正例已保住或必變差。
- 39來源hash、566出生狀態、494既有准入及同毫秒frame順序核對通過。下一個有限閉環候選是整批NEW共用正收益支出空間，再獨立加主動可行縮量，保留開局及canonical pending；尚未準備worker合約或派送。repair→ADD與299秒期限仍待後續。

## 歷史：2026-09-27 V34完成：收益保護對象與入口覆蓋成為下一個阻塞

版本接續前已核對使用者提供的 [Claude V12g v1～v34 交接索引](../handoffs/V12G_VERSIONS_HANDOFF_20260927_ZH.md)，見 [版本對齊與去重補充](../handoffs/V12G_VERSIONS_RECONCILIATION_20260927_ZH.md)。PACE／ADDCAP／WB、修復觸發／保留比例及排隊替換皆有既有測試；68%勝方為正不等於P>L。v24官方評分為PADD80 17/30、WB100 8/29，缺失ERROR保留。此輪僅核對，無新job；仍先做下述分支身份／入口覆蓋的固定狀態檢查。

[最新CURRENT](../../data/research/v12g_reserve_completion_20260927_v34/CURRENT.md)、[完整報告](../../data/research/v12g_reserve_completion_20260927_v34/REPORT_ZH.md)、[下一步範圍](../../data/research/v12g_reserve_completion_20260927_v34/NEXT_STEP.md)。Codex直接完成原v30未開始的RESERVE_ONLY十場，沒有Claude轉交。

- 四格同十場已完整：P>L OFF0%、FULL10%、MARGIN_ONLY10%、RESERVE_ONLY10%。RESERVE ΣP259.7049、ΣL3345.2759、平均成本1991.5013；較OFF P+34.42%、L+3.11%，七場L惡化，沒有晉級。首60秒總份數為OFF98.60%，開局前綴全數一致。2628999雙正+12.2093/+7.7425保留為正例。
- 8個保護啟用後的正收益耗盡成交：5個被動maker通過同輸入准入、3個V12G_PAYOFF_ADD主動taker旁路；全部買入選定方向，但當時正收益在另一物理分支。reserve_ok的side==strong直接放行不等於保護實際正收益。不是所有耗盡都由新增active repair產生。
- 2629019在116.282秒為UP−30.0064/DOWN+22.9059，後續UP改善797.1108被DOWN購買成本997.1932抵銷，淨再失守200.0824。repair與後續ADD需聯動。
- 主動/被動策略route成交訂單433/1500，送單464/3629；實際taker訂單409、maker1558，34個跨兩類，不可混同。22個原始極微數值殘量訂單另註，journal未修改。
- 單次job `btc5m-v12g-reserve-completion-20260927-v34` 643.337秒完成收集；14,648次guard有效、未結owner0、80檔跨機hash一致、127歷史來源pin核對。10場均保留已知legacy active_matches_opportunity失敗，非安全全PASS；候選native/原生來源未改，無live/收集器/訓練變更，無進行中job。
- 下一個有限範圍：在8個耗盡事件與既有雙正正例作固定狀態反事實，分離「選定side或實際收益分支」與「PADD/各路徑是否覆盖」，納入canonical pending支出與可行份數；不掃新參數、不用epsilon收益當成功，再独立驗證repair後ADD。尚未派送。已消費零費用BTC5M診斷，不是泛化或上線前通過。

## 歷史：2026-09-27 Codex直接處理：EOF修復已驗收，下一步補收益保護對照

[最新CURRENT](../../data/research/v12g_eof_execution_repair_20260927_v33r3/CURRENT.md)、[完整報告](../../data/research/v12g_eof_execution_repair_20260927_v33r3/REPORT_ZH.md)、[後續有限批次](../../data/research/v12g_eof_execution_repair_20260927_v33r3/NEXT_STEP.md)。使用者已改由Codex自行處理，不再要求逐輪轉交Claude。

- 實際v4來源99檔釘選，隔離native只編譯一次；7種EOF、20種既有receipt及正確overflow hard-stop合約通過。v33r3八條整合路徑完成回收，11,762次guard有效，40關鍵檔案跨機hash一致；故障恢復1、正常相容7。原native／V12／live／收集器不變。
- 2629019末次rc1後實際clock達300.535秒，完整1480 source、1482 guard；seq73/74合法入帳，UP_153 TERMINAL。終局UP−3.30331、DOWN−0.176594，未結owner0。
- 299.850秒主動修復雖减虧卻花光DOWN正收益；150ms剩餘時間小於模型250ms下單延遲。venue cutoff仍UNKNOWN，未改策略或加尾盤禁單。
- 重用v30九場＋本輪修復格的MARGIN_ONLY派生10場：P>L=1/10（10%），8場兩側非正，ΣP7.591745、ΣL1416.599650、平均成本859.944482；原v30狀態保留。RESERVE_ONLY10場未跑、四格交互UNKNOWN，下一步只補這十場。
- 五條仍有已知legacy active_matches_opportunity失敗；native執行驗收不等於安全全PASS／策略晉級。已消費、零費用、BTC5M診斷，非未見／跨幣種／上線前通過。
- 本次共4個具名單次提交、1次編譯。v33 fixture錯誤、r1舊overflow測試錯誤、r2漏接Python補丁均有留痕；r2完整修復標籤被否決。r3接線實證通過；共16條市場執行，無進行中job、無下一批派送。

## 歷史：2026-09-27 TASK_006已審查；TASK_007準備獨立執行修復

[TASK_007（人工轉交，未派送）](../collaboration/codex_claude/TASK_007.md)、[REVIEW_006](../collaboration/codex_claude/REVIEW_006.md)、[RETURN_006](../collaboration/codex_claude/RETURN_006.md)、[主審原始證據核查](../collaboration/codex_claude/review_006_evidence/CLOCK_CHECK.json)。最新執行為v32單場診斷，接受REPRODUCED_WITH_CAPTURE；不是策略完成或經濟晉級。
- strict實際觀測1481次且全部有效。末次source index1479，target300.535秒、clock300.255秒，elapse280ms回rc1，clock未前進、strict回False，呼叫端仍process。
- journal的seq73/74在300.350秒可收，領先舊clock95ms；UP_153仍SUBMITTED、filled/payment0。v30/v31/v32 failure_trace直接hash完全相同；tools/src/tapes三scratch一致。
- RETURN文字更正：弱邊margin只在p<4.15/5.15時增加，非恆正；與template三檔差異僅限tools，該市場tape在template pin也缺席。原件保留。
- 舊20260911 EOF probe已見相同clock／state落差，不能重複當新研究。本地舊原生goto有EOF提早返回線索，但hash不同於v4；TASK_007須先核對worker實際v4來源，不能直接套本地舊樹。
- 下一輪允許隔離native事件時鐘及advance／source loop／end2／drain修復；不得用查詢上界／receipt最大時間填clock，不丟末幀、不放寬因果斷言。契約測試後最多8條固定路徑：故障格1、正常MARGIN1、FULL2、OFF2、controls2；原資料與策略不改。
- 修復研究仍為主線，保留開局擴張。299.850秒單缺arrival檢查、會耗盡正收益；期限／收益協調在執行修復驗收後另凍結，不用尾盤禁單遮掉engine錯誤。真實venue cutoff保持UNKNOWN。
- v30仍是15完成／1ERROR／10未啟動；RESERVE_ONLY與四格交互UNKNOWN。Codex未派v33，未改live／收集器，未自動接入Claude。

## 歷史：2026-09-27 TASK_006準備時（现已完成，見上方）

[TASK_006（人工轉交，尚未派送）](../collaboration/codex_claude/TASK_006.md)、[REVIEW_005](../collaboration/codex_claude/REVIEW_005.md)、[RETURN_005](../collaboration/codex_claude/RETURN_005.md)、[尾盤單核查](../collaboration/codex_claude/review_005_evidence/LATE_ORDER_CHECK.json)。最新執行為v31單場診斷；v30四格矩陣仍不完整。
- 修復仍為主線：保留開局擴張，改善反向虧損時保住有意義的正收益，協調主被動repair與後續ADD；不追求補平份數或恢復3:1。使用者另指出299秒多仍有主動單不合理。
- v31前綴與v30 byte相同，捕捉UP_153的seq73/74；299.850秒送出、300.100秒模擬成交、300.350秒回報，但native clock為300.255秒。OUR未入帳，carrier仍SUBMITTED；診斷為REPRODUCED_CAPTURE_PARTIAL，不是策略完成。
- 有效推進函式被install換成strict_advance_to；v31掛shift_audit導致advance.n=0，實際elapse rc仍UNKNOWN。OTHER_ERROR是舊檔名／行號判定失準，保留原件並事後更正。未接受直接EOF→drain的修法。
- 該晚單的固定15份被動候選為0.05（僅0.75），因舊最低金額條件轉向主動。只檢查送出未到期，剩150ms小於250ms下單延遲；按本研究窗口無法窗內到達，真實venue截止對應仍UNKNOWN。
- 決策前DOWN正收益1.60473、UP−11.52199，候選限價支出3.10會使兩側皆負。這是MARGIN_ONLY移除reserve的對照，不能說所有版本都會下同單；違規receipt增量僅離線算術，不是canonical終局。
- TASK_006新v32包只允許最多重現同場一條，驗證install後的實際binding、捕捉strict rc及feed邊界，另做只讀尾盤入口／時間／收益審查。不改策略或drain，不補RESERVE_ONLY。Codex未派job；live／收集器未變。
- v30部分證據仍保留：六相容性檢查通過，MARGIN完成9場、1場ERROR，RESERVE十場未啟動；同9場ΣP155.38→7.59。缺格交互計算及完整10場DEGENERATE標籤均UNKNOWN，見[REVIEW_004](../collaboration/codex_claude/REVIEW_004.md)。

## 歷史：2026-09-27 TASK_003 審查（已由上方 TASK_004 接續）

[Codex REVIEW_003](../collaboration/codex_claude/REVIEW_003.md)、[Claude RETURN_003](../collaboration/codex_claude/RETURN_003.md)、[當時提出的 TASK_004（現已部分停止）](../collaboration/codex_claude/TASK_004.md)。本節記錄審查TASK_003當時的v29結果。
- v29 14 路徑完整回收；兩 controls 與一條實驗為 FULL_PASS，其餘11條僅已知 legacy active_matches_opportunity 失敗。帳務、責任守恆、overfill、未結 owner 檢查可用，不是安全全PASS。
- 同10場 OFF→FULL：平均成本1998.32→692.10（平均值之比34.63%；逐場成本比均值36.83%），ΣP193.20→111.89，ΣL3244.43→2327.81。P>L 0/10→1/10，但唯一達成場成本36.67；判為DEGENERATE，不晉級。
- 准入拒絕16364/16390次函式評估：margin失敗7410、reserve失敗8954。PADD及qualified active repair保留既有旁路，不能宣稱所有ADD／repair已被統一控制。
- 原SUMMARY的GATE_EVIDENCE=false是分析混入OFF檢查行的假陰性；以逐arm環境及admissions修正，保留原件與事後更正標記。
- 當時TASK_004規劃MARGIN_ONLY／RESERVE_ONLY各10場，加6條相容性檢查，重用v28 OFF／v29 FULL；不改K、RETAIN、peak、last_H，不擴樣本或訓練。现已人工轉交執行並部分停止，結果見最上方。

## 歷史：2026-09-27 TASK_002 審查（已由上方 TASK_003 接續）

[Codex REVIEW_002](../collaboration/codex_claude/REVIEW_002.md)、[Claude RETURN_002](../collaboration/codex_claude/RETURN_002.md)、[當時提出的 TASK_003（現已完成）](../collaboration/codex_claude/TASK_003.md)。本節記錄審查TASK_002當時的v28結果。
- v28 14路徑帳務／責任檢查通過；其中12條仍rc=2、safety_pass=false，唯一失敗是舊active_matches_opportunity來源登記／數量斷言。不能稱安全全PASS；原始結果不改。
- 同10場：PADD80正收益大於虧損1/10，AR3_R25／AR4_R25均0/10；三組官方勝方為正均5/10。兩分支正收益合計1711.07→193.20（保留11.29%），屬已消費、按結果挑選的零費用診斷。
- 跨分支last_H確實存在，但保留原None規則、按物理側保存的既有軌跡核查，951個觸發入口差異為0；尚不支持先改last_H／peak能改善。
- 當時下一輪固定既有語義，只比較原生准入開／關；PADD仍不經同一入口，必須分開報告。TASK_003當時尚未派送，現已完成，結果見最上方；V12/live／收集器未變。

## 2026-09-27 V12g 主動修復 v4（被甩開條件限弱邊 ≤0.70，10 場）：與 AR3_R25 逐場相同；更正 2629199 的診斷——「修了又加」為候選機制

[active repair v4](../../data/research/v12g_active_repair4_small_20260927_v28/CURRENT.md)。
- 0.70 限制從未生效。
- 2629199 的主動修復買的是便宜的 UP（0.17～0.37，也就是最後的勝方），但換邊後 V49 持續加倉 DOWN，兩者互相抵銷。
- 下一步候選：在選邊之後重新打開防止再借准入規則。V12/live 未變。

## 2026-09-27 V12g 主動修復 v3（被動處理不了才出手，10 場）：AR3_R25 PROMISING（平均 −253→−129），AR3_R50 STOP

[active repair v3](../../data/research/v12g_active_repair3_small_20260927_v27/CURRENT.md)。
- 出手條件：(a) 被動單被甩開，(b) 弱邊分支 ≤−150 且虧損持續擴大，(c) 弱邊 ≤0.15。
- 最虧 5 場：R50 −376、R25 −288（PADD80 −643）。
- 獲利 5 場：R50 +75、R25 +31（PADD80 +136）。
- 更正指標：本批原報告曾將2629199解讀為「修復在0.8追弱邊」；後續v28修復逐筆與TASK_002已否定此說法，見上方審查。凍結原報告保留。
- 下一步候選：用全部 30 場驗證 AR3_R25；弱邊高價時不追。V12/live 未變。

## 2026-09-27 V12g 主動修復 v2（選邊後才啟用、關閉准入規則，10 場）：STOP；大虧縮小，但 RETAIN＝0 會把勝方利潤花光

[active repair v2](../../data/research/v12g_active_repair2_small_20260927_v26/CURRENT.md)。
- 最虧 5 場平均 −643→−228（2628553：−1,114→−168）。
- 獲利 5 場平均 +136→−5。
- 勝方為正 5→3。
- 每場主動修復約 31 單。
- 下一步候選：保留部分修復，但 RETAIN 設為 0.5 或 0.25。V12/live 未變。

## 2026-09-27 V12g 放寬主動修復 AR_OPEN／AR_DP（最新 30 場）：規則判定 IMPROVES_BASE，但無經濟意義——系統在開局就停擺

[active repair 包](../../data/research/v12g_active_repair_20260927_v25/CURRENT.md)。
- 主動修復在第 2～5 秒就出手，之後 V12 原本的防止再借准入規則擋下 99.8% 的下單，成本只剩約 180，兩支分支都約為 0。
- 下一步：放寬後的主動修復只在選邊之後啟用，並放寬防止再借准入規則。V12/live 未變。

## 2026-09-27 案例 2628553：修復能讓兩支都正卻沒做（使用者指出）；另更正 position mix 的換邊方向 bug

[CASE_2628553](../../data/research/v12g_fresh30_bigloss_vs_target_20260927_v1/CASE_2628553.md)。
- 140～240 秒 UP 只有 0.18～0.25，而 DOWN 分支為正。只要買 1,400～2,400 份 UP（約 350～400 元），兩支就都能轉正。
- 修復需求一直是 ELIGIBLE，但每 10 秒只有 1～5 張被動修復單；同時選邊加倉用約 0.7 買 DOWN 2,000 份以上；200 秒後完全不動。
- 目標在同一場先把便宜的 UP 超額修復，反轉後再把便宜的 DOWN 補上，最後兩支都正（+476／+531）。
- position mix 更正：FLIP 事件記錄的是 `to` 不是 `side`。修正後 PADD80 選邊加倉占 54%、弱邊修復只占 15%。V12/live 未變。

## 2026-09-27 最新 30 場 PADD80 大虧場 vs 目標同場：目標在這 8 場也只有 3/8 為正（平均 −132，我們 −491）；我們在後段用 0.68～0.83 追熱門邊

[bigloss vs target](../../data/research/v12g_fresh30_bigloss_vs_target_20260927_v1/CURRENT.md)。
- 這 8 場多為最後一分鐘反轉。
- 120 秒後我們買熱門邊 8,263 份（均價約 0.70），冷門邊只有 4,773 份。
- 目標贏的 3 場，後段兩邊都大量買，或偏向便宜的冷門邊。
- 下一步候選：以價格為條件，不在 0.70 以上追熱門邊。V12/live 未變。

## 2026-09-27 V12g 最新 30 場（9/26）WB100 vs PADD80：WB100 DOES_NOT_HOLD（沒有大虧，但勝方為正只有 22～28%）

[fresh30 包](../../data/research/v12g_fresh30_20260927_v24/CURRENT.md)。
- 以官方結算判定 30 場：
  - PADD80：勝方為正 57%，平均 −70，中位 +28；有 2 場 ≤−500，排除 ≤−100 後平均 +95。
  - WB100：勝方為正 28%，平均 −38，最差 −100。
- 目標在同一批市場（19 場）勝方平均 +166，14/19 為正，而且有明顯的金額不對稱。
- 注意：預先登記的最終中價判定法在新市場只能判定 19 場，而且有一場判錯。V12/live 未變。

## 2026-09-27 V12g 偏向隨價格形成 PLEAN1／中後段掛單 DRV（10 場）：兩組都 STOP

[price lean 包](../../data/research/v12g_price_lean_small_20260927_v23/CURRENT.md)。
- PLEAN1：平均 −109（PADD80 −466，WB100 −34），spread5 +16，仍有一場 −531。最大份數領先中位 33%，價格漲高後偏向照樣長大。
- DRV：4 場因自我交叉被拒而失敗（bug）；完成的 6 場有 5 場比 PLEAN1 差。
- 掛在最佳買價的被動單在模擬中會被逆選擇，多成交並不會帶來成交優勢。V12/live 未變。

## 2026-09-27 更正：同口徑成交價——我們的價位不貴；目標的利潤約等於成交量 × 小幅成交優勢

[position mix 更正節](../../data/research/v12g_position_mix_20260927_v1/CURRENT.md)。
- 先前「目標熱門邊 0.55」取錯口徑。同口徑下熱門邊均價：目標 0.685、我們 0.663；冷門邊：0.304／0.322。
- 成交優勢（時間取到秒）：目標 +0.95¢、我們 +0.38¢。注意時間精度會讓這個數字移動 1.6¢。
- 目標每場約 5,700 份 × 0.95¢ ≈ +55，接近它的實際收益。我們每場約 2,700 份，而且 63% 押在熱門邊。
- 缺口在成交量（後段缺席）與方向押注，不是單價。

## 2026-09-27 V12g 排隊模式敏感度 risk→log（worker，50 場）：QUEUE_MINOR；成交價差不是排隊造成的

[queue log 包](../../data/research/v12g_queue_log_20260927_v22r1/CURRENT.md)。
- 引擎原設定：延遲 250/250ms，排隊為 risk-averse（排最後）。
- 切換到 log_prob 後：
  - PADD80 平均 −168→−140（改善集中在少數大虧場），勝方為正 23→22。
  - WB100 幾乎完全不變。
  - 每份成本不變（約 0.52～0.54）。
- 我們每對成本約 1.04，目標約 0.973，差距來自高價買入與後段缺席，不是排隊。
- v22 在 smoke 階段停止（掛鉤 bug），已由 v22r1 修正；兩個 job 都禁止重送。V12/live 未變。

## 2026-09-27 倉位來源與場中活動（PADD80／WB100 vs 目標）：我們前段成交、後段停止，目標整場平均成交

[position mix](../../data/research/v12g_position_mix_20260927_v1/CURRENT.md)。
- 開局 9%、選邊加倉 30%、其他 62%（主要是弱邊修復與換邊後的成交）。
- 150 秒後成交占比：我們 23%，目標 46%。
- 選邊後空檔 ≥60 秒的場：我們 67%，目標 21%。
- 最後一筆成交中位：我們 214 秒，目標 271 秒。
- 後段缺席是偏向限制救不回利潤的原因之一。下一步：找出 V49 後段停止的原因。V12/live 未變。

## 2026-09-26 V12g 弱邊虧損額度 WB100／WB200（G300_FLIP40＋PADD80，worker，50 場）：兩組都 STOP；大虧被完全消除，但沒有利潤

[WBUDGET 包](../../data/research/v12g_wbudget_20260926_v21/CURRENT.md)。
- WB100：沒有任何一場 ≤−100，最差 −93，平均 −28（PADD80 −168），但勝方為正 15/50（PADD80 23）。
- 趨勢市場兩邊份數接近，每份成本 0.50，所以打平。
- 所有偏向限制（PACE、ADDCAP、ADDGAP、防禦模式、WB）都落在同一條取捨線上：大虧越少，趨勢市場獲利越少，平均都沒有轉正。
- 瓶頸已經從偏向控制轉到成交價格：我們的每對成本約 1.00，目標約 0.973。
- 使用者要求：不要用秒數限制。V12/live 未變。

## 2026-09-26 V12g 選邊後加倉限速 ADDGAP（G300_FLIP40＋PADD80，worker，50 場）：兩組都 STOP；ADDGAP2 大虧大幅改善，但趨勢市場獲利消失

[ADDGAP 包](../../data/research/v12g_addgap_20260926_v20/CURRENT.md)。
- ADDGAP2（選邊後每 2 秒最多 1 張加倉單）：沒有任何一場 ≤−500，最差 −339，平均 −45（PADD80 −168），但勝方為正 18/50（PADD80 23），spread25 的平均從 +77 掉到 +1。
- 同時完成兩項目標資料分析：
  - [目標在大虧同型市場的操作](../../data/research/btc5m_target_bigloss_analog_20260926_v1/CURRENT.md)：反轉前兩邊都在 0.5 附近買，份數差約 2%，反轉後用 0.32 大量買便宜的舊領先邊。
  - 開局偏向比較：我們 60 秒時份數差 36%，目標 6%。
- 目標的 BTC 成交目前只有 9/23 和 9/26 兩天的片段，9/22、9/24 的市場無法直接比對。
- 下一步候選：只在早期限速，讓偏向在方向較明確之後才形成。V12/live 未變。

## 2026-09-26 交接更新（深夜）：目前最佳 G300_FLIP40＋PADD80＋PACE25（100 場勝方為正 64%、無 ≤−500、平均 −20）；大虧形成於第一次反轉前

[BTC5M 交接 09-26（已更新：「目前狀態」與第 4d 節）](../handoffs/BTC5M_HANDOFF_20260926_ZH.md)。畢業標準已改為勝方收益 >0（100 場超過 60%）。v8～v19 的結果、100 場輸入 base、加速流程（arms 打包、收到結果即觸發、12 路平行、只跑受影響市場）、overlay 環境變數清單、禁止重送清單與新的工作偏好都已列入。下一步候選：在基礎版本上改變反轉前的累積方式（加倉跟著修復進度走）。V12/live 未變。

## 2026-09-26 V12g 防禦模式套在基礎版本（G300_FLIP40／PADD80，worker）：兩組都 STOP；最差仍約 −1,270，因為虧損在第一次反轉前就已形成

[防禦模式（基礎版本）包](../../data/research/v12g_defense_base_20260926_v19/CURRENT.md)。job btc5m-v12g-defense-base-20260926-v19：130 條路徑，0 錯誤，對照與檢查市場一致，送出到收回 14.4 分鐘，禁止重送。100 場合併：G300_FLIP40 −55.8／61% → +DEF −48.5／49%；PADD80 −47.3／68% → +DEF −52.1／51%；≤−500 從 8／7 場到 4 場。反轉市場勝方為正降到 22%。對照 PACE25（最差 −470）：關鍵在反轉前的累積量，而不是反轉後的處理。V12/live 未變。

## 2026-09-26 V12g 防禦模式（第一次反轉後只修復不加倉，worker，有反轉的 62 場）：STOP；尾部變小（最差 −286、≤−200 只剩 3 場），但反轉市場勝方為正 45% → 10%

[防禦模式包](../../data/research/v12g_defense_20260926_v18/CURRENT.md)。job btc5m-v12g-defense-20260926-v18：66 條路徑，0 錯誤，對照與檢查市場一致，送出到收回 6.3 分鐘，禁止重送。100 場合併：勝方為正 64% → 43%，平均 −20.1 → −21.0，成本 1,059 → 699。只靠修復無法把反轉後的勝方補到正值；換邊後的追回才是反轉市場將近一半能讓勝方為正的原因。PACE25 仍是最佳版本。V12/live 未變。

## 2026-09-26 V12g 主動修復（PACE25＋AREP60，worker，50 場）：NOT_PREFERRED，兩組市場都變差；目前最佳仍是 PACE25

[主動修復包](../../data/research/v12g_arep_20260926_v17/CURRENT.md)。job btc5m-v12g-arep-20260926-v17：52 條路徑，0 錯誤，對照一致，送出到收回 8 分鐘，禁止重送。另一邊 5 秒內漲 ≥0.02、該支為負、賣價 ≤0.60 時主動買 15 份：50/50 場觸發，每場約 480 份。平均 −64.5 → −94.9，獲利市場 +31 → +12，虧損市場 −160 → −202，出現 2 場 ≤−500。短暫上漲大多是雜訊（與離線 5 秒追漲吃單虧損一致）。V12/live 未變。

## 2026-09-26 V12g 節奏上限調整（worker，50 場：25 場最差＋25 場分散）：PACE30／PACE35 都不如 PACE25；放寬上限時，獲利多出的少於虧損增加的

[節奏上限調整包](../../data/research/v12g_pace_tune_20260926_v16/CURRENT.md)。job btc5m-v12g-pace-tune-20260926-v16：102 條路徑，0 錯誤，對照一致，送出到收回 11.7 分鐘，禁止重送。同一批 50 場的平均：PADD80 −168、PACE25 −64.5、PACE30 −74.2、PACE35 −95.3。最差 25 場：−414／−160／−192／−237；其餘 25 場：+77／+31／+44／+46。PACE30 與 PACE35 各出現 2 場、3 場 ≤−500。結論：偏向大小同時決定趨勢市場的獲利與來回市場的虧損，單調上限找不到讓平均轉正的點；下一步在 PACE25 上加主動修復。V12/live 未變。

## 2026-09-26 V12g 加倉節奏上限 PACE25（worker，100 場）：PROMISING；勝方為正 64%，≤−500 從 7 場到 0 場，最差 −1,248 → −470，平均 −47 → −20

[PACE25 包](../../data/research/v12g_pace_20260926_v15/CURRENT.md)。job btc5m-v12g-pace-20260926-v15：102 條路徑，0 錯誤，對照一致，送出到收回 10.7 分鐘，禁止重送。選定邊份數（含掛單）領先另一邊超過總量 25% 時否決新單並撤掛單：上限確實守住（最大份數差中位 26%）。沒換邊市場 +43（94%），換過邊市場 −60（PADD80 −138）。與價格上限 0.55 相比，大虧改善相近，但成功率保住（64% 對 45%）。代價：沒換邊市場收益 +99 → +43。下一步試 30～35%。V12/live 未變。

## 2026-09-26 選邊到第一次換邊的稽核（100 場）：修復沒有被擋（大虧・開局選錯組 79% 時間可執行、資金規則未砍量），而是加倉速度遠超過修復

[加倉上限包補充](../../data/research/v12g_addcap_20260926_v14/CURRENT.md)。大虧・開局選錯 11 場：約 86 秒內選定邊買約 1,380 份（約 0.63），另一邊只買約 294 份（修復均價 0.38，掛單被上漲甩開：0.425 → 0.616）。換邊時份數差 833 份（50%），另一支收益 −589。V49 的修復角色也有 212 份用在選定邊（補 4.5:1 的偏向目標）。換過邊但沒大虧的組：加倉約 750 份、主動回補 126＠0.23、份數差 36%。V12/live 未變。

## 2026-09-26 V12g 加倉價格上限 0.55（worker，100 場）：ALL 消除大虧（≤−500 從 7 場到 0 場，最差 −1,248 → −459，平均 −47 → −21），但勝方為正 68% → 45% → STOP；POST 無效

[加倉價格上限包](../../data/research/v12g_addcap_20260926_v14/CURRENT.md)。job btc5m-v12g-addcap-20260926-v14：166 條路徑，0 錯誤，對照與檢查市場一致，送出到收回 17.4 分鐘，禁止重送。ALL：≤−200 從 16 場到 6 場，成本 1,483 → 883；沒換邊市場 +99 → +43（97% → 67%）。POST：−42.4，勝方為正 57%，≤−100 仍是 21 場。結論：0.55 太嚴，會削掉 0.55 以上加倉帶來的主要獲利；下一步試中間值（0.62～0.65）或改限數量。V12/live 未變。

## 2026-09-26 大虧場拆解（PADD80，100 場）：虧損主要來自每個方向階段在約 0.60 的加倉（換邊前 −256、換邊後 −300），修復合計約 +19，不是修復崩掉

[最多換邊包補充](../../data/research/v12g_maxflip_20260926_v13/CURRENT.md)。大虧 21 場：勝方 −484，開局選對 10/21，平均換邊 2.4 次，第一次換邊時已 −245。兩邊各買近 1,000 份、均價約 0.60，成本約 2,000。換過邊但沒大虧的 37 場，修復與主動回補在換邊前貢獻 +160，用便宜價格買到最後勝方；在來回的市場，修復買的那邊又反轉，因此無效。V12/live 未變。

## 2026-09-26 V12g 最多換邊 1 次（worker，換邊 ≥2 次的 31 場）：FN／FH 都 STOP；大虧在前 1～2 次換邊就已形成。傳輸改善：送出到收回 10.7 分鐘

[最多換邊包](../../data/research/v12g_maxflip_20260926_v13/CURRENT.md)。job btc5m-v12g-maxflip-20260926-v13：66 條路徑，0 錯誤，對照與檢查市場完全一致，禁止重送。100 場合併：PADD80 −47.3（≤−100 共 21 場）；FH −42.3（中位 +47.3，22 場）；FN −50.1（勝方為正 62%，24 場）。只看換邊 ≥2 次的市場，三者平均都約 −230～−256。選邊時中價離 0.5 小於 0.06 的市場，有 45% 會換邊 ≥2 次（其他 16%），訊號偏弱。arms 打包成 zip 並改為收到結果就觸發後，這次送出到收回只花 10.7 分鐘（上次傳輸 33 分鐘）。V12/live 未變。

## 2026-09-26 100 場大虧統計：PADD80 勝方虧 ≤−100 共 21 場（22%），合計 −10,154；扣掉後平均 +78、勝方為正 88%；大虧全部是換過邊的市場

[100 場驗證包補充](../../data/research/v12g_val100_20260926_v12/CURRENT.md)。依換邊次數：0 次 36 場 +99（97%、0 場大虧）；1 次 −37；2 次 −209；≥3 次 −279。開環停止規則（第 2 次換邊後停止、成本上限）都無改善。傳輸：130MB／約 850 個檔案用 scp 花 33 分鐘；之後 arms 打包成 zip，等待改為收到結果就觸發。正在測試「最多換邊 1 次」（FN 回平衡／FH 固定）：btc5m-v12g-maxflip-20260926-v13。V12/live 未變。

## 2026-09-26 V12g 100 場驗證（V12＋K415，worker）：G300_FLIP40＋PADD80 勝方為正 68%（64/94）→ 達到畢業成功率標準；平均 −47 被 7 場換邊來回大虧拉低（中位 +43.5，不計最差 5 場 +6.6）

[100 場驗證包](../../data/research/v12g_val100_20260926_v12/CURRENT.md)。job btc5m-v12g-val100-20260926-v12：142 條路徑，0 錯誤，對照一致，12 路平行 18 分鐘，禁止重送。PADD80 68%（old20 71%／new30 66%／rest50 69%），G300_FLIP40 61%。最差 6 場都是換邊 ≥2 次、成本 2,200～2,700 的市場（−806～−1,248）。勝方為 UP 的市場 58%、平均 −141；勝方為 DOWN 的市場 78%、平均 +50。輸方平均 −146（最差 −1,077）。下一步：讓多次換邊的市場少虧。V12/live 未變。

## 2026-09-26 V12g 依收益加倉 PADD80（V12＋K415，worker，10 場）：PROMISING，勝方為正 7/10 → 8/10，平均 +1 → +20，最差 −257 → −189

[依收益加倉包](../../data/research/v12g_payoff_small_20260926_v11/CURRENT.md)。job btc5m-v12g-payoff-small-20260926-v11：22 條路徑，0 錯誤，對照一致，禁止重送。G300_FLIP40 加上「選定邊是熱門邊、該支為負、賣價 ≤0.80 時，每 2 秒主動買 15 份」：扣費後 +14.9，中位 +26.5，2491311／2495811 最長靜止 180／207 → 25／14 秒。代價：輸方平均 −117 → −142（2495811 −98 → −447）。再加便宜修復 PREP30 變差（6/10、−19.7）→ STOP。10 場、8 場 UP 勝，需要擴大驗證。V12/live 未變。

## 2026-09-26 V12 長時間靜止普遍存在（50 場原始紀錄）：G300_FLIP40 有 35/46 場靜止 ≥60 秒，最後一筆成交中位第 198 秒

[個案包補充](../../data/research/v12g_case_2491311_20260926_v1/CURRENT.md)。最長靜止中位：G300_FLIP40 91 秒、K415 48 秒、BAL_ONLY 225 秒（最後一筆成交中位第 83 秒）。G300_FLIP40 勝方為負的 17 場中，12 場有 ≥60 秒靜止。目標則整場每分鐘都買約 900～1,400 份。原因：加倉目標綁在開局錨點、修復只看份數。正在測試依收益加倉／修復（v12g_payoff_small_20260926_v11）。V12/live 未變。

## 2026-09-26 個案 2491311（G300_FLIP40 最差場）：開局 80 秒用約 0.62 押 1,272 份 DOWN，反轉後換邊追回，第 110 秒起 190 秒完全不交易

[個案包](../../data/research/v12g_case_2491311_20260926_v1/CURRENT.md)。前 8 秒 V49 先買 360 份 UP；8.4 秒選 DOWN（0.595），10～80 秒買 1,272 份 DOWN（約 0.62），到第 80 秒 P_UP −421；80～110 秒 UP 從 0.34 急漲到 0.80，87.8 秒換邊，買 479 份 UP（0.58～0.78）。之後 190 秒沒有任何交易，因為加倉目標固定在開局錨點（成長權重 0，有效目標 UP 467 < 持有 1,124），而修復規則只看份數（DOWN 已多於 UP）。最終 −257／−86。同場 K415 第一筆成交定在 UP、不換邊，+258。可修方向：限制開局前 1～2 分鐘熱門邊的投入量、換邊後重設加倉錨點、修復改看收益。V12/live 未變。

## 2026-09-26 V12g 偏向大小上限 20%（V12＋K415，worker，10 場）：勝方為正 7/10 → 5/10 → STOP；會削掉好市場收益，上限也守不住

[偏向上限包](../../data/research/v12g_leancap_small_20260926_v10/CURRENT.md)。job btc5m-v12g-leancap-small-20260926-v10：12 條路徑，0 錯誤，對照一致，禁止重送。偏向邊份數差 ≥ 總量 20% 時否決偏向邊新單：平均 −18.8（中位 +1.5），最差 −169（FLIP40 −257），但 2532691 +29 → −130，好市場收益變少（+171 → +114）。實際最大份數差平均 50%：只擋新單、已掛單仍會成交，而且開局時總份數小。10 場中最好的仍是 G300_FLIP40 與 CAP15（都是 7/10）。V12/live 未變。

## 2026-09-26 V12g 換邊加碼上限（V12＋K415，worker，10 場）：來回換邊的市場救回，但會擋住「換對」後的追回 → STOP；大虧根源是開局 4.5:1 偏向

[換邊上限包](../../data/research/v12g_flipcap_small_20260926_v9/CURRENT.md)。job btc5m-v12g-flipcap-small-20260926-v9：12 條路徑，0 錯誤，對照一致，診斷欄位正常，禁止重送。每次換邊後新方向最多加買換邊當下總份數的 15%：勝方為正 7/10（同 FLIP40），平均 −3.9（中位 +31.7，不計最差 3 場 +58.7），最差 −291。只影響 3 場：2495811 −112 → +34（來回換邊）；2493339 +9 → −153、2491311 −257 → −291（換對但追回被擋）。最差幾場都是開局在 0.41～0.62 選錯邊。V12/live 未變。

## 2026-09-26 V12g 後段便宜修復（V12＋K415，worker，10 場）：輸方 −117 → −21，但勝方為正 7/10 → 6/10 → STOP；平均被 2 場換邊大虧拉低

[後段便宜修復包](../../data/research/v12g_latecheap_small_20260926_v8/CURRENT.md)。job btc5m-v12g-latecheap-small-20260926-v8：12 條路徑，0 錯誤，對照一致，禁止重送。G300_FLIP40 加上「180 秒後冷門邊賣價 ≤0.30 且該支為負時，每 2 秒主動買 15 份」：9/10 場觸發，合計約 1,517 份，均價 0.05～0.26。勝方為正 6/10（FLIP40 7/10），平均 −8.9（中位 +21，不計最差 3 場 +51.7），輸方 −21（FLIP40 −117）。最差兩場 −280、−146 來自換邊來回。注意：overlay 結果尾端的 dict 重複傳入 qty，v12g* 診斷未寫入，修復下單已從 trace 重建；下一包要修正。V12/live 未變。

## 2026-09-26 目標如何形成收盤不對稱（999 場，離線；使用者：看收盤收益、不看每對成本）：前 2 分鐘建立不對稱，後段大量便宜買冷門邊修復輸方

[目標不對稱形成包](../../data/research/btc5m_target_asymmetry_build_20260926_v1/CURRENT.md)。目標 TRUE_GOAL 39.4%（多為兩支都正，勝方 +213／輸方 +220）；勝方賺但輸方虧太多 20.5%；勝方虧 34.3%。成功市場前 60 秒朝最終勝方 A +126；180～300 秒只有 40～42% 買熱門邊，大量用 0.24～0.27 買冷門邊，每份讓輸方 +0.75、勝方只 −0.25。失敗市場後段用 0.74～0.82 追熱門邊。第 240 秒熱門邊獲勝 86%。注意：目標自己的 TRUE_GOAL 只有 39%，低於畢業門檻 60%。V12/live 未變。

## 2026-09-26 目標的偏向路徑（1,481 場，離線）：有偏向，但早期、小量、會翻轉、最後修平；偏向方向約擲硬幣

[目標偏向路徑包](../../data/research/btc5m_target_lean_path_20260926_v1/CURRENT.md)。相對偏向在 60 秒時中位 25%，到收盤降到 5%；86% 市場曾達 ≥40%。但絕對份數差一直約 200～300 份（最終總份數的 5～6%），最大中位 778 份。每場偏向階段 2.5 次，偏向邊是勝方 53%、是熱門邊 55%。一半市場收在 <5%，這些兩支平均都約 +58；最終 ≥30% 的市場輸方 −206。對比 V12：一次偏 3:1～4.5:1，份數差上千份。V12/live 未變。

## 2026-09-26 V12g BURST（V12＋K415，worker，10 場）：兩邊平衡＋短暫加碼剛漲的一邊 → STOP，每對成本 1.014 → 1.052

[BURST 包](../../data/research/v12g_burst_small_20260926_v7/CURRENT.md)。job btc5m-v12g-burst-small-20260926-v7：12 條路徑，0 錯誤，對照一致，禁止重送。預設兩邊平衡；某一邊高於 2 秒低點 0.02 就加碼 5 秒（份數差上限 10%）。每場加碼 34.5 次，份數差 0.5%。結果 −26.1（−2.5%），真正標準 0/10；BAL_ONLY −18.7。結論：結構已接近目標的雙向樣子，但看到漲了才加碼太晚，買在較高價格；目標賺的是每次加碼都在走勢剛開始時買進。V12/live 未變。

## 2026-09-26 目標雙向假設（1,481 場，離線）：10～30 秒尺度上兩邊同時建倉（累積份數相關 0.96、89% 窗口同時有加倉與修復）；秒級是短暫交替的單邊連買

[目標雙向包](../../data/research/btc5m_target_twoway_20260926_v1/CURRENT.md)。對照「只打亂買哪一邊」的比較組：±10 秒內另一邊也有買 93%（隨機 99%），±2 秒 58%（隨機 73%）；同邊連買平均 2.65 個事件（隨機 2.0），40% 份數在 ≥5 的長串（隨機 18%）；30 秒單邊窗口 25%（隨機 20%）。份數差每場翻轉中位 4 次。結論：沒有長時間偏向，兩方向同時運作、各自在幾秒內短暫加碼，淨偏向小且經常翻轉；V12 是單一長偏向＋修復。V12/live 未變。

## 2026-09-26 交接更新（晚段）：V12g 系列、目標的反轉應對與加倉／修復優勢、BAL_ONLY 基準、FALL-GUARD

[BTC5M 交接 09-26（已更新：最新結論與第 4c 節）](../handoffs/BTC5M_HANDOFF_20260926_ZH.md)。開局偏向是公平定價的賭注：G300 順勢、K415 逆勢，互為鏡像，50 場平均都約 0。BAL_ONLY 每對成本約 1.036（目標約 0.98）。目標在反轉多的市場賺更多，因為它不換邊；它的加倉與修復各半，都靠成交優勢（有預判的吃單、掛單不被下跌吃到），沒有互補邏輯。FALL-GUARD 在 250ms 下無效。離線與模擬路線已到盡頭，剩下的需實際量測（使用者暫不考慮實單）。禁止重送的 job 清單與可重用的 50 場 base 已列入。V12/live 未變。

## 2026-09-26 V12g FALL-GUARD（V12＋K415，worker，50 場）：在 250ms 下避開下跌邊無效 → STOP

[FALL-GUARD 包](../../data/research/v12g_fallguard_20260926_v6/CURRENT.md)。job btc5m-v12g-fallguard-20260926-v6：102 條路徑，2 條 V49 收盤清算錯誤（不重跑），對照一致，禁止重送。BAL_ONLY 加上「低於近 2／5 秒高點 0.02 就擋住該邊（否決新掛單、撤既有掛單）」：剛跌時成交占比 45% → 40%／42%，配對成本 1.031 → 1.045／1.053（變差），損益/成本 −3.4% → −3.35%／−2.85%。每場實際撤單約 20 筆，擋單主要只是減少買進。結論：成交就在下跌那一刻發生，看到才躲已經太晚；目標的掛單優勢無法用這種規則在現有模擬中複製。V12/live 未變。

## 2026-09-26 目標的主動加倉與修復（1,045 場，離線）：兩者各半、都靠同一種成交優勢，沒有互補邏輯；我們 44% 份數是在那一邊剛跌時被掛單吃到

[目標加倉／修復優勢包](../../data/research/btc5m_target_add_repair_edge_20260926_v1/CURRENT.md)。配對成本中位 0.979。ADD 50.3%／REPAIR 49.5%，結算 +0.007／+0.010，對勝方貢獻 +21／+28。吃單（31%）結算 +0.022，其中剛漲時吃單 +0.040；掛單剛跌時被成交只占 22%，結算約 0。V12 BAL_ONLY：44% 份數是剛跌時的掛單成交，結算 −0.040。公開 5 秒追漲規則吃單 −0.016～−0.037/份（兩半都顯著為負）。結論：配對成本低於 1 是每筆成交的優勢，不是配對規則；我們的關鍵缺口是掛單在下跌邊被成交。V12/live 未變。

## 2026-09-26 V12g BAL_ONLY（V12＋K415，worker，50 場）：整場不偏向，每對成本 1.036，兩支都穩定約 −3.4%；目標 0.964～0.982

[BAL_ONLY 包](../../data/research/v12g_balanced_20260926_v5/CURRENT.md)。job btc5m-v12g-balanced-20260926-v5：52 條路徑，0 錯誤，兩條對照一致，禁止重送。46 場：勝方 −22.5、輸方 −19.5（−3.4%），份數差中位 0.2%，配對成本中位 1.036，最差 −110，兩支都為正 0/46。反轉越多略差（0 次 −17、≥2 次 −27），目標則反轉越多越便宜。結論：拿掉偏向後剩下的就是成交成本；偏向版本等於這個成本加上一個公平定價的方向注。要畢業必須先把配對成本壓到 1 以下，結構調整補不上。V12/live 未變。

## 2026-09-26 V12g NEUTRAL（V12＋K415，worker，50 場）：跌到 0.40 後回到平衡、不換邊 → STOP；G300（順勢）與 K415（逆勢）互補，開局偏向就是賭反轉

[V12g NEUTRAL 包](../../data/research/v12g_neutral_20260926_v4/CURRENT.md)。job btc5m-v12g-neutral-20260926-v4：52 條路徑，0 錯誤，兩條對照一致，禁止重送。46 場：NEUTRAL −33.2（最差 −506，成本 990），FLIP40 −15.5（最差 −848），K415 −3.8。依反轉次數：0 次 NEUTRAL/FLIP +90、K415 −309；1 次 NEUTRAL −109、FLIP −38、K415 +215；≥2 次 NEUTRAL −119、FLIP −166、K415 +151。結論：回到平衡能止血，但會鎖住開局偏錯的虧損。第 15 秒 4.5:1 的偏向本身就是公平定價的賭注；目標一開始就不大幅偏向。V12/live 未變。

## 2026-09-26 目標在反覆反轉市場的應對（1,039 場，離線）：目標不換邊、兩邊持續各買一半，反轉越多賺越多

[目標反轉包](../../data/research/btc5m_target_reversal_20260926_v1/CURRENT.md)。目標的 BTC 資料只到 9/18，V12g 的 50 場只有 2 場有目標成交，因此改用 9/10～9/18 的 1,039 場。反轉 ≥4 次（6%）時，目標勝方中位 +148、輸方平均 +220、63% 兩支都為正（0 次反轉：+56、29%）。每個階段買熱門邊與冷門邊的份數各約 50%；每次反轉後 30 秒，份數差中位只從 −5 變成 −22；兩邊均價合計從 0.982（0 次）降到 0.964（≥4 次）。對比：V12g 每次換邊整個轉向，在 0.60～0.70 一次買進 700～1,700 份。V12/live 未變。

## 2026-09-26 V12g 擴大測試（V12＋K415，worker，50 場／46 場可計分）：G300_FLIP40 與 G100_FLIP35 都 STOP；小樣本的優勢消失

[V12g 擴大包](../../data/research/v12g_expand_20260926_v3/CURRENT.md)。job btc5m-v12g-expand-20260926-v3：112 條路徑，1 條 V49 引擎內部斷言錯誤（不重跑），兩條對照完全一致，禁止重送。46 場：G300_FLIP40 平均 −15.5（中位 +37.7），−1.2%，真正標準 41%，換邊 52 次；G100_FLIP35 −71.4，−4.8%，33%；K415 自選方向 −3.8，−0.4%，39%（勝方為正只有 48%，輸方 +260）。G300_FLIP40 依換邊次數：0 次 +90（18 場）、1 次 −24、2 次 −201、≥3 次 −95。結論：三種做法期望值都約 0 到 −5%，真正標準都遠低於 60%；換邊是最大虧損來源。V12/live 未變。

## 2026-09-26 V12g 確認測試（G300_FLIP40，另外 10 場／7 場可計分）：NOT_CONFIRMED；兩批合計 17 場優於 K415，但樣本太小

[V12g 確認包](../../data/research/v12g_confirm_small_20260926_v2/CURRENT.md)。受測系統：V12＋K415（worker）。job btc5m-v12g-confirm-small-20260926-v2：11 路徑，0 錯誤，對照一致，禁止重送。G300_FLIP40 每場 −7.9（扣費 −14.7），真正標準 3/7；同批 K415 自選方向 +131.3，2/7（方向剛好選對 4/7 且賺很大）。依規則 NOT_CONFIRMED。兩批合計 17 場：G300_FLIP40 −2.6、47% 真正標準；K415 −35.5、29%。弱點：震盪市場換邊太多次（7 場 10 次）；決定時價格已偏離 0.7／0.39 的反轉市場仍大虧。V12/live 未變。

## 2026-09-26 V12g（V12＋K415，worker，10 場）：開局 300 份就依價格定方向＋0.40 換邊 → PROMISING，每場約打平、真正標準 5/10

[V12g 包](../../data/research/v12g_gross_decide_small_20260926_v1/CURRENT.md)。job btc5m-v12g-gross-decide-small-20260926-v1：31 路徑，0 錯誤，對照完全一致，禁止重送。兩邊平衡大量開局，合計 300 份（約第 15 秒）時選中價較高的一邊，之後 K415 修復。G300_FLIP40（跌到 0.40 換邊）：每場 +1.1（扣費 −4.0），勝方為正 7/10，真正標準 5/10，最終方向 10/10，判定 PROMISING。G300 −61.5（5/10）、G600 −126.1（5/10）判定 STOP。同批 K415 自選方向 −152、3/10；V12f −37、0/10。限制：10 場（8 場 UP 勝），扣費後仍略負。V12/live 未變。

## 2026-09-26 V12／K415 修復時機（V49 原始成交，17 場）：修復約第 15 秒就開始，但前 60 秒只做 1/3；方向錯時後段修復買在 0.7～0.8

[修復時機包](../../data/research/v12_repair_timing_20260926_v1/CURRENT.md)。受測系統：V12 與 K415 的原始成交。強邊／弱邊從開局就存在，不需等方向。經濟修復由強邊獲利支付（RETAIN）。K415 方向錯時，弱邊 0～60 秒只買 291 份（約 0.53），180～300 秒買 228 份（0.79），合計均價 0.60；INVENTORY 方向錯時合計均價 0.71。方向正確時後段修復便宜（均價 0.43）。原因是修復量跟著缺口與強邊獲利走，不是等待。開環粗估：把後段修復提前到開局，方向隨機平均約 +30。V12/live 未變。

## 2026-09-26 交接更新（下半段）：方向、結構、成交三層結論與系統名稱更正

[BTC5M 交接 09-26（已更新，第 4b 節）](../handoffs/BTC5M_HANDOFF_20260926_ZH.md)。方向：開局與盤中都沒有勝過 Predict 價格的穩定資訊。結構：V12／K415 方向錯時損失 2/3 來自開局偏向，停止加碼只能 −45 → −30。成交：V12／K415 實際下單拿掉排隊、延遲降到 0 重放，方向隨機仍約 −117。唯一可能轉正的是兩邊配對成本低於 1，需要實際量測（使用者暫不考慮實單）。更正：現貨／不排隊／ORACLE 報價測試是在簡化 PTR 報價器上，不是 V12。V12/live 未變。

## 2026-09-26 盤中方向訊號（90～240 秒）：不看價格的現貨／履約價模型正向測試 +0.04/份，但反向拆分 −0.012，不穩定

[開局方向包補充](../../data/research/btc5m_opening_direction_20260926_v1/CURRENT.md)。使用者指出系統會在盤中決定方向，因此把分析延伸到盤中。含 Predict 價格的模型仍不優於價格本身。NOMKT 模型在 150～240 秒吃單：正向拆分每場一次 +0.040 ±0.018，延遲 1～2 秒後仍約 +0.04；反向拆分 −0.012 ±0.015。結論：依賴特定時期，不能當作方向資訊。盤中決定方向與開局相同，公開資料找不到勝過當下價格的依據。V12/live 未變。

## 2026-09-26 開局方向訊號（1,479 場，時間前後拆分）：公開資料沒有比 Predict 價格更好的方向資訊

[開局方向包](../../data/research/btc5m_opening_direction_20260926_v1/CURRENT.md)。受測對象：市場資料本身。特徵為履約價距離（現貨／Chainlink）、開盤前 30／60／300 秒動能、期貨吃單失衡、掛單失衡、基差，並與 Predict 價格比較。開局後 5～60 秒，加入特徵的對數損失都不優於只用價格；市場準確率第 5 秒 51%、第 60 秒 60%（已反映在價格裡）。簡單方向規則吃單扣費後 −0.02～−0.06/份，模型吃單結果在一個標準誤以內。結論：開局偏向在期望值上是擲硬幣。「先大量開倉、後修復」這條路徑的獲利只能來自成交價格（被動成交在中價以下、兩邊配對成本低於 1；目標約 0.973，我們的模擬約 1.03）。V12/live 未變。

## 2026-09-26 V12／K415 方向錯時損失拆解（V49 原始成交，17 場）：2/3 來自前 60 秒開局偏向；停止加碼只能讓方向隨機平均從 −45 改善到 −30

[方向錯誤損失包](../../data/research/v12_wrong_direction_loss_20260926_v1/CURRENT.md)。受測系統：V12 與 K415 的原始成交。K415 方向錯時勝方 −308：偏向邊 −657，其中前 60 秒 −429（843 份，約 0.5）；跌離高點 ≥0.30 後才買的只有 −86。修補買真正勝方均價約 0.59，花 505 換回 +350。開環停止加碼規則中最好的是「跌離高點 ≥0.20 就停」：方向隨機平均 −45 → −30，真正標準維持 14/17，方向正確時勝方 +218 → +117。結論：沒有方向資訊時，開局偏向就是擲硬幣，結構只能少輸，無法轉正。V12/live 未變。

## 2026-09-26 V12／K415 實際下單流 PTR 重放（不排隊、0ms，10 場）：方向隨機仍約 −117；瓶頸是方向錯時的不對稱損失

[V12 下單重放包](../../data/research/v12_ptr_orderreplay_20260926_v1/CURRENT.md)。受測的是真正的 V12 系列：worker 上實際的 NEW／CANCEL 在本機 PTR 開環重放。K415 方向正確時勝方 +127（有排隊）→ +184（不排隊、0ms），方向錯時 −361 → −417，方向隨機平均都約 −117（V49 原始 −99）。不排隊只是同時放大賺與賠。要打平約需 70% 方向正確率；方向錯時成本較高（1,294 對 1,029），表示偏向的一邊下跌時還在繼續買。B：hftbacktest 沒有「排第一」的排隊模型（只有 risk／log_prob／power_prob／l3_fifo），最接近的是 log_prob。更正：之前的現貨／不排隊／ORACLE 測試是在簡化 PTR 報價器上，不是 V12。V12/live 未變。

## 2026-09-26 PTR 報價器給定最終方向（ORACLE，10 場，0ms）：對稱報價 9/10 場累積輸方；勝方領先 5 單時只有 +0.4% 成本

[ORACLE 包](../../data/research/btc5m_ptr_oracle_20260926_v1/CURRENT.md)。不排隊時：BASE 的 P_W −19、P_L +43，輸方份數多於勝方（9/10 場）。W_LEAD（勝方最多領先 5 單，輸方不超過勝方）P_W +6.9、P_L +2.2，+0.4% 成本，真正標準 3/10。WIN_ONLY +48% 成本，但輸方全虧，0/10。結論：這一版是對稱造市測試器，領先上限太小，給方向也無法畢業；逆選擇在方向上的表現是累積到會輸的一邊。V12/live 未變。

## 2026-09-26 最簡紙上模擬（不排隊，10 場，本機）：虧損減半，但排第一、零延遲仍為負

[不排隊包](../../data/research/btc5m_ptr_noqueue_20260926_v1/CURRENT.md)。PTR 報價器（IMPROVE_FC），掛單生效就排在該價位第一位。損益占成本：0ms −2.40% → −1.18%，250ms −4.47% → −1.99%，1,092ms −1.86%；結算每份 −0.006～−0.010（目標 +0.004）。結論：排隊位置約占缺口的一半；剩下約 0.01/份來自報價策略本身（在哪裡、什麼時候掛單），打到我們價位的成交帶著不利資訊。V12/live 未變。

## 2026-09-26 Predict 盤口警訊與反應時間（離線）：警訊存在但大多只早 0.25～1 秒；即使撤掉，剩下的成交仍為負

[盤口警訊包](../../data/research/btc5m_book_warning_20260926_v1/CURRENT.md)。V12 重播 9,466 筆被動成交，依市場前後各 10 場拆分。「前方排隊量消失 ≥95%」或「近 1 秒賣一下跌 ≥0.02」：測試集撤掉 71% 壞成交、42% 好成交（現貨只有約 10%）。但壞成交的警訊 11% 在成交前 250ms 內、35% 在 250～500ms、29% 在 500～1000ms，只有 3% 早於 1 秒，實際 API 約 785ms 來不及。即使理想地撤掉，保留成交的當下邊際仍為 −0.013（原本 −0.019），因為模擬把我們排在顯示掛量之後。結論：支持「判斷太慢」，並且還需要更前面的排隊位置；離線無法再往下驗證，需要實際量測（使用者目前暫不考慮實單）。V12/live 未變。

## 2026-09-26 PTR 加入現貨（撤單保護＋現貨吃單，10 場，本機）：保護無效，吃單為正但太稀少

[PTR 現貨包](../../data/research/btc5m_ptr_spot_policy_20260926_v1/CURRENT.md)。在 IMPROVE_FC 上加入 Binance 現貨。現貨不利 ≥0.5／1bp 就撤單、不掛新單：被動成交量只減少 2～5%，損益占成本仍約 −2.5%（0ms）、−4.5%（250ms），markout 沒有改善。現貨吃單（1 秒內 ≥2bp、盤口未動）10 場只觸發 2～4 次，5 秒 markout 在 0ms 為 +0.137、250ms 為 +0.041，每場貢獻不到 1。結論：現貨是真實但很小的資訊，補不回報價缺口；被動逆選擇主要來自其他來源。V12/live 未變。

## 2026-09-26 我們漏了什麼：V12／PTR 看不到 Binance 現貨；現貨領先 Predict 約 0.25～0.75 秒

[現貨領先包](../../data/research/btc5m_fairvalue_lead_20260926_v1/CURRENT.md)。離線，1,455 場，依時間前半訓練、後半測試。現貨報酬與 Predict 中價變動的相關，在延後 1 格（250ms）時最高（0.251），1 秒後消失。目標的主動單在成交前 1 秒現貨對它有利 +0.14bp，5 秒後比成交價高 +0.030。單純照現貨跳動（≥2bp、盤口還沒動）吃單：延遲 250ms 扣費後 +0.008/份（622 筆），延遲 1 秒為 −0.006。V12 壞被動成交事前現貨較不利（−0.23 vs −0.07bp），但以現貨撤單只能撤掉約 10%。結論：現貨是漏掉的資訊之一，而且必須在 1 秒內反應；它不能解釋全部的逆選擇。V12/live 未變。

## 2026-09-26 交接：結構層與執行層總整理

[BTC5M 交接 09-26](../handoffs/BTC5M_HANDOFF_20260926_ZH.md)。結構層：K=4.15 修復在方向正確時真正標準 82%，換邊能修正最終方向，平衡開局能消除首筆逆選擇；等待方向規則與 210 秒開局已移除。執行層：目標被動成交 +0.014/份，V12 −0.008；延遲 250→1ms 仍 LATENCY_EDGE 否；PTR 0ms 報價＋快速撤單 −2.3% 成本。結論：結構不創造期望值，瓶頸是成交／方向優勢。下一步候選（待使用者決定）：離線找預測性盤口訊號。含禁止重送 job 清單與實作陷阱。V12/live 未變。

## 2026-09-26 PTR 報價／快速撤單測試（0／250／1,092ms，10 場，本機）：零延遲時成交當下轉正，但成交後仍被逆選擇

[PTR 報價包](../../data/research/btc5m_ptr_quote_policy_20260926_v1/CURRENT.md)。每次盤口更新都決策，兩邊報價，一單 15 份。0ms：JOIN／IMPROVE／IMPROVE＋快速撤單，成交當下 +0.005，5 秒後 −0.008，結算 −0.012～−0.014，損益占成本 −2.3%～−2.9%；250ms 為 −4%～−4.5%；1,092ms 為 −4%～−5%。修正 PTR 偏差後，結算仍約 −0.011（目標：成交當下 +0.014、5 秒後 +0.004、結算 +0.004）。結論：降低延遲有幫助但不足；簡單的貼價、改價與快速撤單達不到目標品質，目標可能還靠預判能力或一秒內反應（PTR 無法模擬）。V12/live 未變。

## 2026-09-26 V12 延遲約 0（1ms）小規模測試：每場改善 +27，但被動成交仍在中價之上，LATENCY_EDGE＝否

[小規模測試包](../../data/research/v12z_zero_latency_small_20260926_v2/CURRENT.md)。job btc5m-v12z-zero-latency-small-20260926-v2：21 路徑，0 錯誤，129 hash 一致，250ms 對照完全一致，20 條 1ms 路徑延遲替換皆已驗證，禁止重送（v1 hook 在模組半初始化時就修改，20/20 失敗，未跑任何模擬）。10 場，方向隨機每場平均：250ms −109.2 → 1ms −82.3；被動成交當下邊際 −0.0067 → −0.0037（目標 +0.014）；主動 5 秒 markout −0.018 → −0.011。結論：只降延遲不會產生成交優勢。受限於排隊位置（排在顯示掛量後方）與 V12 不會快速撤單；目標的優勢需要報價／撤單邏輯加上低延遲。V12/live 未變。

## 2026-09-26 V12 被動單「好成交」條件：掛單當下與壞成交無法區分；V49 引擎延遲其實是 250ms

[成交優勢包補充](../../data/research/v12_fill_edge_20260926_v1/CURRENT.md)。V12 共 22,616 張被動單，成交率 43%，成交中 23% 為好成交（成交那一秒中價仍在成交價上方）。好壞兩組的掛單深度（中位最佳買價下 1 格）、掛單時間（中位約 2 秒）、價差、價位、時段都相同，只差之後的價格走勢（好 0、壞 −0.03）。沒有靜態掛單條件可用，只能靠更快撤單。更正：V49／V12 引擎的下單與回報延遲寫死為 250ms，不是 1,092ms。正在跑延遲約 0（1ms）的小規模測試。V12/live 未變。

## 2026-09-26 成交優勢研究（離線）：目標被動單成交時在中價以下約 0.014，V12 為 −0.008；結構的天花板來自成交位置

[成交優勢包](../../data/research/v12_fill_edge_20260926_v1/CURRENT.md)。以 V12 重播 68 條路徑的逐筆成交（重建與份數完全一致）對照目標約 600 萬份成交。成交當下邊際（中價 − 成交價）：被動目標 +0.014、V12 −0.008；主動 5 秒 markout 目標 +0.031、V12 −0.026。目標在各價位、階段、盤勢都為正；V12 的被動單只在價位被吃穿時才成交。若 V12 被動成交能達到目標水準，每條路徑約 +46，足以把方向隨機的每場 −34 翻正。V49 引擎並未偏樂觀。使用者：系統尚未成熟，暫不考慮實單。V12/live 未變。

## 2026-09-26 V12d 小規模測試（兩邊平衡開局＋依價格延後偏向＋換邊＋K415）：兩臂 STOP；平衡開局修正了逆選擇，但偏向的取捨仍在

[小規模測試包](../../data/research/v12d_delayed_lean_small_20260926_v2/CURRENT.md)。job btc5m-v12d-delayed-lean-small-20260926-v2：21 路徑全部完成、0 錯誤，worker 最後彙整時 NameError，未寫出 RESULT.json；已手動回收，對照在本機重建後一致，禁止重送（v1 preflight 失敗，未提交）。10 場：LEAN10 −76.5（−5.9%），真正標準 3/10，13 秒偏向，偏向正確 5/10；LEAN20 −59.2（−6.4%），真正標準 2/10，60 秒時份數差 2.7%，71 秒偏向，偏向正確 7/10。對照 FLIP20_FULL −2.8%、0/10；K415 自主方向真正標準 3/10。結論：早偏向便宜但只有約五成正確，晚偏向較準但已貴，期望值約 0，減掉摩擦為負；V12 系列換參數無法同時做到方向對與勝方賺夠，需要方向或成交優勢。V12/live 未變。

## 2026-09-26 V12x 修復目標 K=4.15（真正標準）：PROMISING；方向正確時真正標準 82%（V12 為 35%），瓶頸移到方向

[實驗包](../../data/research/v12x_repair_ratio_20260926_v1/CURRENT.md)。job btc5m-v12x-repair-ratio-20260926-v1 只送一次，121 路徑，0 錯誤，729 hash 一致，K=2 對照與 9/18 完全一致，禁止重送。17 場：方向正確時真正標準 V12 35.3% → K415 82.4%；兩固定方向合計 17.6% → 41.2%；方向隨機平均 −33.6 → −44.1；自主方向（INVENTORY）真正標準 17.6% → 29.4%，平均 −31.8 → −35.5，依規則 PROMISING。K415_R25：自主方向 41.2%、平均 −62.0，STOP。結論：修復層對準真正標準後有效，失敗集中在方向錯（第一筆逆選擇，41%）。下一步：K415 結合兩邊平衡開局、延後偏向與換邊，做小規模測試。V12/live 未變。

## 2026-09-26 V12＋場中換邊 × 開局量 小規模測試（10 場）：換邊把平均從 −15% 成本降到 −3%，但真正標準 0/10

[小規模測試包](../../data/research/v12f_flip_full_small_20260926_v3/CURRENT.md)。v2（換邊＋輕開局 10 條有效；9 條因沿用已取消 v1 的路徑名稱、工作目錄撞名而失敗）＋ v3 補跑（11 條，0 錯誤，對照完全一致），禁止重送。10 場結果：不換邊＋原樣開局 −158.3（−14.9%，方向 3/10）；換邊＋原樣開局 −36.9（−2.8%，最終方向 10/10）；換邊＋輕開局 −14.7（−4.9%）。真正標準 1、0、0 場。FLIP_HELPS＝否，HEAVY_OPEN_NEEDED＝是（只代表虧損比例較小）。原因：開局 3:1 重押的一側由第一筆逆選擇成交決定；換邊後這些部位成為輸方大虧，新方向在 0.70 以上才買。下一個候選：前幾秒兩邊平衡、延後偏向，再搭配換邊。V12/live 未變。

## 2026-09-26 V12 開局建倉與方向：總量和方向無關，但不指定方向時由第一筆成交定方向，是逆選擇（離線）

[重播包補充](../../data/research/v12_recent_replay_20260926_v1/CURRENT.md)。固定 UP／固定 DOWN／不指定方向三種模式，開局總份數相同（60 秒約 1,150），開盤目標份數兩邊對稱 448／448，約 10 秒內偏到約 3:1。不指定方向時由第一張被動成交定方向：19／20 場被選中那一側在第一筆成交時剛下跌，之後 0 次換邊，方向正確 7／17。結論：開局大量建倉可保留，但前幾秒應兩邊平衡，偏向不可用第一筆成交決定。V12/live 未變。

## 2026-09-26 凍結 V12 近期 20 場重播：V12_TRANSFER_NOT_HOLDS（方向隨機時每場 −33.6）

[重播包](../../data/research/v12_recent_replay_20260926_v1/CURRENT.md)。job btc5m-v12-recent-replay-20260926-v1 只送一次，82 路徑，0 錯誤，495 hash 一致，禁止重送；對照 2020778 UP/DOWN 與 9/18 完全一致。17 場：方向對 +244.6、方向錯 −311.8，方向隨機每場 −33.6（估計扣費 −37.6），6/17 為正；9/18 為 +402／−201／+104.5。首分鐘大量建倉確認（60 秒約 1,163 份）。NO_DIRECTION＋LEGACY 預設為 UP，與固定 UP 相同；NO_DIRECTION＋INVENTORY 平均 −31.8、真正標準 17.6%（同市場 AUTO20 −3.8、50%）。結論：在近期市場，V12 的大量建倉等於方向押注，平均約為成本 −3%，與理論一致；舊的 +104 來自開發市場。V12/live 未變。

## 2026-09-26 AUTO20 結算表與「一支大正、一支小」格子的來源：輸方彩券份數加上勝方高價追加，不是反轉

[結構維持包補充](../../data/research/btc5m_structure_retention_20260925_v1/CURRENT.md)。逐格結算表 AUTO20_SETTLEMENT_TABLE.csv（400 格，重算一致）。使用者確認：兩支都正＝不管哪邊贏都賺，鎖住後的小正數也算成功，評分定義不變。一支大正、一支小的格子共 63／382，全部是大的那一支＝修復側＝輸方：被動單梯以均價 0.07 買進（全部低於 0.25，65% 在 180 秒後），反轉 0 格；實際結算中位 +1.0。主因是加倉側在 0.9 以上追價（均價 0.88～0.95）。V12/live 未變。

## 2026-09-26 V12 唯讀核對：首分鐘大量建倉確認（60 秒 +272/−300），兩方向平均每場 +104.5（零費、指定方向、12 場舊市場），修復弱

[草稿包補充](../../data/research/btc5m_target_design_draft_20260926_v1/CURRENT.md)。V12＝v49_commitment_preparation_20260918_v12，V49 微縮學生加 2:1 修復層，ORACLE_FIXED_DIRECTION，被動 15 份、主動 ≤5，零費。PREPARE 18 個方向世界（中位）：第一筆 3.9 秒，60 秒 1,172 份（約 78 單），F +272／L −300，份數差約 38 單（目標 60 秒 45.7 單、份數差約 8 單）。方向對 +397／−139，方向錯實現 −188；兩方向平均每場 +104.5（成本 1,154），9/18 為正；真正標準 3/18；最差錯方向 −516。限制：方向指定、零費、市場早且已研究過、未在近期市場及 1,092ms 引擎驗證。使用者確認等待規則一定要拿掉。V12/live 未變（僅讀取回傳）。

## 2026-09-26 前 60 秒建倉：AUTO20 的等待規則跳過了勝方最便宜的時段（離線）

[草稿包補充](../../data/research/btc5m_target_design_draft_20260926_v1/CURRENT.md)。AUTO20 沒有明寫的 60 秒規則，但「等中價到 0.70／0.30 才下單」（Claude 09-25 自主方向篩檢引入，依據為 60–120 秒熱門的事後觀察）使第一筆中位落在 71 秒，違反舊交接「開局積極承擔風險不能消失」。LATE60 僅為已 STOP 的測試臂；210 開局批的 60 秒條件來自 Codex 09-24，已移除。目標勝方那側均價：0–30 秒 0.533 → 240–300 秒 0.671。前 30 秒成功與失敗市場差最大：勝方占量 58% 對 47%，每場 +41.8 對 −18.9（事後分組）。下一版應拿掉等待，開盤即在 0.50 附近兩邊建倉。V12/live 未變。

## 2026-09-26 目標分階段行為與設計草稿（離線，無 job）

[草稿包](../../data/research/btc5m_target_design_draft_20260926_v1/CURRENT.md)，頁面 https://claude.ai/artifact/RS8U6WhCwgdf32pS9ZVMW5（私人）。目標 1,045 場（中位）：第 7 秒於約 0.50 起手，30 秒 18.8 單、兩邊皆有 93%，60 秒 45.7 單（21%）。之後每 30 秒約 20 單至 270 秒，加倉與修補量相當；修補每筆補差距 3.5%，被修那一支約 −8% 成本，加倉後約 3 秒，61% 被動；加倉價中位 0.46（高於 0.80 僅 7%）；多數側換邊約 6 次（新多數側為熱門 48%）；份數差停在約 10 單。成功市場於 120 秒後兩支同升，收盤為兩支都正。AUTO20：第一筆 71 秒，全場 14.7 單，加倉價中位 0.73，每筆修補補 50%，換邊 2 次。草稿規則以預算 B／一單 L 表示。限制：目標的成交優勢（低於中價 0.01～0.02）不在結構內；顆粒度需縮小一單。V12/live 未變。

## 2026-09-26 允許換邊 20 場篩檢：兩臂 STOP；最終方向 100% 正確，但「拉到 ≥0」的修復在換邊後大量買進輸家

[篩檢包](../../data/research/btc5m_flip_screen_20260926_v2/CURRENT.md)。job btc5m-flip-screen-20260926-v2 只送一次，80 路徑，105 hash 一致，禁止重送（v1 因回報只送下單子控制器、破壞 V2 成本模型帳務斷言，9.9 秒失敗，不重送）。真正標準達成：對照 NO210 47%/47%，FLIP20 47%/47%，FLIP30 47%/47%。平均：−2.8/−2.6、−2.8/−2.5、−1.7/−1.5。換邊格 9/7 格，最終方向準確 100%/94%，主動損益轉正（+3.3～+3.8），但被動損益 −5.8～−6.5：換邊後舊加倉側成為修復對象，拉到 ≥0 持續被動買進下跌的輸家（例 2491311：輸家被動 325 份，勝方那一支 −16.3）。結合前一篩檢，下一步候選為換邊＋修到小虧就停。V12/live 未變。

## 2026-09-26 拿掉限制 20 場篩檢：兩臂 STOP；拿掉 210 開局批降低尾部，修到小虧就停反而使真正標準達成率 50%→29%

[篩檢包](../../data/research/btc5m_fewer_limits_screen_20260926_v2/CURRENT.md)。job btc5m-fewer-limits-screen-20260926-v2 只送一次，120 路徑，145 hash 一致，對照 40/40 與前輪完全相同，禁止重送（v1 preflight 載入錯誤從未提交）。真正標準達成：對照 53%/47%，NO210 47%/47%，FREE（無守門、無價格上限、修到小虧就停、無 5 張上限）29%/29%。實際平均：對照 −1.6/−6.0，NO210 −2.8/−2.6（種子 21 最差一成 −70→−22），FREE −2.6/−2.6。原因：小虧目標以加倉側為主方向，方向錯時勝方即被修復側，被保證停在小虧；原本拉到 ≥0 反而在錯方向時留下兩支都正。更根本的限制是「選定後不換邊」（目標多數側換邊 ≥4 次 47.6%）。下一步候選：允許主方向切換、保留拉到 ≥0、移除 210。V12/live 未變。

## 2026-09-26 使用者更正畢業標準＋以新標準重評 AUTO20（離線）

使用者：真正的畢業標準是「正確方向那一支賺錢、另一支小虧或兩支都正」；目前版本限制太多；210 份開局批也有問題。[結構維持包補充](../../data/research/btc5m_structure_retention_20260925_v1/CURRENT.md)：定義為勝方那一支 ≥ 成本 1% 且輸方那一支 ≥ −0.241×勝方那一支。目標費前 38.5%（兩支都正且夠大 34.7%＋小虧 3.7%，勝方那一支／成本 0.068）。AUTO20 為 29.5～36.5%，分布近似目標（勝方那一支 ≤0 皆約 35%），但 17% 為兩支都正卻近乎打平（目標 4.5%），幅度 0.020。此前「泛化 #4 畢業」僅依字面標準，不符真正標準。下一版應先移除限制（修復單梯只拉到 ≥0、配對和守門、固定 210 開局等），而非再加限制。V12/live 未變。

## 2026-09-26 補充：AUTO20 沿用 210 份主動開局批，約占平均虧損一半（離線）

[結構維持包補充](../../data/research/btc5m_structure_retention_20260925_v1/CURRENT.md)。V2 子控制器帶有 SizedOpening(ACTIVE_IF_LEGAL, 210)：開盤 60 秒內、加倉側第一張被動單、最佳價掛量 ≥210 時，改送 210 份主動單。泛化 #3/#4 共 382 格中觸發 16 格（4%），平均 −64.9、為正 50%、最差 −192，貢獻全體 −5.71 中約 −2.7；排除後約 −3.1。「早選定較差」主要即此因。為下一個應移除或設上限的對象（樣本小）。V12/live 未變。

## 2026-09-26 研究階段整理（使用者選「先停下來整理」）：平均無法轉正的瓶頸在執行層；下一步待使用者決定

[整理報告](../../data/research/BTC5M_RESEARCH_SYNTHESIS_20260926_ZH.md)。AUTO20_LADDER 兩批 100 場皆過使用者字面畢業標準（為正 >60%），但每格平均 −2～−6（約成本 −3～−4%）。證據鏈：方向無優勢；目標收益來自次秒級成交品質；主動單複製扣費後 0.785 秒為 −0.007；被動價值地圖全負；PTR 經目標訂單驗證；模仿失敗。控制層（配對類〔已否決路線〕、錯方向路由、有界分配、晚開局／純主動）全部 STOP/FAIL。能改變結論的只有：實測更低延遲、新資訊源，或改寫目標。可選下一步：A 實單小額量測（成交品質，非獲利；僅使用者可操作）；B 主動加倉份數上限 20 場篩檢（保留修復）；C 暫停。衍生資料重建腳本在 btc5m_structure_retention_20260925_v1/builders/。無新 job，V12/live 未變。

## 2026-09-25 開局時間／純主動單 20 場篩檢：四臂全部 STOP（純主動去掉保底、尾部失控）

[篩檢包](../../data/research/btc5m_opentiming_active_screen_20260925_v1/CURRENT.md)。job btc5m-opentiming-active-screen-20260925-v1 只送一次，160 路徑，185 hash 一致，禁止重送。20 場為樣本外重用市場（role-top 泛化 v1，排除診斷與調參市場），17 場可判定勝方，2 種子，1,092ms，只用 HFT。對照 AUTO20 為正 76%/71%，平均 −1.6/−6.0。LATE60 為 −2.5/−2.3。AUTO20_ACTIVE 為 −9.9/−14.8，p10 −151/−220（單場 V2 大額主動加倉，無修復）。LATE60_ACTIVE 為 −1.5/−4.8，兩種子都略勝對照但仍為負。主動邊際拿掉被動後由 +0.01 變為 −0.01～−0.17；被動邊際本批為 +0.05（診斷批 −0.02），不穩定。結論：改開局時間或砍被動通道都無法轉正，被動修復是保底；下一步若做，應為主動加倉的份數上限並保留修復。V12/live 未變。

## 2026-09-25 AUTO20 錯方向虧損來源拆解（離線）：不是錯了還加倉，而是被動逆選擇＋早開局；有界分配規則無法轉正

[結構維持包補充](../../data/research/btc5m_structure_retention_20260925_v1/CURRENT.md)。泛化#3/#4 AUTO20 382格：錯方向格子−27.0＝加倉側反轉前−74.5＋反轉後續加僅−2.2＋修復+49.7（63份@0.21）；方向對格子修復−23.1（198份@0.13）→被動修復在不需要時多成交3倍。每份結算邊際：開局主動−0.081、主動加倉+0.007、主動修復+0.010、被動加倉−0.036、被動修復−0.019。刪單反事實（修復價下限、虧損時才修、加倉上限、合併）平均皆仍負，只在形狀與平均間互換。60秒前選定方向格子−11.2 vs 之後−1.7（兩批一致，自然分組）。候選篩檢（未送出）：晚選定≥60s、選定後主動-only、合併；PTR主、HFT參考、約20場重用市場、1,092ms。V12/live未變。

## 2026-09-25 錯方向轉換＋公開狀態 V1 全新市場重驗：FAIL（依 09-22 交接停止線上錯方向路由抽象）

[報告](../../data/research/BTC5M_ADVERSE_TRANSITION_PUBLIC_V1_FRESH_REPLICATION_20260925.md)。完成 09-22 交接指定的下一關：舊研究在資料包內找不到新錯方向市場（0），本次從目標 1,045 場（9/10–16、9/23）取 174 場未用過的錯方向市場（實際<0、反側>0）、21,271 列，照原腳本與原訓練組只評分一次（重寫程式已逐數重現原 F20 AUC）。ENTER_REPAIR 自身 0.593／加公開 0.563（變差），RESUME_THESIS 0.607／0.608；預登錄門檻（兩頭 ≥0.60 且加公開 ≥+0.02）未過→FAIL。依交接：停止線上 adverse-router（Transition+Public）抽象，課程只留作離線教材；自身狀態的轉換可預測性僅弱重現（約 0.59–0.61）。V12/live 未變。

## 2026-09-25 修正後成功形狀（正側=當時主加倉方向）：我們形成/保住率接近目標，差距在幅度與失敗場虧損

[結構維持包補充](../../data/research/btc5m_structure_retention_20260925_v1/CURRENT.md)。RATIO形狀（主方向≥成本1%、另一支≥−0.241×主方向）：目標形成81.2%/保住43.3%（全場+56、65%正），AUTO20形成85%/82%、保住40%/40%（保住中主方向=我們加倉側78–84%）。保住時主方向/成本中位：目標0.082、我們0.028–0.030。未保住市場實現/成本：目標+0.002（≈打平）、我們−0.196/−0.212。結論：結構頻率非主要差距；最大差距是錯方向/未保住時的虧損控制，其次為保住時幅度。下一重點：錯方向圍堵，對照目標課程錯方向教材。V12/live未變。

## 2026-09-25 目標加倉側是否固定（1,045場，離線）：非固定；約半數有主方向、僅約21%近乎固定

[結構維持包補充](../../data/research/btc5m_structure_retention_20260925_v1/CURRENT.md)。以成交前份數判角色（買多數側=加倉）。每場加倉集中於主方向比例中位0.794；≥0.8 49.1%、≥0.95 20.7%；多數側換邊（30份緩衝）4次以上47.6%、0次僅11.6%。首次加倉方向=勝方50.7%、=主方向61.3%；主方向=勝方53.4%。近乎固定（≥0.95）組216場勝方一致62.5%、換邊0。結論：固定UP加倉是人為條件，1977248類冷門正側形狀不代表目標成功結構；成功形狀的正側條件應隨當時主加倉方向。V12/live未變。

## 2026-09-25 成功形狀形成/維持（泛化#3/#4既有路徑，離線）：AUTO20表面約45%形成實為彩券型；正側須為熱門時僅約1%

[CURRENT](../../data/research/btc5m_structure_retention_20260925_v1/CURRENT.md)。預先定義STRICT（≥+24.17/≥−5.825）與RATIO。AUTO20 STRICT形成43%/47%、保住20%/18%，失去幾乎全為修復主導（41/45、56/58）。但失去例中形成時強側0%為加倉側、僅11–12%為實際勝方：修復單梯買便宜冷門份額使冷門分支變大，其後被拉回平衡，實際價值約不變（凍結+2.2/+2.5對最終+1.5/−0.7）。事後加「正側為當時熱門」條件：AUTO20形成僅1/200、2/200。結論：SUCCESS_TARGET_V1評估須規定正側屬性，否則被彩券型結構灌水；我們控制器幾乎未形成熱門正側的成功形狀。V12/live未變。

## 2026-09-25 更正：本段研究曾繞回已否決的「互補配對＝目標核心」假設

使用者核對舊主線後指出：從收益來源診斷後的配對循環 v2、軟性傾斜、深單梯 v1–v3、被動＋主動配對，本質上是已正式否決的「等量互補／pair-lock／份額配平」路線。錯誤在於把帳面拆解（配對部分＋剩餘方向曝險）倒推成目標的決策目標。

否決依據（後續研究須先讀）：
- [TARGET_SYSTEM_REGULARITIES_RESEARCH_HANDOFF_V1_20260903.md](../../data/research/r4_v0/p0_provenance_v1/TARGET_SYSTEM_REGULARITIES_RESEARCH_HANDOFF_V1_20260903.md)：[REJECTED] 純份額配平；BTC5M Repair 中位只付當前缺口 8.8%、近乎補滿約 0.56%；Repair 未完成仍可 Expand（100 場約 708 輪）；Repair 改善 floor、Expand 購買 upside，核心為 Joint Floor + Upside。
- [BTC5M_PAIR_ECON_FALLBACK_V6_TARGET_COMPARE_SMOKE_RESULT_20260916.md](../../data/research/BTC5M_PAIR_ECON_FALLBACK_V6_TARGET_COMPARE_SMOKE_RESULT_20260916.md)：REJECT pair_sum>1 作為通用 ADD 停止規則（正確方向 +81→+34）。
- [SUCCESS_TARGET_V1.md](../../data/research/btc5m_asymmetric_structure_review_20260922/SUCCESS_TARGET_V1.md)：接受 +24.17／−5.83 類非對稱成功形狀，不要求弱側修成零或正；近期目標為約 50% 獨立市場達成。

配對經濟只可作為風險／價值特徵，不可升格為策略目標。本段的延遲、成交品質與 PTR／價值地圖結論（被動成交在我們延遲下每份約 −0.02～−0.03）仍有效，但只作為執行層事實，不改變 Floor + Upside 主線。V12/live 未變。

## 2026-09-25 被動掛單價值地圖（我們的延遲下，190萬探測單、1,854場）：全面為負；預先登錄選格驗證失敗

[CURRENT](../../data/research/btc5m_valuemap_20260925_v1/CURRENT.md)。離線、PTR規則探測引擎（與ptr.World差≤2%）。訓練9/10–16（1,043場）、驗證9/22–25（811場）。所有邊際（階段、價位、深度0–12格、存活10/60/至停止、5秒方向）成交後持有到期−0.004～−0.038/份，兩段一致。預先登錄選格（訓練t>2、≥100場）1/1602格入選，驗證−0.235失敗。事後觀察：開盤10–60秒某邊跌破0.2後勝率高於中價，BTC +0.017（se 0.021）、BNB +0.029（se 0.018），方向一致合併t≈1.7，但僅約14%市場、勝率約20%（彩券型）。結論：現延遲下無穩定為正策略；可改變結論者為降低延遲（需實測）或更多跨資產資料確認開盤急跌過頭。V12/live未變。

## 2026-09-25 條件式模仿（盤口特徵預測目標掛單）：AUC 0.63，但照模型時機掛單成交品質更差；模仿學習線結論為負

[PTR包補充](../../data/research/btc5m_ptr_reference_20260925_v1/CURRENT.md)。399,988列（訓練9/10–11、驗證9/15–16）；logistic驗證AUC 0.628（價位+1.78、價差+0.79、|5秒變動|+0.36、D3 −0.34）。PTR驗證372場（一單70份、被動、1092ms）：為正69.4%但平均−47.83、持有到期−0.0296/份（分布式模仿−0.0220；目標被動+0.0031）；被動後5秒另一邊也成交0.069對0.555。結論：目標掛單的價值資訊不在可見盤口特徵或需次秒反應；模仿學習無法在我們條件下重現目標成交。V12/live未變。

## 2026-09-25 模仿學習第3步（分布重播，樣本外9/15–16共372場）：複製不了目標成交品質

[PTR包補充](../../data/research/btc5m_ptr_reference_20260925_v1/CURRENT.md)。以一單（70份）照目標掛單率（依5秒價格變動）、深度（依份數差）、存活時間分布隨機重播，PTR被動、無主動：為正32.0%、平均−34.12、持有到期−0.0220/份、5秒markout −0.025；目標同市場被動（費前）58.1%、+11.81、+0.0031/份。路徑：被動後5秒另一邊也被動成交0.139對目標0.555。結論：目標價值在條件式時機（何時、何價、何時撤）而非平均分布；下一步可選條件式模型（盤口失衡/深度/成交流預測目標掛單）並檢驗其成交品質。V12/live未變。

## 2026-09-25 模仿學習第2步：目標掛單資料集（795場、17萬事件）與決策表

[PTR包補充](../../data/research/btc5m_ptr_reference_20260925_v1/CURRENT.md)。份數依日期輪換（9/7–8 70；9/9–11 30+55；9/12 15；9/15–18 15+70；9/23 15）。9/10–11(55)+9/15–16(70)共795場、170,947候選掛單（撤61%、成交18%、未結21%）、451,048對照列。目標行為：兩邊同掛、每邊約每5秒一張；價格急動時掛單率約為持平2倍；平衡時掛很深（中位25格）、失衡時4–8格；約47%掛≥10格（成交7.6%、成交後持有+0.018）、貼價成交46%但持有≈0；最後30秒幾乎不掛。V12/live未變。

## 2026-09-25 模仿學習第1步：目標55/70份掛單可從L2辨認（約77–85%），15/30份不可

[PTR包補充](../../data/research/btc5m_ptr_reference_20260925_v1/CURRENT.md)。目標每場最常用份數僅占其被動單約36%（15/30/55為主）。剛好+L掛量事件每場中位約625、98%為整批份數；其中3.6%後由目標成交、90.4%被剛好−L且無成交移除（撤單候選，存活中位約1.2秒）。依「目標用過/未用過該份數」比較：+55事件226對52、+70事件174對27→約77%/85%可歸因目標；+15、+30僅約15%/33%。可在55/70份市場建立含未成交單的目標掛單資料集（準確率約八成）。推估目標每場掛170–200張、僅數%成交，印證低延遲頻繁撤單重掛。V12/live未變。

## 2026-09-25 目標長掛單特徵與深接單複製（PTR，704場樣本外）：長掛深單≥10格/價≤0.2賺錢，但靜態深接單複製虧損

[PTR包補充](../../data/research/btc5m_ptr_reference_20260925_v1/CURRENT.md)。價格優先一致的目標單：中（2–10秒）持有到期+0.0234最佳；長（≥30秒）價位中位0.29、低於最佳買價中位15格、成交前價格已跌0.185，整體−0.0036，其中≥10格+0.037、價0–0.2 +0.068，1–9格−0.03～−0.10。PTR靜態深接單（下10/15/20、≤0.30、下20/30/40格；不修復）704場樣本外：為正約30–32%，平均−1.9～−5.1，持有到期−0.021～−0.027／份（目標同市場費前66.5%正、+58.5）。推測倖存者偏差＋目標在真崩跌時及時撤單。附帶：若知目標決策但晚1秒上線，PTR持有到期仍約+0.006（僅成交單，有選樣偏差）。V12/live未變。

## 2026-09-25 用PTR反推目標掛單方式：目標頻繁撤單重掛，成交單通常僅存在約1–3秒（修正「多數提前很久掛好」）

[PTR包補充](../../data/research/btc5m_ptr_reference_20260925_v1/CURRENT.md)。重播目標推估訂單擬合（QF 0.5–3×Δ−2～+1秒）：排隊係數幾乎無影響（多為穿價一次成交）；最佳僅約47–49%在±1秒內重現，約31%在PTR早>5秒成交。價格優先上限（18,000單）：43.9%的L2推估掛單時間被其後穿價交易否定；一致掛單時長中位約2.5秒（上限），<2秒39.7%、2–10秒39.7%、≥30秒約10%。修正診斷包補充3（78%掛≥2秒、中位9秒過強）。含意：目標為低延遲主動管理報價的做市，我們約1.1秒下單/1.5秒撤單不可複製；僅約10%長掛深單可借用。V12/live未變。

## 2026-09-25 第二參考模擬PTR（公開成交重播）：以目標自身訂單驗證（持有到期偏差約−0.001/份）；主動修復為主要虧損、純被動L3_C接近打平

[CURRENT](../../data/research/btc5m_ptr_reference_20260925_v1/CURRENT.md)。離線純Python、本機、無job。同20場我們策略被動5秒markout −0.016～−0.027（同HFT），排隊位置與延遲（1092/785/350ms）敏感度皆無改善。決定性驗證：目標18,000張推估被動單在PTR重播94%成交，5秒markout −0.0051對實際+0.0023（短線偏悲觀約0.007），持有到期+0.0009對+0.0018（偏差約0.001）→PTR可作收益參考，我們的掛單政策明顯差於目標。PTR下主動修復每場−7.7～−11.4；純被動L3_C（上漲側貼價＋下跌側暫停）55%為正、平均−1.05（探索，非預先登錄）。V12/live未變。

## 2026-09-25 非對稱單梯v3 20場篩檢（上漲側貼價/下跌側暫停）：三臂STOP；發現目標核心是兩邊貼價的價差捕捉

[CURRENT](../../data/research/btc5m_ladder3_screen_20260925_v1/CURRENT.md)。先研究（300場公開被動量）：目標被動買到贏家49.2%、急跌側被動占比18.9%＞持平10.6%（不暫停下跌側）、上漲側15.7%。job btc5m-ladder3-screen-20260925-v1一次submit、60路徑、hash 85/85，禁止重送。L3_A/B/C實際為正30/35/40%，平均−9.99/−8.47/−7.01，皆未勝L2_B（−5.46）；被動贏家比例0.454/0.465/0.481趨近目標但收益未改善。路徑：被動成交後5秒內另一邊也被動成交，目標0.594、我們0.14–0.17→目標核心為兩邊貼近最佳價、先後被吃的價差捕捉；此正是模擬器被動逆選擇最重、且已證實無法校準的部分。V12/live未變。

## 2026-09-25 深單梯循環v2 20場篩檢（不追跌＋淨曝險45＋少量修復，1092/785ms）：三臂STOP；追跌形狀已對齊目標，剩多出部位落輸家

[CURRENT](../../data/research/btc5m_ladder2_screen_20260925_v1/CURRENT.md)。job btc5m-ladder2-screen-20260925-v1一次submit、120路徑、hash 145/145，禁止重送。1092ms：L2_A/B/C實際為正40/35/25%，平均−6.82/−5.46/−7.18（單梯v1 −10.22），最差−34～−45；兩邊VWAP和0.93–0.95；份額失衡15–24%，多出側為勝方僅18–24%（多落輸家，中位約55份）；被動markout 5秒−0.024。785ms敏感度無系統改善。路徑：同邊追跌run中位2/p90 3、maker_same_lower 0.48（目標0.455），已對齊。V12/live未變。

## 2026-09-25 以前實單（Echtgeld）實測延遲＋扣費後主動複製性（離線，使用者提供來源）

[診斷包補充6](../../data/research/btc5m_edge_source_diagnosis_20260925_v1/CURRENT.md)。echtgeld_engine_v1.db engine_cap100_orders（469筆，368筆有送單計時）：訂單建立→開始送單中位452ms、API下單呼叫中位318ms（p10 260/p95 540）、建立→API完成中位785ms（p95 1155）；撤單→結束中位1465ms（對帳記錄上限）。1092ms為Echtgeld校準之保守參考，非憑空。照目標主動單複製扣200bps費後：0.35秒（樂觀最快）+0.0009、0.5秒−0.0022、0.785秒−0.0073、1.092秒−0.0116（目標自價費後+0.0119）。結論：可預見延遲與費率下主動優勢不可複製；撤單慢支持預掛被動循環；模擬維持1092ms為保守標準、785ms作敏感度。V12/live未變。

## 2026-09-25 延遲量測與「目標是否更早判斷」（離線＋唯讀ping）

[診斷包補充5](../../data/research/btc5m_edge_source_diagnosis_20260925_v1/CURRENT.md)。網路RTT：api.predict.fun ping 64ms、keep-alive請求52–72ms、ws.predict.fun TCP 70–90ms；1092/273ms為記錄參考值非場館實測。照目標主動單複製的持有到期期望隨延遲衰減：0秒+0.0124、+0.25秒+0.0087、+0.5秒+0.0043、+0.75秒≈0、+1.25秒−0.0068（目標實際+0.0205＞archive同時點，顯示其早於公開盤口）。目標主動單前其買側掛量失衡自約1–1.5秒前累積（I：−1.5秒+0.036→0秒+0.173），判斷條件含盤口壓力；但失衡單獨作訊號在1.25秒延遲下跨期不一致。結論：若實際下單延遲可達0.25–0.5秒才值得重評主動部分，需實測（實盤，由使用者決定）。V12/live未變。

## 2026-09-25 目標主動單用途與觸發（離線）：修復57%/加倉42%；優勢在1.25秒延遲內消失

[診斷包補充4](../../data/research/btc5m_edge_source_diagnosis_20260925_v1/CURRENT.md)。份額口徑與金額口徑的修復/加倉判定必然一致（分支損益差=份額差）。41,813主動單：前1秒盤口已同向動0.003–0.008、現貨≈0.1bps；目標持有到期+0.0205/份，晚1.25秒照做−0.0070（se 0.0022）、晚2.25秒−0.0102；修復類延遲後≈−0.015，加倉類+0.004～0.007（約1σ不顯著）。結論：延遲下主動單不可作收益來源，修復只當風險成本；可學且耐延遲的是被動預掛循環。V12/live未變。

## 2026-09-25 被動循環＋主動配對20場篩檢（依目標動作路徑）：三臂STOP；配對形狀學到、反應2–3秒對目標1秒、收益更差

[CURRENT](../../data/research/btc5m_hedgecycle_screen_20260925_v1/CURRENT.md)。job btc5m-hedgecycle-screen-20260925-v1一次submit、60路徑、hash 85/85，禁止重送；同20場重用。PH_A/B/C實際為正10/10/15%，平均−31.4/−26.0/−22.5；份額失衡5–9%，每對實際成本約1.02+。路徑指標（path_metrics.py）：主動單屬「另一邊被動成交後配對」88–90%（目標59%）、反應2.1–3.3秒（目標1.0）、配對和下單時0.95–0.98（目標1.00）、同邊追跌run p90 5（目標3）、同邊主動加買12–16%（目標45%）。延遲下被動折價在配對前流失。V12/live未變。

## 2026-09-25 預掛深單梯配對循環20場篩檢：三臂STOP；配對成本0.84–0.89但失衡21–26%與追跌補單逆選擇

[CURRENT](../../data/research/btc5m_laddercycle_screen_20260925_v1/CURRENT.md)。job btc5m-laddercycle-screen-20260925-v1一次submit、60路徑、hash 85/85，禁止重送；同20場重用。LC_A/B/C實際為正25/30/25%，平均−10.22/−11.00/−11.91；兩邊VWAP和中位數0.894/0.891/0.838（<1，優於目標0.973），但份額失衡21–26%，下跌側多出部位多落輸家；深單被動markout 1秒+0.025、5秒−0.024（單梯每秒依當前買價補齊→跌勢中一路補單）。主動修復3%無效。下一步候選：補單冷卻/錨定單梯/更緊淨曝險。V12/live未變。

## 2026-09-25 被動成交模型校準：NOT_CALIBRATABLE_STOP（依使用者指示不再追）

[CURRENT](../../data/research/btc5m_fillmodel_calib_20260925_v1/CURRENT.md)。job btc5m-fillmodel-calib-20260925-v1一次submit、240路徑、hash 265/265（手動collect），禁止重送；V0逐格重現軟性傾斜篩檢。同20場公開非目標掛單者被動買單5秒markout −0.0014（目標−0.0023）；模擬SC_C被動在6種設定（risk-adverse/power-prob(3)/log-prob × early/mid/late）下為−0.0115～−0.0180，差距−0.010～−0.0165，無一落在±0.003→依規則停止。排隊模型幾乎無影響，late僅部分改善。差距來自掛單位置/時機、未建模撤單競速與秒級成交時間等，非單一參數可校準。V12/live未變。

## 2026-09-25 軟性傾斜配對循環20場篩檢（依目標修復規則）：三臂STOP；份額平衡已複製但模擬配對成本1.025–1.038對目標0.973

[CURRENT](../../data/research/btc5m_softcycle_screen_20260925_v1/CURRENT.md)。job btc5m-softcycle-screen-20260925-v1一次submit、60路徑、hash 85/85，禁止重送。同配對循環v2之20場（重用）。SC_A/SC_B/SC_C實際為正皆25%，平均−11.13/−9.46/−7.82；份額失衡中位數3.2–6.7%（目標3.6%）。平衡後損益=份額−成本，模擬每對成本高於1，雙分支皆虧；差距約5–6分即模擬被動逆選擇（−0.009）對目標（+0.005）雙邊計入＋主動價差與費。結論：只複製結構在現模擬器不賺錢，瓶頸是成交品質；下一步以目標真實被動成交校準模擬被動成交模型。V12/live未變。

## 2026-09-25 雙邊被動配對循環20場篩檢（重用#4市場）：三臂STOP；/goal已由使用者暫停

[CURRENT](../../data/research/btc5m_paircycle_screen_20260925_v2/CURRENT.md)。v1 job 53秒失敗（FLATTEN自成交，不重送）；v2一次submit、60路徑成功、hash一致。PC_JOIN實際為正45%平均−1.56；PC_DEEP2 45%/+0.06；PC_JOIN_FLAT 50%/−2.69；同20場AUTO20_LADDER 60%/−10.24（最差−147）。配對循環去掉方向與費後尾部收斂（最差約−35）、平均≈0，但被動逆選擇使正場次<50%。下一步候選：以目標真實被動成交校準模擬器被動模型、改掛單策略。V12/live未變。

## 2026-09-25 收益來源診斷（/goal：收益轉正＋開局方向）：研究假設下找不到正期望機制；離線、無新job

[CURRENT](../../data/research/btc5m_edge_source_diagnosis_20260925_v1/CURRENT.md)。只讀泛化#3/#4回傳（1600格）及本機唯讀archive（2424場250ms現貨/期貨/Chainlink＋Predict盤口）、目標成交、Polymarket外部盤口。平均負值≈成交摩擦（每格約270主動＋315被動份；主動5秒markout−0.009＋費，被動成交後逆選擇到−0.009），單梯約損益兩平（拿掉只到≈−1）。盤口有效率：現貨模型不如中價、合併無增益；開局方向規則（門檻/持續/時間窗/順逆）準確率≈進場價，測試EV≤0。現貨跳動後盤口1秒內大多已跟上，1.1秒研究延遲（archived reference entry 1092ms）後剩約0.008＜價差＋費。目標費前65.2%場正、主動0.8以下每份+0.03～0.045、1秒markout+0.02，推測次秒級延遲優勢，本研究延遲下不可重現。Polymarket外部37場不領先。結論：平均轉正需使用者決定：低延遲假設（需實盤延遲證據）、新資訊源，或改寫目標為低摩擦＋正場次>60%＋尾部受控。補充（使用者要求）目標收益來源：1045場費前+56/場（估費後+37、61%正）；帳面鎖定配對+66、方向淨曝險−10（淨側為勝方僅54%，非押方向）；經濟來源為成交價優於公平價：主動成交約74%（成交後30秒仍+0.028不回吐，250ms現貨無先行，推測次秒級資訊），被動+0.004/份逆選擇輕（深價位最佳）；本模擬被動5秒markout−0.009對目標+0.005，被動成交模型可能過度悲觀、宜先以目標真實成交校準。V12/live未變。

## 2026-09-25 最後一批自主方向100場泛化（/goal畢業測試#4）：GRADUATE（依預先登錄規則；非晉級、非實盤）

[CURRENT](../../data/research/btc5m_autodirection_generalization_20260925_v2/CURRENT.md)。job btc5m-autodirection-generalization-20260925-v2 一次submit、rc0/stderr0、1837秒、自動回收，禁止重送。9/22–25各25新場、800 native、0fit；905 hash、800指標重算一致；選方向前無下單。AUTO20_LADDER（主）：離線實際為正66.7%/68.8%（CONTROL固定雙向37.5/47.4%），方向對格平均1.88/2.15，方向準確76%→G1、G3兩種子皆過→GRADUATE；泛化#3同臂62.1/64.2%，兩批獨立重複>60%。揭露：實際平均仍負（−6.43/−5.02，S20差於CONTROL−3.91），多小贏少大虧，非整體獲利；結構49/55%（結果前已改為只列）；勝方由最後盤口推斷；兩種子近乎相同；費/延遲/份數/capital_cap=null未實盤驗證。機制：兩向V2觀察至UP中價≥0.70或≤0.30才選加倉側→該側V2加倉＋反側配對和≤0.99守門＋最高價堆疊單梯（依價位份額、只拉反側至≥0）＋到期前3秒清理。V12/live未變。

## 2026-09-25 自主方向v2篩檢（擋0.90以上主動加倉）：三臂STOP；結構與實際收益在順勢策略下本質衝突

[CURRENT](../../data/research/btc5m_autodirection_screen_20260925_v2/CURRENT.md)。job btc5m-autodirection-screen-20260925-v2 一次submit、rc0、436秒、自動回收，禁止重送。困難批（CONTROL實際為正26.3/47.4%）。AUTO10_LADDER實際57.9/57.9%結構60/65%；NOHI 52.6/57.9%、結構65/70%；AUTO20_NOHI 47.4/47.4%、贏格平均≈0.05。解讀：結構指標以50/50為基準，順勢於熱門側加倉（p>0.5）每份使UP+DOWN減少2p−1，方向越準結構越難，雙門檻需近乎全配對而收益壓近零。決定：最後一批100場泛化前改登錄畢業為使用者字面標準（實際為正>60%）＋方向對格平均>0，結構照列不作門檻；已完成結果不重評。V12/live未變。

## 2026-09-25 延後自主選方向100場泛化（/goal畢業測試#3）：NOT_GRADUATED（非常接近）

[CURRENT](../../data/research/btc5m_autodirection_generalization_20260925_v1/CURRENT.md)。job btc5m-autodirection-generalization-20260925-v1 一次submit、rc0、1959秒、自動回收，禁止重送。100新場、800 native；905 hash、800指標重算一致。AUTO10_LADDER（主）：離線實際為正61.1%/60.0%、結構58.0%/62.0%、方向對格平均2.86/3.12、方向準確60%；S20 G2不過、S21 G1=60.0%不過→NOT_GRADUATED。AUTO20_LADDER（次，描述）：實際為正62.1%/64.2%（符合使用者字面>60%），結構51/57%。CONTROL固定雙向實際為正36.8/45.8%。延後自主選方向是目前最大進展；弱點為結構（方向對但虧損側>收益側），實際平均約−5.6。下一步診斷結構未達格並小篩檢改善。V12/live未變。

## 2026-09-25 延後自主選方向20場篩檢：AUTO10_LADDER依規則ADVANCE_TO_100

[CURRENT](../../data/research/btc5m_autodirection_screen_20260925_v1/CURRENT.md)。job btc5m-autodirection-screen-20260925-v1 一次submit、rc0、404秒、自動回收，禁止重送。19/20場轉換（2574580拒收不替補）。開盤前兩向V2只觀察，UP中價≥0.5+δ或≤0.5−δ才選加倉側（價格狀態、非固定秒數），選定前無下單（已驗）。AUTO10_LADDER：離線實際為正64.7%/64.7%（CONTROL固定雙向50.0/47.1），結構73.7/78.9%，雙負0，方向準確58.8%，但平均−9.65/−14.66（小雙正多、2556861 −138.2）。AUTO20_LADDER 64.7/58.8%、平均−3.6、方向準確70.6%，結構較像目標但S21差一格STOP。單梯臂17–18/19格兩種子相同，有效樣本約17市場。依規則以AUTO10_LADDER為主判定臂進100場泛化，AUTO20次要描述。V12/live未變。

## 2026-09-25 ROLE_TOP 100場泛化（/goal畢業測試#2）：NOT_GRADUATED

[CURRENT](../../data/research/btc5m_roletop_generalization_20260925_v1/CURRENT.md)。job btc5m-roletop-generalization-20260925-v1 一次submit、rc0、2959秒、自動回收，禁止重送。100新場、1200 native；1305 hash、1200指標重算一致。ROLE_TOP：結構61.0%/60.0%（CONTROL 25.0/42.0），離線實際為正45.7%/44.7%（35.6/47.3），平均−6.25/−6.96（−4.23/−1.06），贏格平均2.24/1.93。G1兩種子不過、G2 S21=60.0%不過、G3過→NOT_GRADUATED。兩次泛化實際為正57%與45%，批次差異大於機制效果。固定雙方向評估有天花板（錯方向格需逆選擇下的便宜反側成交，10–21%）。探索性：60–120秒市場偏向側的格子實際為正56–77%（事後挑格非可執行策略）。下一步實作延後選方向（價格狀態判定偏向後才選加倉側）+角色單梯+清理，小篩檢，每市場×種子一格。V12/live未變。

## 2026-09-25 配對和守門v3 20場篩檢：三臂依規則STOP；ROLE_TOP相對改善一致，推進100場（偏離已揭露）

[CURRENT](../../data/research/btc5m_pairguard_screen_20260925_v3/CURRENT.md)。job btc5m-pairguard-screen-20260925-v3 一次submit、rc0、728秒、自動回收，禁止重送。20新場。實際為正（每種子/38）：CONTROL_CLEANUP 11/18，ROLE 15/14，ROLE_TOP 19/18，ROLE_TOP100 19/18；結構確認ROLE_TOP 24/23（/40），雙負2/3；平均皆差於CONTROL（S21 CONTROL −0.28）。此cohort較難（ROLE 37–40% vs 100場57%）。探索性：逆勢攤平可忽略，輸格虧損來自開局+順勢加倉後反轉；被動修復有逆選擇，保險必壓低平均，與使用者允許犧牲部分正收益相衝突。決定推進ROLE_TOP 100場泛化，並於結果前改G3為「assigned贏格實際平均>0」；已完成泛化不重評。V12/live未變。

## 2026-09-25 角色守門單梯100場泛化（/goal畢業測試）：NOT_GRADUATED

[CURRENT](../../data/research/btc5m_roleguard_generalization_20260925_v1/CURRENT.md)。job btc5m-roleguard-generalization-20260925-v1 一次submit、rc0/stderr0、1861秒、自動回收，禁止重送。100新場、800 native、0fit；905 hash、800指標重算一致。ROLE_GUARD_LADDER vs CONTROL_CLEANUP：結構確認63.0%/65.5%（17.0%/45.5%），雙負7/7（68/37），離線實際為正57.1%/57.7%（41.3%/45.9%），實際平均−4.30/−4.20（−4.27/−3.35）。預登錄畢業：g2過、g1（>60%實際為正）與g3（平均不差於對照）兩種子皆不過→NOT_GRADUATED；10場篩檢67%為高估。探索性：assigned贏格為正82→93%但平均6.49→3.92，輸格5→21%、−14.1→−12.4；輸格成本主要為開局60秒主動（中位25.4），單梯配對上限≈0.49在逆行時不成交。下一步10場篩檢：單梯全掛最高價（ROLE_TOP）、再放寬單梯配對上限至1.00（ROLE_TOP100）。V12/live未變。

## 2026-09-25 配對和守門v2（角色判定）10場小篩檢：兩臂ADVANCE，角色版呈目標式結構，進入100場泛化

[CURRENT](../../data/research/btc5m_pairguard_screen_20260925_v2/CURRENT.md)。job btc5m-pairguard-screen-20260925-v2 一次submit、rc0、263秒、自動回收，禁止重送。ROLE_GUARD_LADDER（assigned買入恆為加倉不擋；反側買入受配對和≤0.99守門；單梯只把反側承諾損益拉至≥0）：結構確認18/20、18/20（CONTROL_CLEANUP 3、8），雙負0，離線實際為正12/18、12/18（8、6），平均−1.46（−2.98/−8.51），峰值88/81，assigned成交中位93（CONTROL 121）。40格：26雙正、14 assigned正/反側小虧、0雙負。揭露：18/20格兩種子相同，V2種子差異多被中和；實際平均仍小負。份數版19/20但加倉降至47。V12/live未變。

## 2026-09-25 配對和守門10場小篩檢：LADDER依規則ADVANCE，但加倉被誤擋，不直接泛化

[CURRENT](../../data/research/btc5m_pairguard_screen_20260925_v1/CURRENT.md)。job btc5m-pairguard-screen-20260925-v1 一次submit、rc0、291秒、自動回收，禁止重送。診斷依據：A2 CONTROL_CLEANUP失敗格配對VWAP和中位1.044/1.098（PASS 0.97）。10新場×2種子×UP/DOWN×3臂。PAIR_GUARD_LADDER：結構確認13/20、13/20（CONTROL_CLEANUP 5、10），雙負0，離線實際為正15/20、15/20（7、9），平均−1.68/−1.28（−3.52/−5.82），峰值資金31。但19/20格兩種子相同：以份額多寡判修復，單梯使反側成多數，V2 assigned側主動加倉被擋3560次，成本中位31、assigned成交32（CONTROL 121），違反開局/加倉積極承擔風險。下一步改角色判定（assigned買入恆為加倉不擋、反側買入才受配對和≤0.99守門）再篩10場。V12/live未變。

## 2026-09-25 預掛修復Stage-A2（到期前清理）：預先登錄規則兩臂PASS；但離線實際結算變差，未晉級

[CURRENT](../../data/research/btc5m_prepost_repair_stageA2_20260925_v1/CURRENT.md)、PROTOCOL.json、SUMMARY.json。唯一job btc5m-prepost-repair-stagea2-20260925-v1 一次submit、rc0/stderr0、3319秒、自動回收，禁止重送。新100場（與父批/Stage-A不重疊）、1050 native、0fit；本機核1255 hash、400格、242觸發臂路徑前綴、242指標重算。剩3秒撤全部掛單/停新單的清理使未確認32→16、26→16。觸發格（對CONTROL_CLEANUP）：依價位份額確認達標13→47、20→41，雙負28→14、11→5，Δ和中位+34/+71，未確認4→4，峰值資金+147/+186；15+150類似；(a)–(d)皆過→PASS候選。全場畢業口徑（確認max>0且和>0）：CONTROL_CLEANUP 22.5%/39.5%→依價位39.5%/50.0%。事後離線實際結算（最後盤口推斷勝方98/100）：為正比例36.2%/43.9%→30.6%/38.8%，平均−4.16/−3.43→−5.95/−5.03，結構改善來自0.02–0.05買便宜反側、未轉成實際收益。畢業須同時看實際結算為正比例。下一步10場小篩檢，以低於1配對鎖真實收益。V12/live未變。

## 2026-09-25 預掛修復Stage-A v2：完成，預先登錄規則下兩臂皆FAIL

[CURRENT](../../data/research/btc5m_prepost_repair_stageA_20260925_v2/CURRENT.md)、PROTOCOL.json、SUMMARY.json。v1 job在首個觸發格因wrapper逐決策加evidence標記被凍結前綴檢查攔下（失敗現場手動回收，未重送）；v2只修此點＋測試。v2 job btc5m-prepost-repair-stagea-20260925-v2 preflight PASS、一次submit、rc0/stderr0、2268秒、自動回收，禁止重送。9/22–24未用100場（34/33/33），100轉換、664 native、0fit；本機核869 hash、400格完整、128觸發格256臂路徑觸發前前綴一致、256/256指標重算。觸發格S20/S21：依價位份額 帳面達標19→53、28→42，雙負25→17、6→4，Δ和中位+61、+74；但確認達標13→14（12改/11退）、21→20（10/11），未確認19→51、8→30。15+150類似（S21確認21→13）。規則(b)(c)通過，(d)兩種子皆不過、(a)S21不過→FAIL。另：加倉份數中位+43～+255、峰值資金+110～+299，正側保留中位0.35–0.78。探索性：未確認多為到期時仍掛單（約50條V2自身、18條僅預掛），資料流到期後約0.55秒結束而撤單往返約1.37秒，掛到到期的單在此模擬無法取得終態。下一步建議到期前按量測延遲清理所有掛單＋資金需求揭露，新cohort另行凍結。V12/live未變。

## 2026-09-25 高價加倉同時預掛修復smoke V1：完成，未晉級

[CURRENT](../../data/research/btc5m_postrepair_prepost_repair_smoke_20260925_v1/CURRENT.md)、PROTOCOL.json、VERIFICATION.json。唯一job btc5m-postrepair-prepost-repair-smoke-20260925-v1 preflight PASS（worker13測試）、一次submit、rc0/stderr0、40.5秒、自動回收，禁止重送。9新native＋3 CONTROL重用、0fit；世界變體只放寬被動非15份額（票額≥$1/post-only/自成交守衛不變），舊引擎不改。本機核16 hash、9前綴、2573871三臂逐事件同CONTROL、12/12指標重算。UP/DOWN：2498712 CONTROL −211.00/+11.72，15份同CONTROL（0.07單EXPIRED、後續14次票額不合法→支持份額擋修復），15+70 −75.41/+8.34⚠，依價位份額+8.14/+8.14（配對和0.997、支出45.92、峰值67）；2495998 CONTROL −196.26/+9.26，15份−4.10/−4.09⚠，15+70 +13.36/−1.65⚠，依價位+82.59/−2.42（配對0.993、峰值240）。⚠＝V2自身0.96–0.97被動加倉到期才撤、資料流結束前無終態，owner未釋放、僅帳面。揭露：掛著的修復單經自成交規則把後續加倉價限於<1−修復價，依價位臂加倉量219→46、420→118。3根1種子已消費，機制smoke。下一步Stage-A新根雙種子比較依價位份額與目標式固定兩檔（如15/150），另行凍結。V12/live未變。

## 2026-09-25 目標高價加倉→修復跟隨：唯讀觀察完成（離線，0 native/fit）

[CURRENT](../../data/research/btc5m_target_add_repair_response_scan_20260925_v1/CURRENT.md)、SUMMARY.json、scan.py。目標逐筆成交在data/target_wallet_official_v1.db wallet_shadow_target_events（原交接漏寫）。個案2571839：DOWN 0.90–0.93與UP 0.03–0.06各一組150份梯形被動單，L2顯示加倉梯約成交前7秒掛出、修復單在加倉成交時已在簿；第二段配對和0.950（鎖正收益），第一段1.054。跨幣種17,991場48,530段（主數字乾淨/未截斷/跌≥5）：BTC 9,500段30秒內有修復80.8%、段內即開始32.8%、首修中位6秒、修復/加倉份數1.12、回收中位0.45、配對和中位0.987且55.6%<1、修復VWAP 0.129、幾乎全被動；ETH 65.3%/0.55/1.015；BNB 2.8%（5份×$1票額最低≈0.2，疑無法便宜修復）。BTC≥0.90加倉主動:被動≈1:9，≥0.90時有修復僅56%但修復時配對和78%<1。被動份數隨價位≈1/p（.06→18,.05→20,.04→30,.03→35–40,.02→55；另55/70/150），BTC 8月18份→9月15份，ETH10、BNB5。含意（假說）：修復多與加倉同時預掛、便宜被動為主；份數應依價位，現行15份擋住0.02–0.06修復。三個崩盤根點無目標資料。winner未讀；V12/live未變。

## 2026-09-25 修復後ADD授權smoke V1：本批完成，未晉級

[CURRENT](../../data/research/btc5m_postrepair_add_authority_smoke_20260925_v1/CURRENT.md)、[報告](../../data/research/btc5m_postrepair_add_authority_smoke_20260925_v1/REPORT_ZH.md)、[協議](../../data/research/btc5m_postrepair_add_authority_smoke_20260925_v1/PROTOCOL_ZH.md)。唯一job btc5m-postrepair-add-authority-smoke-20260925-v1 preflight PASS（worker13測試）、一次submit、rc0/stderr0、程式23.6秒、自動回收，禁止重送。6新native＋3 CONTROL hash重用、0fit。本機核13 artifact hash、6條根前前綴、assigned NEW與閘門放行1:1、9/9指標重算一致；2573871 SUM_MARGIN與CONTROL逐事件相同（確定性）。3根S21已消費指定方向，UP/DOWN：2498712 CONTROL −211.00/+11.72、SUM −8.48/+8.48（和0.004）、FLOOR +0.01/+8.23；2495998 CONTROL −196.26/+9.26、兩臂+1.57/−1.05（和0.52，FLOOR地板被CANCEL_PENDING拉低未作用，95次TICKET擋）；2573871 CONTROL −107.23/+610.79＝SUM，FLOOR −18.36/+34.49（ADD 690→11.25份）。閘門避開崩盤尾部、資金占用降至1/3～1/8，但SUM用盡總和餘裕至近零（V6不穩健），FLOOR壓掉便宜擴張；皆未晉級，不直接Stage-A。與9/22 r118（事件額度授權，REJECT）不同：本批無credit/持續性；r118所指候選形成前介入仍未做。下一步：只約束降低總和的ADD且以保留收益方式約束、放行提高總和的便宜ADD、投影不依賴撤單中在途單，先確認不構成固定比例再凍結。V12/live未變。

## 2026-09-25 修復後ADD episode掃描：唯讀完成，候選已凍結

[CURRENT](../../data/research/btc5m_postrepair_add_episode_scan_20260925_v1/CURRENT.md)、[CANDIDATES](../../data/research/btc5m_postrepair_add_episode_scan_20260925_v1/CANDIDATES.json)、[本機重現](../../data/research/btc5m_postrepair_add_episode_scan_20260925_v1/LOCAL_REPRODUCTION.json)。唯一job btc5m-postrepair-add-episode-scan-20260925-v1（read-only/no-native/no-fit，CURRENT記載rc0/stderr0、87.218秒；回收目錄只有SCAN.json，rc/耗時未本機重核），禁止重送。掃父批396已消費trace：反側成交後至少2筆assigned側成交者244/396，是幾何篩選，非成功率、非244獨立市場。本機以同邏輯重算396/244、top20與3候選欄位全一致。選根未用終局PnL/winner：2498712/S21/DOWN（交接反例，15筆0.97→0.99）、2495998/S21/DOWN（20筆0.97→0.91）、2573871/S21/DOWN（47筆0.20→0.12）。注意assigned側買入不一定是經濟上的ADD（2495998根點assigned側為負）。0 native/HFT/fit，V12/live未變。

## 2026-09-25 到期接收流修正：3市場小測完成，策略仍NO_GO

[CURRENT](../../data/research/btc5m_prelive_execution_gate_20260925_v1/CURRENT.md)、[報告](../../data/research/btc5m_prelive_execution_gate_20260925_v1/REPORT_ZH.md)。唯一job btc5m-prelive-execution-gate-20260925-v1 成功自動回收rc0/stderr0、worker idle，禁止重送。22元件測試雙環境PASS；3已消費市場×兩種子×UP/DOWN=12條，8新native/4輸入不變重用。只補真實到期後LOCAL depth，不增撮合/成交/terminal；2577422與2577655共8條由未到期變確認到期且責任結清；2539607缺raw尾端4條仍未確認。主核17結果hash/10候選hash/12基準trace，12/12完整receipt、events、payoff及既有觀測前綴完全相同。KEEP_BOUNDED_EXPIRY_COMPONENT，非PnL改善或實盤資格；第三cutoff市場2579312未測，舊100市場封存不改。0fit/V12/live/份額/方向/資金設定變更。下一必要工作為修回再ADD的完整續行、收益保留與風險/部署資金需求；實單額度、venue/API/斷線對帳未驗證，NO_GO。

## 2026-09-25 100市場上線前泛化：固定批次完成，NO_GO

[CURRENT](../../data/research/btc5m_prelive_generalization_20260925_v1/CURRENT.md)、[報告](../../data/research/btc5m_prelive_generalization_20260925_v1/REPORT_ZH.md)。唯一job `btc5m-prelive-generalization-20260925-v1` accepted一次、rc0/stderr0、native1071.190秒（launcher1076.191）、自動回收且workeridle，禁止重送。100市場檢查、99市場396路徑、368確認到期；1receive倒置拒收，7市場28路徑到期未確認，分母每配置仍100。20UP/DOWN、21UP/DOWN帳面正收益>虧損27/24/45/50%，到期且責任全清21/20/38/46%；固定雙方向皆確認4/100、19/100。每配置僅8/100條件式210套用，非普遍大倉驗證。2498712DOWN/21約4分鐘+8.96/−5.55再ADD至+11.72/−211.00且owner0，修回後擴張仍失控。主396trace/599hash/6716receipt/12007owner列次核對PASS；無新fit/V12/live變更。費/尺寸/延遲/資金/自主方向/實盤回報尚未驗证，NO_GO。本批已消費，下一主線資料與cutoff到期完整性、開局覆蓋和資金約束完整續行教材，再用新時段驗收；不自動重訓、重送或部署。前批金額尺寸18新增+27重用已封存未晉級；舊EOF零確認另以新包PRIOR_ENDPOINT_AUDIT修正分層，舊檔不改。

## 2026-09-25 掛單維護V2：9根四臂完成，新增正收益大於虧損百分比

[CURRENT](../../data/research/btc5m_order_maintenance_screen_20260924_v2/CURRENT.md)、[報告](../../data/research/btc5m_order_maintenance_screen_20260924_v2/REPORT_ZH.md)。唯一job `btc5m-order-maintenance-screen-20260924-v2` 18新native＋18重用、73.780秒（launcher78.657）、rc0/stderr0、回收核對workeridle，禁止重送。9根來自當時F正/L負且單張15份repair owner，7已消費市場、5train/4dev。原策略/只撤單/撤後調價/撤後主動的帳面正收益大於虧損（含雙正）為5/9=55.6%、6/9=66.7%、5/9=55.6%、3/9=33.3%；開發2/4、3/4、3/4、2/4。達標且責任清除4/9、5/9、3/9、2/9；加非EOF且到期嚴格確認皆0/9，不可稱可靠成功率。新overlay保留parent NEW，等canonical terminal扣在途成交後最多補一單；調價送7/9、主動8/9。2019018主動+76.23/−69.57→+65.43/−65.48失去比例優勢，2021315主動退双負，不能固定主動升級。主18前綴/36列帳務責任核對與限定獨立覆核完成，10機制測試PASS，0fit/V12/live變更。前36組補算兩臂均16/36=44.4%，不可與9根直接比泛化。下一主線研究當前repair金額尺寸與完整ADD續行價值，本固定批次結束。

## 2026-09-24 掛單需求與KEEP/CANCEL：36組配對完成，教材整理未fit

[CURRENT](../../data/research/btc5m_cancel_keep_paired_20260924_v1/CURRENT.md)、[報告](../../data/research/btc5m_cancel_keep_paired_20260924_v1/REPORT_ZH.md)。唯一job `btc5m-cancel-keep-paired-20260924-v1` 36新native＋36hash重用、153.501秒、rc0/stderr0、回收核對workeridle，禁止重送。18早期KEEP為1改善/4退步/2取捨/11同；15補回CANCEL為1/1/6/7；3尾盤為0/1/1/1。2021315DOWN/21 +0.99/−2.28→+16.41/+17.33、owner1→0但風險面積增加；2021302DOWN尾盤損益不變、owner4→0、reserved57.90→0。174舊trace只3筆尾盤窗前maker回報且單齡<4.2秒，未證實中段掛單晚成交主因；529筆撤單在途回報不等於終態後成交。主76hash/36前綴/1598receipt/3677責任列次PASS，獨立2根4trace一致。36根72候選標籤已準備（24train/12dev、1缺側報價null），0fit；9已消費市場指定方向診斷，非自主方向。下一主線補同根掛單需求與調價/主動repair/後續ADD完整續行再fit；V12/live/舊封存不變，本批完成。

## 2026-09-24 首批開局60／210與完整修復：回收及主核對完成，未fit／未晉級

[CURRENT](../../data/research/btc5m_opening_size_screen_20260924_v1/CURRENT.md)、[結論](../../data/research/btc5m_opening_size_screen_20260924_v1/CONCLUSION_ZH.md)、[報告](../../data/research/btc5m_opening_size_screen_20260924_v1/REPORT_ZH.md)。唯一job `btc5m-opening-size-screen-20260924-v1` accepted一次、102新增native＋114hash重用（72基準／42top-depth不足），固定216格；357.995秒、rc0/stderr0、自動回收、workeridle，禁止重送。被動4／14張各15份、主動單張60／210；首批同根干預，之後真實交接凍結V2。2032652 DOWN主動210早期+105/−100.8，20終局+46.65/−51.01；21續ADD峰值+141.07/−138終局+71.02/−52.85，owners0。但2021342 UP/21峰值+123.58/−127.10終局−9.55/−9.89。開發主動210各僅2/6放大，平均F增加但L更負；被動210開發12條首批168票全零成交，140CANCELED/28EXPIRED，12條送後1秒提案撤單，其中10條送後2秒實際CANCEL，不能稱大被動持倉修復成功；21/210開發仍2owner。219hash／102前綴／3606receipt／7752責任列次主核對PASS。下一份教師用實際大倉同根比較續ADD、主被動repair尺寸及掛單維護，再fit；不固定全市場210或禁撤單。0fit，V12/live/舊封存未變。

## 2026-09-24 首分鐘持倉規模：既有72路徑核對完成，未新fit

[CURRENT](../../data/research/btc5m_opening_minute60_20260924_v1/CURRENT.md)、[報告](../../data/research/btc5m_opening_minute60_20260924_v1/REPORT_ZH.md)。固定開始後60秒已處理receipt含費：9市場雙方向平均，被動20 F/L +11.43/−10.73、主動20 +8.62/−8.76，被動21 +18.92/−12.64、主動21 +16.58/−15.53。首分鐘記錄checkpoint同時F≥100/L≤−100為0/72；少數被動+124.19/−68.05、主動+97.79/−65.70保留。使用者希望積極首分鐘擴張，V12±100是USER_REPORTED參考，尚未同引擎對齊，不新增硬性虧損獎勵。72trace/74來源hash/557回報核對PASS，2路徑原生receive_ts與處理時刻跨60秒另存；獨立4trace一致。0新native/fit、舊封存包不變，不重送已完成job。下一教材共同學開局金額尺寸、持續ADD及並行主被動repair時機；目前只補回路線，未學到穩定較大開局。

## 2026-09-24 空倉開局路線／因果交接V2r1：回收與核對完成，未fit／未晉級

[CURRENT](../../data/research/btc5m_opening_route_handoff_20260924_v2r1/CURRENT.md)、[報告](../../data/research/btc5m_opening_route_handoff_20260924_v2r1/REPORT_ZH.md)。唯一job `btc5m-opening-route-handoff-20260924-v2r1` accepted一次、72全場native、程式212.651秒、rc0/stderr0/auto-collected/workeridle，禁止重送。雙路線同原提案量，首次當場相反側修復意圖＋實際F>0/L<0綁新anchor交凍結V2，無原30秒起點資訊回灌；9已消費市場×雙方向×兩種子，0fit。開發主動開局成交0/6→6/6，但交接F份額52.5→32.5、F25.925→15.262；20終局F/L−1.119/−8.642→+2.299/−6.225，21+6.553/−7.406→+2.662/−5.076，未見穩定較大開局與收益優勢。2032652DOWN同15份均價.55→.48、費後F/L+6.75/−8.25→+7.50/−7.20；其他回撤路徑有更早買卻更貴反例。64交接/8缺失全保留，開發owners0、訓練2/4對2/2。主76hash/1226receipt/2479carrier/36被動前綴PASS，獨立72格/64actual anchor/8未交接與接管前無V1/V2列PASS（未重算feature hash）。下一步真實開局同根續ADD／並行repair／交接與合法尺寸教材，不直接全改主動或擴倉。原v2未submit，同名測試遮蔽修正r1本包9測試來源hashPASS；舊stage保留。V12/live未變，本批停止。

## 2026-09-24 開局擴張納入完整循環：要求更新與覆核完成，尚未新fit

[CURRENT](../../data/research/btc5m_opening_full_cycle_20260924_v1/CURRENT.md)、[契約](../../data/research/btc5m_opening_full_cycle_20260924_v1/CONTRACT.md)、[報告](../../data/research/btc5m_opening_full_cycle_20260924_v1/REPORT_ZH.md)。使用者要求加回開局承擔風險的擴張；主線改為空倉開局主動／被動ADD＋可並行的主動／被動repair＋再ADD完整評估。54既有路徑（18R115+36V2）、56來源hash及raw成交費用／兩側收益核對PASS；原開局前綴保留但18開局F側ACTIVE送單／成交皆0，5個有另一側ACTIVE成交，8/18 Fgross≤15，F僅+0.3854至+28.05。17anchor30.5秒、1個111.5秒是舊抽樣窗而非開局完成，不能拿原終局宣稱完整主動擴張能力。舊終局已含開局支出，非帳務漏算。下一實作先補可執行開局候選與實際成交交接，再做金額尺寸與repair完整續行；不直接續訓固定15份票，也不永久因票未滿禁止ADD。0新native/fit/dispatch/live；新policy尚未接入，V12未改且未稱V12續訓。下方V3成果不覆寫、不重送。

## 2026-09-24 OUR持續修復／調價／主動升級V3：機制比較完成，未fit

[CURRENT](../../data/research/btc5m_receipt_reprice_screen_20260924_v3/CURRENT.md)、[報告](../../data/research/btc5m_receipt_reprice_screen_20260924_v3/REPORT_ZH.md)。唯一job `btc5m-receipt-reprice-screen-20260924-v3` rc0/stderr0成功回收、workeridle，禁止重送。47因果起點（17blocked/30passive）×6臂282結果，235新native+47hash重用，程式789.654秒，零fit。有限gross修復票持續追蹤至receipt與canonical終態，KEEP42/47完成、REPRICE/ACTIVE43/47、快慢階梯46/47；最終基本原V2 14/47，各臂15/15/16/16/14，重疊根非獨立市場。開發20/21原1/7、1/9，KEEP與ACTIVE皆3/7、3/9，REPRICE2/7、3/9，FAST2/7、3/9，SLOW3/7、2/9；未證明固定升級更好。2032652DOWN/20同根KEEP票收尾+0.15/+0.15，ACTIVE因價格/費用0/−0.30；FAST殘0.0039份、所有owner已terminal，任務未完成不等於訂單未結。開發16根ACTIVE/FAST各9根票成本可耗盡rootF（加法歸因非反事實）。主239hash/5391receipt/11722carrier核對列次、獨立235前綴/466repairNEW與36父tracehash均PASS；220票滿量收尾，15未滿任務保留。次輪應先補F/L金額與合法剩餘數量比較及後續ADD，再訓練選擇器；不固定未成交秒數規則。原9市场已消費、無純WAIT消融，V1/V2/V12/live不變，本批停。

## 2026-09-24 OUR訪問狀態／兩步協調續訓V2：完成，退步，未晉級

[CURRENT](../../data/research/btc5m_visited_two_step_coordination_20260924_v2/CURRENT.md)、[報告](../../data/research/btc5m_visited_two_step_coordination_20260924_v2/REPORT_ZH.md)。唯一job `btc5m-visited-two-step-coordination-20260924-v2` 成功回收rc0/stderr0、worker全局idle，禁止重送。42根/334教材，275新教師+59重用，兩LIFECYCLE同種子權重/Adam800續訓各800，原參數1600/殘差800，freeze後36閉環，共311新增native、程式872.975秒。開發基本修復V1的2/6、3/6均退1/6，完整0/6；主被動11/16→17/12、28/28→54/68，支出114.54→152.45、271.12→639.82，平均L改善6.09→7.13、7.39→−4.56。開發0未結，訓練1/3owner未結。局部2021315DOWN/20 F/L+18.07/−8.98→+1.32/−1.84；2032652DOWN/21四次連續ADD_ACTIVE_REPAIR第二步因PENDING_SELF_CROSS等待，下一方案又ADD，L−12.05至−60.95，終點+5.94/−25.10，責任保護正確但相鄰兩決策不保證repair完成。主322hash/5093receipt及11520carrier核對列次PASS；獨立334教材/36評估前綴、35459候選特徵及5055兩步紀錄PASS（不另跑native合法性）。原9市場均已消費，非自主方向學習；V1/V12/live不變。下一步先做receipt/責任終態推進的修復續行比較，保留未完成repair狀態，處理凍結teacher與反覆新策略的差異；本批已停，未自動調參或續批。

## 2026-09-24 OUR主被動生命週期協調訓練V1：完成，未晉級

[CURRENT](../../data/research/btc5m_lifecycle_coordination_training_20260924_v1/CURRENT.md)、[報告](../../data/research/btc5m_lifecycle_coordination_training_20260924_v1/REPORT_ZH.md)。唯一job `btc5m-lifecycle-coordination-training-20260924-v1` 第二台成功回收356.148秒、rc0/stderr0、全局idle，禁止重送。31根183教材（原105核對重用；175有效=105訓練/70開發、8未執行CANCEL排除fit）；56新教師native、四模型各800更新、freeze後72閉環native，共128。STATE_ONLY vs加36OUR歷史LIFECYCLE各兩種子；開發3已消費市場×雙方向，CONTROL基本2/6完整1/6；STATE_ONLY基本2/6、2/6；LIFECYCLE2/6、3/6，壓光F由4/6降3/6、2/6，但完整皆0/6、平均L改善6.09/7.39低於控制8.92，原雙正2032652 DOWN退一正一負。LIFECYCLE99.3%/93.5%決策仍CONTROL；開發0未結，訓練閉環共4owner未結保留。主140hash/1644receipt/3452carrier核對、獨立273前綴/17932候選特徵重建PASS。新critic非V12續訓；Target50僅診斷，capital_cap=null、V12/live不變。下一步模型實際偏離狀態（修回再擴張/負側再失守/到期責任）的連續動作完整續行教材；本批已停，未自動續跑。

## 2026-09-24 被動修復空窗／主動升級假說：固定50場完成

[CURRENT](../../data/research/btc5m_passive_repair_escalation_20260924_v1/CURRENT.md)、[報告及圖](../../data/research/btc5m_passive_repair_escalation_20260924_v1/REPORT_ZH.md)。依使用者要求拆開formation/ADD/普通repair/crossing/mixed，固定前述50市場24,208成交/6,247秒批；49tape只讀book、0native。F>0/L<0風險秒11,151，普通主動修復1,146/被動1,568。主動高16對低16場，被動中位37.5/24.5批；風險分鐘校正率Spearman+.563[.337,.725]，未支持市場間反向頻率。前5秒同L方向持續需求：无被動修復成交下一秒主動471/5715=8.24%，有成交533/4243=12.56%；BASE MH .923[.783,1.076]，加強側流量/ask後1.005[.827,1.203]（6494可比較秒），1/10秒敏感度也未明確高於1。整市場bootstrap2000次、稀疏層/未知私人掛單保留；不能否定未成交張數閾值，但公開空窗代理未支持，未加規則。29/1146普通repair的隔離TAKER作用使原F非正，非正例保證。主55來源hash/全legs/1142舊嚴格過去ask對照核對，獨立重建11151主樣本/5秒表/MH/相關一致。下一步OUR已知掛單生命週期下固定延遲/部分成交情境，同根比較WAIT/維護被動/主動修復完整價值；先查重旧escalation工具。0policy fit/HFT/worker/live，V12不變，未自動續批。

## 2026-09-24 固定50場Target路徑、被動觸發與主動ADD／repair比對

[CURRENT](../../data/research/btc5m_target50_path_compare_20260924_v1/CURRENT.md)、[完整報告](../../data/research/btc5m_target50_path_compare_20260924_v1/FINAL_REPORT_ZH.md)、[驗證](../../data/research/btc5m_target50_path_compare_20260924_v1/FINAL_VALIDATION.json)。固定22雙正／22混合／6雙負同批50市場，直接重建24,208 Target成交legs、6,247時間批與49場完整tape。前5秒MAKER有無的下一秒TAKER率22.91%對8.72%，但控制市場/分鐘/最近TAKER後MH OR 1.257，不能硬作單一觸發。TAKER decisive repair占58.2%；ADD前同側MAKER流占優54.0%，repair前反側占優48.9%。完整tape下ADD側僅50.4%為市場價格較高側；repair 60.4%在弱側ask<0.50。雙正/混合/雙負repair ask中位0.415/0.430/0.480；雙負總legs、成本及ADD/repair更多，偏向昂貴循環而非repair不足。反轉穿越中位7/4/5且50/50有ADD→repair→ADD，反轉或循環存在本身不分好壞。下一步同根ADD/repair/WAIT續行價值；winner不入runtime。0HFT/fit/worker/live，V12不變。

## 2026-09-24 凍結50場Target結算詳報與教材結構

[CURRENT](../../data/research/btc5m_recent50_settlement_detail_20260924_v1/CURRENT.md)、[50場逐場報告](../../data/research/btc5m_recent50_settlement_detail_20260924_v1/REPORT_ZH.md)。固定使用者所指22雙正/22一正一負/6雙負的同批50ID，逐筆由target_market_results重讀並核份額/成本，非HFT。雙正22作終局主教材：強側Q/C中位1.0896（IQR1.0428–1.1162）、弱側1.0322（1.0174–1.0868）、份額比1.0194；一正一負作repair過渡/未完成對照，強/弱1.0616/0.9454，實際勝方代理正13/22；雙負6作反例，雖份額比1.0146仍因成本兩側負。全50勝方代理正35/50。學兩側相對共同成本覆蓋與owners，不學單純1:1或winner。DB回填中，生成時當下最新50另為11/30/9且ID不同，不混批。未顯式計費/私人期初pending未知。50列、摘要與漂移核對封存；0HFT/fit/live，未啟動訓練。

## 2026-09-24 Target結算來源與最近場次口徑釐清

[CURRENT](../../data/research/btc5m_target_settlement_provenance_20260924_v1/CURRENT.md)、[報告](../../data/research/btc5m_target_settlement_provenance_20260924_v1/REPORT_ZH.md)。前輪12場為真實Target已記錄成交加已匹配份額費，實際勝方由target_market_results核對，兩側收益仍是互斥的條件代理，非HFT、非完整私人錢包PnL。12場9月7日早上勝方代理正10/12、3:1為0；同日午後已消費50場未顯式計費代理3/50勝方3:1、6/50雙正。資料庫最後結算9月16日23:35臺北時間；固定最後50場9月16日19:30–23:35，勝方3:1為0/50但22/50雙正、勝方代理正34/50。不能以12場概括所有「最近」、兩側風險分支不是同時已實現輸贏、最後50場距本日約一週，費用/私人期初與pending未知。唯讀摘要查詢及勝方欄位核對，0HFT/fit/live；最後50場結果摘要已消費，不得當未見驗收。

## 2026-09-24 代表性終局教材：12費後＋50代理診斷完成

[CURRENT](../../data/research/btc5m_teacher_typicality_20260924_v1/CURRENT.md)、[報告及圖](../../data/research/btc5m_teacher_typicality_20260924_v1/REPORT_ZH.md)。已消費NET12與未顯式計費PROXY50分開、無重疊。三尺度全局代表各為2019143/2022527；NET12代表留一11/11（自身存在）穩定，固定k群成員跨尺度一致；PROXY50群界不穩，不認定固定策略族。終局最近鄰与完整收益路徑最近鄰僅3/12一致，event/receipt同。2019143在214秒主動修復以正支2.74換負支改善41.97；166秒固定根終點保留96.3%、改善75.07，仍部分修復、私有owners未知。優先候選2019143＋2019018對照，保留2018666/2018854修回失守與2019008反轉失敗；未把整場動作全標正例。50場三代表tape本機hash核對，但未解碼/費用未補。主核18分群、24市場時鐘帳本/根歸因、18來源hash及獨立原始重算一致，62場表/2圖封存。下一步對齊代表場OUR可重放根並查重已有續行，再固定主動/被動/WAIT完整HFT比較。0fit/native/HFT/worker/live，V12不變，未自動續批。

## 2026-09-24 HFT批次能力確認；代表性終局教材方案

[CURRENT](../../data/research/btc5m_hft_acceleration_feasibility_20260924_v1/CURRENT.md)、[報告](../../data/research/btc5m_hft_acceleration_feasibility_20260924_v1/REPORT_ZH.md)、[教材方案](../../data/research/btc5m_hft_acceleration_feasibility_20260924_v1/TEACHER_SELECTION_PROPOSAL.md)。唯讀核對原生loop/批次快取/官方文件及實測收件：R88三批45/54/64新native約75/87/102秒，單市場參數情境非獨立市場；bundle11新+7重用約446秒。完整HFT可加速批次，未證明微縮世界等級吞吐或任意狀態快照分岔；官方近似加速省略queue/partial/response，不作主被動協調主要裁判。使用者提出終局結構平均相似度高者作核心教材：先分常見金額結構群、選真實代表，再核四路線完整循環；離群保留作執行/反轉對照，驗收自然分布且不按終局挑選。方案未選樣/未凍結門檻，現有12已消費場不足代表全體。下一產物是覆蓋盤點、結構表及代表路徑；HFT新策略先小型吞吐/一致性核對。0新fit/native/worker/live，V12未改，未自動續訓；歷史計時非新性能測試。

## 2026-09-24 主動成交使用價值調查完成

[CURRENT](../../data/research/btc5m_active_use_diagnosis_20260924_v1/CURRENT.md)、[報告](../../data/research/btc5m_active_use_diagnosis_20260924_v1/REPORT_ZH.md)。108條既有模型dev路徑/12個Target市場唯讀調查。Target主動372/2263列=16.4%、份額21.1%、金額18.7%，私人期初/pending未知；非不同cohort績效勝負。ACCOUNTING21已有399/63主被動NEW、321/43FILL，161/303相鄰主動間距≤5秒、188換側。失敗案例先兩筆主動+6.15/−8.85→−7.35/+7.05→+5.3786/−9.75，兩側退步；整場−52.6547/+84.6297還含過量未配對曝險，非單純費用。教師pending自有被動单直接WAIT，缺等待期維護/另一側修復；學生程序狀態與固定15/30尺寸、CE抽樣主動27–29%、ask-cross被動近似皆需查，未完成因果消融。獨立72條V4及權重抽樣/FIFO/程式核對PASS，主代理核12Target來源與原V4封存328檔不變。下一步先補被動維護與同狀態主動/被動/WAIT完整續行價值，OUR記憶只用自身過去動作/回報；不加任意主動比例/冷卻門檻，不再盲加epochs。0fit/inference/replay/worker/native/HFT/live、V12未改，未自動續訓。

## 2026-09-24 候選修復會計輸入 V4 完成，未晉級

[CURRENT](../../data/research/btc5m_candidate_accounting_repair_20260924_v4/CURRENT.md)、[報告](../../data/research/btc5m_candidate_accounting_repair_20260924_v4/REPORT_ZH.md)。第二台唯一 job btc5m-candidate-accounting-repair-20260924-v4 成功回收、終態 idle，57.502 秒。V6允許部分F犧牲；同容量 ACTION_ONLY vs ACCOUNTING 兩種子各1200更新，共4800；父Adam5472→6672，新residual1200。重用276 selected／2248 preferences／22356完整程序，51852派生列，四模型freeze後324評估。基本合格兩臂均5/18、6/18；ACCOUNTING完整修復2、4對照2、2，但2021342 DOWN FAST種子21修成−52.6547/+84.6297，原F根+6.15，嚴重退步；同根SLOW成功+14.8825/+2.4615。未結責任5/18、6/18。13小測試、324評估/16633回報/291 hashes主稽核、15項獨立派生稽核及實際權重讀回PASS。相同可見輸入不同標籤33/38組，不能據此斷言教材錯誤或失敗因果。下一主線為同可見狀態多動作完整續行價值，協調修復量/pending/再加倉。三已消費開發市場非新驗收、0native/HFT/V12/live、capital_cap=null。已停止固定批，禁止重送，未自動開下一批。

## 2026-09-24 V6允許收益犧牲；V3r1續訓與獨立稽核完成，未晉級

[V6契約](../../data/research/btc5m_positive_gain_contract_20260924_v6/CONTRACT.md)、[CURRENT](../../data/research/btc5m_positive_gain_repair_20260924_v3r1/CURRENT.md)、[報告](../../data/research/btc5m_positive_gain_repair_20260924_v3r1/REPORT_ZH.md)。使用者允許F下降但不得壓光；基本F>0、L改善、owners0，F不減只作強保留，無50%/3:1門檻。V2新標準6/18、4/18；WAIT3/18為既有單成交。V3漏return於4.101秒/0fit失敗已保留，r1限定修復後224.926秒成功收回、worker idle。276情境/22356完整程序，四fit各1200；ROOT對VISITED兩種子基本5/18、6/18對4/18、6/18，通過數無一致增加，但VISITED合格減虧中位約71%，F非正5/18、2/18對ROOT6/18、6/18；仍pending5/18、6/18，2021342無合格。216評估/7807回報/289hash主核對與權重讀回PASS，獨立教材稽核13項PASS（22356程序/2248唯一偏好對/360根與截點/四checkpoint）。九既有市場、三開發已消費；非native或新泛化，world/Input不變、capital_cap=null、0V12/live/HFT。下一主線是費後候選修復量與pending責任的完整循環教學；未晉級、未開下一批，禁止重送。

## 2026-09-24 完整微縮修復示範 V2 完成：少數保留收益修復，未晉級

[CURRENT](../../data/research/btc5m_microworld_repair_demonstrations_20260924_v2/CURRENT.md)、[報告](../../data/research/btc5m_microworld_repair_demonstrations_20260924_v2/REPORT_ZH.md)。第二台唯一 job 成功收回、idle，66.356 秒完成 72 情境 × 65 程序 = 4,680 完整成功／失敗軌跡及四次續訓共 4,800 更新；V1 權重與 Adam 3072→4272 已讀回。65/72 示範覆蓋是離線 hindsight，不是學生成功率。固定開發 18 情境（3 已消費市場 × 兩根方向 × 三合成執行情境）：UNIFORM 兩種子皆 0 保留修復，BALANCED 為 1/18、2/18；正收益下降 15/18、12/18，未結責任 4/18、6/18，均 UNKNOWN 壓力。三個成功均只是部分减虧，不能稱每次再加倉後都修回。主稽核 216 評估／4007 回報及 hash PASS，獨立稽核 4680 軌跡／182 偏好／split／四 checkpoint PASS。world/Input 同 V1、capital_cap=null、無 native/HFT/V12/live 變更；非新泛化、非 V12 續訓。原 expansion_segments 截斷於下次 F fill，另存跨後續 fill 的重疊基準診斷。下一主線為學生偏離狀態的同根多步修復教材；不再加相同 epochs 或下單獎勵。本批未晉級、未自動開下一批，禁止重送。

## 2026-09-24 連續微縮修復策略訓練 V1 完成：快速訓練成立，策略停手

[CURRENT](../../data/research/btc5m_microworld_repair_cycle_20260924_v1/CURRENT.md)、[完整報告](../../data/research/btc5m_microworld_repair_cycle_20260924_v1/REPORT_ZH.md)。第二台唯一job成功收回，112.369秒完成兩種子524,288步／6,144梯度更新／1,927完整循環；權重optimizer已讀回核對。新初始化38因果特徵PPO、秒級市場300秒循環、11種主動／被動兩側與WAIT/CANCEL，三種明列合成回報／成交情境、capital_cap=null。兩模型固定開發18案例皆0收益保留修復，終點與WAIT完全相同；種子20全部WAIT，種子21僅訓練市場三次CANCEL，皆無NEW。訓練探索27/965、34/962成功不能當最終能力。324案例44,099事件帳務／owner核對PASS，全部基線與退步保留。既有九市場／18根，六訓練三開發；微縮假設非native、非新泛化、非V12續訓；V12/live未改、worker idle。下一主線改同根多步修復示範與失敗對照，完整保存序列及各段擴張基準，不再增加同套獎勵訓練量或任意下單獎勵；本批未晉級，未自動開下一輪。

## 2026-09-24 修復元件首次訓練完成：V2未晉級

[CURRENT](../../data/research/btc5m_repair_root_retention_training_20260924_v2/CURRENT.md)、[完整報告](../../data/research/btc5m_repair_root_retention_training_20260924_v2/REPORT_ZH.md)。使用者已授權開始訓練。第二台完成71條新增native同根續行＋34重用，九個已消費市場18根105行，含主動／被動兩側候選；V1r2兩種子各600更新，發現相對CONTROL標籤不等於收益保留後，V2凍結改為root-relative、重用同教材再各600更新，V2為0新native。最終兩種子檢查皆0/6保住root正收益且負側改善，6/6收益下降、1/6雙正另列，比CONTROL兩側各差−2.10；訓練3/12、2/12，後者另有1未執行CANCEL誤選。三檢查市場已参与診斷，非未見驗收。模型是新初始化critic，精確V12checkpoint仍未唯一對齊，不能稱V12續訓；固定R115後續控制器限制保留，尚無閉環自主修復證據。worker終態成功且idle、無live變更、V12未改。下一主線為逐段ADD後修復／再ADD的連續決策教材與可執行撤單狀態；不再加這批epochs或挑種子，未自動開下一輪。下方V5條目是訓練前契約重評歷史。

## 2026-09-24 當前階段改為修復並保留收益：V5

使用者明確要求暫不追求 3:1，先學修回負側且正收益不被壓掉。[當前契約](../../data/research/btc5m_repair_retention_contract_20260924_v5/CONTRACT.md)、[CURRENT](../../data/research/btc5m_repair_retention_contract_20260924_v5/CURRENT.md)、[重評報告](../../data/research/btc5m_repair_retention_contract_20260924_v5/REPORT_ZH.md)。V4 原檔及舊結果保留，但其 3:1／50% 市場成功率不再是當前階段門檻。全部 30 已有路徑 × 3 終點重評，以擴張後兩側金額為主基準、擴張前另列；event15於觀測終點6負側改善且正收益不減、3修復但犧牲收益、3收益保留但負側未改善、3兩側惡化。兩時鐘所有30觀測終點的原負側仍為負，不能稱完整修復或獨立市場成功率。主被動修復均保留，四路線與完整再加倉循環都要評估。90行獨立數學核對PASS、34父檔hash未改，封存新成果；0fit/native/HFT/worker/live，V12未改。下一步對齊精確父checkpoint及可重放根狀態，做同狀態完整循環價值比較，再決定續訓或接模組；未自動啟動訓練。

## 2026-09-24 擴張後修復／整場表現／反轉路徑完成

[CURRENT](../../data/research/btc5m_expansion_reversal_paths_20260924_v1/CURRENT.md)、[報告與圖](../../data/research/btc5m_expansion_reversal_paths_20260924_v1/REPORT_ZH.md)。同12已消費市場：正式winner核對後，主要4場觀測成交帳本3盈1虧、合计+320.12；12場10盈2虧、+936.44，但最後皆未達雙正/正零/3:1，私人完整PnL/pending仍未知。event15正分支買入14買公價較高側、13首分鐘、6前5秒同側上漲；不能變成60秒交易門檻。後續15有對側Maker、14有對側Taker；完整保留原正收益且負支回到加倉前，30秒2/15、曾達8/15、終點維持5/15。修復有主被動不同組合，窗口重疊非獨立試驗。原四場2019008有2次40/60反轉且最後雙負；按價格路徑補看2018407（5次，實際UP勝但UP−163.14）、2018839（3次，UP+65.04/DOWN−236.11）。12總帳、24band、30恢復路徑、52反轉回應窗核對PASS，34檔與2圖封存；0新fit/native/HFT/worker/live，V12未改。現有已消費資料已含反轉，可作慢修/恢復再流失/反轉追不回的完整路徑對照，不能當穩定成功教師或新驗收。本輪停止，未自動繼續。

## 2026-09-24 主動切側會計核對完成：7/8不能證明方向加倉

[CURRENT](../../data/research/btc5m_active_switch_accounting_20260924_v1/CURRENT.md)、[報告](../../data/research/btc5m_active_switch_accounting_20260924_v1/REPORT_ZH.md)。同四已消費市場event44/receipt41窗，0新fit/native/HFT/worker/live，V12未改。8個event切側為4買原負分支、3雙負、1買原正分支；P3唯一漏掉最後一窗，UP+131.69/DOWN−71.93→+133.85/−79.34。4個買負分支保留97.46%–99.44%正收益；全44窗買負分支P3 11/11，但買正分支10/15低於PUBLIC12/15，receipt正分支兩者皆14/15。未識別私有ADD/repair，不重新發現既有雙系統。八切側前5秒有Maker者5個、僅1個反向；固定P3移除Maker流量群logit項無一改選側，不構成Target因果解釋。event8與receipt7差別由回報合併雙側窗解釋。85窗會計與模型獨立核對PASS、21檔封存、282舊檔未改。下一問題是15個買正分支窗口的觸發與後續修復進展，須先對齊既有224單分析；本輪停止，未自動繼續。

## 2026-09-24 成交沉默與切側測試完成：被動整體改善掩蓋切側退步

[CURRENT](../../data/research/btc5m_quiet_restart_diagnostic_20260924_v1/CURRENT.md)、[報告](../../data/research/btc5m_quiet_restart_diagnostic_20260924_v1/REPORT_ZH.md)。沿用上一批六種凍結模型、四已消費驗證市場，按嚴格過去同路線沉默1/5/10秒作72格重新評分，0新fit/native/HFT/worker/live，V12未改。event MAKER P3延續原側184/194、切側11/47；P2為131/194、23/47；receipt切側P3 4/34、P2 16/34。TAKER切側event P3 7/8、P2 6/8，receipt皆6/7，差異太小不可確立信念。沉默5/10秒後P3/P2仍有機率評分增量，但不是新程序啟動證据；event TAKER沉默5秒的26個單側窗中18個前5秒有MAKER活動。沉默不是無掛單、切側不是repair真標籤或信念反轉。4,564來源上下文、96方向分層組、72格數學、96成對比較與144cell artifacts核對PASS；已保留逐市場退步。下一問題是全部主動切側窗的事前金額／MAKER活動／價格案例，未自動啟動。 

## 2026-09-24 價格／路徑／成交歷史固定測試完成：選邊改善尚不能識別信念

[CURRENT](../../data/research/btc5m_price_path_flow_ablation_20260923_v1/CURRENT.md)、[完整報告](../../data/research/btc5m_price_path_flow_ablation_20260923_v1/REPORT_ZH.md)。同 12 已消費市場，固定 8 訓練／4 驗證，兩時鐘 × MAKER/TAKER × 六種表示共 24 個小型本機單執行緒 logistic fits，無調參。價格水準＋倒數相對觀測持倉有穩定增量，價格路徑對 MAKER 小幅改善、對 TAKER 不穩定，簿增量不穩定。P3 加歷史後 event 選邊 MAKER 198/251、TAKER 36/44；但共同覆蓋窗上一筆任一路線成交側為 197/243、31/36，P3 為 196/243、30/36，成交延續已可解釋多數選邊表現，不能確認成交強化方向信念。這不是最後勝方準確率；MAKER/TAKER 不等於私有 ADD/repair，主動修復仍包含在樣本。沒有雙正驗證支持、私有 pending 與初始持倉 UNKNOWN；event 為 Target 離線資訊、receipt 預測回報到達。13,352 列來源重建、24 模型獨立復算、96 cell artifacts、120 檔 manifest 均核對通過。V12 未改，0 native/HFT/worker/live。下一問題是對齊既有 quote-program 研究，區分新程序啟動與舊程序延續，保留上一成交側基線；本輪已停止，未自動開下一輪。

## 2026-09-23 價格水準／變化假說與文獻：使用者已澄清平均只是類比

[文獻與問題定義](../../data/research/btc5m_price_level_path_literature_20260923.md)。新例為UP/DOWN .50/.50 → .40/.60 → .45/.55：DOWN仍較被看好，但最近價格變化朝UP。先把公開價格作方向基線，分開價格路徑、訂單流與自身成交的增量；不再要求使用者指定均線。已查一手預測市場價格、庫存做市、方向做市、micro-price與OFI研究，未證明Target機制或5M可用性。比較需共用持倉/成本/收益/pending狀態，保留主被動ADD及主被動repair；0fit/native/worker/live。

## 2026-09-23 新假說：加權價格錨點與成交回饋；保留主動修復

[假說與四路線契約](../../data/research/btc5m_weighted_anchor_fill_feedback_20260923_HYPOTHESIS.md)。主動／被動 ADD 與主動／被動 repair 都是循環候選；主動 repair 不只作被動失敗後的兜底。使用者提出價格跌破某加權平均後掛單、成交再增加方向強度的假說；錨點是BTC、合約市價或持倉成本，以及下跌對應買哪側，仍待釐清。區分價格偏離、成交歷史增量、持倉成本更新與舊掛單續補；成交不直接證明被買入側胜率提高。已讀既有quote-program continuity與lowdim程式查重，未重跑或擬合。狀態HYPOTHESIS_RECORDED_NOT_TESTED。

## 2026-09-23 ADD／repair 金額可行區間：固定離線批次完成

[CURRENT](../../data/research/btc5m_add_repair_monetary_frontier_20260923_v1/CURRENT.md)、[報告](../../data/research/btc5m_add_repair_monetary_frontier_20260923_v1/REPORT_ZH.md)。使用者已確認主動／被動 ADD 與主動／被動 repair，修復管理金額而非股數配平；未見市場給最終勝方可形成結構屬 USER_REPORTED oracle 條件能力，非自主選向。新批重用12市場224主動單，76筆買當時正分支者事前全部未達3:1，不能當私有ADD真標籤。保留原正收益50%、固定同一對側ask，前後修復區間為57筆皆不可行、18筆皆可行、1筆可行轉不可行；該筆在bid仍名義可行但未證實成交。另2019143被動擴張失形後3秒恢復，再擴張後未再觀測恢復；Target例子不是V12軌跡。獨立帳務／989來源群核對PASS，0fit/native/HFT/worker/live。歷史V12、R101與V13/V49學生來源分開，未猜測checkpoint；下一步先對齊父版本，作修復後ADD的同根完整循環價值比較，不能直接把本輪收益保留参照變成硬門檻。

## 2026-09-23 使用者校正：repair／ADD 雙系統是既有前提

[解讀修正](../../data/research/btc5m_target_active_formula_20260923_interpretation_correction.md)優先於下方主動公式 V1 的解讀。Target 主動 repair 與主動 ADD 是既有規律，不得當成本轮發現。混合主動樣本的公式吻合率不能代表 ADD 專屬機制；多數／少數側成交分類也不等同已識別的兩系統標籤。R115 少數側 ACTIVE 限制只屬 OUR 特定實作。下一步先對齊既有雙系統規則與來源，再調查 ADD 觸發、選邊、數量與當下市場／訂單簿／持倉的公式關係。原凍結數據與 manifest 保留，未新增實驗。

## 2026-09-23 主動買入公式／候選覆蓋 V1：固定批次完成，尚未訓練

[CURRENT](../../data/research/btc5m_target_active_formula_20260923_v1/CURRENT.md)、[報告](../../data/research/btc5m_target_active_formula_20260923_v1/REPORT_ZH.md)：同12已消費市場，372 TAKER legs合成224單／142時間戳群；116買原多數側、95修復、4修復跨側、9起始。同時間戳順序上下界下108單仍為多數側買入。MAKER占費後量79.1%、10/12場與總偏倉同向，但主動只修復不成立。固定少數側／便宜ask／市價加權不足／抵銷MAKER／10秒趨勢吻合46.0/38.5/50.0/51.4/58.9%，沒有確立Target公式；下單時刻、私有庫存與該批BTC spot-strike未知。7,176側別秒為已記錄成交分母，不是無下單標籤。從前輪11根實際核對44候選，PASSIVE22/22、ACTIVE21/22通過凍結基本檢查、1個pending自交叉保留；11個通過的多數側ACTIVE不會被R115少數側修復限定提出。下一步做多數／少數側ACTIVE與原PASSIVE/KEEP/CANCEL的同根完整循環估值，不直接開放動作或先重訓方向分類器。0fit/native/HFT/worker/live，V12未改；尚無新action-value續行標籤。

## 2026-09-23 方向信念／持倉修復辨識 V1：診斷完成，尚未訓練

[CURRENT](../../data/research/btc5m_belief_repair_identification_20260923_v1/CURRENT.md)、[報告](../../data/research/btc5m_belief_repair_identification_20260923_v1/REPORT_ZH.md)：固定重用 12 已消費市場、2,263 費後成交、雙時鐘 696 根。事件時鐘 444 完整修復窗中 334 在 30 秒內再買原側，292 當時原側仍為多數側；反側買入不能標成信念翻轉。公價不利與反側成交有關聯，但多為 maker，私有信念／下單狀態 UNKNOWN。同行情不同缺口只有事件 5 對／收到 3 對，未分離因果。既有 23 DROP 配對中，原一正一負 10 組沒有原負支改善。已準備 3 場兩臂共 11 個 OUR 因果快照及[完整循環教材規格](../../data/research/btc5m_belief_repair_identification_20260923_v1/NEXT_COURSE_SPEC.json)，還沒有替代動作價值標籤；7 個階段缺位保留 UNKNOWN。下一步先核對合法候選覆蓋再建配對教師，不重訓終局分類器／同 Q 權重，不直接接風險頭。V12 凍結；0 fit/native/HFT/worker 派送/live 變更。

## 2026-09-23 現行節點：V13 v10 負分支教師未晉級，保留 V12 主幹

[V9 修正 V4 結構加分](../../data/research/btc5m_v13_loss_side_teacher_20260923_v9/REPORT_ZH.md)與[V10 直接負分支差額教師](../../data/research/btc5m_v13_causal_loss_delta_20260923_v10/REPORT_ZH.md)均在第二台完成兩種子各 800 次更新並核對 25 個結果檔；0 新 native/HFT、0 實盤變更。V9 BASE 合格結構 471／458（原 V13 476／470），負分支縮小且保住原正收益僅 0／1 條。V10 名義嚴重尾損降至 2／2，但合格結構掉到 396／421、未清責任升到 318／301；直接損失教師在 256 題／種子選 KEEP 141／154，原策略僅 20／12。兩者未晉級，V12 未接入。下一步停止同一 76 維人工剩餘收據 Q 教材反覆微調，改建含市場倒數、owner 生命周期與完整循環的同根配對教師；HFT 僅作執行參考。所有數字是已消費條件微縮路徑，不是獨立市場達成率。

## 2026-09-23 使用者修正結構最低標準：雙正通過

[V4 結構契約](../../data/research/btc5m_structure_floor_contract_20260923_v4/SUCCESS_TARGET_V4.md)：兩支正值、正／零，或一正一負且正收益至少為負損失三倍，都通過；UP／DOWN 對稱。**學習主指標是同狀態行動能否壓小虧損側，同時保住正收益。** V12 V2 已計雙正但未計正／零；V13 v3 的「雙正不計成功」被本版取代。[凍結結果重評](../../data/research/btc5m_structure_floor_contract_20260923_v4/REGRADING.json)：V13 BASE 每種子 1,152 路徑，原 V13 總合格 476／470，v7 462／474，v8 456／463；因此「一正一負增加」不能當整體成功率提高。這批資料沒有確認的正／零終點，新增邊界不改數字。V14 23 配對 CONTROL／DROP 均為 6 合格（2 一正一負＋4 雙正），模型仍選 0 次 DROP。v7/v8 的訓練獎勵仍是舊錯誤口徑，重評不是重新訓練；目前沒有獨立市場 50% 證據。

## 2026-09-23 現行評估節點：V13 v8 與 V14 動作模型均未晉級

[V13 v8 完整結果](../../data/research/btc5m_v13_tail_risk_distillation_20260923_v8/REPORT_ZH.md)：第二台完成兩種子 800 次更新；BASE 真 3:1 由原 V13 98／98 升至 111／104，但嚴重虧損 37／19 升至 43／42，雙正 378／372 降至 345／359。[V14 固定動作模型](../../data/research/btc5m_v14_action_value_course_20260923_v2/REPORT_ZH.md)在 9 個已消費市場完成 46 條 native 續行和 23 配對；模型雖擬合，逐市場留出選 0 次略過，沒有新增 3:1。兩者均未晉級、V12 未接入。現有資料和 76 維人工 profile 不能證明風險壓制已學會；需評估完整循環狀態與教師改建，不再做同類風險權重微調。

## 2026-09-23 V14 同根動作教材：正向保留、錯向小虧已有兩個配對

[同根 native 分叉](../../data/research/btc5m_v14_owner_action_forks_20260923_v1/REPORT_ZH.md)在一個已消費市場 5 組、10 路徑完成，CONTROL 完全重現來源事件；2 組加倉有收據差異且終點無未清責任。正向 UP 原加倉保住雙正（略過後雙負）；錯向 DOWN 略過加倉使實際 UP 虧損由 −4.89 收斂至 −3.97，但仍未達真正 3:1。三個撤單根沒有終點價值差異，不能作教師正負例。HFT 只作執行參考，沒有調時鐘；尚未新 fit 或晉級。

## 2026-09-23 訓練焦點：V13 整段動作價值 v7r1 完成、未晉級

[V13 v7r1 完整訓練結果](../../data/research/btc5m_v13_tail_counterfactual_distillation_20260923_v7r1/REPORT_ZH.md)：第二台電腦單次提交、兩種子 1,600 更新、31 檔雜湊通過。BASE 真一正一負 3:1 從各 98 增至 113／109，但嚴重虧損從 37／19 增至 44／50，修復後 ADD 尾部未穩定改善，因此**未晉級、V12 未接入**。v7 來源雜湊筆誤在預檢攔下，0 fit／0 提交。另有[首次 V14 因果風險頭實際擬合](../../data/research/btc5m_v14_causal_risk_head_20260923_v1/REPORT_ZH.md)：9 已消費市場、510 狀態逐市場留一，加入 OUR 責任後 Brier 0.1610，較直接公價 0.1528 更差，未晉級。使用者要求停止擴大挑選資料；100 個新市場候選的未見資格仍 UNKNOWN，不作評分。

## 2026-09-23 V14 公開 Target 代理與 owner 尾部核對

[公開現金流代理審計](../../data/research/btc5m_v14_public_proxy_audit_20260923_v1/REPORT_ZH.md)：固定已消費膠囊 50 場只有 3 場呈現實際勝方正／反向負且至少 3:1，全部 DOWN；擴大唯讀聚合的 7,725 已結算 BTC 場有 447 個代理正例（UP 202、DOWN 245），其中 245 個與舊 registry 的 ready 市場重合。這是未顯式計費的已觀測成交代理，非 Target 私有核帳或 OUR 達標。ready 旗標不保證未消費或已有 OUR 配對結果，未見驗證集須在訓練前另凍結。

v1r1 逐事件再核對：JOINT 在 1977248 於市場終點前 144.5 至 46.5 秒間，每秒產生一筆 DOWN 15 的 `NEW/SENT`，合計 99 筆；終點同時 `CANCEL`，觀測至終點後 0.813 秒仍全為 `CANCEL_PENDING`。這說明重複建單和未清責任，並非只是一個終點撤單表象；`SENT` 不代表交易所接受。不要把 JOINT 當風險教師或 V12 接入候選。

## 2026-09-23 整組修復固定批次已完成：現行 V4 仍未確認成功

[v1r1 完整收件與逐事件稽核](../../data/research/btc5m_bundle_repair_value_20260922_v1r1/CURRENT.md)已核對 6 已消費市場 × CONTROL/GAIN/JOINT：18 路徑、31,262 狀態點、448 成交收據。按現行 V4（雙正也通過），終點名義合格 CONTROL 2/6、GAIN 4/6、JOINT 4/6；三臂確認終結後均為 0/6。GAIN 在 1977248 名義 +25.35/−4.65、pending 由 5 降至 2，但另三場有收益／虧損側取捨；JOINT 同場 99 張 CANCEL_PENDING 與嚴重收益流失，不晉級。0 fit、0 實盤更動。這是已消費市場的機制／條件執行證據，不代表獨立市場或學得風險壓制；下一版需整組 owner 責任與市場倒數的完整循環教師。[舊有號 3:1 細分評估](../../data/research/btc5m_v13_bundle_ratio3_assessment_20260923_v1/REPORT_ZH.md)保留為歷史子型態資料。

## 2026-09-23 V14 因果資料可行性（V13 主線接續）

[V14 決策前資料包](../../data/research/btc5m_v14_causal_frame_feasibility_20260923_v1/REPORT_ZH.md)從 R115 的 9 個已消費市場、18 條 OUR 模擬收據路徑抽出 5,418 個無標籤決策前 frame；市場真實五分鐘倒數、receive-ordered 公開 book、OUR 庫存和 owner 狀態通過逐筆檢查，0 fit／0 新 HFT/native。Target 公開掛單推估可作離線輔助行為標籤，不能當真實私有帳務或已核帳 3:1 收益。下一步須從相同過去狀態比較動作到收據／終結責任的配對結果，才有可用的 V14 action-value 訓練標籤。以下 V13 v5r3／v6 結果與現行 3:1 契約仍有效。

## 2026-09-23 V13 大贏小虧主線

以下為歷史 V13 v3 的錯誤口徑：當時把 3:1 理解成必須一正一負，雙正另報且不計達標；已被上方 [V4 現行契約](../../data/research/btc5m_structure_floor_contract_20260923_v4/SUCCESS_TARGET_V4.md)取代。[V13 v5r3 整段風險訓練](../../data/research/btc5m_v13_episode_risk_training_20260923_v5r3/REPORT_ZH.md)完成但未見跨種子重大改善；[v6 末段責任教師篩選](../../data/research/btc5m_v13_terminal_obligation_teacher_screen_20260923_v6/REPORT_ZH.md)完成 0 fit，雖降低 pending 卻把末步 ADD 全壓掉，未晉級。當前 76 維策略含人工 profile 剩餘機會特徵，真實市場兩類 Target 終局標籤亦未核帳。見[當時下一步評估](../../data/research/btc5m_v13_asymmetric_ratio3_contract_20260923_v3/NEXT_STEP_ASSESSMENT.md)。以下段落是舊口徑歷史紀錄。

## 收益結構保留診斷V1完成（2026-09-22；R97候選支線）



























[CURRENT](../../data/research/btc5m_shape_retention_diagnosis_20260922_v1/CURRENT.md)、[報告](../../data/research/btc5m_shape_retention_diagnosis_20260922_v1/REPORT_ZH.md)：63保存路徑、1876條件微縮投影完成，0新native/fit。QUOTE在4市場5條條件曾達24/-6；只有1977248觀測終點仍達，但5個pending未確認。兩條2313167轉雙正是有用取捨，不能因收益<24叫失敗。1977248及2312996實際有多張高價修復一起吃掉收益，R96的主動修復也有同類問題。下一方向為整組pending＋新增修復的兩分支經濟評估，非新硬限制；尚未實作/派送。job `btc5m-shape-retention-diagnosis-20260922-v1` succeeded/collected，不重送；不是泛化成功率或模型晉級。其他研究線原文保留。



























## 2026-09-22 成功參照更新：+24/-6 已足夠，目標50%市場達成





























使用者接受1977248約+24.17/-5.83為現階段成功形狀，不再要求近零虧損；下一目標為獨立市場約50%達成。[評估定義與接續](../../data/research/btc5m_asymmetric_structure_review_20260922/SUCCESS_TARGET_V1.md)。使用者確認：收尾保有計成功；過程形成後失去則保留作失敗原因分析，分開報率。該例形狀已形成，5個未終結owner使收尾仍未確認。沒有新派送或live動作；舊實驗結果不倒改。





























## 2026-09-22 使用者目標澄清：大贏小虧，不能只看 floor





























[評估修正與既有結果重讀](../../data/research/btc5m_asymmetric_structure_review_20260922/CURRENT.md)：同時保留收益側、壓低虧損側；無交易或把收益一起修平不算成功。R97 QUOTE 1977248 的已成交部位 +24.17/-5.83 有結構訊號，但5個未終結 owner，並非鎖住收益；兩候選仍不promotion。以下各支線原結果與凍結檢查不變；R101已做ratio2/peak retention，不重複研究。





























# R96候選支線：R97完成，兩版均未升級（2026-09-22）





























[R97 CURRENT](../../data/research/btc5m_cycle_completion_20260922_r97/CURRENT.md)、[完整報告](../../data/research/btc5m_cycle_completion_20260922_r97/REPORT_ZH.md)。先做256組狀態微縮與15元件測試，再比較6市場21條件、63路徑（43新native／20核驗重用）。VALUE有20/21零成交；QUOTE有4改善17惡化，觀測floor市場等權-1.201151→-8.760031，20路徑保留46未終結owner。均不promotion；R96保留研究對照。





























已核實新報價與舊掛單維護衝突，會撤單後同輪重掛同價；不能把全部退步歸因於此。支線下一個候選方向為統一NEW/KEEP/CANCEL估值，尚未實作或派送。job `btc5m-cycle-completion-20260922-r97` succeeded/rc0、已收集，不重送；live0、capital_cap=null。下方另一流程的R100/R101主線及Target支線原文保留；不要只憑輪號混用不同研究job。





























以下保留歷史狀態與支線。



































---



































# Current research pointer








































## 使用者指定新主線：不以EBM為前提的直接控制實驗（2026-09-22）

[R120 CURRENT](../../data/research/btc5m_target_post_repair_add_blocker_20260922_r120/CURRENT.md)、[報告](../../data/research/btc5m_target_post_repair_add_blocker_20260922_r120/REPORT_ZH.md)：主線 Target core-loop blocker 已定位。R119發現 Target 12/12 repair後再EXPAND、OUR只有4/12；R120鎖2019018+2018988做read-only對齊。Target post-repair expand對齊時，OUR strong UP formation/AttemptMemory後CE仍正，但R89 repair-service-pressure把它壓成負：2019018 +0.3163 - 2.6020 = -2.2858；2018988 +0.3242 - 4.9362 = -4.6120。兩場mode_active=false，ratio/retention不是strong-side第一blocker。從OUR first weak repair後，2019018有45次pre-R89正CE strong ADD、**45/45被R89壓成<=0**；2018988 32次、**32/32被壓掉**；兩場之後strong receipts皆0。更關鍵：2019018在尚未repair、只有UP15 directional gap時，R89就把gap視為unserviceable repair debt開始阻止後續ADD。結論：第一 core-loop blocker = **R89 strong-ADD repair-debt penalty**，語義上延續09-14已發現的1:1 repair-debt/balance trap，與Target「保留方向曝險、EXPAND與REPAIR並行」衝突。下一輪尚未執行：不要直接關R89；先定義 existing confirmed gap vs candidate新增repair responsibility 的最小causal decomposition，再做fixed-correct mechanism smoke。native0/live0。依使用者要求，每輪完成先回報。

## 新增支線：Target修復辨識真實12場（2026-09-22）







































[接續入口](../../data/research/btc5m_target_repair_identification_real12_20260922_v2r1/CURRENT.md)、[報告](../../data/research/btc5m_target_repair_identification_real12_20260922_v2r1/REPORT_ZH.md)：56固定模型完成（19核對復用+37新fit），19tests/50package/115artifact/58380預測重算通過。事件時鐘修復量MAE12.757劣於median10.214、4/4退步；ADD有關聯但未泛化；不promotion、HFT/live0。V1漏查單數taker費用，三場零費/exact強結論已撤回。兩job均terminal，不重送。下一步為父單/連續修復段標籤辨識，未執行。經濟主線仍如下R94/R89。







































2026-09-22：R94同市場HFT profile bridge完成，α.02優勢依執行條件改變，未升級；R89仍基準。











































- **入口：** [R94 CURRENT](../../data/research/btc5m_hft_profile_bridge_20260922_r94/CURRENT.md)、[報告](../../data/research/btc5m_hft_profile_bridge_20260922_r94/REPORT_ZH.md)。job `btc5m-hft-profile-bridge-20260922-r94` succeeded/rc0、19.645s，auto-collected；20格=10新native+10核驗舊路徑，7package/29artifact/15分析核對PASS。無待派或執行中job。





















- **結論：** 同R92兩市場五設定，舊profile αmeanfloor差+1.49356（3/1/1）；reference1092/273差-.166833（1/2/2）。原最大+7.0036案例在新profile經濟成交相同；α在1977248有1小改善2退步。不能把R92係數認作通用穩健值，也不要重跑R93。





















- **取捨：** 新profile mean pairing改善+1.680954，但未配對成本增+1.847786；meanfloor -1.868147→-2.03498。負floor積分-15.64%、整批最差期中改善，但最差期末變差。成交67→51、新單145→100。五設定均成交、pending0，不代表完整修復。





















- **辨識邊界：** entry/response改變同時，原控制器known entry成本也改變；非純網路延遲試驗。雙版原native皆非夢幻成交。reference非新量測、conditional200bps/sourceclock UNKNOWN，仍EXECUTION_REALISM_INCOMPLETE、notOOS。





















- **下一步：** [修復可完成性診斷](../../data/research/btc5m_hft_profile_bridge_20260922_r94/NEXT_REPAIR_COMPLETION_DIAGNOSTIC_PLAN.json)。固定HFT profile/α，核查舊退步場的所有ACTIVE量價候選、CE／legality／pending與費後剩餘，不另盲掃係數。原active已有最低ceil(1/ask)候選，Taker無15份上限；不要把TICKET紀錄當成所有方案都只被最低票面擋住。無新機制／job。





















- **歷史不改寫：** R92仍11/12，R93三不同市場無增量，R94不替前輪改PASS。R89基準和α.02診斷候選保留；所有凍結來源與結果不變。





















- **執行界線：** secondPC一heavy/max_threads4、capital_cap=null、R65/R84/Adam不改、NN0/live0，共用矩陣與Console/auto-collector。一批完成先報告，不無限連跑。





















