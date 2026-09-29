from __future__ import annotations
import argparse,json,sqlite3,math,os,random
from pathlib import Path
from collections import defaultdict,Counter
import numpy as np
import torch,torch.nn as nn
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score

SEED=20260904
random.seed(SEED);np.random.seed(SEED);torch.manual_seed(SEED)
EPS=1e-9;SEQ=24
CUR_FEATURES=['seconds_left_norm','pair_coverage','absnet_ratio','floor_ratio','best_ratio','gross_log','overflow_gap_ratio','price','route_taker','delta_floor_ratio','delta_best_ratio','recent_repair_frac','recent_expand_frac','recent_taker_frac','prev_age_log','event_count_norm']
TOK_FEATURES=['econ_role','route_taker','price','qty_gap_ratio','delta_absnet','delta_paircov','delta_floor_ratio','delta_best_ratio','elapsed_log','post_absnet','post_paircov','crossing','overflow_gap_ratio']
TASKS=('next_role_repair','next_composite')

def econ_role(side,up,dn):
    if up+dn<=EPS or abs(up-dn)<=EPS:return 0
    weak='UP' if up<dn else 'DOWN'
    return 1 if side==weak else -1

def met(up,dn,cost):
    gross=up+dn;pair=min(up,dn);gap=abs(up-dn);pc=2*pair/gross if gross>EPS else 1.;ab=gap/gross if gross>EPS else 0.;floor=pair-cost;best=max(up,dn)-cost
    return {'gross':gross,'pair':pair,'gap':gap,'pc':pc,'ab':ab,'floor':floor,'best':best}

def pad(tokens):
    x=np.zeros((SEQ,len(TOK_FEATURES)),np.float32);m=np.zeros(SEQ,np.float32);h=tokens[-SEQ:]
    if h:x[-len(h):]=np.asarray(h,np.float32);m[-len(h):]=1.
    return x,m

def build(db):
    c=sqlite3.connect(db);c.row_factory=sqlite3.Row
    mend={int(r['market_id']):int(r['window_end_ms']) for r in c.execute("select market_id,window_end_ms from target_markets where asset='ETH' and window_end_ms is not null")}
    raw=list(c.execute("select parent_id,market_id,role,side,first_event_ms,average_price,shares from target_parent_orders where asset='ETH' order by market_id,first_event_ms,parent_id"));c.close()
    by=defaultdict(list)
    for r in raw:by[int(r['market_id'])].append(r)
    ends=sorted(set(mend.values()));cut1=ends[int(len(ends)*.60)];cut2=ends[int(len(ends)*.80)]
    rows=[];all_cross=0;continued=0
    for mid,evs in by.items():
        up=dn=cost=0.;tokens=[];hist=[];prev_t=None
        for i,r in enumerate(evs):
            side=str(r['side']);route=str(r['role']);t=int(r['first_event_ms']);q=float(r['shares']);px=float(r['average_price']);pre=met(up,dn,cost);rel=econ_role(side,up,dn);pre_gap=pre['gap']
            if side=='UP':up+=q
            else:dn+=q
            cost+=q*px;post=met(up,dn,cost);cross=(rel==1 and pre_gap>EPS and q>=pre_gap-EPS);overflow=max(0.,q-pre_gap) if cross else 0.;elapsed=1e6 if prev_t is None else t-prev_t
            tok=np.asarray([float(rel),float(route=='TAKER'),px,max(0.,min(4.,q/max(pre_gap,1.))),post['ab']-pre['ab'],post['pc']-pre['pc'],(post['floor']-pre['floor'])/max(abs(cost),1.),(post['best']-pre['best'])/max(abs(cost),1.),math.log1p(min(elapsed,120000))/math.log1p(120000),post['ab'],post['pc'],float(cross),max(0.,min(6.,overflow/max(pre_gap,EPS)))],np.float32)
            tokens.append(tok);hist.append({'t':t,'rel':rel,'route':route})
            if cross:
                all_cross+=1;nxt=evs[i+1] if i+1<len(evs) else None;nr=None;nc=None;delay=None
                if nxt is not None:
                    delay=int(nxt['first_event_ms'])-t
                    if delay<=30000:
                        continued+=1;ns=str(nxt['side']);nq=float(nxt['shares']);nr=econ_role(ns,up,dn);ng=post['gap'];nc=int(nr==1 and ng>EPS and nq>=ng-EPS)
                if nr is not None:
                    recent=hist[-12:];rc=Counter(x['rel'] for x in recent);rt=Counter(x['route'] for x in recent);end=mend.get(mid);sl=0. if end is None else max(-30.,min(330.,(end-t)/1000.))/300.;gross=post['gross'];seq,mask=pad(tokens)
                    cur=np.asarray([sl,post['pc'],post['ab'],max(-5.,min(5.,post['floor']/max(abs(cost),1.))),max(-5.,min(5.,post['best']/max(abs(cost),1.))),math.log1p(gross)/math.log1p(500),max(0.,min(6.,overflow/max(pre_gap,EPS))),px,float(route=='TAKER'),max(-5.,min(5.,(post['floor']-pre['floor'])/max(abs(cost),1.))),max(-5.,min(5.,(post['best']-pre['best'])/max(abs(cost),1.))),rc[1]/max(1,len(recent)),rc[-1]/max(1,len(recent)),rt['TAKER']/max(1,len(recent)),math.log1p(min(elapsed,120000))/math.log1p(120000),math.log1p(len(hist))/math.log1p(128)],np.float32)
                    rows.append({'market':mid,'end':end,'t':t,'cur':cur,'seq':seq,'mask':mask,'next_role_repair':int(nr==1),'next_composite':int(nc),'next_delay_ms':delay})
            prev_t=t
    for r in rows:r['split']='train' if int(r['end'] or 0)<cut1 else 'validation' if int(r['end'] or 0)<cut2 else 'test'
    return rows,cut1,cut2,len(ends),{'crossingRepairStates':all_cross,'continuedWithin30s':continued,'continuationRate':continued/max(1,all_cross)}

