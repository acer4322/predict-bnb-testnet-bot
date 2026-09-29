from __future__ import annotations
import argparse,json,lzma,math,sys
from collections import Counter,defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from tools import test_r4_p0b_lifecycle_checkpoint_simulator_v1 as sim

FIX=ROOT/'data/research/r4_v0/p0_prep_v1/r4_p0_fixed_cohorts_v1.json'
SRC=ROOT/'data/hft_forward_paper_v1/markets'
OUTDIR=ROOT/'data/research/r4_v0/p0_provenance_v1'
POLICY='ROLL_KEEP_GAP_OWNER'
EPS=1e-9


def load_market(mid:int):
    p=SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz'
    with lzma.open(p,'rt',encoding='utf-8') as f: return json.load(f)


def phase_boundary(mid:int):
    rows=sim.public_source_rows(mid)
    cand=[]
    for t,z in rows:
        try: sl=float(z.get('secondsLeft'))
        except (TypeError,ValueError): continue
        if 0.0 < sl <= 60.0: cand.append((int(t),sl))
    return min(cand,key=lambda x:x[0]) if cand else (None,None)


def provenance_state_at(journal,cutoff:int):
    st={}
    for e in sorted(journal,key=lambda x:(int(x.get('received_at_ms') or 0),int(x.get('event_seq') or 0))):
        t=int(e.get('received_at_ms') or 0)
        if t>=cutoff: break
        rid=str(e.get('responsibility_id') or '')
        if not rid: continue
        z=st.setdefault(rid,{
            'side':str(e.get('side') or ''),'requested':0.0,'confirmed':0.0,'opened_at':None,
            'acked_ever':False,'active':set(),'pending':set(),'terminal':False
        })
        et=str(e.get('event_type') or '')
        iid=str(e.get('intent_id')) if e.get('intent_id') else None
        if et=='RESPONSIBILITY_OPENED':
            z['requested']=max(z['requested'],float(e.get('requested_qty') or 0.0)); z['opened_at']=t if z['opened_at'] is None else min(z['opened_at'],t)
        if e.get('requested_qty') is not None: z['requested']=max(z['requested'],float(e.get('requested_qty') or 0.0))
        if e.get('cum_confirmed_fill_qty') is not None: z['confirmed']=max(z['confirmed'],float(e.get('cum_confirmed_fill_qty') or 0.0))
        if et=='SUBMIT_SENT' and iid: z['pending'].add(iid)
        elif et=='ACK_NEW' and iid:
            z['pending'].discard(iid); z['active'].add(iid); z['acked_ever']=True
        elif et in {'ACK_CANCELED','SUBMIT_REJECTED','IOC_TERMINAL','FULL_FILL'} and iid:
            z['active'].discard(iid); z['pending'].discard(iid)
        if et in {'RESPONSIBILITY_COMPLETED','RESPONSIBILITY_TERMINATED'}:
            z['terminal']=True; z['active'].clear(); z['pending'].clear()
    return st


def exact_takers(d,mid:int):
    _events,_times,meta=sim.tape.build_archive_events(mid,trade_offset='mid')
    _orders,takers,_dec=sim.v2.prep(d,meta,sim.ENTRY+sim.MAX_REST)
    return takers


def combined_realized_events(d,rep):
    ev=[]
    for e in rep.get('provenanceJournal') or []:
        if str(e.get('event_type') or '') not in {'PARTIAL_FILL','FULL_FILL'}: continue
        q=float((e.get('extras') or {}).get('fillDeltaQty') or 0.0)
        px=float(e.get('price') or 0.0); side=str(e.get('side') or '').upper(); t=int(e.get('received_at_ms') or 0)
        if q>EPS and side in {'UP','DOWN'} and 0<=px<=1.05:
            ev.append({'t':t,'prio':0,'role':'MAKER','side':side,'px':px,'q':q,'rid':str(e.get('responsibility_id') or '')})
    for x in exact_takers(d,int(d['marketId'])):
        q=float(x.get('q') or 0.0); px=float(x.get('px') or 0.0); side=str(x.get('side') or '').upper(); t=int(x.get('t') or 0)
        if q>EPS and side in {'UP','DOWN'} and 0<=px<=1.05:
            ev.append({'t':t,'prio':1,'role':'TAKER','side':side,'px':px,'q':q,'rid':''})
    return sorted(ev,key=lambda x:(x['t'],x['prio']))


