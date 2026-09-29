"""Fast structural micro-world search using OUR-native execution surrogate V1.

No Target data enters transitions. Target acquisition path is loaded only after each
simulated rollout for scoring. Search is discrete architecture only: no threshold,
sizing or coefficient optimization.

Every fast-world winner remains research-only and must return unchanged to native HFT.
"""
from __future__ import annotations
import argparse,bisect,gzip,itertools,json,math,os,random,re,statistics,time
from pathlib import Path

EXEC_MODEL=Path(r'C:/BTC5M-worker/.lan_worker_v1/results/core-execution-surrogate-fit-v1-20260912/result.json')
TRACE_ROOT=Path(r'C:/BTC5M-worker/.lan_worker_v1/results/open-funding-recovery-train-20260911-v3')
BUNDLE=Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/open_funding_recovery_train_20260911_v3')
MARKET=2022527
TICK=.01
MIN_PASSIVE_SHARES=18.


def sigmoid(z):
 if z>=0:
  e=math.exp(-min(z,30));return 1/(1+e)
 e=math.exp(max(z,-30));return e/(1+e)

def softplus(x):return max(x,0)+math.log1p(math.exp(-abs(x)))

def load_public_path():
 p=TRACE_ROOT/'TRAIN_INITIAL_2022527.jsonl.gz';out=[]
 with gzip.open(p,'rt',encoding='utf-8') as f:
  for line in f:
   z=json.loads(line)
   if z['kind']!='complete_policy_step':continue
   d=z['data'];ft=d.get('features') or {}
   if not ft:continue
   out.append(dict(t=int(d['t']),phase=float(ft['phase']),mid=float(ft['mid']),up_bid=float(ft['up_bid']),up_ask=float(ft['up_ask']),depth_imbalance=float(ft['depth_imbalance'])))
 if not out:raise RuntimeError('public path missing')
 return out

def load_source(mid):
 p=BUNDLE/f'input_{mid}.json.gz'
 with gzip.open(p,'rt',encoding='utf-8') as f:return json.load(f)

def target_observed(source):
 groups={}
 for a in source['targetActions']:
  if a.get('quote_type')!='BID' or a.get('side') not in ('UP','DOWN'):continue
  groups.setdefault(int(a['event_ms']),[]).append(a)
 inv={'UP':0.,'DOWN':0.};cost=0.;out=[]
 for t,aa in sorted(groups.items()):
  for a in aa:inv[a['side']]+=float(a['shares']);cost+=float(a['price'])*float(a['shares'])
  out.append((t,dict(inv),cost,aa))
 return out

def training_qref():
 gross=[]
 for mid in (2022527,2022538):
  for _,inv,_,_ in target_observed(load_source(mid)):gross.append(inv['UP']+inv['DOWN'])
 return float(statistics.median(gross))

def target_profile(obs,start,end,bins=6):
 seq=[];hist=[0.]*bins
 for t,_,_,aa in obs:
  uq=sum(float(a['shares']) for a in aa if a['side']=='UP');dq=sum(float(a['shares']) for a in aa if a['side']=='DOWN');seq.append('UP' if uq>=dq else 'DOWN')
  ph=max(0.,min(.999999,(t-start)/max(1,end-start)));hist[min(bins-1,int(ph*bins))]+=uq+dq
 z=sum(hist);hist=[x/z if z else 0. for x in hist];sw=sum(a!=b for a,b in zip(seq,seq[1:]))/max(1,len(seq)-1)
 return dict(event_batches=len(seq),switch_rate=sw,phase_hist=hist)

def own_profile(fill_events,start,end,bins=6):
 seq=[];hist=[0.]*bins
 for t,du,dd in fill_events:
  if du+dd<=1e-12:continue
  seq.append('UP' if du>=dd else 'DOWN');ph=max(0.,min(.999999,(t-start)/max(1,end-start)));hist[min(bins-1,int(ph*bins))]+=du+dd
 z=sum(hist);hist=[x/z if z else 0. for x in hist];sw=sum(a!=b for a,b in zip(seq,seq[1:]))/max(1,len(seq)-1)
 return dict(event_batches=len(seq),switch_rate=sw,phase_hist=hist)

def state_vec(inv,cost,qref):return [inv['UP']/qref,inv['DOWN']/qref,(inv['UP']-cost)/qref,(inv['DOWN']-cost)/qref]

def score_rollout(states,fill_events,obs,tp,start,end,qref):
 times=[x[0] for x in states];terms=[0.,0.,0.,0.]
 for t,tinv,tcost,_ in obs:
  j=bisect.bisect_right(times,t)-1
  if j>=0:_,u,d,c=states[j];oinv={'UP':u,'DOWN':d};ocost=c
  else:oinv={'UP':0.,'DOWN':0.};ocost=0.
  tv=state_vec(tinv,tcost,qref);ov=state_vec(oinv,ocost,qref)
  for k in range(4):terms[k]+=(ov[k]-tv[k])**2
 path=sum(x/len(obs) for x in terms)/4.;op=own_profile(fill_events,start,end);geometry=math.exp(-6*path);phase=max(0.,1-.5*sum(abs(a-b) for a,b in zip(tp['phase_hist'],op['phase_hist'])));switch=max(0.,1-abs(tp['switch_rate']-op['switch_rate']));activity=math.exp(-abs(math.log((op['event_batches']+1)/(tp['event_batches']+1))))
 return .60*geometry+.15*phase+.15*switch+.10*activity,path,op

