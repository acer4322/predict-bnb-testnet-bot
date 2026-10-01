"""Lightweight component + disabled full baseline-plan replay; never runs native HFT."""
import importlib.util,json,os,sys
from pathlib import Path
from types import SimpleNamespace as NS
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P));sys.path.insert(0,str(P/'overlay'))
from analyze import read
from economics import with_plan
def load(enabled):
 old=os.environ.get('V12G_DEEP_LAYER');os.environ['V12G_DEEP_LAYER']='ON' if enabled else 'OFF'
 try:
  spec=importlib.util.spec_from_file_location('deep_test_'+str(enabled),P/'deep_layer.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
 finally:
  if old is None:os.environ.pop('V12G_DEEP_LAYER',None)
  else:os.environ['V12G_DEEP_LAYER']=old
def snapshot(f,l):
 owners=[dict(key=k,side=l.grants[c.parent_id].side,qty=c.qty-c.filled,limit=c.limit,state=c.state) for k,c in l.carriers.items() if c.state!='TERMINAL']
 return dict(owners=owners,pending_qty={s:sum(o['qty'] for o in owners if o['side']==s) for s in ('UP','DOWN')},pending_cash={s:sum(o['qty']*o['limit'] for o in owners if o['side']==s) for s in ('UP','DOWN')})
def frame(t=1000):
 l=NS(carriers={},grants={1:NS(side='UP'),2:NS(side='DOWN')})
 l.account=lambda pid:dict(reserved_qty=sum(c.qty-c.filled for c in l.carriers.values() if c.parent_id==pid and c.state!='TERMINAL'))
 return dict(t=t,start=0,end=300000,index=0,ledger=l,cancellable={},book=dict(bids={.50:100},asks={.52:100}),world_profile=dict(tick=.01,quantity_step=.01,asset='BTC',max_live_owners=100),own_view=dict(n=0))
roles=NS(pid=lambda s:1 if s=='UP' else 2)
def call(m,f,ops=None,freeze=False,gov=15,risk=15,cross=False):
 return m.extend(f,[] if ops is None else ops,roles,lambda:freeze,snapshot,with_plan,lambda *a:gov,lambda *a:risk,lambda *a,**kw:None,lambda *a:cross)
def run(inert=True):
 tests=[]
 m=load(True);f=frame();ops=call(m,f)
 assert [(o['side'],o['price'],o['qty']) for o in ops]==[('UP',.48,15),('DOWN',.46,15)]
 tests.append('two_physical_sides_exact_price_fixed15_no_DECIDE_gate')
 for o in ops:
  f['ledger'].carriers[o['key']]=NS(parent_id=o['parent_id'],state='LIVE',qty=15.,filled=0.,limit=o['price']);f['cancellable'][o['key']]=True
 assert call(m,f)==[]
 f['t']=2999;assert call(m,f)==[]
 f['t']=3000; canc=call(m,f);assert len(canc)==2 and all(o['kind']=='CANCEL' for o in canc)
 assert m.pending(f['ledger'],'UP')==15
 for c in f['ledger'].carriers.values():c.state='CANCEL_PENDING'
 f['t']=3100;assert call(m,f)==[] and m.pending(f['ledger'],'UP')==15
 for c in f['ledger'].carriers.values():c.state='TERMINAL'
 f['t']=3300;f['own_view']['n']=2;assert len(call(m,f))==2
 tests.append('TTL_exact_2000_and_cancel_pending_no_same_plan_replacement')
 m=load(True);f=frame();ops=call(m,f);o=ops[0]
 f['ledger'].carriers[o['key']]=NS(parent_id=1,state='LIVE',qty=15.,filled=1.,limit=.48);f['cancellable'][o['key']]=True
 f['t']=5000;f['own_view']['n']=2;out=call(m,f)
 assert not any(o['kind']=='CANCEL' for o in out) and not any(o.get('side')=='UP' for o in out)
 assert m.pending(f['ledger'],'UP')==14
 tests.append('partial_fill_kept_and_remaining_reserved')
 for reason,kw in [('freeze',dict(freeze=True)),('governor',dict(gov=0)),('risk_floor',dict(risk=0)),('self_cross',dict(cross=True))]:
  m=load(True);assert call(m,frame(),**kw)==[] and m.STATS[reason]==2;tests.append(reason)
 m=load(True);f=frame(290000);cancel=dict(kind='CANCEL',key='UP_old',reason='USER_STOP290_CANCEL_ALL');assert call(m,f,[cancel])==[cancel];assert m.STATS['STOP290']==2;tests.append('STOP290_preserves_cancel_all')
 m=load(True);f=frame();f['ledger'].carriers['UP_8']=NS(parent_id=1,state='CANCEL_PENDING',qty=15.,filled=0.,limit=.48)
 out=call(m,f,[dict(kind='CANCEL',key='UP_8')]);assert not any(o.get('side')=='UP' for o in out);assert m.STATS['maintenance_conflict']==1;tests.append('same_price_cancel_pending_conflict')
 m=load(True);f=frame();f['ledger'].carriers['DOWN_8']=NS(parent_id=2,state='CANCEL_PENDING',qty=15.,filled=0.,limit=.53)
 out=call(m,f);assert not any(o.get('side')=='UP' for o in out) and m.STATS['self_cross']>=1;tests.append('native_rounding_self_cross_cancel_pending')
 source=(P.parent/'btc5m_cg1at_fresh100a_20260930/base/frozen_runner.py').read_text(encoding='utf-8')
 patched=m.instrument(source);compile(patched,'deep_manager','exec');assert "if __import__('deep_layer').owns(k):continue" in patched and "-__import__('deep_layer').pending(ledger,s)" in patched;tests.append('maintenance_skip_owned_exclusion')
 m=load(True);m.install(NS(),roles,lambda:False,None,None,None);assert m.SERVICE is not None;assert m.owns('arbitrary') is False;m.KEYSET.add('arbitrary');assert m.owns('arbitrary');tests.append('post_install_mutable_globals_callback')
 result=dict(status='PASS',native_executed=0,components=tests)
 if inert:
  off=load(False);assert off.instrument(source)==source;assert off.on_plan(None,[])==[]
  plan=read(P/'PROTOCOL.json');root=P.parents[2]/'data/research/lan_worker_returns'
  rows=[]
  for mid in plan['markets']:
   base=root/plan['baseline'][str(mid)]['remote_relative'];tr=read(base/'clock_trace.json.gz');n=0
   for p in tr['plans']:
    old=json.dumps(p['operations'],sort_keys=True,separators=(',',':'))
    out=off.extend(None,p['operations'],None,None,None,None,None,None,None,None)
    assert out is p['operations'] and json.dumps(out,sort_keys=True,separators=(',',':'))==old;n+=len(out)
   rows.append(dict(market=mid,status='PASS',plans=len(tr['plans']),operations=n,receipts=len(tr['demand_final']['full_raw_receipts'])))
  result['inert']=dict(status='PASS',scope='disabled source identity + every existing BASE plan operation full-field replay; receipt-producing source unchanged; no new native replay',rows=rows)
 return result
if __name__=='__main__':
 result=run();(P/'LOCAL_TESTS.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
