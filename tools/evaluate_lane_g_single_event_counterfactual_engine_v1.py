from __future__ import annotations
import argparse,json,math,os,sys,tempfile,zipfile,shutil
from pathlib import Path
import numpy as np,joblib
from sklearn.metrics import mean_absolute_error

ROOT=Path(__file__).resolve().parents[1]
STAGED=Path.cwd()/'.lan_worker_v1'/'staging'
if (STAGED/'diagnose_lane_g_exact_postsubmit_world_state_v1.py').exists() and (STAGED/'train_lane_g_r264_execution_world_v1.py').exists():
    sys.path.insert(0,str(STAGED))
    import diagnose_lane_g_exact_postsubmit_world_state_v1 as diag
    import train_lane_g_r264_execution_world_v1 as wm
else:
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    from tools import diagnose_lane_g_exact_postsubmit_world_state_v1 as diag
    from tools import train_lane_g_r264_execution_world_v1 as wm

ACTIONS=['KEEP_REPAIR','ORDINARY_REEXPAND','R303_CONTINGENT_COMPOSITE']; EPS=1e-9

def payoff(u,d,c):
    b=max(u,d)-c;f=min(u,d)-c;return {'upQty':u,'downQty':d,'cost':c,'floor':f,'best':b,'gap':b-f}

def apply(base,side,price,qty):
    u=float(base['upQty']);d=float(base['downQty']);c=float(base['cost'])
    if side=='UP':u+=qty
    else:d+=qty
    c+=price*qty
    return payoff(u,d,c)

def delta(p,b):return {'dFloor':float(p['floor'])-float(b['floor']),'dBest':float(p['best'])-float(b['best']),'dGap':float(p['gap'])-float(b['gap'])}

class CaptureSim(diag.SnapSim):
    def __init__(self,*a,**kw):super().__init__(*a,**kw);self.involvedWorldRows={}
    def _carrier_row(self,t,key,source):
        o=self.orders.get(str(key))
        if not o:return None
        side=str(o['side']); role=str(self.key_role.get(str(key)) or 'SATELLITE_REPAIR'); price=float(o['price']); qty=float(self._remaining(str(key)))
        return wm.TraceSim._state_row(self,int(t),side,role,price,qty,'PASSIVE',str(key),source)
    def _arm_fork(self,t,branch_key=None,pre_state=None):
        super()._arm_fork(t,branch_key,pre_state)
        sk=str(self.spec['siblingKey']); sr=self._carrier_row(t,sk,'EXACT_SIBLING_POSTSUBMIT');
        if sr:self.involvedWorldRows[sk]=sr
        if branch_key:
            br=self._carrier_row(t,str(branch_key),'EXACT_BRANCH_POSTSUBMIT')
            if br:self.involvedWorldRows[str(branch_key)]=br

def predict_carrier(bundle,row):
    x=wm.X([row],True);m=bundle['models'];p=float(m['fillFirst'].predict_proba(x)[0,1]);lag=max(.05,float(np.expm1(m['eventLagLog'].predict(x)[0])));q=max(0.,min(float(row['qty']),float(m['firstFillQty'].predict(x)[0])))
    return {'pFillFirst':p,'eventLagSec':lag,'conditionalFirstFillQty':q,'rate':1.0/lag,'fillRate':p/lag,'terminalRate':(1.0-p)/lag}

def engine(base,carrier_rows,bundle):
    preds=[];events=[]
    for key,row in carrier_rows.items():
        pr=predict_carrier(bundle,row);preds.append({'key':key,'row':row,'pred':pr})
        events.append((pr['fillRate'],'FILL',key,row,pr));events.append((pr['terminalRate'],'TERMINAL',key,row,pr))
    total=sum(max(0.,e[0]) for e in events)
    if total<=EPS:return {'expected':{'dFloor':0.,'dBest':0.,'dGap':0.},'predictedEventLagSec':None,'carriers':preds,'eventMixture':[]}
    exp=np.zeros(3,float); mix=[]
    for rate,kind,key,row,pr in events:
        prob=max(0.,rate)/total
        if kind=='FILL':pd=delta(apply(base,str(row['side']),float(row['price']),float(pr['conditionalFirstFillQty'])),base)
        else:pd={'dFloor':0.,'dBest':0.,'dGap':0.}
        exp+=prob*np.asarray([pd['dFloor'],pd['dBest'],pd['dGap']],float)
        mix.append({'key':key,'kind':kind,'probability':prob,'localDelta':pd})
    return {'expected':{'dFloor':float(exp[0]),'dBest':float(exp[1]),'dGap':float(exp[2])},'predictedEventLagSec':1.0/total,'carriers':preds,'eventMixture':mix}