class ExecModel:
 def __init__(self,d):
  m=d['model'];self.mu=m['feature_mean'];self.sd=m['feature_sd'];self.w=m['fill_logistic_weights'];self.fm=m['fill_fraction_ridge_mean'];self.fs=m['fill_fraction_ridge_sd'];self.fw=m['fill_fraction_ridge_weights'];self.lm=m['latency_ridge_mean'];self.ls=m['latency_ridge_sd'];self.lw=m['latency_log_ridge_weights']
 def lin(self,x,mu,sd,w):return w[0]+sum(w[i+1]*((x[i]-mu[i])/sd[i]) for i in range(len(x)))
 def predict(self,x):
  p=sigmoid(self.lin(x,self.mu,self.sd,self.w));uncond=max(0.,min(1.,self.lin(x,self.fm,self.fs,self.fw)));cond=max(.01,min(1.,uncond/max(p,.02)));lat=max(1.,math.expm1(self.lin(x,self.lm,self.ls,self.lw)));return p,cond,lat

def arch_space():
 dims=[('clock',['TIME','SOURCE_EVENT']),('own_feedback',[0,1]),('residual',['FRESH','PERSIST']),('progression',['REVERSE','FLAT','FORWARD']),('rearm',['FILL_ONLY','TERMINAL','ALL']),('objective',['REPAIR_ONLY','EXPAND_ONLY','ALTERNATE','SERIAL_FLOOR_FIRST','JOINT']),('market_direction',[0,1])]
 out=[]
 for vals in itertools.product(*(v for _,v in dims)):out.append(dict(zip((k for k,_ in dims),vals)))
 return out