def apply_event(state,e):
    return sim.v2.apply(state,e['side'],e['px'],e['q'],e['role']=='TAKER')


def relation(state,side):
    return str(sim.v2.relation(state,side))


def one_market(mid:int):
    d=load_market(mid)
    bt,sl=phase_boundary(mid)
    if bt is None: return {'marketId':mid,'status':'NO_60S_BOUNDARY','rows':[]}
    rep=sim.simulate(d,POLICY,collect_shadow=True,collect_provenance=True)
    journal=rep.get('provenanceJournal') or []
    roots=provenance_state_at(journal,bt)
    events=combined_realized_events(d,rep)
    state=(0.,0.,0.,0.,0.)
    for e in events:
        if int(e['t'])>=bt: break
        state=apply_event(state,e)
    bg=sim.v2.geom(state)
    inherited={}
    for rid,z in roots.items():
        unresolved=max(0.0,float(z['requested'])-float(z['confirmed']))
        if not z['acked_ever'] or z['terminal'] or unresolved<=EPS or len(z['active'])<1: continue
        rel=relation(state,z['side'])
        inherited[rid]={
            'marketId':mid,'boundaryMs':int(bt),'boundarySecondsLeft':float(sl),'responsibilityId':rid,'side':z['side'],
            'boundary_floor':float(bg['floor']),'boundary_absnet':float(bg['absNet']),'boundary_gross':float(bg['gross']),
            'boundary_coverage':float(bg['coverage']),'boundary_edge':float(bg['edge']),
            'root_requested_qty':float(z['requested']),'root_confirmed_qty':float(z['confirmed']),'root_unresolved_qty':float(unresolved),
            'root_progress_ratio':float(z['confirmed']/z['requested']) if z['requested']>EPS else 0.0,
            'root_age_s':float((bt-int(z['opened_at']))/1000.0) if z['opened_at'] is not None else 0.0,
            'active_owner_count':int(len(z['active'])),'relation':rel,
            'inherited_fill_shares_0_60':0.0,'inherited_fill_events_0_60':0,'reserve_spend_amount':0.0,
            'reserve_spend_events':0,'base_break_events':0,'base_break_by_inherited_root':0,'reserve_spend_by_inherited_root':0
        }
    # only earned positive-reserve handoff rows are in-scope by preregistration
    if float(bg['floor'])<=0:
        return {'marketId':mid,'status':'NONPOSITIVE_BOUNDARY_FLOOR','boundaryMs':bt,'boundaryFloor':float(bg['floor']),'inheritedRoots':len(inherited),'rows':[]}
    for e in events:
        if int(e['t'])<bt: continue
        pre=sim.v2.geom(state); post_state=apply_event(state,e); post=sim.v2.geom(post_state)
        rid=e.get('rid') or ''
        if rid in inherited and e['role']=='MAKER':
            r=inherited[rid]; r['inherited_fill_shares_0_60']+=float(e['q']); r['inherited_fill_events_0_60']+=1
            if float(pre['floor'])>0 and float(post['floor'])<float(pre['floor'])-EPS:
                spend=float(pre['floor'])-float(post['floor']); r['reserve_spend_amount']+=spend; r['reserve_spend_events']+=1; r['reserve_spend_by_inherited_root']=1
                if float(post['floor'])<=0:
                    r['base_break_events']+=1; r['base_break_by_inherited_root']=1
        state=post_state
    return {'marketId':mid,'status':'OK','boundaryMs':bt,'boundaryFloor':float(bg['floor']),'inheritedRoots':len(inherited),'rows':list(inherited.values())}


def run_cohort(key:str):
    fx=json.loads(FIX.read_text(encoding='utf-8')); ids=[int(x) for x in fx[key]['markets']]
    rows=[]; markets=[]
    for i,mid in enumerate(ids,1):
        try: r=one_market(mid)
        except Exception as e: r={'marketId':mid,'status':'ERROR','error':f'{type(e).__name__}:{e}','rows':[]}
        rows.extend(r.pop('rows',[])); markets.append(r)
        print(json.dumps({'cohort':key,'i':i,'n':len(ids),'marketId':mid,'status':r.get('status'),'rowsTotal':len(rows)},ensure_ascii=False),flush=True)
    return ids,markets,rows


