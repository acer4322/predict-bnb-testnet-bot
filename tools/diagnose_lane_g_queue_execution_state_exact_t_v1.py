from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.run_lane_g_r264_targeted_repair_reanchor_exact_t_forks import ExactTRepairReanchorFork
from tools import run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand as r264
v2=r264.v2

def clean(v,depth=0):
    if depth>3:return str(type(v).__name__)
    if v is None or isinstance(v,(str,bool,int)):return v
    if isinstance(v,float):return v if math.isfinite(v) else None
    if isinstance(v,dict):
        out={}
        for k,x in list(v.items())[:100]:
            try:out[str(k)]=clean(x,depth+1)
            except Exception:pass
        return out
    if isinstance(v,(list,tuple,set)):
        return [clean(x,depth+1) for x in list(v)[:50]]
    return {'type':type(v).__name__,'repr':repr(v)[:500]}

class D(ExactTRepairReanchorFork):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw);self.diag=None
    def _request_cancel(self,t,sid,reason):
        key=self.slot_key.get(int(sid));role=self.key_role.get(key) if key is not None else None
        if self.diag is None and int(t)==self.target_t and str(key)==self.target_key and role=='SATELLITE_REPAIR' and reason=='SATELLITE_FRONTIER_REANCHOR':
            o=self.orders.get(key)
            try:s=self.snap(o)
            except Exception as e:s={'error':repr(e)}
            try:q=v2.base.quotes(self.book)
            except Exception as e:q={'error':repr(e)}
            attrs={}
            try:
                cur=self.bt.orders(0).get(o['n'])
                cdir=[n for n in dir(cur) if not n.startswith('_')] if cur is not None else []
                cvals={}
                for n in cdir:
                    if any(z in n.lower() for z in ('queue','price','qty','status','time','tick','order','exec','leaves','cumul')):
                        try:
                            v=getattr(cur,n)
                            if isinstance(v,(str,bool,int,float)) or v is None:cvals[n]=v
                        except Exception:pass
            except Exception as e:
                cdir=[];cvals={'error':repr(e)}
            for n,v in self.__dict__.items():
                ln=n.lower()
                if any(z in ln for z in ('queue','book','order','exec','fill','tape','depth','slot')):
                    if n in ('orders','book','slot_history','splitEvents'):continue
                    attrs[n]={'type':type(v).__name__,'repr':repr(v)[:300]}
            self.diag={'t':int(t),'sid':int(sid),'key':str(key),'role':str(role),'order':clean(o),'snap':clean(s),'quotes':clean(q),'bookType':type(self.book).__name__,'book':clean(self.book),'nativeOrderDir':cdir,'nativeOrderValues':cvals,'attrs':attrs}
        return super()._request_cancel(t,sid,reason)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--targets',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    tg=json.loads(Path(a.targets).read_text(encoding='utf-8'))['targets'];by={}
    for x in tg:by.setdefault(int(x['marketId']),[]).append(x)
    tmp=Path(tempfile.mkdtemp(prefix='qdiag_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in by:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[]
        for m,xs in sorted(by.items()):
            for x in xs:
                sim=D(tmp/f'{m}.json.xz',x['key'],x['t'],1,4)
                try:r=sim.run_target(co[m]['winner'])
                finally:sim.close()
                rows.append({'marketId':m,'key':x['key'],'t':int(x['t']),'captured':sim.diag is not None,'diag':sim.diag,'correct':bool(r.get('r264CorrectnessPass'))})
                print(json.dumps({'marketId':m,'key':x['key'],'captured':sim.diag is not None,'correct':bool(r.get('r264CorrectnessPass'))},ensure_ascii=False),flush=True)
        out={'version':'LANE_G_QUEUE_EXECUTION_STATE_EXACT_T_DIAGNOSTIC_V1','rows':rows,'allCaptured':all(x['captured'] for x in rows),'allCorrect':all(x['correct'] for x in rows)}
        Path(a.output).write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
        print(json.dumps({'ok':True,'allCaptured':out['allCaptured'],'allCorrect':out['allCorrect']},ensure_ascii=False))
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
