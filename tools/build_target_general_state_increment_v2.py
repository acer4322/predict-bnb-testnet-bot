from __future__ import annotations
import bisect,json
from collections import defaultdict
from pathlib import Path
import pandas as pd
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import train_target_general_maker_side_hazard_v1 as g
coord=g.coord
OUT=ROOT/'data'/'research'/'supervisor_options_v0'
CSV=OUT/'target_general_state_increment_v2.csv'
REPORT=OUT/'target_general_state_increment_v2_report.json'
OLD_MAX=1787132400000

def build():
    OUT.mkdir(parents=True,exist_ok=True);b,t=coord.ro(coord.BOOK_DB),coord.ro(coord.TARGET_DB)
    try:
        meta=coord.load_market_meta(b)
        candidate={m for m,z in meta.items() if int(z['window_end_ms'])>OLD_MAX}
        updates={int(r[0]) for r in b.execute('select distinct market_id from maker_book_inference_updates') if int(r[0]) in candidate}
        # target_parent_orders is current and indexed enough for this small incremental set.
        target={int(r[0]) for r in t.execute("select distinct market_id from target_parent_orders where asset='BTC' and quote_type='BID' and first_event_ms>?",(OLD_MAX-300000,))}
        markets=candidate&updates&target
        events=coord.load_events(t,markets);parents=coord.load_anchored_maker_parents(b,markets);rows=[];drop=defaultdict(int)
        ordered=sorted(markets,key=lambda x:int(meta[x]['window_end_ms']))
        for mi,m in enumerate(ordered,1):
            if not events.get(m):drop['no_wallet_shadow_events']+=1;continue
            mend=int(meta[m]['window_end_ms']);mstart=mend-300000;ev=events[m];ps=parents.get(m,[]);pt=[int(p['placement_first_ms']) for p in ps];inv=coord.Inventory();ei=0;state={'bids':{},'asks':{}};last=None;checkpoints=list(range(mstart+500,mend-1500,1000));ci=0
            for u in b.execute('select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(m,)):
                ut=int(u['source_timestamp_ms'])
                while ci<len(checkpoints) and checkpoints[ci]<ut:
                    cp=checkpoints[ci]
                    while ei<len(ev) and int(ev[ei]['event_ms'])<=cp:inv.apply(ev[ei]);ei+=1
                    age=cp-last if last is not None else 10**9
                    if 0<=age<=2000:
                        f=inv.features(cp);cn=float(f.pop('_combined_net'));dom='UP' if cn>1e-9 else 'DOWN' if cn<-1e-9 else None;bf=coord.outcome_book(state,dom)
                        if bf:
                            j0=bisect.bisect_right(pt,cp);j1=bisect.bisect_right(pt,cp+1000);future=ps[j0:j1]
                            rows.append({'market_id':m,'market_end_ms':mend,'checkpoint_ms':cp,'book_age_ms':age,'seconds_left':(mend-cp)/1000.0,'label_up_next1s':int(any(str(p['target_side'])=='UP' for p in future)),'label_down_next1s':int(any(str(p['target_side'])=='DOWN' for p in future)),**f,**bf,**g.placement_features(ps,pt,cp)})
                        else:drop['empty_book']+=1
                    else:drop['book_stale']+=1
                    ci+=1
                if int(u['is_checkpoint']):state={'bids':{float(k):float(v) for k,v in (coord.dec(u['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (coord.dec(u['native_asks_z']) or {}).items()}}
                else:coord.apply_changes(state,coord.dec(u['changes_z']) or {})
                last=ut
            if mi%10==0:print(json.dumps({'progressMarkets':mi,'total':len(ordered),'rows':len(rows)}),flush=True)
        d=pd.DataFrame(rows).sort_values(['market_end_ms','checkpoint_ms','market_id']).reset_index(drop=True);d.to_csv(CSV,index=False)
        rep={'reportVersion':'TARGET_GENERAL_STATE_INCREMENT_V2','researchOnly':True,'oldMaxEndMs':OLD_MAX,'candidateMarkets':len(candidate),'eligibleMarkets':len(markets),'builtMarkets':int(d.market_id.nunique()) if len(d) else 0,'rows':len(d),'minEndMs':int(d.market_end_ms.min()) if len(d) else None,'maxEndMs':int(d.market_end_ms.max()) if len(d) else None,'drop':dict(drop),'file':str(CSV),'guards':['Increment only; old V1 untouched.','No winner/PnL.','2026-08-16 absent by time cutoff.','No runtime changes.']};REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2));return d
    finally:b.close();t.close()
if __name__=='__main__':build()
