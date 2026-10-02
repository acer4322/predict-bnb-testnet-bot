"""Create a source-linked Chinese handoff; no replay, dispatch or live mutation."""
import hashlib,json,re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];R=ROOT/'data/research';D=ROOT/'docs/handoffs'
OUT=D/'CODEX_TO_CLAUDE_V12G_V35_V58_HANDOFF_20260928_ZH.md'
CHECK=D/'CODEX_TO_CLAUDE_V12G_V35_V58_HANDOFF_20260928_CHECK.json'
read=lambda p:json.loads(p.read_text(encoding='utf8'))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
sources={}
def link(rel,label):
    p=ROOT/rel;assert p.is_file(),p
    sources[p.relative_to(ROOT).as_posix()]=sha(p)
    return f'[{label}]({p.as_posix()})'
packages={}
for p in R.glob('v12g_*_2026092*_v*'):
    m=re.search(r'_v(\d+)(r\d+)?$',p.name)
    if m and 35<=int(m[1])<=58:packages[m[1]+(m[2] or '')]=p
def pl(v,file='REPORT_ZH.md',label=None):
    if str(v) in ('45','46','47') and file=='REPORT_ZH.md':file='CURRENT.md'
    return link((packages[str(v)]/file).relative_to(ROOT),label or 'V'+str(v)+' '+file.replace('.md',''))