def standardize(tr,others):
    X=np.stack([r['cur'] for r in tr]);mu=X.mean(0);sd=np.where(X.std(0)<1e-5,1.,X.std(0));T=np.concatenate([r['seq'][r['mask']>0] for r in tr if r['mask'].sum()>0],0);tm=T.mean(0);ts=np.where(T.std(0)<1e-5,1.,T.std(0))
    for z in [tr]+list(others):
        for r in z:r['curz']=((r['cur']-mu)/sd).astype(np.float32);r['seqz']=((r['seq']-tm)/ts).astype(np.float32)*r['mask'][:,None]
    return {'mu':mu,'sd':sd,'tokenMu':tm,'tokenSd':ts}

def metric(y,p):
    y=np.asarray(y,int);p=np.asarray(p,float);pred=(p>=.5).astype(int)
    return {'n':int(len(y)),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum()>0 else None,'balancedAccuracy':float(balanced_accuracy_score(y,pred))}

class CurrentNet(nn.Module):
    def __init__(self):super().__init__();self.body=nn.Sequential(nn.Linear(len(CUR_FEATURES),64),nn.ReLU(),nn.LayerNorm(64),nn.Linear(64,40),nn.ReLU());self.role=nn.Linear(40,1);self.comp=nn.Linear(40,1)
    def forward(self,c,s=None):z=self.body(c);return self.role(z).squeeze(-1),self.comp(z).squeeze(-1)

class TransitionNet(nn.Module):
    def __init__(self):super().__init__();self.cur=nn.Sequential(nn.Linear(len(CUR_FEATURES),48),nn.ReLU(),nn.LayerNorm(48));self.gru=nn.GRU(len(TOK_FEATURES),40,batch_first=True);self.mix=nn.Sequential(nn.Linear(88,64),nn.ReLU(),nn.LayerNorm(64),nn.Linear(64,40),nn.ReLU());self.role=nn.Linear(40,1);self.comp=nn.Linear(40,1)
    def forward(self,c,s):_,h=self.gru(s);z=self.mix(torch.cat([self.cur(c),h[-1]],1));return self.role(z).squeeze(-1),self.comp(z).squeeze(-1)

def arrays(rows):return np.stack([r['curz'] for r in rows]),np.stack([r['seqz'] for r in rows]),np.asarray([r['next_role_repair'] for r in rows],np.float32),np.asarray([r['next_composite'] for r in rows],np.float32)
def perturb_seq(S,rng):
    Z=S.copy()
    for i in range(len(Z)):
        valid=np.where(np.abs(Z[i]).sum(1)>0)[0]
        if len(valid) and rng.random()<.55:
            k=int(rng.integers(1,min(4,len(valid))+1));Z[i,valid[-k:]]=0.
    return Z

def wbce(logits,y,dev):
    pos=max(float(y.mean()),1e-4);return nn.functional.binary_cross_entropy_with_logits(logits,y,pos_weight=torch.tensor((1-pos)/pos,device=dev).clamp(.25,5))
