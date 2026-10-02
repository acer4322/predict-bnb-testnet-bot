"""Finalize already auto-collected small evidence. No transfer, replay or fitting."""
from pathlib import Path
import hashlib
import json

ROOT=Path(__file__).resolve().parents[1]
D=ROOT/'data/research/lan_worker_returns/minimal-student-native-system-plan-20260910-v1'
R=ROOT/'data/research/r4_v0/p0_provenance_v1'


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for x in iter(lambda:f.read(262144),b''):h.update(x)
    return h.hexdigest()


def main():
    p=D/'COMPACT.json';assert p.stat().st_size<40000
    assert sha(p)=='a9cdc06964b01a42e9e518adb7f4d86f0cef59873c9a5bced864650dc5159aa6'
    r=json.loads(p.read_text(encoding='utf-8'))
    assert r['verdict']=='NATIVE_WHOLE_PLAN_SHAM_AND_CONTROL_AUTHORITY_PASS'
    assert r['attemptedBE']==r['completedBE']==5 and r['tests']==dict(run=17,failures=0,errors=0,passed=True)
    for run in r['runs']:
        t=D/run['trace']['trace'];assert t.exists() and t.stat().st_size<200000
        assert sha(t)==run['trace']['trace_sha256']
        assert not run['unresolved']
    refs=[x for x in r['runs'] if x['mode']=='REFERENCE']
    sham=[x for x in r['runs'] if x['mode']=='SHAM']
    probe=next(x for x in r['runs'] if x['mode']=='CONTROL_PROBE')
    comparisons=[]
    for a,b in zip(refs,sham):
        assert a['marketId']==b['marketId']
        assert a['trace']['counts']==b['trace']['counts'] and a['trace']['hashes']==b['trace']['hashes']
        assert a['owners']==b['owners']
        comparisons.append(dict(marketId=a['marketId'],qty=a['requestedCase'],submits=a['submits'],
            cancelRequests=a['trace']['counts']['actions']-a['submits'],receipts=a['nativeReceipts'],
            ownStateCheckpoints=a['trace']['counts']['states'],exactActionReceiptStateStreams=True,
            planFrames=b['planFrames'],ownersExactlyEqual=True))
    ref=refs[0]
    summary=dict(version='MINIMAL_STUDENT_NATIVE_SYSTEM_PLAN_ACCEPTANCE_V1',
        verdict='PASS_NATIVE_PASSIVE_CONTROL_INTERFACE_NOT_LEARNED_SYSTEM',
        nativeJob='minimal-student-native-system-plan-20260910-v1',jobState='succeeded',
        worker=r['host'],maxThreads=4,workerElapsedSeconds=r['elapsedSeconds'],
        uniqueConsumedMarkets=2,nativeAttempts=5,nativeCompleted=5,unitTests=17,
        shamComparisons=comparisons,
        shamTotals=dict(submits=sum(x['submits'] for x in comparisons),
            cancelRequests=sum(x['cancelRequests'] for x in comparisons),
            nativeReceipts=sum(x['receipts'] for x in comparisons),
            stateCheckpoints=sum(x['ownStateCheckpoints'] for x in comparisons),
            planFrames=sum(x['planFrames'] for x in comparisons)),
        controlProbe={k:probe[k] for k in ['marketId','submits','nativeReceipts','inventory','cost',
            'UPBranch','DOWNBranch','partialTerminalOrders','unresolved','planFrames','multiNewPlans',
            'postReceiptPlans','planKinds','policyIds','hiddenNativeDecisionFallbacks']},
        branchComparisonDiagnosticOnly=dict(referenceUP=ref['UPBranch'],referenceDOWN=ref['DOWNBranch'],
            probeUP=probe['UPBranch'],probeDOWN=probe['DOWNBranch'],
            referenceFloor=min(ref['UPBranch'],ref['DOWNBranch']),
            probeFloor=min(probe['UPBranch'],probe['DOWNBranch']),
            floorDelta=min(probe['UPBranch'],probe['DOWNBranch'])-min(ref['UPBranch'],ref['DOWNBranch']),
            winnerRead=False,actualPnlNotScored=True,feesAndImpactNotCertified=True),
        nativeCapabilities=['PASSIVE'],nativeActiveUnsupported=True,economicGrantIssuerImplemented=False,
        trainingModelFits=0,completeTargetTeacher=False,liveChanges=0,productionChanges=0,
        sameScaleTargetTraining=False,traceFilesVerified=5,
        fullModelInputFramesExported=False,
        nextScope='Whole-policy demonstrable supervision/value with complete own-state context and continuation; no return to fill-mark classifier. Active and economic grants remain explicit capability gaps.',
        sourceResultPath=p.relative_to(ROOT).as_posix(),sourceResultSha256=sha(p),
        adapterPath='tools/minimal_student_native_system_plan_v1.py',
        adapterSha256=r['loadedSourceHashes']['tools.minimal_student_native_system_plan_v1'],
        nativeSha256=r['nativeSha256'],
        resultCollection='Manual collect blocked; pre-existing automatic collector completed. Local result and five trace files were read and hashes verified. No alternate transfer used.',
        masterIndexUpdated=False)
    sp=R/'MINIMAL_STUDENT_NATIVE_SYSTEM_PLAN_ACCEPTANCE_V1_20260910.json'
    assert not sp.exists();sp.write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
    report='''# 極簡學生：系統方案原生接線驗收 V1

2026-09-10，接續SYSTEM_LOGIC_CONTRACT；本輪沒有重跑旁路分类器、份額前置稽核或只做合成gateway。

## 裁決
**PASS：被動學生的完整方案入口已接到原生回放，完成原策略等價驗收與替代完整方案的控制權驗收。不是已學會Target、不是獲利畢業。**

2個已消耗普通市場，5次原生回放全部完成；17項介面unit tests PASS。第二台DESKTOP-JIERAGF、max_threads4，主工作約74.17秒。0模型fit、0fresh、0live8781/production修改。原始Minimal/V3/V2、quantity seam、gateway及native binary hash均保留。

## 1. 原策略經新入口：兩場逐筆一致
|市場|明示份額案例|新單|取消請求|原生成交收據|逐回報自身狀態|結果|
|---|---:|---:|---:|---:|---:|---|
|2022527|30|8|3|19|1487|逐筆完全一致|
|2022538|55|47|45|13|1489|逐筆完全一致|
|合計|非訓練標籤|55|48|32|2976|全部hash/count及最終owners一致|

Reference與Sham各執行一次完整市場，表中數字按單一路徑，不把兩次重複執行當兩倍有效樣本。比較的是實際native submit/cancel stream、完整canonical receipt stream，以及每次native process之後的持倉/成本/owner累計與狀態，而不是只比較最終PnL。

新入口共產生2974個完整plan frame，全部由同一已聲明policy+continuation產生。Sham producer明確保留舊策略作為隔離的參考policy，沒有假稱它是學習模型。其detached view無native engine、future tape、winner或Target私人持倉；消費端不能自行回頭呼叫未聲明的角色、選價、TTL或reanchor決策。

## 2. 控制權測試：不是只記錄建議
事前固定的一個獨立手寫完整方案，在2022527透過同一入口：
- 一個plan同時提出UP與DOWN各30份，兩張native passive訂單送出。
- 產生5筆原生成交收據、1筆部分成交終止、1次native取消。
- 三個含真實新收據的後續觀測均再由同一policy處理；整場1486個plan frame沒有換回旧controller。
- 最終UP6份、DOWN30份，成本17.22，無未釋放預留。

這只證明joint plan與自身狀態回饋能控制真實回放。大量空plan不算交易活動或學習經驗；2張單/1取消/3次receipt後規劃才是本probe有實際支援的部分。不是完整反覆多輪修復/擴張已成熟。

## 3. 不能把工程PASS說成經濟PASS
手寫probe兩側原始報價.52與.47相加雖小於1，但成交不對稱：UP只6、DOWN30。結算分支為UP=-11.22、DOWN=+12.78；原參考同場則UP=+5.480392、DOWN=+0.519608。Probe worst floor相對參考下降約11.739608。

這是兩個條件結算分支，沒有讀winner，也沒有完整實盤費用/衝擊認證。不能挑+12.78宣稱改善，更不能把不同持倉的兩端拼成新策略。這個手寫方案從一開始就只用來驗證控制權，未被選作經濟候選或policy teacher；看到結果後未改規則重測。

此例實際展示：同時掛出看似便宜的雙邊單，不等於後續成交、取消、剩餘責任與延續組成了一個安全系統。正式訓練需要對整段後果負責，而非只給joint提交或單步pair geometry好評。

## 4. 新入口實作與保留邊界
`tools/minimal_student_native_system_plan_v1.py`：producer收causal current frame，輸出完整KEEP/CANCEL/NEW與角色/價格/份額/continuation。每個仍占用授權的owner必須明列處置；native消費端先驗state/market frame及全部授權，再逐筆送單。原生多單不是原子成交；未知送出/晚到成交不rollback為未發生。

保留Pair gate、4slots、取消等待占用、原生terminal才釋放、<=180秒禁止新單，以及BTC被動最低18份/金額1。這些是此測試學生的明示邊界，不是Target規則。30/55為份額傳值案例，不代表已取得Target原始requested qty或近期同尺度訓練。

native本輪仍只有PASSIVE。ACTIVE整套拒絕，不刪除後默認HOLD；Economic Grant來源仍是capital100、每側cash50/quantity110的既定測試授權，不是已學到的動態資金／責任管理。

## 5. 正式訓練還缺什麼，以及接續點
已接通「外部完整policy → 原生行動 → 自身回饋 → 同policy延續」，因此不再需要回去訓練成交類型分類器來證明控制器存在。

但目前producer仍是舊policy或手寫probe；沒有Target在OUR當前state的專家答案，也沒有足夠獨立完整方案/後續結果支援經濟價值學習。此次trace保存完整方案、frame hash、原生行動/收據、持倉成本及帳務來源，但沒有直接匯出每一個完整模型輸入frame，不能把它說成可立即餵網路的完整system dataset。

下一步沿用此consumer，把完整自身state/authority/時點可见資訊與整段policy continuation接成可學樣本與明確結果評分；候選與老師必須有可觀察或可回放支援，UNKNOWN不補假標籤。評價同時看兩個結算分支、整段Floor/Best、資金、未解責任、活動和有害干預。Active/經濟授權來源仍要明列，不能把被動小範圍稱FULL_TARGET，也不能因缺私有資訊永久退回無控制權的旁路分類器。

## 6. 回傳與索引
工作已succeeded、rc0。手動collect被工具阻擋，沒有改走另一種傳輸繞過；先前啟動的auto collector已完成。已在主機讀取COMPACT及五個trace檔，全部壓縮檔SHA256核對通過。

本輪寫入新的專用RETURN與ACCEPTANCE，不聲稱共用MASTER索引已更新。下輪直接讀本檔及SYSTEM_LOGIC_CONTRACT接續，勿只依舊MASTER最後entry。

## 證據
- result：data/research/lan_worker_returns/minimal-student-native-system-plan-20260910-v1/COMPACT.json
- result SHA256：a9cdc06964b01a42e9e518adb7f4d86f0cef59873c9a5bced864650dc5159aa6
- adapter SHA256：1137d6d3128bf0cc81b633ec87b45a5d56306d341fbd6fa45e9010d8e61a0931
- native SHA256：7de2335528234bea9381bea4208a90594a7bec9795d09b913f430ff1f8cf8ebf
- prereg：data/research/r4_v0/p0_provenance_v1/MINIMAL_STUDENT_NATIVE_SYSTEM_PLAN_PREREG_V1_20260910.md
- acceptance：data/research/r4_v0/p0_provenance_v1/MINIMAL_STUDENT_NATIVE_SYSTEM_PLAN_ACCEPTANCE_V1_20260910.json
'''
    rp=R/'MINIMAL_STUDENT_NATIVE_SYSTEM_PLAN_RETURN_V1_20260910.md'
    assert not rp.exists();rp.write_text(report,encoding='utf-8')
    print(json.dumps(dict(summaryPath=sp.relative_to(ROOT).as_posix(),summarySha256=sha(sp),
        reportPath=rp.relative_to(ROOT).as_posix(),reportSha256=sha(rp),summary=summary),ensure_ascii=False))


if __name__=='__main__':main()
