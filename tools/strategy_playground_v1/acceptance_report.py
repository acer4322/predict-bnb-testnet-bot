"""Read-only acceptance aggregation; do not hide the precision test failure."""
from pathlib import Path
from bisect import bisect_right
from datetime import datetime,timezone
import json,math,hashlib
P=Path(__file__).resolve().parent;ROOT=P.parents[1]
OUT=ROOT/'data/research/strategy_playground_v1_20260921'

def read(p):return json.loads(p.read_text(encoding='utf-8'))
def main():
    rows=[];max_error=0.;checks=0;mismatches=0;unique_views=set()
    for p in sorted((OUT/'jobs').glob('*/RESULT.json')):
        r=read(p)
        rows.append({'job_id':p.parent.name,'request':r['request'],'native_new':r['native_new'],'audit':r['audit'],
            'final_baseline':r['final_baseline'],'final_candidate':r['final_candidate']})
        for name,h in r['files'].items():
            f=p.parent/name
            assert hashlib.sha256(f.read_bytes()).hexdigest()==h
            if h in unique_views:continue
            unique_views.add(h);b=read(f);fills=b['fills'];times=[x['ms'] for x in b['frames']]
            fill_times=[x['ms'] for x in fills];cost_prefix=[0.];inv_prefix=[{'UP':0.,'DOWN':0.}]
            for x in fills:
                cost_prefix.append(cost_prefix[-1]+x['qty']*x['price']+x['fee'])
                z=dict(inv_prefix[-1]);z[x['side']]+=x['qty'];inv_prefix.append(z)
                ns=b['start_ms']*1000000+round(x['ms']*1000000)
                err=abs((ns/1000000-b['start_ms'])-x['ms']);max_error=max(max_error,err)
            positions={0,b['duration_ms']}
            for v in times+fill_times:
                positions.update((math.floor(v),math.ceil(v)))
            for ms in sorted(t for t in positions if 0<=t<=b['duration_ms']):
                fi=bisect_right(times,ms)-1;ri=bisect_right(fill_times,ms)
                state=b['frames'][fi] if fi>=0 else {'cost':0.,'inv':{'UP':0.,'DOWN':0.}}
                checks+=1
                if abs(state['cost']-cost_prefix[ri])>1e-6 or any(abs(state['inv'][s]-inv_prefix[ri][s])>1e-6 for s in ('UP','DOWN')):mismatches+=1
    report={'status':'CORE_NATIVE_AND_UI_VERIFIED_WITH_KNOWN_SUBMICRO_DISPLAY_LIMITATION','created_utc':datetime.now(timezone.utc).isoformat(),
        'successful_native_runs':sum(r['native_new'] for r in rows),'unique_markets':len({r['request']['market'] for r in rows}),
        'runs':rows,'integer_millisecond_view_checks':checks,'integer_millisecond_mismatches':mismatches,
        'maximum_measured_export_timestamp_error_ms':max_error,'maximum_measured_export_timestamp_error_ns':max_error*1000000,
        'local_test_report':read(OUT/'LOCAL_VALIDATION.json'),'latest_browser_report':read(OUT/'UI_VALIDATION.json'),
        'known_issue':'Absolute epoch float conversion loses sub-microsecond display timestamp precision. Native actor/tape/receipts/accounting are unchanged. A strict sub-microsecond exported-frame test remains failed; do not claim all tests passed.',
        'repair_attempt':'Direct timeline precision edit was blocked by connector safety checks; not applied. No workaround changed native results.',
        'failed_startup_job_preserved':'pgv1-c-362aff11721841791c24','worker_workdir_collision_fixed':True,'training_updates':0,'live_changes':0,'promoted':False}
    (OUT/'ACCEPTANCE.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('runs','latest_browser_report')},ensure_ascii=False))
if __name__=='__main__':main()
