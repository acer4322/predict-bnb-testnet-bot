"""Scale new raw inventory targets before growth/repair, never a ledger/goal."""
import gzip,json,math,os
MODE=os.environ.get('V12G_DEMAND_SCALE_MODE','OFF')
FACTOR=float(os.environ.get('V12G_DEMAND_SCALE','.2'))
assert MODE in ('OFF','OPEN','POST','BOTH') and 0<FACTOR<=1
ROWS=[]

def targets(desired,side,mode,factor):
    assert set(desired)=={'UP','DOWN'} and side in (None,'UP','DOWN')
    active=mode=='BOTH' or (mode=='OPEN' and side is None) or (mode=='POST' and side is not None)
    return {s:float(v)*factor for s,v in desired.items()} if active else desired

def apply(frame,desired,side):
    out=targets(desired,side,MODE,FACTOR)
    ROWS.append(dict(t=int(frame['t']),index=int(frame['index']),side=side,raw=dict(desired),scaled=dict(out),
        inventory=dict(frame['own_view']['inv'])))
    return out

def instrument(source):
    marker="desired={s:gross*share[s] for s in ('UP','DOWN')}"
    assert source.count(marker)==1
    pos=source.index(marker)
    assert pos<source.index('desired=self.addition_growth.apply')<source.index('desired=self.demand.apply')
    return source.replace(marker,marker+";desired=__import__('demand_scale').apply(f,desired,roles.side)",1)

def finish(out):
    payload=dict(mode=MODE,factor=FACTOR,rows=ROWS)
    with gzip.GzipFile(filename=str(out/'demand_scale_trace.json.gz'),mode='wb',mtime=0) as f:
        f.write(json.dumps(payload,sort_keys=True,separators=(',',':')).encode())
    return dict(mode=MODE,factor=FACTOR,rows=len(ROWS),changed=sum(x['raw']!=x['scaled'] for x in ROWS),
        scope='Raw gross-share demand upstream of growth and monetary finite repair; no ledger mutation',live_eligible=False)
