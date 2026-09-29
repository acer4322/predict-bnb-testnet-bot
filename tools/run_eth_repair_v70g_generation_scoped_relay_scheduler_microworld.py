from __future__ import annotations
import json,argparse
from dataclasses import dataclass,field
from pathlib import Path
EPS=1e-9

@dataclass
class Gen:
 gid:int; expand_quota:float=0.0; expand_filled:float=0.0; debt:float=0.0; paid:float=0.0
 authority:bool=False; carrier:str|None=None; transfer_used:bool=False; terminal:bool=False
 seen:set=field(default_factory=set)

class Scheduler:
 def __init__(self):
  self.gens={};self.next_gid=1;self.current=None;self.viol=[];self.events=[]
 def new_generation(self):
  if self.current is not None:
   g=self.gens[self.current]
   if g.debt-g.paid>EPS and not g.terminal: return None
  gid=self.next_gid;self.next_gid+=1;self.gens[gid]=Gen(gid);self.current=gid;return gid
 def authorize(self,gid,quota,seconds_left,existing_carrier=None,event_id=None):
  g=self.gens[gid];eid=event_id or f'a{gid}'
  if eid in g.seen:return False
  g.seen.add(eid)
  if seconds_left<=180:return False
  if g.authority:return False
  g.authority=True;g.expand_quota=float(quota);g.carrier=existing_carrier or f'RESP_{gid}'
  self.events.append(('AUTH',gid,g.carrier,quota));return True
 def repair_submit(self,gid,event_id):
  g=self.gens[gid]
  if event_id in g.seen:return False
  g.seen.add(event_id);self.events.append(('REPAIR_SUBMIT',gid,event_id));return True
 def terminal_zero_fill(self,gid,event_id):
  g=self.gens[gid]
  if event_id in g.seen:return False
  g.seen.add(event_id)
  if not g.authority or g.expand_filled>EPS or g.transfer_used:return False
  g.transfer_used=True;g.carrier=f'ACTIVE_{gid}';self.events.append(('TRANSFER',gid,g.carrier));return True
 def expand_fill(self,gid,qty,event_id):
  g=self.gens[gid]
  if event_id in g.seen:return 0.0
  g.seen.add(event_id);q=min(float(qty),max(0.0,g.expand_quota-g.expand_filled));g.expand_filled+=q;g.debt+=q
  self.events.append(('EXPAND_FILL',gid,q));return q
 def repair_payment(self,gid,qty,event_id,post_birth=True):
  g=self.gens[gid]
  if event_id in g.seen:return 0.0
  g.seen.add(event_id)
  if not post_birth:return 0.0
  q=min(float(qty),max(0.0,g.debt-g.paid));g.paid+=q;self.events.append(('PAY',gid,q));return q
 def check(self):
  for g in self.gens.values():
   if g.expand_filled-g.expand_quota>EPS:self.viol.append('overfill')
   if g.paid-g.debt>EPS:self.viol.append('overpay')
  return not self.viol

def case(name,fn):
 try:ok,detail=fn();return {'name':name,'pass':bool(ok),'detail':detail}
 except Exception as e:return {'name':name,'pass':False,'detail':repr(e)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--output',required=True);a=ap.parse_args();rows=[]
 def c1():
  s=Scheduler();g=s.new_generation();x=s.authorize(g,2,250,event_id='edge');y=s.authorize(g,2,249,event_id='edge2');return x and not y,(x,y)
 rows.append(case('one authorization edge per generation',c1))
 def c2():
  s=Scheduler();g=s.new_generation();s.authorize(g,2,250,event_id='edge');[s.repair_submit(g,f'r{i}') for i in range(4)];n=sum(1 for e in s.events if e[0]=='AUTH');return n==1,n
 rows.append(case('repair reprices do not reauthorize',c2))
 def c3():
  s=Scheduler();g=s.new_generation();x=s.authorize(g,2,250,existing_carrier='V44_PASSIVE',event_id='edge');return x and s.gens[g].carrier=='V44_PASSIVE',s.gens[g].carrier
 rows.append(case('existing passive occupancy absorbs responsibility',c3))
 def c4():
  s=Scheduler();g=s.new_generation();s.authorize(g,2,250,existing_carrier='V64_ACTIVE',event_id='edge');x=s.authorize(g,2,245,event_id='dup');return not x,s.gens[g].carrier
 rows.append(case('existing active occupancy blocks duplicate',c4))
 def c5():
  s=Scheduler();g=s.new_generation();s.authorize(g,2,250,existing_carrier='V44_PASSIVE');x=s.terminal_zero_fill(g,'z1');y=s.terminal_zero_fill(g,'z2');return x and not y,s.gens[g].carrier
 rows.append(case('zero-fill transfers once',c5))
 def c6():
  s=Scheduler();g=s.new_generation();s.authorize(g,3,250);q=s.expand_fill(g,1.2,'f1');return abs(q-1.2)<EPS and abs(s.gens[g].debt-1.2)<EPS and abs(s.gens[g].expand_quota-s.gens[g].expand_filled-1.8)<EPS,(s.gens[g].debt,s.gens[g].expand_filled)
 rows.append(case('partial fill births exact debt',c6))
 def c7():
  s=Scheduler();g=s.new_generation();s.authorize(g,2,250);s.expand_fill(g,2,'f');pre=s.repair_payment(g,1,'pre',False);post=s.repair_payment(g,1.5,'post',True);return pre==0 and abs(post-1.5)<EPS and abs(s.gens[g].paid-1.5)<EPS,(pre,post)
 rows.append(case('strict post-birth payment only',c7))
 def c8():
  s=Scheduler();g=s.new_generation();s.authorize(g,1,250);s.expand_fill(g,1,'f');blocked=s.new_generation();s.repair_payment(g,1,'p');n=s.new_generation();return blocked is None and n==2,(blocked,n)
 rows.append(case('debt discharge unlocks next generation',c8))
 def c9():
  s=Scheduler();g=s.new_generation();x=s.authorize(g,2,180,event_id='late');r=s.repair_submit(g,'r');return (not x) and r,(x,r)
 rows.append(case('late gate blocks expand not repair',c9))
 def c10():
  s=Scheduler();g=s.new_generation();s.authorize(g,2,250,event_id='a');s.authorize(g,2,250,event_id='a');s.expand_fill(g,1,'f');s.expand_fill(g,1,'f');s.repair_payment(g,1,'p');s.repair_payment(g,1,'p');return s.check() and abs(s.gens[g].expand_filled-1)<EPS and abs(s.gens[g].debt-1)<EPS and abs(s.gens[g].paid-1)<EPS,(s.gens[g].expand_filled,s.gens[g].debt,s.gens[g].paid)
 rows.append(case('duplicate events idempotent',c10))
 passed=sum(r['pass'] for r in rows);out={'version':'ETH_REPAIR_V70G_GENERATION_SCOPED_RELAY_SCHEDULER_MICROWORLD','date':'2026-09-03','researchOnly':True,'cases':rows,'aggregate':{'passed':passed,'total':len(rows)},'functionalPass':passed==10,'decision':'AUTHORIZE_ONE_MARKET_V70G_HFT_INTEGRATION' if passed==10 else 'REJECT_FIX_MICROWORLD_BEFORE_HFT','boundary':['generation-scoped responsibility ownership only','no market/PnL/winner/threshold tuning','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out['aggregate']|{'functionalPass':out['functionalPass'],'decision':out['decision']}))
if __name__=='__main__':main()
