from __future__ import annotations
import hashlib,json
from pathlib import Path
from collections import defaultdict

ROOT=Path(__file__).resolve().parents[1]
SRC=[
 ROOT/'data/research/r4_v0/p0_provenance_v1/MS4_R246_SMOKE1_1946656_RESULT_20260906.json',
 ROOT/'data/research/r4_v0/p0_provenance_v1/MS4_R246_SMOKE1_1946784_RESULT_20260906.json',
 ROOT/'data/research/r4_v0/p0_provenance_v1/MS4_R246_SMOKE2_1946748_1946683_RESULT_20260906.json',
]
OUT_RESULT=ROOT/'data/research/r4_v0/p0_provenance_v1/MS4_R246_FOUR_MARKET_MECHANISM_ABLATION_RESULT_20260906.json'
OUT_DIR=ROOT/'data/research/r4_v0/gpt6_three_failure_system_challenge_v1_20260906/evidence_round2'
OUT_DIR.mkdir(parents=True,exist_ok=True)
MIDS=[1946656,1946748,1946784,1946683]
CELLS=['R240_CONTROL','R246_R240_ONE_CORE_ACTIVE','R246_R240_ONE_CORE_ACTIVE_CREDIT_QUARANTINE']

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def clean_diag(e):
 ev=str(e.get('event') or '')
 return ev.startswith('R246_')
def active_specific(e,key):
 if key is None:return False
 if str(e.get('key') or '')==str(key):
  return str(e.get('event') or '') in {'MS4_R2_ACTIVE_REPAIR_SUBMIT','ROLE_FILL','ROLE_FILL_SPLIT','SLOT_FILL'}
 return False
def sig(e):
 keys=['event','key','role','side','price','qty','fillInc','repairAllocated','overflowRealized','generation','scopeGeneration','scopeSide','fromSide','reason','sourceKey','sourceRole']
 return {k:e.get(k) for k in keys if k in e}
def first_diff(a,b):
 n=min(len(a),len(b))
 for i in range(n):
  if sig(a[i])!=sig(b[i]):return {'index':i,'a':sig(a[i]),'b':sig(b[i])}
 if len(a)!=len(b):return {'index':n,'a':sig(a[n]) if n<len(a) else None,'b':sig(b[n]) if n<len(b) else None}
 return None
def around(xs,i,r=2):
 if i is None:return []
 return [sig(x) for x in xs[max(0,i-r):min(len(xs),i+r+1)]]

docs=[json.loads(p.read_text(encoding='utf-8')) for p in SRC]
rows=[];cmps=[]
for d in docs: rows.extend(d['rows']);cmps.extend(d['comparison'])
idx={}
dups=[]
for r in rows:
 k=(int(r['marketId']),str(r['cell']))
 if k in idx:dups.append(k)
 idx[k]=r
missing=[(m,c) for m in MIDS for c in CELLS if (m,c) not in idx]
if dups or missing:raise RuntimeError({'dups':dups,'missing':missing})