def support(rows,label):
    pos=sum(int(r[label]) for r in rows); return {'rows':len(rows),'positive':pos,'negative':len(rows)-pos,'markets':len(set(int(r['marketId']) for r in rows))}


def feature_matrix(rows,extended:bool):
    X=[]
    for r in rows:
        if not extended: X.append([float(r['boundary_floor'])]); continue
        rel=str(r.get('relation') or '')
        X.append([
            float(r['boundary_floor']),float(r['root_progress_ratio']),float(r['root_unresolved_qty']),float(r['root_age_s']),float(r['active_owner_count']),
            1.0 if rel=='FLAT' else 0.0,1.0 if rel=='WEAK' else 0.0,1.0 if rel=='SURPLUS' else 0.0
        ])
    return X


def eval_binary(train,test,label):
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score,log_loss
    out={}
    ytr=[int(r[label]) for r in train]; yte=[int(r[label]) for r in test]
    sup=support(test,label); out['support']=sup
    if min(sup['positive'],sup['negative'])<10 or len(set(ytr))<2:
        out['status']='INSUFFICIENT_SUPPORT'; return out
    for name,ext in [('floorOnly',False),('floorPlusResponsibility',True)]:
        m=make_pipeline(StandardScaler(),LogisticRegression(C=1.0,max_iter=2000,solver='lbfgs'))
        m.fit(feature_matrix(train,ext),ytr); p=m.predict_proba(feature_matrix(test,ext))[:,1]
        out[name]={'auc':float(roc_auc_score(yte,p)),'logLoss':float(log_loss(yte,p,labels=[0,1]))}
    out['delta']={
        'aucLift':out['floorPlusResponsibility']['auc']-out['floorOnly']['auc'],
        'logLossImprovement':out['floorOnly']['logLoss']-out['floorPlusResponsibility']['logLoss']
    }
    out['status']='EVALUATED'; return out


def chronological_dev_split(rows):
    bym=defaultdict(list); btime={}
    for r in rows:
        m=int(r['marketId']); bym[m].append(r); btime[m]=int(r['boundaryMs'])
    mids=sorted(bym,key=lambda m:(btime[m],m)); cut=max(1,int(math.floor(len(mids)*2/3)))
    train_m=set(mids[:cut]); test_m=set(mids[cut:])
    return [r for r in rows if int(r['marketId']) in train_m],[r for r in rows if int(r['marketId']) in test_m],mids[:cut],mids[cut:]