def run_trial(cfg,seed,pub,model,qref):
 rng=random.Random(seed);start=pub[0]['t'];end=pub[-1]['t'];inv={'UP':0.,'DOWN':0.};cost=0.;pending=[];states=[];fills=[];submitted=False;last_des={'UP':0.,'DOWN':0.};serial_next='REPAIR';nsub=0;nterm=0
 for i,f in enumerate(pub):
  t=f['t'];due=[];keep=[]
  for o in pending:
   (due if o['term_t']<=t else keep).append(o)
  pending=keep;terminal_event=bool(due);fill_event=False;du=dd=0.
  for o in due:
   nterm+=1
   if o['will_fill']:
    q=o['qty']*o['fill_fraction'];inv[o['side']]+=q;cost+=q*o['price'];fill_event=True
    if o['side']=='UP':du+=q
    else:dd+=q
  if du+dd>0:fills.append((t,du,dd))
  states.append((t,inv['UP'],inv['DOWN'],cost))
  allow=(not submitted) or cfg['rearm']=='ALL' or (cfg['rearm']=='TERMINAL' and terminal_event) or (cfg['rearm']=='FILL_ONLY' and fill_event)
  if not allow:
   if submitted and not pending and cfg['rearm']!='ALL':break
   continue
  p=f['phase'] if cfg['clock']=='TIME' else i/max(1,len(pub)-1);prog=2*p-1
  gross=qref*math.exp((1 if cfg['progression']=='FORWARD' else -1 if cfg['progression']=='REVERSE' else 0)*prog)
  own_gross=inv['UP']+inv['DOWN'];own_net=(inv['UP']-inv['DOWN'])/(1+own_gross);z=0.
  if cfg['own_feedback']:z+=-.8*own_net
  if cfg['market_direction']:z+=1.2*(2*f['mid']-1)+.3*f['depth_imbalance']
  exposure=math.tanh(z);share={'UP':(1+exposure)/2,'DOWN':(1-exposure)/2};reserved={'UP':0.,'DOWN':0.}
  for o in pending:reserved[o['side']]+=o['qty']
  desired={s:gross*share[s] for s in ('UP','DOWN')};full={s:max(0.,desired[s]-inv[s]-reserved[s]) for s in desired};fresh={s:max(0.,desired[s]-last_des[s]) for s in desired};service=full if cfg['residual']=='PERSIST' else {s:min(full[s],fresh[s]) for s in full}
  if abs(inv['UP']-inv['DOWN'])<=1e-12:roles={'UP':'NEUTRAL','DOWN':'NEUTRAL'}
  else:
   weak='UP' if inv['UP']<inv['DOWN'] else 'DOWN';strong='DOWN' if weak=='UP' else 'UP';roles={weak:'REPAIR',strong:'EXPAND'}
  floor=min(inv.values())-cost;new_any=False
  for s in sorted(('UP','DOWN'),key=lambda x:-service[x]*(f['mid'] if x=='UP' else 1-f['mid'])):
   role=roles[s];ok=role=='NEUTRAL'
   if cfg['objective']=='JOINT':ok=True
   elif cfg['objective']=='REPAIR_ONLY':ok=ok or role=='REPAIR'
   elif cfg['objective']=='EXPAND_ONLY':ok=ok or role=='EXPAND'
   elif cfg['objective']=='SERIAL_FLOOR_FIRST':ok=ok or role==('REPAIR' if floor<0 else 'EXPAND')
   elif cfg['objective']=='ALTERNATE':ok=ok or role==serial_next
   if not ok or service[s]<=0:continue
   bb=f['up_bid'] if s=='UP' else 1-f['up_ask'];ba=f['up_ask'] if s=='UP' else 1-f['up_bid'];price=math.floor((bb-softplus(-1.2)*TICK+1e-10)/TICK)*TICK
   if not (TICK<=price<1 and price<ba-1e-10):continue
   qty=math.floor((min(30.,service[s])+1e-10)/.01)*.01
   if qty+1e-9<MIN_PASSIVE_SHARES or qty*price<1:continue
   side_imb=f['depth_imbalance'] if s=='UP' else -f['depth_imbalance'];side_net=own_net if s=='UP' else -own_net;depth=max(0.,(bb-price)/TICK);spread=max(0.,(ba-bb)/TICK);x=[p,math.log1p(depth),spread,side_imb,side_net,math.log1p(own_gross),math.log(max(qty,1e-9)),price,float(len(pending))]
   pf,ff,lat=model.predict(x);will=rng.random()<pf;term_t=t+int(round(lat));pending.append(dict(side=s,qty=qty,price=price,term_t=term_t,will_fill=will,fill_fraction=ff));nsub+=1;new_any=True
  if new_any:
   submitted=True;last_des=dict(desired)
   if cfg['objective']=='ALTERNATE':serial_next='EXPAND' if serial_next=='REPAIR' else 'REPAIR'
 if not states:states=[(start,0.,0.,0.)]
 return states,fills,nsub,nterm

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--shard-index',type=int,required=True);ap.add_argument('--shards',type=int,default=4);ap.add_argument('--seeds',type=int,default=50);a=ap.parse_args();out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);out.mkdir(parents=True,exist_ok=True);t0=time.perf_counter()
 model_doc=json.loads(EXEC_MODEL.read_text(encoding='utf-8'));assert model_doc['pilot_gate']['pass_for_fast_structural_pilot'] and model_doc['target_data_used'] is False;model=ExecModel(model_doc);pub=load_public_path();qref=training_qref();src=load_source(MARKET);obs=target_observed(src);start=pub[0]['t'];end=pub[-1]['t'];tp=target_profile(obs,start,end);arch=arch_space();selected=[(i,c) for i,c in enumerate(arch) if i%a.shards==a.shard_index];rows=[]
 for pos,(aid,cfg) in enumerate(selected):
  ss=[];pp=[];ev=[];sub=[]
  for k in range(a.seeds):
   states,fills,nsub,nterm=run_trial(cfg,202609120000+aid*1000+k,pub,model,qref);sc,pm,op=score_rollout(states,fills,obs,tp,start,end,qref);ss.append(sc);pp.append(pm);ev.append(op['event_batches']);sub.append(nsub)
  rows.append(dict(architecture_id=aid,config=cfg,mean_similarity=statistics.fmean(ss),sd_similarity=statistics.pstdev(ss),mean_path_mse=statistics.fmean(pp),mean_event_batches=statistics.fmean(ev),mean_submits=statistics.fmean(sub),seeds=a.seeds))
  if (pos+1)%25==0:print(json.dumps({'stage':'progress','done':pos+1,'total':len(selected),'best':max(x['mean_similarity'] for x in rows)}),flush=True)
 result=dict(version='TARGET_CORE_CYCLE_FAST_SURROGATE_V1',researchOnly=True,market_id=MARKET,shard_index=a.shard_index,shards=a.shards,seeds_per_architecture=a.seeds,total_architectures=len(arch),architectures_in_shard=len(selected),trials=len(selected)*a.seeds,execution_surrogate_job='core-execution-surrogate-fit-v1-20260912',execution_surrogate_heldout_auc=model_doc['heldout_metrics']['auc_fill'],target_runtime_access=False,target_scoring_only=True,numeric_tuning=False,rows=rows,top=sorted(rows,key=lambda x:x['mean_similarity'],reverse=True)[:20],elapsedSec=time.perf_counter()-t0,boundary=['Discrete architecture search only.','Target path used only after rollout for scoring.','Execution transitions use OUR-native held-out-validated surrogate.','Fast-world winners require unchanged native-HFT replay before any mechanism claim.'])
 (out/'result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8');print(json.dumps({'status':'COMPLETE','shard':a.shard_index,'architectures':len(selected),'trials':result['trials'],'best':result['top'][0]['mean_similarity'],'elapsedSec':result['elapsedSec']}),flush=True)
if __name__=='__main__':main()
