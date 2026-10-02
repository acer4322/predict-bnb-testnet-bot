from __future__ import annotations
import json
from pathlib import Path
from collections import defaultdict, Counter, deque

EPS=1e-9
SRC=Path('data/research/r4_v0/p0_provenance_v1/MS4_R223_FULL24_PENDING_CLAIM_AWARE_RESULT_20260906.json')
OUTJ=Path('data/research/r4_v0/p0_provenance_v1/MS4_R238_PER_FILL_FAILURE_CAUSALITY_RESULT_20260906.json')
OUTM=Path('data/research/r4_v0/p0_provenance_v1/MS4_R238_PER_FILL_FAILURE_CAUSALITY_REPORT_20260906.md')


def build_maps(r):
    active_by_key={}; passive_ev={}; active_src={}; fanout=set()
    for x in r.get('ms4R2ExecutionDecisions') or []:
        if x.get('event')=='MS4_R2_ACTIVE_REPAIR_SUBMIT' and x.get('key'):
            active_by_key[str(x['key'])]=x
    for x in r.get('failureEvidenceActiveDrainEvents') or []:
        if x.get('event')=='PASSIVE_REPAIR_GENUINE_ZERO_FILL_TERMINAL' and x.get('sourceKey'):
            passive_ev[str(x['sourceKey'])]=x
        elif x.get('event')=='FAILURE_EVIDENCE_ACTIVE_DRAIN_SUBMIT' and x.get('sourceKey'):
            active_src[int(x['t'])]=x
    for x in r.get('r26Events') or []:
        if x.get('event')=='PARALLEL_PASSIVE_REPAIR_FANOUT_SUBMIT' and x.get('key'):fanout.add(str(x['key']))
    return active_by_key,passive_ev,active_src,fanout


def role_label(raw,key,active_by_key,fanout):
    if key in active_by_key:return 'ACTIVE_REPAIR'
    if raw=='SATELLITE_REPAIR' and key in fanout:return 'FANOUT_REPAIR'
    if raw=='SATELLITE_REPAIR':return 'NATIVE_SATELLITE_REPAIR'
    return raw


def fills_for(r):
    active_by_key,passive_ev,active_src,fanout=build_maps(r)
    fs=[];seq=0
    for x in r.get('splitEvents') or []:
        if x.get('event')!='ROLE_FILL_SPLIT' or float(x.get('fillInc') or 0)<=EPS:continue
        seq+=1;y=dict(x);k=str(x['key']);raw=str(x.get('role') or 'UNKNOWN')
        y['seq']=seq;y['analysisRole']=role_label(raw,k,active_by_key,fanout)
        a=active_by_key.get(k)
        if a:
            s=active_src.get(int(a['t']));p=passive_ev.get(str(s.get('sourceKey'))) if s else None
            y['activeContext']={'submitT':int(a['t']),'activePrice':float(a['activePrice']),'qty':float(a['qty']),
                'debt':float(a.get('debt') or 0),'reservedBefore':float(a.get('reservedBefore') or 0),
                'sourceKey':s.get('sourceKey') if s else None,'sourcePassivePrice':float(p['sourcePrice']) if p and p.get('sourcePrice') is not None else None,
                'activeVsSourcePremium':(float(a['activePrice'])-float(p['sourcePrice'])) if p and p.get('sourcePrice') is not None else None,
                'repairProgressClock':int(s['repairProgressClock']) if s and s.get('repairProgressClock') is not None else None}
        fs.append(y)
    fs.sort(key=lambda z:(int(z['t']),int(z['seq'])))
    return fs


def component_for(fill,kind):
    role=fill['analysisRole'];raw=str(fill.get('role') or '')
    if kind=='repair':
        if role=='ACTIVE_REPAIR':return 'ACTIVE_REPAIR'
        if role=='FANOUT_REPAIR':return 'FANOUT_REPAIR'
        if role=='NATIVE_SATELLITE_REPAIR':return 'NATIVE_SATELLITE_REPAIR'
        if raw=='ECONOMIC_CORE':return 'ECONOMIC_CORE_REPAIR'
        return role+'_REPAIR'
    if kind=='overflow':
        if raw=='ECONOMIC_CORE':return 'ECONOMIC_CORE_OVERFLOW'
        if raw=='SATELLITE_EXPAND':return 'SATELLITE_EXPAND'
        if raw=='SATELLITE_REPAIR':return role+'_OVERFLOW'
        return role+'_OVERFLOW'
    return role


