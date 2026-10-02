"""Combine two already-collected small result files; no replay or training."""
from pathlib import Path
import hashlib
import json

ROOT=Path(__file__).resolve().parents[1]
R=ROOT/'data/research/r4_v0/p0_provenance_v1'
J1='minimal-student-quantity-smoke3-20260910-v1'
J2='minimal-student-quantity-trace-retry-20260910-v2'


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    paths=[ROOT/'data/research/lan_worker_returns'/j/'COMPACT.json' for j in (J1,J2)]
    assert all(p.stat().st_size<30000 for p in paths)
    a,b=[json.loads(p.read_text(encoding='utf-8')) for p in paths]
    assert a['error']=='AssertionError: bounded trace exceeded; never truncate silently'
    assert b['verdict']=='EXACT_QUANTITY_NATIVE_CAPTURE_RETRY1_PASS_NOT_TRAINING_READY'
    assert a['loadedSourceHashes']==b['loadedSourceHashes'],'policy/source drift on logger-only retry'
    assert a['nativeSha256']==b['nativeSha256']
    assert all(x['tests']==dict(run=25,failures=0,errors=0,passed=True) for x in (a,b))
    rows=a['rows']+b['rows'];assert [r['marketId'] for r in rows]==[2022527,2022538,2022602]
    compact=[]
    for row,p in [(a['rows'][0],paths[0]),(a['rows'][1],paths[0]),(b['rows'][0],paths[1])]:
        assert row['correctness'] and row['unresolvedCount']==0
        assert all(o['requestedQty']==row['requestedCase'] for o in row['orders'])
        trace=p.parent/row['trace'];assert trace.stat().st_size<1024**2 and sha(trace)==row['traceSha256']
        compact.append({k:row[k] for k in ['marketId','requestedCase','submits','nativeReceipts',
            'zeroFillOrders','partiallyFilledOrders','unresolvedCount','maxSlots','minSubmittedPrice',
            'traceEvents','traceBytes','traceSha256','UP','DOWN','cost','UPBranch','DOWNBranch']})
        compact[-1]['tracePath']=trace.relative_to(ROOT).as_posix()
    result=dict(version='MINIMAL_STUDENT_QUANTITY_SEAM_ACCEPTANCE_V1',
        verdict='SIZE_INTERFACE_AND_OWN_NATIVE_CAPTURE_PASS_LIMITED_SCOPE',
        componentTestsUnique=25,componentTestsPass=True,completedMarkets=3,nativeAttempts=4,
        loggingOnlyRetry=True,sourceHashesIdenticalAcrossRetry=True,
        submits=sum(r['submits'] for r in rows),nativeReceipts=sum(r['nativeReceipts'] for r in rows),
        zeroFillOrders=sum(r['zeroFillOrders'] for r in rows),
        partiallyFilledOrders=sum(r['partiallyFilledOrders'] for r in rows),
        unresolvedReservations=0,ownDecisionRecords=sum(r['traceEvents']['OWN_DECISION'] for r in rows),
        traceCompressedBytes=sum(r['traceBytes'] for r in rows),rows=compact,
        originalSourceHashes=a['loadedSourceHashes'],nativeSha256=a['nativeSha256'],
        resultSources=[dict(path=p.relative_to(ROOT).as_posix(),sha256=sha(p)) for p in paths],
        quantityDomain=[dict(q=18,firstTickPrice=.06),dict(q=30,firstTickPrice=.04),dict(q=55,firstTickPrice=.02)],
        domainAssumptions=dict(passiveMinNotional=1.,researchTick=.01,btcMinimumShares=18.,ethMinimumShares=12.),
        originalTargetRequestedQuantity='UNKNOWN_NOT_FILLED_FROM_OBSERVED_QTY',
        modelFits=0,liveChanges=0,freshRowsUsed=0,profitabilityClaim=False,
        limitations=['30/55 are external transport test cases, not original Target-size labels.',
            'Consumed Sep7 markets with scenario sizes are not a same-era imitation-training dataset.',
            'Virtual100/two50cash/110qty-per-side grants are test fixtures; private Target capital unknown.',
            'Low-price legal boundary validated in component tests, NOT exercised by native accepted orders below.06.',
            'Own decision/receipt states recorded; public-feature join, reliable teacher and loss masks remain.',
            'Native queue/timing/model assumptions remain; receipt correctness is not market-impact/full-cost validation.',
            'Pair/slot4/legacy all-role180s/TTL restrictions remain; Active unsupported, not negative HOLD labels.'])
    op=R/'MINIMAL_STUDENT_QUANTITY_SEAM_ACCEPTANCE_V1_20260910.json'
    assert not op.exists();op.write_text(json.dumps(result,indent=2),encoding='utf-8')
    report='''# 極簡學生：份額動作介面與原生回饋驗收

2026-09-10。接續上輪前置檢查；不是重跑原preflight。

## 本輪結論
**份額介面與三場原生OWN狀態回饋通過；未開始模型擬合，未宣稱完整Target模仿或獲利。**
新模組`tools/minimal_student_quantity_seam_v1.py`以research-local subclass接在凍結MinimalPairRoleSim上。原始Minimal/V3/V2未修改；重試前後全部loaded-source hash與native hash相同。

動作先提出明確份額，再檢查同一價格上的金額下限、份額下限、量刻度、現金及共享授權。不再q=1/p覆寫數量，也不以max12裁掉book價格。超過現金/份額授權時拒絕，不自動縮成另一筆單，不自動增發grant。缺少Active回報UNSUPPORTED，不當HOLD教學。

## 份額與最低合法價格
在OUR被動最低金額1、固定本次q與研究tick.01下，p>=1/q：18份首個合法tick.06、30份.04、55份.02。BTC至少18／ETH至少12仍只是新被動委託下限，不是上限，更不是固定份額。
所以「先保存參考尺度，再做部署尺度校準」方向合理；但不能固定55套所有舊場，不能把不同時期/資產的q與price拆開後亂配。已知原始quantity才可作精確尺寸標籤；累計成交量和不完整委託推估另欄，UNKNOWN不填18或30/55。
這是合法性算術與元件證據，沒有查證Target近期原始最低掛價真的下降，更沒有辨識其因果。較大q可能因最低金額、資金、深度共同改变可行動作；合法不等於經濟有利。

## 完成結果
|市場|預先指定尺寸案例|送單|原生收據|零成交訂單|未全成即終止訂單|未釋放預留|
|---|---:|---:|---:|---:|---:|---:|
|2022527|30|8|19|1|1|0|
|2022538|55|47|13|44|0|0|
|2022602|55|8|10|4|2|0|
|總計|非比較處理組|63|42|49|3|0|

25項不同元件測試全通過；重試再跑相同25項，不記成50項不同測試。3場共4458筆OWN decision記錄，完整OWN狀態/明確委託/原生收據/下一狀態事件串已保存成3個gzip，共588112bytes。零成交、部分成交與拒絕未刪除。拒絕事件通常是同一決策枚舉多個價格，不是獨立樣本或Teacher HOLD。

63張owner的requested qty均保留該案例的30/55。收據份額、現金、policy持倉、grant帳本及native帳務核對通過。最大槽位不超4、serializationFalse、所有新單仍在剩餘180秒以上。最低實際送出價格依序.45/.31/.40；因此.02/.04/.06的低價邊界只有本輪元件驗證，不能宣稱原生低價排隊/成交已驗收。

## 已處理的工程中斷
V1第三場因記錄超過30000事件而中斷；前兩場已完成。保留V1原結果與第三場不完整trace，僅重試2022602。修改的是logger資源界限：按32MiB未壓縮內容上限串流寫入，而不是粗略筆數上限。沒有改policy、數量、資金、排序或選場；來源hash逐項相同。最終共有4次native嘗試、3個完整市場，不隱藏一次不完整嘗試。兩job都已terminal並collect，沒有尚待回傳工作。

## 測試邊界，不可省略
本輪q30/55只是外部指定的傳值/收據案例，不是訓練好的policy。使用9/7已消耗市場，並不是把9/10的Target尺寸當9/7當時可知的Teacher，不能作時序經濟驗證。假設虛擬capital100、每側cash50/quantity110作預先明示的授權測試，並非查得Target資金，也不是與無此限制的舊Minimal做純尺寸A/B。

預算維度也要納入後續學習：單量相同、資金比例不同，仍可能較早耗盡budget而走到不同狀態。Fixture的拒絕不能誤當Target想HOLD。保留绝對q/price/深度/資金與正規化指標，不把整個世界等比縮放。

Native回放沿用risk queue、250ms entry/response、tick/lot.01與已驗證receipt binary。這不是實盤、不是任意大單無衝擊的保證，也未補完真實成本。HftBacktest官方Order Fill說明歷史回放不會因OUR單改寫市場深度/成交；大尺寸不能靠舊fill或PnL乘倍率代替重測。外部參考：https://hftbacktest.readthedocs.io/en/latest/order_fill.html 。

## 下一個精確接點
不要再重做份額preflight。接下來定義可訓練資料契約：同時期同資產的可觀察行為、原始qty未知遮罩、OUR自己狀態、依保存時鐘的市場特徵、unsupported與budget rejection分離。先做小型受限模仿，不等待Target所有私有資料；但不得以假原單尺寸、歷史Target下一筆或OUR固定案例當真實專家。真正經濟價值／部署尺寸校準各自驗收。沒有自動進Stage-A16、沒有大規模訓練、沒有8781變更。

## 檔案
- 介面：tools/minimal_student_quantity_seam_v1.py
- 測試：tests/test_minimal_student_quantity_seam_v1.py
- 事前契約：data/research/r4_v0/p0_provenance_v1/MINIMAL_STUDENT_QUANTITY_SEAM_SMOKE3_PREREG_V1_20260910.md
- 紀錄修正：data/research/r4_v0/p0_provenance_v1/MINIMAL_STUDENT_QUANTITY_TRACE_RETRY_AMENDMENT_V2_20260910.md
- 完整驗收JSON：data/research/r4_v0/p0_provenance_v1/MINIMAL_STUDENT_QUANTITY_SEAM_ACCEPTANCE_V1_20260910.json
- V1 result：data/research/lan_worker_returns/minimal-student-quantity-smoke3-20260910-v1/COMPACT.json
- 第三場retry：data/research/lan_worker_returns/minimal-student-quantity-trace-retry-20260910-v2/COMPACT.json
- Native SHA：7de2335528234bea9381bea4208a90594a7bec9795d09b913f430ff1f8cf8ebf
'''
    rp=R/'MINIMAL_STUDENT_QUANTITY_SEAM_RETURN_V1_20260910.md'
    assert not rp.exists();rp.write_text(report,encoding='utf-8')
    note='''\n\n<!-- MINIMAL_STUDENT_QUANTITY_SEAM_ACCEPTANCE_V1_20260910 -->
## 極簡學生份額介面：最新接續
`MINIMAL_STUDENT_QUANTITY_SEAM_RETURN_V1_20260910.md`覆蓋上一輪此支線的尺寸未接合狀態：research-local exact-q介面已接，25tests PASS、三場OWN native receipt capture PASS；q=30/55原樣63單、42收據、零未釋放預留。V1 logger中斷第三場，僅修logging後單場retry，合計4attempts/3complete；原碼/native hashes一致、全collect。0modelFits/0live，非profitability/完整Teacher PASS。低價domain .02/.04/.06僅元件測試，不誇大native低價成交。原Target requested qty未知；新舊時期不可強套同一固定數量；budget/深度/合法性要一起保存。下一步同時期可觀察模仿資料契約與UNKNOWN label mask，不重跑preflight、不直接大訓練。詳列fixture100資金/每側50與110份，非Target額度，不是策略改良績效。其他root-value主線不覆蓋。
'''
    for name in ['GPT6_MASTER_ENTRYPOINT_MIN_V2_20260910.md','PAIR_CORE_RULE_FIDELITY_CURRENT_20260910.md']:
        p=R/name;assert 'MINIMAL_STUDENT_QUANTITY_SEAM_ACCEPTANCE_V1_20260910 -->' not in p.read_text(encoding='utf-8')
        with p.open('a',encoding='utf-8') as f:f.write(note)
    print(json.dumps(dict(summaryPath=op.relative_to(ROOT).as_posix(),summarySha256=sha(op),
        reportPath=rp.relative_to(ROOT).as_posix(),reportSha256=sha(rp),
        **{k:v for k,v in result.items() if k not in ('originalSourceHashes','resultSources')}),ensure_ascii=False))


if __name__=='__main__':main()
