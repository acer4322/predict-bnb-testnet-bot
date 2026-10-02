"""Finalize small collected evidence without further replay, training or expanded gates."""
from pathlib import Path
import hashlib
import json

ROOT=Path(__file__).resolve().parents[1]
R=ROOT/'data/research/r4_v0/p0_provenance_v1'
FAILED=ROOT/'data/research/lan_worker_returns/minimal-student-training-rules-v2-20260910'
DONE=ROOT/'data/research/lan_worker_returns/minimal-student-training-rules-v2-clockfix-20260910'


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(262144),b''):h.update(b)
    return h.hexdigest()


def main():
    assert sha(FAILED/'COMPACT.json')=='c7cc7ccfd094066b8101b1cb42dc83c49944301d4d8c940db66be36121e42296'
    assert sha(DONE/'COMPACT.json')=='b767107d17ae911b825e0898ec153353002e5da6d3ac5fe88d77ee865e2a4309'
    old=json.loads((FAILED/'COMPACT.json').read_text(encoding='utf-8'))
    r=json.loads((DONE/'COMPACT.json').read_text(encoding='utf-8'))
    assert r['verdict']=='NATIVE_RULE_WITNESS_NOT_FULLY_EXERCISED'
    assert r['unitTests']==dict(run=41,failures=0,errors=0,passed=True)
    assert len(r['rows'])==1 and r['nativeComplete']==1
    x=r['rows'][0];trace=DONE/x['trace']['path']
    assert trace.stat().st_size==x['trace']['bytes'] and sha(trace)==x['trace']['sha256']
    assert x['lateNewOrders']==5 and x['maxLiveSlots']==5 and x['nativeReceipts']==0
    assert x['keptBeyondRemovedTTL'] and not x['unresolved'] and x['nonDisplayedNewPrices']==0
    rules=[
        dict(rule='180s all-role and speculative-only hard entry veto',decision='REMOVED_FROM_TRAINING_WORLD',evidence='native5new at179.940s + pure boundary tests'),
        dict(rule='Pair average-cost sum<=1 / forced floor protection / forced balance',decision='POLICY_ECONOMICS_NOT_WORLD_VETO',evidence='unit costly opposite and negative-floor ADD reachable; no new economic rollout claim'),
        dict(rule='TTL5s automatic cancel',decision='REMOVED_AUTOMATIC_CANCEL_POLICY_OWNS_KEEP_CANCEL',evidence='native5orders kept past5s; explicit cancellation age5198ms'),
        dict(rule='book membership-driven reanchor/cancel',decision='POLICY_DECISION_NOT_ENVIRONMENT',evidence='unit KEEP with absent price; native disappearing-price case not exercised'),
        dict(rule='new quote must equal currently displayed price',decision='REMOVED_MEMBERSHIP_REQUIREMENT_KEEP_POSTONLY_LEGALITY',evidence='unit non-displayed/improved price PASS; native selected ticks all displayed; coverage pending'),
        dict(rule='passive4 / active1 pools',decision='REPLACE_WITH_DECLARED_RESOURCE_CENSOR',evidence='native5passive owners; ledger2active unit only; resource32 fixture, native ACTIVE unsupported'),
        dict(rule='same-side serialization / one-new-per-receipt / legacy role priority',decision='NO_HIDDEN_POLICY_IN_CONSUMER',evidence='five same-side new children in one native plan; hidden legacy hooks fail closed'),
        dict(rule='fixed q=1/price or fixed18/30/55 teacher quantity',decision='EXPLICIT_REQUESTED_QTY_NOT_WORLD_FIXED_SIZE',evidence='18/30/55/100 exact unit tests; native18 test case is NOT Target label'),
        dict(rule='cash50/50 and total add110 as immutable strategy rules',decision='EXPLICIT_MANAGER_ALLOCATIONS_WITHIN_TOTAL_CAP',evidence='native65/35, total100unchanged; acquisition limit update and spent/pending protection unit tests'),
        dict(rule='venue tick/lot/asset-route minimum; real market open/end',decision='KEEP_AS_DECLARED_EXECUTION_CONTRACT',evidence='BTC18/ETH12 andnotional1 are OUR assumptions not established Target originals; source-verified clocks; partials exempt'),
        dict(rule='cash cap, pending, cancel-pending, immutable debt/owner, no own-cross, canonical receipts',decision='KEEP_ACCOUNTING_AND_AUTHORITY_INVARIANTS',evidence='unit partial/late/duplicate/unowned receipts, atomic allocations and native zero-fill terminal closure'),
        dict(rule='source/receipt latency and queue model',decision='KEEP_EXECUTION_WORLD_ASSUMPTIONS_NOT_STRATEGY_DELAY',evidence='same native binary/receipt source hashes; no dream fills'),
        dict(rule='unsupported native ACTIVE',decision='CAPABILITY_GAP_NOT_HOLD_TEACHER',evidence='explicit unsupported; ledger capability does not imply native execution implemented'),
        dict(rule='Target unknown original qty, private placement/intent, OUR expert answers',decision='UNKNOWN_MASK_NOT_SYNTHETIC_TRUTH',evidence='0expert labels; diagnostic producer never policy target')]
    summary=dict(version='MINIMAL_STUDENT_TRAINING_RULES_V2_ACCEPTANCE',
        verdict='RULE_SEPARATION_IMPLEMENTED_UNIT_PASS_NATIVE_SUBSET_VERIFIED',
        overallNativePreregisteredGate='NOT_FULLY_EXERCISED',
        nonDisplayedNativeQuoteGate='NOT_EXERCISED_NO_PROMOTION',
        userResearch180OverrideApplied=True,live180SettingsChanged=False,
        unitTests=41,unitFailures=0,nativeAttemptsIncludingPreActionSchemaAbort=2,
        nativeCompletedMarkets=1,plannedMaximumMarkets=3,remainingTwoMarketsRun=False,
        testedMarket=2022527,nativeNewOrders=5,nativeCancelRequests=5,nativeFillReceipts=0,
        zeroFillTerminalOrders=5,unresolvedReservations=0,
        newOrderRemainingSeconds=(x['windowEndMs']-x['owners'][0]['placed'])/1000.,
        maxNativeOwners=5,keptBeyondOldTTL=True,firstExplicitCancelAgeMs=x['earliestCancelAgeMs'],
        fullInputPlanFrames=1486,nativeOwnStateObservations=1487,
        traceBytes=x['trace']['bytes'],traceSha256=x['trace']['sha256'],
        explicitBudgetAllocation={'UP':65.,'DOWN':35.},totalCapital=100.,initialCapital=100.,
        worldResourceCeiling=32,resourceCeilingIsTargetLaw=False,
        rules=rules,activeNativeImplemented=False,teacherLabels=0,modelFits=0,
        retainedWorldAssumptions=['native tick/lot.01','current OUR passive minima','same native latency/queue','actual market interval'],
        usedFreshOrSealed=False,newStrategyPerformanceClaim=False,
        worker=r['worker'],maxThreads=4,
        sourceResultPath=(DONE/'COMPACT.json').relative_to(ROOT).as_posix(),sourceResultSha256=sha(DONE/'COMPACT.json'),
        abortedResultSha256=sha(FAILED/'COMPACT.json'),
        modulePath='tools/minimal_student_training_world_v2.py',moduleSha256=sha(ROOT/'tools/minimal_student_training_world_v2.py'),
        nativeSha256=r['nativeSha256'],
        scope='RESEARCH_TRAINING_STUDENT_ONLY_NOT_8781',
        next='Use V2 to prepare complete-state whole-plan supervision/value evidence. Do not revive180/Pair/TTL gates or auxiliary classifiers. Missing native off-book branch and Active capability remain explicitly unverified.')
    sp=R/'MINIMAL_STUDENT_TRAINING_RULES_V2_ACCEPTANCE_20260910.json'
    assert not sp.exists();sp.write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
    report='''# 極簡學生：訓練規則去除舊策略偏見 V2

日期2026-09-10。使用者明確要求：180秒等使OUR與Target分裂的規則不得繼續當訓練環境限制，並授權逐项決定其他規則。只適用研究學生，不更動8781實單限制或資金。

## 結論與狀態
**已實作新的policy-neutral research world，41項單元測試通過；1場原生小測確認180後送單、第五張並行單、超過舊TTL保留及資金重新分配。整個事前原生coverage gate尚未全通過，不宣稱三場成功或全面驗收。**

原生缺口很具體：事先指定.06/.07/.08/.09/.10在該場都是已存在價位，因此沒有觸發「未出現在book的合法報價」原生分支。此規則的移除和單元測試已完成，但不能把未測到說成原生成交驗收。依事前stop規則，後兩場沒有再跑，沒有換價格湊過關。

## 一、已移除或改成策略決策
1. 原生入口的全role180s截止、grant gateway的180s後投機新增禁令，一起移出training world；不改成60/30秒。remaining time仍是資訊。
2. 平均Pair價和<=1、一定保護正floor、必須份額平衡，不作世界法律。價格/成本/兩端盈虧與風險仍必須計入整段評價；允許昂貴修復不等於推薦它。
3. TTL5s自動撤單、book價位消失就reanchor、固定role/weak-side優先，不由原生消費端偷偷執行。完整policy負責每次KEEP/CANCEL/NEW及後續延續；不能用舊producer包裝成無偏學生。
4. 新單不必等於已顯示的價格，仍需合法tick且符合PASSIVE/post-only，不暗中變成Taker。此部分原生branch coverage仍缺，見上。
5. 被動4／主動1、每次只能新一單、同側序列化，不當Target結構。新resource limit32為可宣告的工程界限，超過回RESOURCE_CENSOR而非專家HOLD；它不是4改32的策略調參。原生ACTIVE仍不支援。
6. 數量必須來自顯式意圖；不以q=1/p覆寫，不把18/30/55當固定單量或原始Teacher。既有asset minima也不能套到每筆partial fill。
7. 固定50/50與110份配額不再是不可調的策略法則。完整方案可明確改分配，但總資金100不變，不可搬走已花費、pending/cancel-pending資金或撤回已取得/已預留份額；既有parent/generation/repair debt與成交不重置。

## 二、保留的不是舊腦，而是交易與帳本現實
保留真實市場開盤/結束、當前声明tick/lot和asset×route最低委託規格、價格合法性、post-only與主動通道區分、own-cross（含pending）、cash/quantity authority、receipt idempotency、確認terminal才釋放、無未來資訊。

BTC18/ETH12＋notional1是目前OUR声明的被動規格，不是已證明Target原始單限制；不同時期有尺寸不相容時應分組／mask，不能擅自把Target成交量改成合規原單。執行延遲/queue模型保留為需校準的世界假設，不能教成策略等幾秒。Resource上限與資金不足也不能標成Target主動HOLD。

對同一明示Repair debt的repair-first allocation保留為責任記帳規則；不是要求交易方向優先修弱側，也不是看到imbalance就創造新debt。完整管理層負責要不要建立／服務何種經濟責任。

## 三、已核對的實際原生證據
固定consumed市場2022527，180秒舊邊界後第一個可見觀測remaining179.940s，整套提出5張UP18份，價格.06/.07/.08/.09/.10。5張都進入native owner，最高同時5張。

完整policy在舊TTL5秒後仍KEEP，至5198ms才顯式送出5次取消；最後5張全部CANCELED/TERMINAL，無未釋放預留。它們本次全部零成交，native fill receipts=0。因此只證送單/保留/取消/預留閉合，不證明新規則下的部分成交修復、多輪循環或盈利。

同一方案把原50/50改成UP65/DOWN35，總資金仍100；實際最大已用加預留只有7.20。資金搬動不是自動提高總資本。昂貴Pair、部分成交、晚到成交、重複credit、撤單等待與資金不可挪用等另在41項元件測試覆核，不混成此場原生已發生成交。

本次新增完整input frame匯出，1486個input+whole-plan、1487個native own-state觀測，壓縮318049bytes（原始約10.39MB）。含當時book/quotes、全部OWN/pending/ack、資金與責任、world profile及時間來源；actual後果在後續事件，不借Target庫存。這些是規則回歸測試資料，expert policy mask全部false，不能當Target訓練標籤。

## 四、中斷、恢復與計數
初始job39/39tests PASS，但舊tape沒有window_start_ms，第一個frame匯出前KeyError；0實際送單/0完成市場。沒有自行以end-300000補猜，而改綁已經source-verified的preflight market interval，核對native window_end；新增2個來源時間測試。

修正只涉及來源schema接合，沒有改數量、價格、時間案例、資金或策略。第二個job41/41tests PASS並完成1市場，因未顯示價位分支未命中而停止。兩個job均terminal且已collect。合計2次native嘗試（其中1次送單前中止）、1個完整市場，不宣稱三場通過。

主機僅source review/syntax/hash/copy與精簡結果；第二台DESKTOP-JIERAGF、max_threads4執行測試、native及完整trace串流覆核。沒有大量DB掃描、新fresh/SEALED、模型fit或8781修改。

## 五、為何不把解除180直接當策略進步
既有PAIR_CORE_FULL_HORIZON3_RETURN_20260910已測過僅放開180，增加活動但經濟結果更差。本輪沿用該負結果，不重新測成新假說。去除世界中的舊規則是讓完整policy有機會學到合理行動，不保證自動產生好policy；不能因小測亏損又把它們偷偷裝回世界。

正式系統學習仍要評價整條Floor/Upside、兩端結算、成本/資金、責任與活動；不能以單步pair、循環數、幾乎不交易或分類AUC代替。本輪0model fits。

## 六、接續與剩餘邊界
此使用者新授權覆蓋舊SYSTEM_LOGIC_CONTRACT中research180保留條款；歷史檔與結果保留，不覆蓋實單。新資料/訓練請顯式選`MINIMAL_SYSTEM_TRAINING_WORLD_V2`，不要用舊Minimal teacher反過來把全部硬規則教回來；舊策略只作具名基準。

不再重做已完成quantity/data/prefit或把此次零成交測試當下一個模型老師。沿用完整input/plan/own-state接口，建立有來源支持的系統級示範／方案結果。未知原始qty、私有目的、OUR狀態專家答案不能偽造。Native Active仍缺：這是能力缺口，不是可合理永久保留的Target限制，也不能用夢幻成交補上。未顯示合法價位的原生分支另列待驗，不能被unit PASS掩蓋。

## 證據路徑
- 實作 tools/minimal_student_training_world_v2.py
- 測試 tests/test_minimal_student_training_world_v2.py
- 本輪結果 data/research/lan_worker_returns/minimal-student-training-rules-v2-clockfix-20260910/COMPACT.json
- 結果SHA b767107d17ae911b825e0898ec153353002e5da6d3ac5fe88d77ee865e2a4309
- 新模組SHA e1c231f255ed8c77dc4f9a37464ec3960edc44effa7bd21b42ac170af608fa34
- Trace SHA01363d131a29f0feca0fbf73a44adb1a90407c04befc9339637d9ace510214a5
- Native SHA7de2335528234bea9381bea4208a90594a7bec9795d09b913f430ff1f8cf8ebf
- 舊Target後段觀察 PAIR_CORE_TARGET_POST180_CADENCE_RETURN_V1_20260910.md；150場後段成交證據不是精確placement-time證據。
'''
    rp=R/'MINIMAL_STUDENT_TRAINING_RULES_V2_RETURN_20260910.md'
    assert not rp.exists();rp.write_text(report,encoding='utf-8')
    current='''# 極簡系統學生：有效訓練規則入口（2026-09-10）

使用者最新明示：研究／訓練學生不得保留180秒硬截止，以及會冒充世界規則的舊策略判斷。**本入口優先於舊SYSTEM_LOGIC_CONTRACT的research180保留段與V1 native測試邊界；不改8781實單規則。**

先讀MINIMAL_STUDENT_TRAINING_RULES_V2_RETURN_20260910.md及MINIMAL_STUDENT_TRAINING_RULES_V2_ACCEPTANCE_20260910.json。

有效profile：MINIMAL_SYSTEM_TRAINING_WORLD_V2；module tools/minimal_student_training_world_v2.py。完整policy擁有KEEP/CANCEL/NEW與連續管理，世界只留可聲明的合法性、真實執行、資金與帳本不變式。180、Pair hard gate、TTL、自動reanchor、固定角色順序等不得在native消費端暗中回來。舊Minimal producer仍有這些偏好，僅作具名baseline，不當新學生老師。

Resource32是工程上限、50/50與110只是可由完整方案明確更新的初始測試分配；總資金100不變，不挪用spent/pending。Active仍native unsupported，不能當HOLD。BTC18/ETH12與notional1是目前OUR声明規格，不是Target原始qty已知；partial不套新單下限。

本輪41unit tests PASS，1個原生完整市場：179.94s後5單，5個並行owner，超過5秒KEEP，5198ms顯式取消，5零成交terminal、0未釋放，1486完整輸入方案frame。不是3場PASS：未顯示價位native分支未命中，後2場依stop規則未跑。初次schema中止0單，總2attempts/1complete。0fit/0live/0fresh。

完整訓練的教師／價值支持與Active能力缺口仍須如實處理；不回頭做旁路成交分類器、不將回歸測試策略當Target、不因解除限制就宣稱獲利。重運算仍第二台max_threads4。
'''
    cp=R/'MINIMAL_STUDENT_TRAINING_RULES_CURRENT_20260910.md'
    assert not cp.exists();cp.write_text(current,encoding='utf-8')
    print(json.dumps(dict(reportPath=rp.relative_to(ROOT).as_posix(),reportSha256=sha(rp),
        acceptancePath=sp.relative_to(ROOT).as_posix(),acceptanceSha256=sha(sp),
        currentEntry=cp.relative_to(ROOT).as_posix(),summary=summary),ensure_ascii=False))


if __name__=='__main__':main()