def sublots(fill):
    q=float(fill['fillInc']);rq=min(q,max(0.0,float(fill.get('repairAllocated') or 0)));oq=min(max(0.0,q-rq),max(0.0,float(fill.get('overflowRealized') or 0)))
    rest=max(0.0,q-rq-oq);out=[]
    if rq>EPS:out.append({'kind':'repair','component':component_for(fill,'repair'),'qty':rq})
    if oq>EPS:out.append({'kind':'overflow','component':component_for(fill,'overflow'),'qty':oq})
    if rest>EPS:out.append({'kind':'base','component':component_for(fill,'base'),'qty':rest})
    return out


def analyze_market(r):
    winner=str(r['winnerPostHocOnly']).upper();pnl=float(r['pnlDiagnosticOnly']);fs=fills_for(r)
    dq={'UP':deque(),'DOWN':deque()};details={}
    for f in fs:
        d={'seq':f['seq'],'t':int(f['t']),'key':str(f['key']),'role':f['analysisRole'],'rawRole':f.get('role'),'side':str(f['side']),
           'price':float(f['price']),'qty':float(f['fillInc']),'repairAllocated':float(f.get('repairAllocated') or 0),'overflowRealized':float(f.get('overflowRealized') or 0),
           'directSettlementContributionAtOrderPrice':float(f['fillInc'])*((1-float(f['price'])) if str(f['side'])==winner else -float(f['price'])),
           'negativePairDamage':0.0,'positivePairEdge':0.0,'losingResidualDamage':0.0,'winnerResidualGain':0.0,'pairSegments':[],'damageBySubcomponent':defaultdict(float),'gainBySubcomponent':defaultdict(float)}
        if f.get('activeContext'):d['activeContext']=f['activeContext']
        details[f['seq']]=d;side=str(f['side']);opp='DOWN' if side=='UP' else 'UP';p=float(f['price'])
        for sl in sublots(f):
            rem=float(sl['qty']);comp=sl['component']
            while rem>EPS and dq[opp]:
                lot=dq[opp][0];m=min(rem,lot['rem']);ps=p+lot['price'];edge=(1-ps)*m
                seg={'qty':m,'pairSum':ps,'pairEdge':edge,'currentSubcomponent':comp,'counterpartSeq':lot['seq'],'counterpartKey':lot['key'],'counterpartComponent':lot['component'],'counterpartRole':lot['role'],'counterpartPrice':lot['price'],'counterpartT':lot['t']}
                d['pairSegments'].append(seg)
                if edge<0:d['negativePairDamage']+=-edge;d['damageBySubcomponent'][comp]+=-edge
                else:d['positivePairEdge']+=edge;d['gainBySubcomponent'][comp]+=edge
                rem-=m;lot['rem']-=m
                if lot['rem']<=EPS:dq[opp].popleft()
            if rem>EPS:dq[side].append({'seq':f['seq'],'key':str(f['key']),'role':f['analysisRole'],'component':comp,'kind':sl['kind'],'side':side,'price':p,'t':int(f['t']),'rem':rem})
    for side,q in dq.items():
        for lot in q:
            qty=lot['rem'];contrib=qty*((1-lot['price']) if side==winner else -lot['price']);d=details[lot['seq']]
            if contrib<0:d['losingResidualDamage']+=-contrib;d['damageBySubcomponent'][lot['component']]+=-contrib
            else:d['winnerResidualGain']+=contrib;d['gainBySubcomponent'][lot['component']]+=contrib
    for d in details.values():
        d['damageBySubcomponent']=dict(d['damageBySubcomponent']);d['gainBySubcomponent']=dict(d['gainBySubcomponent'])
        d['totalAttributedDamage']=d['negativePairDamage']+d['losingResidualDamage'];d['netEconomicContribution']=d['positivePairEdge']-d['negativePairDamage']+d['winnerResidualGain']-d['losingResidualDamage']
    negpair=sum(x['negativePairDamage'] for x in details.values());pospair=sum(x['positivePairEdge'] for x in details.values());resloss=sum(x['losingResidualDamage'] for x in details.values());resgain=sum(x['winnerResidualGain'] for x in details.values());decomp=pospair-negpair+resgain-resloss;err=decomp-pnl
    comp_damage=defaultdict(float);comp_gain=defaultdict(float)
    for d in details.values():
        for k,v in d['damageBySubcomponent'].items():comp_damage[k]+=v
        for k,v in d['gainBySubcomponent'].items():comp_gain[k]+=v
    # submission/path evidence
    submits={};filled_keys={str(f['key']) for f in fs};cancel_reason=defaultdict(list)
    for x in r.get('slotHistory') or []:
        ev=x.get('event');k=str(x.get('key')) if x.get('key') is not None else None
        if ev=='ROLE_SLOT_SUBMIT' and k:submits[k]={'t':int(x['t']),'key':k,'role':str(x.get('role')),'side':str(x.get('side')),'price':float(x.get('price')),'qty':float(x.get('qty'))}
        elif ev=='SLOT_CANCEL_REQUEST' and k:cancel_reason[k].append(str(x.get('reason')))
    zero_attempts=[]
    for k,s in submits.items():
        if k not in filled_keys and s['role'] in {'ECONOMIC_CORE','SATELLITE_REPAIR'}:
            zero_attempts.append({**s,'cancelReasons':cancel_reason.get(k,[])})
    genuine=[x for x in (r.get('failureEvidenceActiveDrainEvents') or []) if x.get('event')=='PASSIVE_REPAIR_GENUINE_ZERO_FILL_TERMINAL']
    # identify first eventual losing exposure fill and opposite repair attempts after it
    residual_harms=[d for d in details.values() if d['losingResidualDamage']>EPS]
    first_res=min(residual_harms,key=lambda z:(z['t'],z['seq']),default=None)
    post_opp=[]
    if first_res:
        opp='DOWN' if first_res['side']=='UP' else 'UP'
        post_opp=[z for z in zero_attempts if z['t']>=first_res['t'] and z['side']==opp]
    no_opp_fill=False
    if first_res:
        opp='DOWN' if first_res['side']=='UP' else 'UP'
        later_opp_fills=[f for f in fs if int(f['t'])>=first_res['t'] and str(f['side'])==opp]
        no_opp_fill=(len(later_opp_fills)==0 and len(post_opp)>0)
    harmful=[x for x in details.values() if x['totalAttributedDamage']>EPS];harmful.sort(key=lambda z:z['totalAttributedDamage'],reverse=True)
    first_harm=min(harmful,key=lambda z:(z['t'],z['seq']),default=None)
    # mechanism signatures
    repair_components={'ACTIVE_REPAIR','FANOUT_REPAIR','NATIVE_SATELLITE_REPAIR','ECONOMIC_CORE_REPAIR'}
    repair_damage=sum(v for k,v in comp_damage.items() if k in repair_components or k.endswith('_REPAIR'))
    active_damage=comp_damage.get('ACTIVE_REPAIR',0.0)
    passive_damage=comp_damage.get('FANOUT_REPAIR',0.0)+comp_damage.get('NATIVE_SATELLITE_REPAIR',0.0)+comp_damage.get('ECONOMIC_CORE_REPAIR',0.0)
    newexp_components={'ECONOMIC_CORE_OVERFLOW','SATELLITE_EXPAND','PROBE_CORE','SCOPE_BIRTH'}
    newexp_residual=sum(d['losingResidualDamage'] for d in details.values() for k,v in d['damageBySubcomponent'].items() if k in newexp_components and v>0) # upper bound; detailed component total below is authoritative
    newexp_damage=sum(v for k,v in comp_damage.items() if k in newexp_components)
    sig=[]
    if active_damage>EPS:sig.append('ACTIVE_REPAIR_NEGATIVE_PAIR_ECONOMICS')
    if passive_damage>EPS:sig.append('PASSIVE_OR_CORE_REPAIR_NEGATIVE_PAIR_ECONOMICS')
    if resloss>EPS:sig.append('LOSING_SIDE_UNMATCHED_EXPOSURE')
    if comp_damage.get('ECONOMIC_CORE_OVERFLOW',0)>EPS:sig.append('ECONOMIC_CORE_OVERFLOW_LEFT_UNRECOVERED')
    if comp_damage.get('SATELLITE_EXPAND',0)>EPS:sig.append('SATELLITE_EXPAND_LEFT_UNRECOVERED')
    if comp_damage.get('PROBE_CORE',0)>EPS:sig.append('INITIAL_PROBE_LEFT_UNRECOVERED')
    if genuine:sig.append('GENUINE_PASSIVE_REPAIR_ZERO_FILL_EVIDENCE')
    if no_opp_fill:sig.append('NO_OPPOSITE_FILL_DESPITE_REPAIR_ATTEMPTS')
    if abs(err)>1e-6:sig.append('EXECUTION_PRICE_ACCOUNTING_RESIDUAL_UNRESOLVED')
    if not sig:sig=['NO_DIRECT_HARM_IDENTIFIED']
    dominant=max(comp_damage,key=comp_damage.get) if comp_damage else 'NONE'
    # family for global-vs-exception reasoning
    if no_opp_fill and comp_damage.get('PROBE_CORE',0)>EPS:family='INITIAL_EXPOSURE_REPAIR_LIVENESS_FAILURE'
    elif repair_damage>resloss+EPS:family='REPAIR_ECONOMICS_DOMINANT'
    elif resloss>repair_damage+EPS:family='UNRECOVERED_EXPOSURE_DOMINANT'
    else:family='MIXED_REPAIR_AND_EXPOSURE'
    return {'marketId':int(r['marketId']),'winnerPostHocOnly':winner,'pnl':pnl,'floor':float(r['floor']),'best':float(r['best']),'fills':int(r['fillEvents']),'submits':int(r['submits']),
      'decompositionPnlAtOrderPrices':decomp,'decompositionErrorVsActualHftPnl':err,'positivePairEdge':pospair,'negativePairDamage':negpair,'winnerResidualGain':resgain,'losingResidualDamage':resloss,
      'damageBySubcomponent':dict(comp_damage),'gainBySubcomponent':dict(comp_gain),'dominantDamageSubcomponent':dominant,'mechanismFamily':family,'signatures':sig,
      'genuinePassiveRepairZeroFillCount':len(genuine),'zeroFillRepairLikeAttemptCount':len(zero_attempts),'postFirstResidualOppositeZeroFillAttempts':post_opp[:40],'noOppositeFillDespiteRepairAttempts':no_opp_fill,
      'firstHarmfulFill':first_harm,'topCulprits':harmful[:10],'allHarmfulFills':harmful,'fillTimeline':[details[k] for k in sorted(details)]}


