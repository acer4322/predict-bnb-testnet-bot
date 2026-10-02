from __future__ import annotations
import json,lzma,os,sys,zipfile
from pathlib import Path
ROOT=Path.cwd()
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_marginal_successor_probe_simulator_v1 as sim
from tools import test_r4_p0b_objective_ledger_runtime_materialization_v1 as led
from tools import test_r4_p0b_objective_context_memory_v1 as ctxm
ST=ROOT/'.lan_worker_v1/staging'; OUT=Path(os.environ['BTC5M_LAN_RESULT_DIR'])
CLOSED=ST/'r4_p0b_stage3_lane_f_31_market_bundle_v1.zip'
MINI=ST/'r4_p0b_stage3_lane_f_worker_minidata_v1.zip'

def prepare_minidata():
    tape_root=ROOT/'data/execution_tape_v1/markets'; tape_root.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(MINI) as z:
        for n in z.namelist():
            if n.startswith('execution_tape_v1/markets/') and n.endswith('.json.xz'):
                (tape_root/Path(n).name).write_bytes(z.read(n))
        db=ST/'public_source_snapshot_archive_v2.db'; db.write_bytes(z.read('public_source_snapshot_archive_v2.db'))
    sim.PUBLIC_SOURCE_DB=db

def main():
    prepare_minidata(); rows=[]
    with zipfile.ZipFile(CLOSED) as z:
        manifest=json.loads(z.read('manifest.json'))
        for i,m in enumerate(manifest,1):
            mid=int(m['marketId']); key=str(m['candidateKey']); pl=str(m['parentLogical'])
            d=json.loads(lzma.decompress(z.read(f'{mid}_r2_hft_closed_loop_v1.json.xz')).decode('utf-8'))
            r=sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True,successor_mode='ONE',successor_target=key)
            journal,_,_=led.materialize(mid,r.get('provenanceJournal') or [])
            t=int(key.split('|',1)[0]); poid=led.oid(mid,pl); idx=None; side=None
            for j,e in enumerate(journal):
                if e.get('event_type')=='OBJECTIVE_OPENED' and e.get('parent_objective_id')==poid and abs(int(e.get('received_at_ms') or 0)-t)<=1:
                    idx=j; side=str(e.get('side') or ''); break
            context=ctxm.ctx_before(journal,idx,poid,side,t) if idx is not None else None
            rows.append({'marketId':mid,'candidateKey':key,'role':m['role'],'contextFound':context is not None,'context':context})
            print(json.dumps({'progress':i,'marketId':mid,'found':context is not None,'role':m['role']}),flush=True)
    out={'version':'R4_P0B_STAGE3_GROUP_CONTEXT_EXTRACT_V1','researchOnly':True,'actionAuthority':False,'rows':rows,'aggregate':{'markets':len(rows),'contextFound':sum(x['contextFound'] for x in rows)},'guard':'Context is computed only from Objective Ledger journal events strictly before candidate successor OBJECTIVE_OPENED. Frozen Lane-F role is teacher label only.'}
    (OUT/'stage3_group_context.json').write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps(out['aggregate']))
if __name__=='__main__': main()
