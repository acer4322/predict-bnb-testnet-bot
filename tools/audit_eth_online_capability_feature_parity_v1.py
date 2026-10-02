from __future__ import annotations
import argparse,json,sqlite3,sys,importlib.util
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
try:
    from tools import train_eth_persistent_repair_specialist_v3_parent_child_graph as v3
except ImportError:
    p=Path(__file__).resolve().with_name('train_eth_persistent_repair_specialist_v3_parent_child_graph.py');s=importlib.util.spec_from_file_location('v3_parity',p);v3=importlib.util.module_from_spec(s);s.loader.exec_module(v3)
try:
    from tools.eth_persistent_repair_online_capability_runtime import OnlineCapabilityState
except ImportError:
    p=Path(__file__).resolve().with_name('eth_persistent_repair_online_capability_runtime.py');s=importlib.util.spec_from_file_location('online_runtime_parity',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);OnlineCapabilityState=m.OnlineCapabilityState

def mx(a,b):return float(np.max(np.abs(np.asarray(a)-np.asarray(b)))) if np.size(a) else 0.0

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output');a=ap.parse_args()
 offline,cut,nwin,births=v3.build(a.db);oby={}
 for r in offline:oby.setdefault(int(r['market']),[]).append(r)
 c=sqlite3.connect(a.db);c.row_factory=sqlite3.Row
 mend={int(r['market_id']):int(r['window_end_ms']) for r in c.execute("select market_id,window_end_ms from target_markets where asset='ETH' and window_end_ms is not null")}
 raw=list(c.execute("select parent_id,market_id,role,side,first_event_ms,average_price,shares from target_parent_orders where asset='ETH' order by market_id,first_event_ms,parent_id"));c.close();by={}
 for r in raw:by.setdefault(int(r['market_id']),[]).append(r)
 maxima={k:0.0 for k in ('cur','graph','allseq','allmask','repseq','repmask','expseq','expmask')};compared=0;rowMismatch=0;worst=[]
 for mid,evs in by.items():
  st=OnlineCapabilityState();off=oby.get(mid,[]);j=0;end=mend.get(mid)
  for e in evs:
   role=str(e['role']);side=str(e['side']);t=int(e['first_event_ms']);sh=float(e['shares']);px=float(e['average_price']);pre_rel=v3.rel_for(side,st.up,st.dn) if role=='MAKER' else 0
   rel,s=st.apply_event(role,side,t,sh,px,end)
   if role=='MAKER' and pre_rel!=0:
    if j>=len(off):rowMismatch+=1;continue
    r=off[j];j+=1;ds={'cur':mx(s['cur'],r['cur']),'graph':mx(s['graph'],r['graph']),'allseq':mx(s['allseq'],r['allseq']),'allmask':mx(s['allmask'],r['allmask']),'repseq':mx(s['repseq'],r['repseq']),'repmask':mx(s['repmask'],r['repmask']),'expseq':mx(s['expseq'],r['expseq']),'expmask':mx(s['expmask'],r['expmask'])}
    compared+=1
    for k,v in ds.items():maxima[k]=max(maxima[k],v)
    w=max(ds.values())
    if w>1e-7:worst.append({'marketId':mid,'t':t,'index':j-1,'maxDiff':w,'diffs':ds})
  if j!=len(off):rowMismatch+=abs(len(off)-j)
 passv=bool(compared==len(offline) and rowMismatch==0 and max(maxima.values())<=1e-6)
 out={'version':'ETH_ONLINE_CAPABILITY_FEATURE_PARITY_V1','db':str(a.db),'windows':nwin,'offlineRows':len(offline),'comparedRows':compared,'rowMismatch':rowMismatch,'offlineParentBirths':births,'maxAbsDiff':maxima,'worstExamples':sorted(worst,key=lambda x:x['maxDiff'],reverse=True)[:10],'parityPass':passv,'passRule':'all offline maker responsibility rows reproduced; no row mismatch; every current/graph/sequence/mask max abs diff <=1e-6'}
 op=Path(a.output) if a.output else Path(__import__('os').environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'parityPass':passv,'compared':compared,'rows':len(offline),'maxDiff':maxima,'rowMismatch':rowMismatch,'output':str(op)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