assert not OUT.exists() and not CHECK.exists()
lines=[]
def add(s=''):lines.append(s.strip('\n'))
add('# Codex → Claude：V12g 從上次版本交接到 V58 的完整續接檔')
add('日期：2026-09-28（Asia/Taipei）。本檔是研究交接與已完成工作索引，不是自動派送指令。')
add(f'工作區：`{ROOT.as_posix()}`')
add('## 1. 接手先讀這裡')
add('本檔接續 '+link('docs/handoffs/V12G_VERSIONS_HANDOFF_20260927_ZH.md','Claude 上次交來的 V1～V34 版本交接')+'，新增範圍是 V35～V58。舊檔仍保留，當中「目前V34」「不用秒數限制」等歷史敘述不能覆蓋本次使用者的最新要求。V33／V34是上次交接已包含的Codex修復，不重做。')
add('**目前主線：先用原規模、無300 USDT限額研究反轉大虧；300版保留但暫停調參。已建立V58＝原V50規模＋使用者指定290秒停新單及撤單，作下一輪比較基準。V58執行限制通過，經濟結果略退步，沒有實單晉級。新的有限部分修復尚未實作或派送。**')
add('閱讀順序：\n\n1. '+link('AGENTS.md','AGENTS')+'、'+link('docs/agents/research.md','研究規則')+'，接著 '+link('docs/agents/RESEARCH_CURRENT.md','RESEARCH_CURRENT 最上方 V58')+'。\n2. '+pl(58,'CURRENT.md','V58 CURRENT')+'、'+pl(58,label='V58 報告')+'、'+pl(58,'VERIFIED.json','V58 核驗')+'。\n3. '+pl(58,'NEXT_MECHANISM_DESIGN_ZH.md','下一個機制設計')+'；需要因果證據時讀 '+pl(51,label='V51 部分修復診斷')+' 與 '+pl(40,label='V40 失敗與副作用')+'。\n4. 要改代码才讀 '+link('docs/agents/development.md','開發規則')+'；要派新native才讀 '+link('docs/agents/worker.md','第二機派送規則')+'，先查精確job及全域狀態，不能直接重送舊job。')
add('V58收尾的 '+pl(58,'WORKER_AFTER.json','worker狀態快照')+' 顯示沒有非終局job及其他研究process。這是收尾快照，不保證接手當下仍空閒。本次交接製作沒有啟動新測試、訓練、服務或下單。')
add('## 2. 使用者目標、最新決策與不能漏掉的語義')
add('''- 主要目標是保留未反轉場盈利，降低反轉場的平均及尾部虧損，改善全體平均。反轉場可以小虧；雙負或P>L沒改善不再單独否決候選，但必須揭露。
- 保留開局主動承擔風險、建立收益空間的行為。不能只把交易量壓光、兩側補平，便說風險控制成功。
- ADD和repair都有主動／被動兩種路由。修復看兩側金額與總成本，不是把份數補平。修復允许付出部分正收益，但不能把收益保留問題藏起來。
- 300明確是300 USDT／美元等值，不是新台幣。每張被動10份曾被保留作小額候選，但目前主線已回原15份；不要把V57的10份／POST20／cap300誤當V58。
- 使用者已明確指定290秒起不送新單並撤單。此規則覆蓋主動／被動及ADD／repair，沒有repair豁免；它覆蓋舊「不新增秒數限制」偏好，但不是允許新增任意冷卻秒數或其他時間門檻。
- 至少用10場看機制，不用4場便宣稱穩定；後续曾要求新30場，V45～48已執行，現在也已消費。提高速度用最多4條獨立單執行緒native路徑；不要自行改成12路或主機HFT。
- Target／最終勝方／settlement只能離線分析與評分。OUR輸入只用當下可見行情、實際持倉與訂單責任。FLIP是策略事件標籤，不等於獨立公共價格反轉、更不是事前知道整場不反轉。
- 所有dirty worktree、既有啟用／armed／stakes／服務與收集器保持原樣。研究授權不是實單、重啟服務或部署授權。''')
add('## 3. 現在究竟跑的是什麼')
add('這段延續V12g／V12＋K415控制與執行研究，V35～V58沒有新增神經網路fit，不要稱成「又訓練了一個V58模型」。V12g策略版本V49、既有V49引擎、以及V12本體是不同命名層；看來源manifest及env，不按相同數字認親。')
add('V58確切環境取自 '+pl(58,'PROTOCOL.json','凍結 PROTOCOL')+' 的 `jobs[arm=STOP290].env_v12`，並已由native結果核對：')
env=read(packages['58']/'PROTOCOL.json')['jobs'][1]['env_v12']
add('| 項目 | 現況及含義 |\n|---|---|\n| 資金上限 | `V12G_MARKET_CAP` 未設定，runtime cap=null；不是把上限設成很大的數字 |\n| 被動票量／PADD提案量 | 15份／15份 |\n| 開局選邊 | `V12G_GROSS=300` 是總份數門檻，不是300 USDT成本；另有原30秒fallback |\n| 換邊 | `V12G_FLIP=0.1` 是delta，判斷為強側mid ≤0.5−0.1＝0.40；仍是FLIP40，不是0.10價格門檻 |\n| PADD價格 | ask上限0.80，原機制保留 |\n| 修復原參數 | K=4.15、RETAIN=0.5；是凍結原版設定，不是新加的使用者通用成功門檻 |\n| V40部分修復 | POSTFLIP_MONEY_REPAIR=OFF |\n| V41風險保護 | POSTFLIP_RISK_FLOOR=ON |\n| V43物理工單接續 | WORK_CONTINUATION=SERVICE |\n| V44需求接入 | CONTINUATION_DEMAND=OFF |\n| V50合格工單續修 | QUALIFIED_CONTINUATION=ON |\n| 反轉後ADD消融 | POSTFLIP_ADD_MODE=BASE，沒有套V52 HALF／REPAIR_ONLY |\n| 需求縮放 | DEMAND_SCALE_MODE=OFF；env仍記0.2但OFF時不縮需求 |\n| 截止 | STOP290=ON，elapsed ≥290000ms |\n| 研究假設 | 零費用、原250ms送單／250ms回報、保守排隊；不是已驗證的實單成本與延遲 |')
add('參數語義可直接查 '+pl(58,'overlay/run_variant.py','策略接入原件')+'；零費用、研究最低單量／最小金額不能当即時交易所規則。依 '+pl(58,'CANDIDATE.json','native指紋')+' 使用隔離修復版backend，SHA256為 `033469835b44f94f1a022e419e79be61be72a9f2501ceea11b8e255b4824d145`；不可混用舊binary。')
add('## 4. V35～V49：為何走到原規模修復主線')
early=[
('35','唯讀；8個耗盡事件＋雙正對照','實際正收益分支×全入口同狀態攔住8/8；4/8在NEW前既有pending已超可花收益。不是8/8策略成功，無新native。'),
('36','4場×BLOCK／CLIP_ACTIVE＋1控制＝9新路徑','兩候選P>L仍25%；BLOCK少虧但首分鐘一場約剩48%持倉。主動縮量不優於等待／被動修復。'),
('37','沿用4場，補6場×2＝12新路徑','完整10場三組P>L都10%。BLOCK相對V34減少L48.65%、增加P45.65%，但不能拿這個弱基線聲稱保留PADD80收益。'),
('38','唯讀重評同10場歷史版本','原PADD80未FLIP4場+148.04，BLOCK只+38.47；原獲利5場+136.28→−1.17。主線轉回PADD80收益參照，BLOCK只作風險參照；不是PACE25/WB100路線。'),
('39','唯讀查修復入口及FLIP前負債','角色weak與實際負收益物理分支可能不同；不能把可負擔部分數量等同已能成交或有後續收益。銜接V40獨立檢驗。'),
('40','10控制＋10換邊後金額部分修復＝20路徑','同10場−253.31→−137.28，未FLIP4場全保留，但FLIP組收益被壓光；新修復提早啟動ctx.first，改寫被動准入而PADD另走入口。候選不採用。'),
('41','原PADD80＋反轉後共用風險下限；11路徑','V40新修復OFF。已確認改善才提高含pending最壞損益下限；同10場−253.31→−50.99，最差−1114.05→−453.54。仍有鎖虧與換邊前深負債。'),
('42','唯讀；同10場物理工單與停滯診斷','換邊後用另一側持倉核對原scalar target，會錯標完成；小餘量低於15份又無新服務。這不是底層cash提前釋放證據。'),
('43','BIND10＋SERVICE10＋控制＝21路徑','BIND修正工作物理方向/withdraw語義，經濟相同；SERVICE有限接續讓同10場−50.99→−39.70，但正常組只保留93.71%。新需求與舊surplus撤單不一致。'),
('44','把接續需求接入desired與維護；11路徑','同10場−39.70→−40.06，小幅退步；不應禁撤單來遮掩需求／維護問題。後與V43凍結進新30。'),
('45','凍結V43/V44後按時間抽新30；原job回傳7路徑後STOPPED','審核同毫秒join問題；不是全部native策略失敗，不重跑已完成路徑。'),
('46','審核修正後只接未跑；回傳6路徑後STOPPED','後續發現真正期後source EOF；保留2629444兩臂UNKNOWN，不能補場或當0。'),
('47','新增CENSORED分類；回傳20路徑後STOPPED','2632221已處理完整source，但合法drain多兩個決策步驟，舊frames==source_updates稽核拒絕。保留原錯誤；actor/clock/native不改。'),
('48','修正source/drain核驗，四路接剩29；合併共62唯一native','重核原33，不重跑；60測試＋2控制，29完整配對＋1雙臂UNKNOWN。V43/V44平均−65.12/−65.24，P>L44.83%，V44不晉級；全30嚴格平均UNKNOWN。'),
('49','唯讀；29個V43＋6個V44差異路徑','2629327原五張qualified深度拆單耗完筆數但原有限修復責任未完成；另外兩大虧主要是完整修復不可負擔。只延長既有有限工作，不直接取消所有次數限制。')]
add('| 版本 | 範圍／變更 | 結論 |\n|---|---|---|')
for v,scope,result in early:add(f'| {pl(v,label="V"+v)} | {scope} | {result} |')
add('V45～48是同一輪修核驗並續接，7＋6＋20＋29＝62條，不能把每個STOPPED包都重新跑一次。V48的早期PROTOCOL計畫數與最後實跑29不同，完成數以各RESULT及V48合併核验為準。新30原先未見，但結果已用於V49之後設計，現在全屬已消費。')
add('## 5. V50～V58：有限續修、小額支線、再回原規模')
recent=[
('50','同29場；30唯一native，原22＋r1剩8','既有qualified工單耗完五筆後，在原有限目標與收益保留條件內跨深度續作。平均−65.12→−30.04，最差−1036.13→−400.73；正常9場+117.39→+117.40。改善集中2629327；排除此場其餘28配對平均−0.867，不可說全面穩定。'),
('51','唯讀29場，0新native','局部部分修復可行28/29，全部正常9場也有。2630303／2630045確有修復但被後續支出超過；新部分修復不能沿用會提早設定ctx.first的舊入口。'),
('52','HALF／REPAIR_ONLY／SMALL300各29＋控制＝88','換邊後少ADD／停ADD僅平均−30.04→−28.83/−29.05，最差都−400.73。300版平均−12.18但正常9場+117.40→−3.45，並非合格縮小版。'),
('53','300限額下被動10/7.5/5/3各6＋控制＝25','原15份六場重用。平均依15/10/7.5/5/3為−11.60/−10.83/−12.39/−13.24/−12.84；10份僅研究候選。小票增加訂單數，成本仍約289，原持倉需求未縮。'),
('54','唯讀六場18路徑；凍結下一輪原高5＋低5','開局需求每側約448，選邊後仍600～900；300版早在5.382～7.804秒就有資金裁量。兩個原好場沒有PADD支出，所以不能只縮主動PADD。'),
('55','被動10份拿掉300上限；六場＋控制＝7','平均成本1436.81，原15份1463.52，只少1.83%；好場收益恢復，大虧也恢復。這六場是刻意挑選診斷，不是實單建議本金。'),
('56r1','同10場BASE／OPEN／POST／BOTH；38新路徑＋重用3','原6條保存，r1只修growth物理錨點審核再接32。平均−9.30/−9.71/−6.24/−9.36；POST較少虧但正常盈利未恢復，PADD支出反增，另發現exchange到期後模擬成交。'),
('57','POST20/cap300/被動10＋STOP290；10場＋控制＝11','平均−6.24→−5.42，4改善6相同，P>L仍10%。290時都無owner，實際CANCEL0，當時只能證明停NEW；非空全撤僅元件測試。'),
('58','原V50 cap=null/被動15/不縮需求＋STOP290；29＋控制＝30','平均−30.04→−30.54，3改善1退步25相同，P>L48.28%→41.38%。四場13筆真實撤單補齊非空覆蓋；主要尾虧不變。作符合用戶限制的新對照，不是經濟晉級。')]
add('| 版本 | 範圍／新路徑 | 結果與決策 |\n|---|---|---|')
for v,scope,result in recent:add(f'| {pl(v,label="V"+v)} | {scope} | {result} |')
add('不要跨表直接比較平均：V35～44主要是早期選出的十場；V45～52及V58是後來29完整場；V53/55是其中挑出的六場；V54/56/57是原V50最高5＋最低5十場。V56／V57的小額結果不能拿來宣称比V58原規模更穩。')
add('## 6. 最新V58結果及不能省略的退步')
c=read(packages['58']/'COMPARISON.json');b=c['aggregates']['V50_BASE'];n=c['aggregates']['STOP290']
add('| 指標 | 原V50 | V58 |\n|---|---:|---:|')
for label,key in [('全29平均損益','winner_mean'),('最差一場','winner_worst'),('最差兩場平均','worst2_mean'),('最差五場平均','worst5_mean'),('平均成本','cost_mean')]:add(f"| {label} | {b['VALID_PAIRED'][key]:.2f} | {n['VALID_PAIRED'][key]:.2f} |")
for label,group in [('未FLIP9場平均','BASE_NO_FLIP'),('有FLIP20場平均','BASE_ANY_FLIP')]:add(f"| {label} | {b[group]['winner_mean']:.2f} | {n[group]['winner_mean']:.2f} |")
add('| 盈利場數 | 16/29 | 16/29 |\n| P>L | 14/29＝48.28% | 12/29＝41.38% |\n| 雙正／雙負／正零 | 6／11／0 | 6／11／0 |')
add('P=max(UP,0)+max(DOWN,0)，L=max(−UP,0)+max(−DOWN,0)。兩分支收益不是可動用現金；勝方盈利率與P>L不同。未FLIP9場仍9/9盈利，均值保留106.30%，但三場勝方改善也伴隨另一分支變差，不能當風險下降。')
add('| 改變市場 | 勝方損益：V50→V58 | 原因／代價 |\n|---|---:|---|\n| 2632221 | +247.80→+264.06 | 少付修復成本，但UP由−760.65變−840.38 |\n| 2632537 | +105.10→+136.18 | DOWN由−80.42變−147.03，P>L失去 |\n| 2633749 | +206.27→+225.47 | DOWN由−36.40變−117.20；舊基準另含一筆到期後成交效力限制 |\n| 2634488 | −5.24→−86.24 | 五張旧掛單被撤＋一張290秒後NEW被阻止；少買90份UP、少花9，UP少81 |')
add('2634488的五張被動修復原在276.188～287.214秒建立，第六張290.510秒建立；全部90份原在291.500秒附近以0.10成交，早於300秒，這筆退步不是到期後假成交造成。證據 '+pl(58,'TAIL_REPAIR_TRADEOFF.json','逐筆配對')+'。290規則依用戶要求保留，未擅自開修復例外；下一步重點是能否更早完成有價值的修復。2630303仍−400.73、2630045仍−341.19，主要大虧沒有被尾盤限制解決。')
add('### 290秒限制的真正驗證範圍')
add('''- 全29場290前prefix與原V50一致，290起所有主動／被動ADD／repair NEW＝0；不是只關閉PADD。
- 事件驅動首個截止後策略幀延遲2～510ms，没有新增獨立timer；在首個可處理幀全撤可撤owner，暫不可撤者後續追蹤。
- 四場13筆撤單請求全部owner最終TERMINAL；45次CANCEL_PENDING觀察保留正確份額／現金預留。不是說13筆全都零成交撤銷。
- 2632221與2632537兩張截止前舊單仍在290秒後成交，合計四筆fill receipt，已計帳；撤單意圖不能抹掉在途成交。
- 實際exchange ≥300秒成交＝0，最終owner＝0。receive_ts晚不一定exchange晚，必須分開看。
- 30路徑中25仍有legacy active_matches_opportunity失敗。新限制／prefix／receipt／ownership驗證PASS，不等於所有策略安全與效力門檻PASS。
- 原V50有2633749一筆exchange到期後成交，原始PnL保留限制；不能把原舊flag no_postexpiry_acquisition當成交有效性證明。''')
add('原件：'+pl(58,'CANCEL_RECEIPT_AUDIT.json','撤單與預留')+'、'+pl(58,'CANCEL_RACE_DETAILS.json','在途成交')+'、'+pl(58,'COMPARISON.json','全29比較')+'。V58新native與worker核驗10.58分鐘（不含準備、傳輸、主機核驗），45凍結檔、484跨機hash核對。')
add('## 7. 下一步：尚未實作的研究，不要誤認已經成功')
add('主問題是「完整修復不符合原收益保留條件時，能否先做有價值、有限的部分修復，並在後續主被動加倉中保留改善」。不是單純全面放寬主動下單，也不是再測一次換邊後全停ADD。')
add('既有V51的重要觀察：\n\n| 市場 | 首次拒絕到FLIP前，弱側買入淨改善 | 強側後續買入支出 | 含義 |\n|---|---:|---:|---|\n| 2630303 | +395.92 | 690.23 | F支出全為被動；其中656.87來自拒絕後出生單 |\n| 2630045 | +110.70 | 763.55 | F只有8.55為主動PADD；722.00來自拒絕後出生單 |\n| 2632221盈利對照 | +620.76 | 1379.88 | 同樣修復落後，卻由F勝方結算盈利；不是必輸訊號 |\n| 2629327修回正例 | +1726.12 | 1640.24 | 既有合格工作續作實際修回，不能推廣為所有場已成功 |')
add('這裡F/W固定首次DECIDE的物理方向，不把所有F買入都重命名成ADD，區間也各不相同。28/29場有局部部分上界、全部9正常場也有；不能只因完整修復被拒或負債大，就判斷即將反轉。價量可行只是上界，不是實際成交機會。')
add('建議按以下有限步驟接續，先看 '+pl(58,'NEXT_MECHANISM_DESIGN_ZH.md','詳細設計')+' 與 '+pl(51,'NEXT_STEP.md','V51約束')+'：\n\n1. 先做獨立有限部分修復元件。新work有唯一ID、固定物理側、准入快照、有限數量及支出；不可直接設定或重置舊ctx.first。原全域入口仍根據新的真實組合自行判斷，不固定成基準的啟動時間。\n2. 先測已成交／pending／同計畫NEW／部分成交／零成交終態／方向改變／最低金額／自成交衝突／290截止。撤單在途不釋放；同一成交只能抵扣一次。\n3. 明確處理新增已確認擴張如何形成新修復責任；不能每幀無限再生預算，也不能把首次2.10 USDT的小預算當整場能力。沿用凍結收益保留條件，勿為單場結果改K或RETAIN。\n4. 元件語義成立後，再凍結一個候選，以V58作符合290規則的原規模對照。保留正常9場、反轉20場以及所有退步；同時報主／被動貢獻、實際支出、未結責任、最差2／5與整體平均。\n5. 機制呈現可重複改善後才進未使用的跨日期／跨幣種／實際費率資料。300縮放屆時再做，不能只縮單張票或用原成交乘比例聲稱實測。')
add('本次交接沒有建立下一個候選或job，沒有新的fit。這份步驟不是允許接手者重送已完成實驗，也不授權實單。')
add('## 8. 樣本、原始結果與從哪查')
plan=read(packages['58']/'PROTOCOL.json')
add('目前29場完整ID：\n\n`'+', '.join(map(str,plan['markets']))+'`')
add('未FLIP9場（離線固定分組）：\n\n`'+', '.join(map(str,plan['groups']['BASE_NO_FLIP']))+'`；其他20為有FLIP組。`2629444`仍是原30中的截斷UNKNOWN，不得把它補0、丟掉不說或換成容易的場。')
add('V58基準逐場位置與檔案hash都在 '+pl(58,'PROTOCOL.json','PROTOCOL.baseline')+'：\n\n- `baseline[market].local_source`：本機研究目錄下的原V50 arm來源；分布於V50及V50r1兩job。\n- `baseline[market].remote_relative`：第二機results相對路徑。\n- V58自身結果位於 '+link('data/research/lan_worker_returns/btc5m-v12g-original-scale-stop290-20260928-v58/RESULT.json','已收回 RESULT')+' 所在目錄的 `arms/v12g58_STOP290_<market>`。\n- 每arm讀 `EXECUTION.json`確定env；`clock_trace.json.gz`查plans、direction_rows與各proposal；`execution_clock.json`查receipt、carrier及exchange/receive時鐘；`stop290_trace.json.gz`查截止撤單和預留；`AUDIT.json`查各檢查與legacy失敗。\n- 頂層clock_trace.states只有t/inv/cost，沒有owners；owner在proposal的state.owners或stop290_trace.rows[].owners。同毫秒必須對上具體phase/index，不能隨便拿第一個row。\n- V50 COMPARISON的arm=V43是V50當時的舊對照；本輪V58 baseline是arm=V50的source_path，不要接錯。\n- 本段OUR回放已具備凍結資料和結果，不需掃51GB wallet_maker_book_inference.db找Target來續作。若另做Target研究，走獨立來源查核；其私人意圖不可視為已知。')
add('## 9. 已完成／停止後已續接的任務：不能重送')
add('下表以本機已收回RESULT讀取，不代表現在重新派送。STOPPED是保留原歷史，續接結果已完成時不能只看STOPPED就重跑。每個精確job只曾送一次；真正續接用另一份凍結修正包。')
add('| 版本包 | 已存在job | 原RESULT狀態／路徑數 |\n|---|---|---|')
registry=[]
for v,p in sorted(packages.items(),key=lambda kv:(int(re.match(r'\d+',kv[0])[0]),kv[0])):
    proto=read(p/'PROTOCOL.json') if (p/'PROTOCOL.json').exists() else {}
    sub=read(p/'SUBMIT.json') if (p/'SUBMIT.json').exists() else {}
    job=sub.get('job_id') or proto.get('job_id') or proto.get('reserved_job_id')
    item=dict(version=v,package=p.relative_to(ROOT).as_posix(),job_id=job)
    if job:
        f=R/'lan_worker_returns'/job/'RESULT.json';assert f.is_file(),f
        result=read(f);item.update(result_status=result.get('status'),returned_paths=len(result.get('paths',[])),result_file=f.relative_to(ROOT).as_posix())
        add(f"| V{v} | `{job}` | {link(f.relative_to(ROOT),str(item['result_status']))}／{item['returned_paths']} |")
    else:item['job_note']='No named native job found in this package; see report for read-only scope.'
    registry.append(item)
