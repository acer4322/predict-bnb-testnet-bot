from __future__ import annotations
import argparse,hashlib,json,math,random,time
from pathlib import Path
import psutil
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'

def load_seed_rows():
    rows=[]
    for p in sorted(P.glob('r4_p0b_objective_grouping_context_replication_branches_*_v1.json')):
        try:
            d=json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            continue
        rows.extend(d.get('rows') or [])
    return rows

def stable_hash(x):
    return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def clamp(x,a,b): return max(a,min(b,x))

def episode(seed,idx,rng):
    # Event-driven management micro-world.  Uses only strict-past seed context;
    # known role remains reference/scoring metadata and never changes transition generation.
    c=dict(seed.get('context') or {})
    active=float(c.get('same_side_active_objectives') or 0)
    residual=float(c.get('same_side_total_residual') or 0)
    age=float(c.get('same_side_oldest_objective_age_s') or 0)
    # bounded empirical perturbations around observed management topology
    active2=max(0,int(round(active+rng.choice([-1,0,0,0,1]))))
    residual2=max(0.0,residual+rng.choice([-18.0,0.0,0.0,18.0]))
    age2=max(0.0,age+rng.uniform(-5.0,5.0))
    # Generate a short semi-Markov event chain rather than an L2 timeline.
    n_events=rng.randint(6,18)
    state={'active':active2,'residual':residual2,'age':age2,'reserved':18.0,'confirmed':0.0,'status':'UNKNOWN'}
    checksum=0.0
    for step in range(n_events):
        u=rng.random()
        if u<0.22 and state['reserved']>0:
            fill=min(state['reserved'],rng.choice([3.0,6.0,9.0,18.0]))
            state['reserved']-=fill; state['confirmed']+=fill; state['residual']=max(0.0,state['residual']-fill)
        elif u<0.36:
            # new objective candidate appears; preserve UNKNOWN relation in V0
            state['active']+=1
        elif u<0.50 and state['active']>0:
            state['active']-=1
        elif u<0.68:
            state['age']+=rng.uniform(0.2,1.5)
        else:
            state['residual']=max(0.0,state['residual']+rng.choice([-3.0,0.0,3.0]))
        checksum += state['active']*0.7 + state['residual']*0.03 + state['age']*0.001
    out={
      'seedMarketId':seed.get('marketId'),
      'seedCandidateIndex':seed.get('candidateIndex'),
      'episodeIndex':idx,
      'final':state,
      'events':n_events,
      'checksum':round(checksum,8),
      'referenceRole':seed.get('role')
    }
    return out

def resource_state():
    vm=psutil.virtual_memory()
    return {'cpu':psutil.cpu_percent(interval=0.05),'ram':vm.percent,'rssMB':psutil.Process().memory_info().rss/1024**2}

def run(n,batch=100,ram_abort=86,cpu_abort=75):
    seeds=load_seed_rows()
    if not seeds: raise RuntimeError('no frozen grouping seed rows found')
    start_res=resource_state(); start=time.perf_counter(); hashes=[]; role_counts={}; peak_rss=start_res['rssMB']; peak_ram=start_res['ram']; peak_cpu=start_res['cpu']; aborted=False; reason=None
    base_seed=20260828
    for b0 in range(0,n,batch):
        rs=resource_state(); peak_rss=max(peak_rss,rs['rssMB']); peak_ram=max(peak_ram,rs['ram']); peak_cpu=max(peak_cpu,rs['cpu'])
        if rs['ram']>=ram_abort: aborted=True;reason=f'RAM_GUARD_{rs["ram"]:.1f}';break
        if rs['cpu']>=cpu_abort: aborted=True;reason=f'CPU_GUARD_{rs["cpu"]:.1f}';break
        if rs['cpu']>=60: time.sleep(0.05)
        elif rs['cpu']>=45: time.sleep(0.02)
        b1=min(n,b0+batch)
        for i in range(b0,b1):
            seed=seeds[i%len(seeds)]
            rng=random.Random(base_seed+i)
            out=episode(seed,i,rng)
            hashes.append(stable_hash(out))
            role=str(out.get('referenceRole')); role_counts[role]=role_counts.get(role,0)+1
        # discard episode objects each batch; keep hashes only for deterministic audit
    elapsed=time.perf_counter()-start
    end_res=resource_state(); peak_rss=max(peak_rss,end_res['rssMB']); peak_ram=max(peak_ram,end_res['ram']); peak_cpu=max(peak_cpu,end_res['cpu'])
    completed=len(hashes)
    summary={
      'requestedEpisodes':n,'completedEpisodes':completed,'aborted':aborted,'abortReason':reason,
      'seedRows':len(seeds),'elapsedSec':elapsed,'episodesPerSec':completed/elapsed if elapsed else None,
      'startResource':start_res,'endResource':end_res,'peakRSSMB':peak_rss,'peakRamPercent':peak_ram,'peakCpuPercent':peak_cpu,
      'rssGrowthMB':end_res['rssMB']-start_res['rssMB'],
      'streamHash':hashlib.sha256(''.join(hashes).encode()).hexdigest(),
      'referenceRoleCounts':role_counts,
    }
    return summary

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--episodes',type=int,default=1000);a=ap.parse_args()
    s=run(a.episodes)
    out=P/f'r4_management_simulator_v0_feasibility_{a.episodes}_v1.json'
    out.write_text(json.dumps({'version':'R4_MANAGEMENT_SIMULATOR_V0_FEASIBILITY_V1','researchOnly':True,'summary':s},indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'artifact':str(out.relative_to(ROOT)),'summary':s},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
