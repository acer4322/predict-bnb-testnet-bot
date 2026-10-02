from __future__ import annotations
import argparse,json,math
from pathlib import Path
from collections import defaultdict

GROUPS=[
 'MARKET_STATE_DIRECTION','OWN_EXPOSURE_FEEDBACK','PHASE_EXPOSURE',
 'PHASE_UTILIZATION','DEFICIT_TICKET_FEEDBACK','INVENTORY_QUOTE_FEEDBACK']

def mean(xs):return sum(xs)/len(xs) if xs else None

def main():
 ap=argparse.ArgumentParser();ap.add_argument('job_ids',nargs='+');ap.add_argument('--output',required=True);a=ap.parse_args()
 root=Path('data/research/lan_worker_returns');rows=[];sources=[]
 for jid in a.job_ids:
  p=root/jid/'result.json';s=json.loads(p.read_text(encoding='utf-8'));sources.append(dict(job_id=jid,status=s.get('status'),path=str(p)))
  rows.extend(s.get('rows',[]))
 good=[r for r in rows if r.get('status')=='PASS' and isinstance(r.get('core_similarity'),(int,float))]
 bymask={int(r['mask']):r for r in good}
 marginal=[]
 for bit,name in enumerate(GROUPS):
  on=[r['core_similarity'] for r in good if int(r['mask'])&(1<<bit)]
  off=[r['core_similarity'] for r in good if not(int(r['mask'])&(1<<bit))]
  pon=[r['path_mse'] for r in good if int(r['mask'])&(1<<bit)]
  poff=[r['path_mse'] for r in good if not(int(r['mask'])&(1<<bit))]
  marginal.append(dict(bit=bit,name=name,on_mean_similarity=mean(on),off_mean_similarity=mean(off),
                       similarity_lift=(mean(on)-mean(off)) if on and off else None,
                       on_mean_path_mse=mean(pon),off_mean_path_mse=mean(poff),
                       path_mse_improvement=(mean(poff)-mean(pon)) if pon and poff else None))
 pairs=[]
 for i in range(len(GROUPS)):
  for j in range(i+1,len(GROUPS)):
   vals={}
   for ai in (0,1):
    for aj in (0,1):
     xs=[r['core_similarity'] for r in good if bool(int(r['mask'])&(1<<i))==bool(ai) and bool(int(r['mask'])&(1<<j))==bool(aj)]
     vals[(ai,aj)]=mean(xs)
   if all(v is not None for v in vals.values()):
    interaction=vals[(1,1)]-vals[(1,0)]-vals[(0,1)]+vals[(0,0)]
    pairs.append(dict(a=GROUPS[i],b=GROUPS[j],interaction=interaction,
                      mean_00=vals[(0,0)],mean_10=vals[(1,0)],mean_01=vals[(0,1)],mean_11=vals[(1,1)]))
 top=sorted(good,key=lambda r:r['core_similarity'],reverse=True)[:12]
 full=bymask.get(63);null=bymask.get(0)
 out=dict(version='TARGET_CORE_CYCLE_MICROWORLD_V1_AGGREGATE',purpose='MECHANISM_DISCOVERY_NOT_PARAMETER_TUNING',
          sources=sources,total_rows=len(rows),pass_rows=len(good),complete_factorial=len(bymask)==64,
          null_mask=null,full_mask=full,
          top_masks=[{k:r.get(k) for k in ('mask','enabled','core_similarity','path_mse','geometry_similarity','phase_similarity','switch_similarity','activity_similarity','native_receipts','receipt_conditioned_continuations','bidirectional_plans','multi_new_plans')} for r in top],
          marginal_mechanism_effects=sorted(marginal,key=lambda x:(x['similarity_lift'] if x['similarity_lift'] is not None else -999),reverse=True),
          strongest_positive_interactions=sorted(pairs,key=lambda x:x['interaction'],reverse=True)[:10],
          strongest_negative_interactions=sorted(pairs,key=lambda x:x['interaction'])[:10],
          interpretation_boundary=[
           'One consumed development market only: discovery evidence, never promotion.',
           'Target actions are scoring-only; executable policy frames contain no Target labels/actions.',
           'No parameter sweep: coefficients are fixed; only six feedback mechanism groups are ablated.',
           'No PnL/winner optimization. Core similarity emphasizes normalized inventory/floor/upside trajectory, phase shape and side-switch recurrence.',
           'A mechanism is only a candidate regularity until zero-retune transfer to other markets reproduces the lift.'
          ])
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
 print(json.dumps({'ok':True,'rows':len(rows),'pass':len(good),'complete_factorial':len(bymask)==64,
                   'best_mask':top[0]['mask'] if top else None,'best_similarity':top[0]['core_similarity'] if top else None,
                   'top_mechanisms':[(x['name'],x['similarity_lift']) for x in out['marginal_mechanism_effects'][:3]]},ensure_ascii=False))
 if len(bymask)!=64:raise SystemExit(2)
if __name__=='__main__':main()
