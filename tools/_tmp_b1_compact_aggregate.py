import json
from pathlib import Path
src=Path('data/research/lan_worker_returns/continuation-protocol-crossover-smoke4-20260908-v1/result.json')
outp=Path('data/research/r4_v0/p0_provenance_v1/CONTINUATION_PROTOCOL_CROSSOVER_SMOKE4_V1_COMPACT_20260908.json')
d=json.loads(src.read_text(encoding='utf-8'))
out={'version':'CONTINUATION_PROTOCOL_CROSSOVER_SMOKE4_V1_COMPACT_20260908','source':str(src),'allCorrectnessPass':d['allCorrectnessPass'],'verdict':d['verdict'],'summary':d['summary'],'rows':[]}
for r in d['rows']:
    rr={'marketId':r['marketId'],'t':r['t'],'researchStratum':r.get('researchStratum'),'correctnessPass':r['correctnessPass'],'checks':r['checks'],'exercise':r['exercise'],'estimand':r['estimand'],'seedDirectSignatureSame':r['seedDirectSignatureSameForCleanInterpretation'],'activityExposureConfounded':r['activityExposureConfounded'],'activityConfoundDetails':r['activityConfoundDetails'],'branches':{}}
    for b in ('NATIVE','II','FI','IF','FF'):
        z=r['branches'][b]; a=z['activity']; p=z['payoff']; cats={}
        for k,v in z['cashflow']['categories'].items():
            cats[k]={q:v[q] for q in ('fills','qty','repairQty','overflowQty','notional','upPayoffDelta','downPayoffDelta')}
        rr['branches'][b]={'payoff':p,'seedDirectFillPaymentSignature':z['seedDirectFillPaymentSignature'],'futureSuppressedDistinctCount':z['futureSuppressedDistinctCount'],'activity':a,'cashflow':cats,'accountingChecks':z['accountingChecks']}
    out['rows'].append(rr)
outp.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'ok':True,'output':str(outp),'verdict':out['verdict'],'rows':[(r['marketId'],r['estimand']['deltaI'],r['estimand']['deltaF'],r['estimand']['Gamma'],r['estimand']['terminalRankReversal'],r['activityExposureConfounded']) for r in out['rows']]},ensure_ascii=False))