def pareto(f,b):
    out=set()
    for i in range(len(f)):
        dom=False
        for j in range(len(f)):
            if i==j:continue
            if f[j]>=f[i]-1e-9 and b[j]>=b[i]-1e-9 and (f[j]>f[i]+1e-9 or b[j]>b[i]+1e-9):dom=True;break
        if not dom:out.add(i)
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--spec',required=True);ap.add_argument('--model',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    sd=json.loads(Path(a.spec).read_text(encoding='utf-8'));specs={int(k):v for k,v in sd['markets'].items()};mids=sorted(specs)
    for m,s in specs.items():diag.ma.FROZEN[m]=s
    bundle=joblib.load(a.model);tmp=Path(tempfile.mkdtemp(prefix='lane_g_single_event_cf_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[]
        for m in mids:
            for action in ACTIONS:
                sim=CaptureSim(tmp/f'{m}.json.xz',m,action,1,4)
                try:r=sim.run_exact(co[m]['winner']);carrier_rows=dict(sim.involvedWorldRows)
                finally:sim.close()
                tr=(r.get('trigger') or {}).get('payoff');ev=(r.get('firstStructuralEvent') or {});ep=ev.get('payoff');actual=delta(ep,tr) if tr and ep else None;pred=engine(tr,carrier_rows,bundle) if tr and carrier_rows else None
                rec={'marketId':m,'action':action,'triggered':r['triggered'],'resolved':r['resolved'],'triggerParityErrors':r['triggerParityErrors'],'rawCorrect':r['correct'],'triggerPayoff':tr,'actualFirstEvent':ev,'actualDelta':actual,'involvedRows':carrier_rows,'prediction':pred};rows.append(rec)
                print(json.dumps({'marketId':m,'action':action,'actual':actual,'pred':(pred or {}).get('expected'),'predLag':(pred or {}).get('predictedEventLagSec'),'carriers':list(carrier_rows)},ensure_ascii=False),flush=True)
        valid=[x for x in rows if x['actualDelta'] is not None and x['prediction'] is not None]
        labels=['dFloor','dBest','dGap'];metrics={}
        for k in labels:
            y=[x['actualDelta'][k] for x in valid];p=[x['prediction']['expected'][k] for x in valid];z=[0.0]*len(y)
            metrics[k]={'engineMAE':float(mean_absolute_error(y,p)),'zeroChangeMAE':float(mean_absolute_error(y,z)),'improvementVsZero':float(mean_absolute_error(y,z)-mean_absolute_error(y,p))}
        floor_ok=best_ok=0;js=[];market_eval=[]
        for m in mids:
            rr=[x for x in valid if x['marketId']==m];af=np.array([x['actualDelta']['dFloor'] for x in rr]);ab=np.array([x['actualDelta']['dBest'] for x in rr]);pf=np.array([x['prediction']['expected']['dFloor'] for x in rr]);pb=np.array([x['prediction']['expected']['dBest'] for x in rr])
            fi=int(np.argmax(pf));bi=int(np.argmax(pb));fok=bool(af[fi]>=af.max()-1e-8);bok=bool(ab[bi]>=ab.max()-1e-8);floor_ok+=fok;best_ok+=bok;tf=pareto(af,ab);pp=pareto(pf,pb);j=len(tf&pp)/len(tf|pp) if tf|pp else 1.;js.append(j);market_eval.append({'marketId':m,'predFloorAction':rr[fi]['action'],'floorChoiceCorrectTieAware':fok,'predBestAction':rr[bi]['action'],'bestChoiceCorrectTieAware':bok,'truePareto':[rr[i]['action'] for i in sorted(tf)],'predPareto':[rr[i]['action'] for i in sorted(pp)],'paretoJaccard':j})
        selection={'floorChoiceAccuracyTieAware':floor_ok/len(mids),'bestChoiceAccuracyTieAware':best_ok/len(mids),'paretoJaccardMean':float(np.mean(js)),'markets':market_eval}
        gates={'allTriggered':all(x['triggered'] for x in rows),'allResolved':all(x['resolved'] for x in rows),'triggerParityClean':all(not x['triggerParityErrors'] for x in rows),'allPredictionsPresent':len(valid)==len(rows),'engineBeatsZeroOnFloor':metrics['dFloor']['improvementVsZero']>0,'engineBeatsZeroOnBest':metrics['dBest']['improvementVsZero']>0,'floorChoiceAtLeast60pct':selection['floorChoiceAccuracyTieAware']>=.60,'bestChoiceAtLeast60pct':selection['bestChoiceAccuracyTieAware']>=.60}
        out={'version':'LANE_G_SINGLE_EVENT_COUNTERFACTUAL_ENGINE_V1_20260907','researchOnly':True,'runtimeAuthority':False,'rows':rows,'metrics':metrics,'selection':selection,'gates':gates,'enginePass':all(gates.values()),'rawCorrectWarningCount':sum(1 for x in rows if not x['rawCorrect']),'boundary':['one structural event only','exact post-submit state for every involved carrier','competing exponential event rates derived from H100 structural-event p(fill-first) and predicted event lag','conditional fill quantity from H100 structural-event model','no multi-step synthetic market rollout','terminal PnL not used','whole-episode validation remains historical exact HFT','consumed only/no fresh/no winner future/no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
        print(json.dumps({'ok':True,'metrics':metrics,'selection':selection,'gates':gates,'enginePass':out['enginePass'],'rawCorrectWarningCount':out['rawCorrectWarningCount']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
