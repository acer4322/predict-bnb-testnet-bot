"""Exact events.npz transport plus declared engine settings, no alpha rules."""
import hashlib,json,os
from pathlib import Path
P=Path(__file__).resolve().parent
def install(base):
    import numpy as np
    mid=int(os.environ['OML_MARKET']);queue=os.environ['OML_QUEUE'];lat=int(os.environ['OML_LATENCY'])
    meta=json.loads((P/'META'/f'{mid}.json').read_text())
    path=P/'fixtures'/str(mid)/'events.npz'
    with np.load(path) as z:ev=z['data'].copy();times=z['local_times_ms'].tolist()
    fx=json.loads((path.parent/'FIXTURE.json').read_text())['conversion_info']
    assert fx['firstReceivedMs']==meta['conversion']['firstReceivedMs']
    fx={**fx,**meta['conversion']}
    assert fx['lastReceivedMs']>=fx['firstReceivedMs']
    base.feed.build_archive_events=lambda market_id,**kw:(ev,times,fx)
    nb=base.ex.new_bt
    def new_bt(events,*,entry_latency_ms,response_latency_ms,queue_model):
        assert np.array_equal(events,ev)
        bt=nb(events,entry_latency_ms=lat,response_latency_ms=lat,queue_model=queue)
        out=Path(os.environ['BTC5M_LAN_RESULT_DIR'])
        (out/'ENGINE_SETTINGS.json').write_text(json.dumps({'queue_model':queue,'entry_latency_ms':lat,'response_latency_ms':lat,'tick':.01,'lot':.01,'maker_fee':0,'taker_fee':0,'exchange':'PartialFill','events_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'strategy_source_changed':False},indent=2))
        return bt
    base.ex.new_bt=new_bt