def summaries(rows):
    vals=[float(r['reserve_spend_amount']) for r in rows]
    relation=Counter(str(r.get('relation')) for r in rows)
    rel_break=Counter(str(r.get('relation')) for r in rows if int(r['base_break_by_inherited_root']))
    return {
        'rows':len(rows),'markets':len(set(int(r['marketId']) for r in rows)),
        'baseBreakSupport':support(rows,'base_break_by_inherited_root'),
        'reserveSpendSupport':support(rows,'reserve_spend_by_inherited_root'),
        'sumReserveSpend':float(sum(vals)),'meanReserveSpendPerRoot':float(sum(vals)/len(vals)) if vals else 0.0,
        'relationCounts':dict(relation),'baseBreakRelationCounts':dict(rel_break)
    }


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--dev-key',default='managementHftFresh');ap.add_argument('--rep-key',default='managementHftReplication');a=ap.parse_args()
    OUTDIR.mkdir(parents=True,exist_ok=True)
    _di,dm,dr=run_cohort(a.dev_key); _ri,rm,rr=run_cohort(a.rep_key)
    dataset={'version':'R4_P0B_PROTECTION_INHERITED_RESPONSIBILITY_DATASET_V1','researchOnly':True,'actionAuthority':False,'policy':POLICY,'developmentMarkets':dm,'replicationMarkets':rm,'developmentRows':dr,'replicationRows':rr,'guards':['2026-08-16 SEALED','no dream fill','receipt-clock HFT','MAIN successor simulator imported read-only','future outcomes scoring-only','no action mutation']}
    dp=OUTDIR/'r4_p0b_protection_inherited_responsibility_dataset_v1.json'; dp.write_text(json.dumps(dataset,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    tr,dh,trm,dhm=chronological_dev_split(dr)
    targets={}
    for label in ['base_break_by_inherited_root','reserve_spend_by_inherited_root']:
        dev=eval_binary(tr,dh,label); rep=eval_binary(dr,rr,label); targets[label]={'developmentHoldout':dev,'independentReplication':rep}
    primary=targets['base_break_by_inherited_root']; ds=primary['developmentHoldout']; rs=primary['independentReplication']
    if ds.get('status')!='EVALUATED' or rs.get('status')!='EVALUATED': lane='INCONCLUSIVE'
    else:
        keep=ds['delta']['aucLift']>0 and ds['delta']['logLossImprovement']>=0 and rs['delta']['aucLift']>0 and rs['delta']['logLossImprovement']>=0
        lane='KEEP' if keep else 'REJECT'
    result={
        'version':'R4_P0B_PROTECTION_INHERITED_RESPONSIBILITY_V1','status':lane,'researchOnly':True,'actionAuthority':False,
        'question':'Does inherited responsibility state at the 60s Protection handoff add portable information beyond floor-only reserve about reserve consumption/base break?',
        'preregistered':'data/research/r4_v0/p0_provenance_v1/r4_p0b_protection_inherited_responsibility_preregistered_v1.json',
        'dataset':'data/research/r4_v0/p0_provenance_v1/r4_p0b_protection_inherited_responsibility_dataset_v1.json',
        'developmentSummary':summaries(dr),'replicationSummary':summaries(rr),
        'developmentSplit':{'trainMarkets':trm,'holdoutMarkets':dhm,'trainRows':len(tr),'holdoutRows':len(dh)},
        'targets':targets,
        'semanticInterpretation':('Primary base-break target met preregistered portability gate; inherited responsibility semantics are a research KEEP signal for Protection belief only.' if lane=='KEEP' else 'Primary base-break target did not meet the preregistered portable KEEP gate. No veto/defer authority may be inferred.' if lane=='REJECT' else 'Primary base-break support was insufficient for preregistered portable evaluation. Keep the question open; do not create veto/defer authority from secondary diagnostics.'),
        'guards':['No R3/8781/Echtgeld writes','2026-08-16 SEALED','No dream fill','No MAIN successor simulator edit','No threshold sweep','No action/cancel/reprice/size mutation','Protection research only in 0-60','Future labels scoring-only']
    }
    rp=OUTDIR/'r4_p0b_protection_inherited_responsibility_v1.json';rp.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    lane_status={
        'version':'R4_P0B_PROTECTION_LANE_STATUS_V1','lane':'LANE_B_PROTECTION','status':lane,'researchOnly':True,'actionAuthority':False,
        'question':result['question'],'preregistered':result['preregistered'],'cohorts':{'development':a.dev_key,'independentReplication':a.rep_key},
        'exactResults':{'developmentSummary':result['developmentSummary'],'replicationSummary':result['replicationSummary'],'primary':targets['base_break_by_inherited_root'],'secondary':targets['reserve_spend_by_inherited_root']},
        'guards':result['guards'],
        'filesChanged':['tools/test_r4_p0b_protection_inherited_responsibility_v1.py',result['preregistered'],result['dataset'],'data/research/r4_v0/p0_provenance_v1/r4_p0b_protection_inherited_responsibility_v1.json','data/research/r4_v0/p0_provenance_v1/r4_p0b_protection_lane_status_v1.json']
    }
    sp=OUTDIR/'r4_p0b_protection_lane_status_v1.json';sp.write_text(json.dumps(lane_status,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({'status':lane,'result':str(rp.relative_to(ROOT)).replace('\\','/'),'laneStatus':str(sp.relative_to(ROOT)).replace('\\','/'),'development':result['developmentSummary'],'replication':result['replicationSummary'],'targets':targets},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