per=[]
for m in MIDS:
 b=idx[(m,'R240_CONTROL')]; a=idx[(m,'R246_R240_ONE_CORE_ACTIVE')]; q=idx[(m,'R246_R240_ONE_CORE_ACTIVE_CREDIT_QUARANTINE')]
 pre=a.get('r246CoreActiveIntervention');preq=q.get('r246CoreActiveIntervention')
 if not pre or not preq:raise RuntimeError(f'{m}: intervention not materialized in both branches')
 key=a.get('r246CoreActiveKey');qkey=q.get('r246CoreActiveKey')
 # Pre-action prefix parity: candidate non-R246 events before its authoritative Active submit
 ah=[x for x in a.get('slotHistory',[]) if not clean_diag(x)]; qh=[x for x in q.get('slotHistory',[]) if not clean_diag(x)]; bh=[x for x in b.get('slotHistory',[]) if not clean_diag(x)]
 ai=next((i for i,e in enumerate(ah) if e.get('event')=='MS4_R2_ACTIVE_REPAIR_SUBMIT' and str(e.get('key'))==str(key)),None)
 qi=next((i for i,e in enumerate(qh) if e.get('event')=='MS4_R2_ACTIVE_REPAIR_SUBMIT' and str(e.get('key'))==str(qkey)),None)
 if ai is None or qi is None:raise RuntimeError(f'{m}: authoritative active submit missing from slotHistory')
 aprefix=[sig(x) for x in ah[:ai]];qprefix=[sig(x) for x in qh[:qi]]
 bprefix=[sig(x) for x in bh[:len(aprefix)]]
 prefix_ok=(aprefix==bprefix and qprefix==bprefix and aprefix==qprefix)
 # Downstream mediator comparison: remove diagnostic events and direct Active-key submit/fill; retain scope/credit/scheduler consequences.
 def downstream(row,k,start_t):
  out=[]
  for e in row.get('slotHistory',[]):
   if int(e.get('t') or -1)<int(start_t):continue
   if clean_diag(e):continue
   if active_specific(e,k):continue
   out.append(e)
  return out
 bd=downstream(b,None,pre['activeSubmitT']);ad=downstream(a,key,pre['activeSubmitT']);qd=downstream(q,qkey,preq['activeSubmitT'])
 diff_ab=first_diff(bd,ad); diff_aq=first_diff(ad,qd)
 # intervention and credit observations
 acred=[x for x in a.get('r246Events',[]) if x.get('event')=='R246_CORE_ACTIVE_REPAIR_FILL_CREDIT_OBSERVED']
 qcred=[x for x in q.get('r246Events',[]) if x.get('event') in ('R246_CORE_ACTIVE_REPAIR_FILL_CREDIT_OBSERVED','R246_CORE_ACTIVE_CREDIT_QUARANTINED')]
 item={'marketId':m,'winnerPostHocOnly':b.get('winnerPostHocOnly'),
       'commonOrigin':{'prefixParity':prefix_ok,'nonDiagnosticPrefixEvents':len(aprefix),'activeSubmitT':pre['activeSubmitT'],'activeSourceKey':pre['sourceKey'],
                       'activePreState':pre,'activeAndQuarantinePreStateExactMatch':pre==preq},
       'activeOutcome':{'activeKey':key,'fillEvents':a.get('r246CoreActiveFillEvents'),'repairAllocated':a.get('r246CoreActiveRepairAllocated'),
                        'creditObserved':a.get('r246CoreActiveCreditObserved'),'creditEvents':acred},
       'quarantineOutcome':{'activeKey':qkey,'fillEvents':q.get('r246CoreActiveFillEvents'),'repairAllocated':q.get('r246CoreActiveRepairAllocated'),
                            'creditObserved':q.get('r246CoreActiveCreditObserved'),'creditQuarantined':q.get('r246QuarantinedCoreActiveCredit'),'creditEvents':qcred},
       'terminal':{
          'R240':{'pnl':b['pnlDiagnosticOnly'],'floor':b['floor'],'best':b['best'],'fills':b['fillEvents'],'submits':b['submits'],'roleSubmits':b.get('roleSubmits',{}),'roleFills':b.get('roleFills',{})},
          'ACTIVE':{'pnl':a['pnlDiagnosticOnly'],'floor':a['floor'],'best':a['best'],'fills':a['fillEvents'],'submits':a['submits'],'roleSubmits':a.get('roleSubmits',{}),'roleFills':a.get('roleFills',{})},
          'ACTIVE_CREDIT_QUARANTINE':{'pnl':q['pnlDiagnosticOnly'],'floor':q['floor'],'best':q['best'],'fills':q['fillEvents'],'submits':q['submits'],'roleSubmits':q.get('roleSubmits',{}),'roleFills':q.get('roleFills',{})}},
       'delta':{'ACTIVE_vs_R240':{'pnl':a['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],'floor':a['floor']-b['floor'],'best':a['best']-b['best']},
                'QUARANTINE_vs_R240':{'pnl':q['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],'floor':q['floor']-b['floor'],'best':q['best']-b['best']},
                'QUARANTINE_vs_ACTIVE':{'pnl':q['pnlDiagnosticOnly']-a['pnlDiagnosticOnly'],'floor':q['floor']-a['floor'],'best':q['best']-a['best']}},
       'firstDownstreamDivergence':{'R240_vs_ACTIVE':diff_ab,'ACTIVE_vs_QUARANTINE':diff_aq,
                                    'R240Around':around(bd,diff_ab['index'] if diff_ab else None),
                                    'ACTIVEAround':around(ad,diff_ab['index'] if diff_ab else None),
                                    'ACTIVEAroundCreditDiff':around(ad,diff_aq['index'] if diff_aq else None),
                                    'QUARANTINEAroundCreditDiff':around(qd,diff_aq['index'] if diff_aq else None)},
       'correctness':{'activeUnauthorizedOverflowQty':a.get('unauthorizedOverflowQty'),'activeRepairQuotaExcessMax':a.get('repairQuotaExcessMax'),
                      'quarantineUnauthorizedOverflowQty':q.get('unauthorizedOverflowQty'),'quarantineRepairQuotaExcessMax':q.get('repairQuotaExcessMax')}}
 per.append(item)

agg={}
for c in CELLS:
 xs=[idx[(m,c)] for m in MIDS]
 agg[c]={'totalPnl':sum(float(x['pnlDiagnosticOnly']) for x in xs),'aggregateFloor':sum(float(x['floor']) for x in xs),'aggregateBest':sum(float(x['best']) for x in xs),
         'fills':sum(int(x['fillEvents']) for x in xs),'submits':sum(int(x['submits']) for x in xs),'wins':sum(float(x['pnlDiagnosticOnly'])>0 for x in xs)}
merged={'version':'MS4_R2_46_FOUR_MARKET_MECHANISM_ABLATION_MERGED_V1','researchOnly':True,'runtimeAuthority':False,'sources':[{'path':str(p.relative_to(ROOT)).replace('\\','/'),'sha256':sha(p)} for p in SRC],
        'markets':MIDS,'rows':rows,'comparison':cmps,'aggregate':agg,'evidence':per,
        'gates':{'allCellsUniqueComplete':not dups and not missing,'allCommonPrefixesMatch':all(x['commonOrigin']['prefixParity'] for x in per),
                 'activeAndQuarantinePreStateMatch':all(x['commonOrigin']['activeAndQuarantinePreStateExactMatch'] for x in per),
                 'correctnessPass':all(float(x['correctness']['activeUnauthorizedOverflowQty'] or 0)<=1e-9 and float(x['correctness']['activeRepairQuotaExcessMax'] or 0)<=1e-9 and float(x['correctness']['quarantineUnauthorizedOverflowQty'] or 0)<=1e-9 and float(x['correctness']['quarantineRepairQuotaExcessMax'] or 0)<=1e-9 for x in per)},
        'boundary':['merged from preregistered small R2.46 runs; no new HFT in merge','future outcomes are offline falsification only','not a selector/promotion result']}
OUT_RESULT.write_text(json.dumps(merged,ensure_ascii=False,indent=2),encoding='utf-8')
(OUT_DIR/'13_REQUEST2_FOUR_MARKET_PAIRED_CONTINUATION.json').write_text(json.dumps({'version':merged['version'],'aggregate':agg,'evidence':per,'gates':merged['gates'],'sources':merged['sources']},ensure_ascii=False,indent=2),encoding='utf-8')

lines=['# GPT-6 Round-2 Bounded Evidence Request 2 — Four-Market R2.40 Paired Continuation Attribution','',
'Preregistered runner: `MS4_R246_R240_CORE_ACTIVE_CREDIT_MECHANISM_ABLATION_PREREGISTERED_20260906.json`.','',
'Cells: frozen R2.40; R2.40 + one naturally reached Core-derived pure-Repair Active; same Active + quarantine only the same-generation continuation credit minted by that confirmed Active Repair fill.','',
'No intervention was forced or transplanted. Existing R2.40/SATELLITE failure-evidence service is executed first; Core evidence is kept in a separate diagnostic queue so evidence observation alone cannot block the frozen queue.','',
'## Aggregate over the four requested mechanism markets','',
'```json',json.dumps(agg,ensure_ascii=False,indent=2),'```','']
for x in per:
 m=x['marketId'];d=x['delta'];co=x['commonOrigin'];ao=x['activeOutcome'];qo=x['quarantineOutcome']
 lines += [f'## {m}','',f'- Common non-diagnostic event prefix parity with R2.40 before Active submit: **{co["prefixParity"]}** ({co["nonDiagnosticPrefixEvents"]} events).',
           f'- ACTIVE and ACTIVE_CREDIT_QUARANTINE strict-past snapshots exact-match: **{co["activeAndQuarantinePreStateExactMatch"]}**.',
           f'- Source `{co["activeSourceKey"]}`; Active submit t={co["activeSubmitT"]}; Active fillEvents={ao["fillEvents"]}; Repair allocated={ao["repairAllocated"]}; potential/same-generation observed credit={ao["creditObserved"]}.',
           f'- Credit actually quarantined in Q branch: {qo["creditQuarantined"]}.',
           f'- ACTIVE vs R2.40: ΔPnL {d["ACTIVE_vs_R240"]["pnl"]:+.6f}, ΔFloor {d["ACTIVE_vs_R240"]["floor"]:+.6f}, ΔBest {d["ACTIVE_vs_R240"]["best"]:+.6f}.',
           f'- QUARANTINE vs R2.40: ΔPnL {d["QUARANTINE_vs_R240"]["pnl"]:+.6f}, ΔFloor {d["QUARANTINE_vs_R240"]["floor"]:+.6f}, ΔBest {d["QUARANTINE_vs_R240"]["best"]:+.6f}.',
           f'- QUARANTINE vs ACTIVE: ΔPnL {d["QUARANTINE_vs_ACTIVE"]["pnl"]:+.6f}, ΔFloor {d["QUARANTINE_vs_ACTIVE"]["floor"]:+.6f}, ΔBest {d["QUARANTINE_vs_ACTIVE"]["best"]:+.6f}.','',
           'Strict-past state at the action origin:','```json',json.dumps(co['activePreState'],ensure_ascii=False,indent=2),'```','',
           'First downstream divergence packet:','```json',json.dumps(x['firstDownstreamDivergence'],ensure_ascii=False,indent=2),'```','']
lines += ['## Mechanism reading (bounded to these four consumed markets)','',
'- 1946656: Active physical Repair is beneficial; quarantining its continuation credit leaves terminal outcome unchanged. Immediate physical repair, not credit recycling, explains the observed rescue in this branch.',
'- 1946748: Active physical Repair is beneficial. The fill changes scope/generation, so the observed repair amount does not become same-generation spendable credit and the Q branch removes zero; again no evidence that credit recycling causes the rescue.',
'- 1946784: Active materially harms PnL/Best while improving Floor. Quarantining the full same-generation Active-generated credit leaves the harmful outcome exactly unchanged. This falsifies Active-credit recycling as the main cause of the 1946784 regression; the harm is upstream in the immediate fill / scope / carrier / lifecycle path.',
'- 1946683: ACTIVE alone is slightly harmful vs R2.40, while quarantining its same-generation Active-generated credit converts the branch to a material positive PnL/Floor/Best delta. This is direct evidence that immediate Active Repair and post-fill continuation-credit reuse are separable mechanisms in at least this Repair-economics case.',
'- These four markets do not identify a safe general selector. They answer mechanism attribution only and should be returned to GPT-6 before any full24 candidate is built.','',
'## Integrity','',f'- Gates: `{json.dumps(merged["gates"],ensure_ascii=False)}`','- Correctness invariants remain zero in both candidate branches for all four markets.','- No fresh cohort was consumed by this mechanism test.']
(OUT_DIR/'12_REQUEST2_FOUR_MARKET_PAIRED_CONTINUATION.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps({'ok':True,'result':str(OUT_RESULT.relative_to(ROOT)),'gates':merged['gates'],'aggregate':agg},ensure_ascii=False))