def main():
    d=json.load(open(SRC,encoding='utf-8'));rows=[r for r in d['rows'] if r.get('cell')=='MS4_R28_CAP1_CONTROL'];losses=[analyze_market(r) for r in rows if float(r['pnlDiagnosticOnly'])<0]
    sig=Counter();fam=Counter();dom=Counter();compmarkets=Counter();compdamage=defaultdict(float);famdamage=defaultdict(float)
    for m in losses:
        fam[m['mechanismFamily']]+=1;famdamage[m['mechanismFamily']]+=-m['pnl']
        dom[m['dominantDamageSubcomponent']]+=1
        for s in m['signatures']:sig[s]+=1
        for k,v in m['damageBySubcomponent'].items():
            if v>EPS:compmarkets[k]+=1;compdamage[k]+=v
    # Exception candidates are not based on rarity alone: flag low-severity/near-flat or a family with single support, for review only.
    family_support=dict(fam);exceptions=[];repeated=[]
    for m in losses:
        x={'marketId':m['marketId'],'pnl':m['pnl'],'family':m['mechanismFamily'],'familySupport':fam[m['mechanismFamily']],'dominant':m['dominantDamageSubcomponent'],'signatures':m['signatures']}
        if fam[m['mechanismFamily']]==1 or abs(m['pnl'])<0.05:exceptions.append(x)
        else:repeated.append(x)
    out={'version':'MS4_R2_38_PER_FILL_FAILURE_CAUSALITY_V2','date':'2026-09-06','canonicalSource':'R223 full24 / frozen MS4_R28_CAP1_CONTROL','lossMarkets':len(losses),'markets':losses,
         'commonality':{'mechanismFamilyCounts':dict(fam),'mechanismFamilyLossMagnitude':dict(famdamage),'signatureCounts':dict(sig),'damagingSubcomponentMarketCounts':dict(compmarkets),'damagingSubcomponentDamage':dict(compdamage),'dominantSubcomponentCounts':dict(dom),'repeatedMechanismMarkets':repeated,'exceptionCandidatesForReview':exceptions},
         'provenanceNote':'R213 markets[] represented R2.13 candidate (sum -6.627195), while current analysis uses frozen CAP1 control (sum -7.045414). 1945869 and 1946448 differ for this reason; not replay drift.',
         'boundary':['consumed latest24 only','FIFO repair-first/overflow-second sublot accounting attribution','order price is used for pair decomposition; actual HFT execPrice is not persisted in these rows, so markets with nonzero decomposition residual are explicitly flagged','winner post-hoc only','do not treat a fill attribution as proof that removing the fill preserves downstream path','rare/near-flat cases cannot authorize global controller changes','no runtime change','no fresh data consumed','no 8781']}
    OUTJ.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    L=['# MS4 R2.38 Per-Fill Failure Causality Report V2 — 2026-09-06','',f'- Canonical frozen CAP1 losses: **{len(losses)}**','- Each fill is split into Repair vs Overflow/new-exposure sub-lots before FIFO pair attribution.','- This fixes the misleading V1 label where ECONOMIC_CORE overflow was being blamed on ECONOMIC_CORE as a whole.','']
    L+=['## Cross-market mechanism families','']
    for k,v in fam.most_common():L.append(f'- **{k}**: {v}/{len(losses)} losses; aggregate loss magnitude={famdamage[k]:.6f}')
    L+=['','Damaging subcomponents:']
    for k,v in compmarkets.most_common():L.append(f'- {k}: damages {v}/{len(losses)} losses; attributed damage={compdamage[k]:.6f}')
    L+=['','Repeated signatures:']
    for k,v in sig.most_common():L.append(f'- {k}: {v}/{len(losses)}')
    L+=['','## Per-market culprit orders','']
    for m in sorted(losses,key=lambda z:z['pnl']):
        L.append(f"### {m['marketId']} — PnL {m['pnl']:+.6f} — {m['mechanismFamily']}")
        L.append(f"- pairDamage={m['negativePairDamage']:.6f}, losingResidual={m['losingResidualDamage']:.6f}, positivePair={m['positivePairEdge']:.6f}, winnerResidual={m['winnerResidualGain']:.6f}")
        L.append('- damage components: '+(', '.join(f'{k}={v:.6f}' for k,v in sorted(m['damageBySubcomponent'].items(),key=lambda kv:kv[1],reverse=True)) or 'none'))
        L.append(f"- genuine passive zero-fill evidence={m['genuinePassiveRepairZeroFillCount']}; repair-like zero-fill attempts={m['zeroFillRepairLikeAttemptCount']}; no opposite fill after harmful exposure={m['noOppositeFillDespiteRepairAttempts']}")
        if abs(m['decompositionErrorVsActualHftPnl'])>1e-6:L.append(f"- WARNING actual-HFT vs order-price attribution residual={m['decompositionErrorVsActualHftPnl']:+.6f}; do not over-interpret exact cents for this market")
        for i,h in enumerate(m['topCulprits'][:6],1):
            comps=', '.join(f'{k}:{v:.6f}' for k,v in sorted(h['damageBySubcomponent'].items(),key=lambda kv:kv[1],reverse=True) if v>EPS)
            ac=''
            if h.get('activeContext'):
                a=h['activeContext'];ac=f"; active source {a.get('sourceKey')} passive@{a.get('sourcePassivePrice')} -> active@{a.get('activePrice')} premium={a.get('activeVsSourcePremium')}"
            L.append(f"  {i}. #{h['seq']} **{h['key']} {h['role']} {h['side']} @{h['price']:.4f} q={h['qty']:.4f}** damage={h['totalAttributedDamage']:.6f} [{comps}]{ac}")
            bad=[s for s in h['pairSegments'] if s['pairEdge']<0]
            if bad:L.append('     - '+ '; '.join(f"pairs {s['counterpartKey']}({s['counterpartComponent']}) sum={s['pairSum']:.4f} damage={-s['pairEdge']:.6f}" for s in bad))
        if m['noOppositeFillDespiteRepairAttempts']:
            xs=m['postFirstResidualOppositeZeroFillAttempts'];L.append(f"- execution chain: {len(xs)} recorded opposite repair-like zero-fill attempts after the harmful exposure; first keys: "+', '.join(x['key'] for x in xs[:8]))
        L.append('')
    L+=['## Global-change policy','',
        '1. Repeated mechanism support is required before changing controller behavior.',
        '2. A concrete culprit fill is diagnostic evidence, not sufficient proof that deleting it helps; path-preserving counterfactual is required.',
        '3. Near-flat or one-family-only cases are recorded as exception candidates and should not drive global gates.',
        '4. Prefer narrow fixes at the repeated seam (Repair pricing, overflow continuation, or execution liveness) and test unaffected winning markets for collateral damage.','']
    if exceptions:
        L.append('Current exception candidates for review:')
        for x in exceptions:L.append(f"- {x['marketId']}: PnL={x['pnl']:+.6f}, family={x['family']}, familySupport={x['familySupport']}")
    OUTM.write_text('\n'.join(L),encoding='utf-8')
    print(json.dumps({'ok':True,'lossMarkets':len(losses),'familyCounts':dict(fam),'componentCounts':dict(compmarkets),'componentDamage':dict(compdamage),'signatureCounts':dict(sig),'exceptionCandidates':exceptions},ensure_ascii=False))

if __name__=='__main__':main()