add('三組接續合併需特別記住：V45/46/47/48＝7+6+20+29；V50/V50r1＝22+8；V56/V56r1＝6+32。V37只補12，沿用V36已有8候選；V53已有15份基準不重跑；V56已有3BASE不重跑。這些計數包含控制及被保留截斷／錯誤證據，不能當有效市場數。')
add('## 10. 第二機、驗證順序與禁止誤操作')
add('''原確認身分：SSH alias `btc5m-worker`，既有位址192.168.68.52，hostname `DESKTOP-JIERAGF`；第二機根目錄 `C:/BTC5M-worker`，解譯器 `C:/BTC5M-worker/.venv/Scripts/python.exe`。接手仍需核對主機key及身分，IP不是身分證明。

native/HFT只在第二機，一次一個重工作，最多4路各單執行緒。依序：查精確job和全域狀態 → probe → 核對來源hash → stage/load-only → 單次submit → status/tail → 終局collect及hash/帳務/來源核驗。SSH逾時只接回查狀態，不當作未派成功；native失敗保留frame/owner/receipt/source證據再診斷，不重送遮掉錯誤。

父版、MANIFEST所列檔案、已核驗結果不能覆寫。新變更另建後繼包與唯一job；舊手寫persona、TASK/RETURN、historical NEXT_STEP不是現行自動啟動授權。不要重啟或更改收集器、live服務、enabled/armed或本金。''')
add('V58使用的 '+pl(58,'dispatch.py','傳輸範例')+'、'+pl(58,'worker.py','30路徑凍結worker')+' 只供理解流程；它們指向已完成job，**不可直接用submit再執行**。一般規則以 '+link('docs/agents/worker.md','worker規則')+' 為準。')
add('## 11. 接手檢查與一段可直接使用的起始說明')
add('接手後先回報：已讀V58、理解它不是經濟晉級、知道300支線暫停、知道290規則仍有效、知道下一步尚未實作，以及沒有重跑既有job。若準備新實驗，先寫明與V40/V52/V57/V58的不同假說、固定比較來源、邊界與停止條件。')
add('> 請先讀本交接檔與RESEARCH_CURRENT的V58條目。保留原規模無300上限、被動15與290秒停NEW／全撤限制，以V58作研究基準。下一步先驗證「獨立有限部分修復，不直接改寫舊全域啟用狀態」的元件及責任生命週期，再決定整場候選；同時保留正常盈利場，檢查主被動後續加倉是否耗掉修復成果。不要重跑已完成job、不要用未來勝方作runtime輸入、不要改實單或資料收集服務。')
add('原始V1～V34歷史、V35～V58原件與全部限制均可由本檔連結追溯。核對清單另存與本檔同名的CHECK.json，記錄此次交接製作使用的source hashes；不是新增模型或交易結果。')
text=re.sub(r'\|\n\n(?=\|)', '|\n', '\n\n'.join(lines)+'\n')
OUT.write_text(text,encoding='utf8')
check=dict(status='HANDOFF_WRITTEN_PENDING_LINK_AND_EVIDENCE_CHECK',scope='V34 handoff boundary through V58; no new experiment',handoff_file=OUT.relative_to(ROOT).as_posix(),handoff_sha256=sha(OUT),source_hashes=sources,packages=registry,new_native=0,new_fit=0,worker_dispatch=0,live_changes=0)
CHECK.write_text(json.dumps(check,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({'handoff':str(OUT),'lines':len(OUT.read_text(encoding='utf8').splitlines()),'sources':len(sources),'packages':len(registry)},ensure_ascii=False))

if __name__=='__main__':pass