def train(model,tr,dev,specialist):
    X,S,yr,yc=arrays(tr);rng=np.random.default_rng(SEED+(1 if specialist else 0));opt=torch.optim.AdamW(model.parameters(),lr=8e-4,weight_decay=1e-4);model.to(dev)
    for ep in range(12):
        run=0.
        for _ in range(120):
            idx=rng.integers(0,len(X),size=min(512,len(X)));cx=X[idx];cs=S[idx];ry=yr[idx];cy=yc[idx]
            if specialist and rng.random()<.5:cs=perturb_seq(cs,rng)
            xt=torch.from_numpy(cx).to(dev);st=torch.from_numpy(cs).to(dev);rt=torch.from_numpy(ry).to(dev);ct=torch.from_numpy(cy).to(dev);lr,lc=model(xt,st if specialist else None);loss=wbce(lr,rt,dev)+wbce(lc,ct,dev);opt.zero_grad();loss.backward();nn.utils.clip_grad_norm_(model.parameters(),5);opt.step();run+=float(loss.detach().cpu())
        print(json.dumps({'model':'SPECIALIST' if specialist else 'CURRENT_BASELINE','epoch':ep+1,'loss':run/120}),flush=True)
    return model

def evaluate(model,rows,dev,specialist,noisy=False):
    X,S,yr,yc=arrays(rows);rng=np.random.default_rng(SEED+99)
    if noisy and specialist:S=perturb_seq(S,rng)
    pr=[];pc=[];model.eval()
    with torch.no_grad():
        for i in range(0,len(X),4096):
            a,b=model(torch.from_numpy(X[i:i+4096]).to(dev),torch.from_numpy(S[i:i+4096]).to(dev) if specialist else None);pr.append(torch.sigmoid(a).cpu().numpy());pc.append(torch.sigmoid(b).cpu().numpy())
    return {'next_role_repair':metric(yr,np.concatenate(pr)),'next_composite':metric(yc,np.concatenate(pc))}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);ap.add_argument('--model-out',required=True);a=ap.parse_args();rows,c1,c2,nwin,anatomy=build(a.db);tr=[r for r in rows if r['split']=='train'];va=[r for r in rows if r['split']=='validation'];te=[r for r in rows if r['split']=='test'];stats=standardize(tr,[va,te]);dev='cuda' if torch.cuda.is_available() else 'cpu'
    torch.manual_seed(SEED);base=train(CurrentNet(),tr,dev,False);bm={'validation':evaluate(base,va,dev,False),'test':evaluate(base,te,dev,False)}
    torch.manual_seed(SEED);spec=train(TransitionNet(),tr,dev,True);sm={'validation':evaluate(spec,va,dev,True),'test':evaluate(spec,te,dev,True)};nm={'validation':evaluate(spec,va,dev,True,True),'test':evaluate(spec,te,dev,True,True)}
    delta={s:{t:(sm[s][t]['auc']-bm[s][t]['auc']) for t in TASKS} for s in ['validation','test']};rob={s:{t:(nm[s][t]['auc']-sm[s][t]['auc']) for t in TASKS} for s in ['validation','test']}
    out={'version':'TARGET_ETH_POST_SETTLEMENT_TRANSITION_MODEL_V1','date':'2026-09-04','researchOnly':True,'device':dev,'sourceDb':os.path.abspath(a.db),'windows':nwin,'datasetAnatomy':anatomy,'splitCutoffs':{'trainEndExclusiveMs':c1,'validationEndExclusiveMs':c2},'rows':len(rows),'trainRows':len(tr),'validationRows':len(va),'testRows':len(te),'features':{'current':CUR_FEATURES,'token':TOK_FEATURES,'sequenceLength':SEQ},'tasks':list(TASKS),'baselineCurrentOnly':bm,'transitionSpecialist':sm,'transitionSpecialistPerturbed':nm,'aucLiftSpecialistMinusCurrent':delta,'aucPerturbDrop':rob,'boundary':['Target crossing-Repair post-fill states with observed continuation <=30s','future Target parent role/composite is offline label only','no winner/PnL/settlement outcome feature','economic role is side-vs-inventory; Maker/Taker remains execution route feature','information/shadow model only; no action authority','ETH-only gradients','no threshold sweep']}
    Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');torch.save({'version':out['version'],'currentFeatures':CUR_FEATURES,'tokenFeatures':TOK_FEATURES,'stats':stats,'state_dict':spec.state_dict(),'boundary':out['boundary']},a.model_out);print(json.dumps({'ok':True,'device':dev,'rows':[len(tr),len(va),len(te)],'baseline':bm,'specialist':sm,'perturbed':nm,'lift':delta,'robustness':rob},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
