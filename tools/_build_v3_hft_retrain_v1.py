from pathlib import Path
src=Path('tools/run_target_blind_promoted_controller_closed_loop_v3.py')
out=Path('tools/run_target_blind_promoted_controller_closed_loop_v3_hft_retrain_v1.py')
s=src.read_text(encoding='utf-8')
s=s.replace("from predict_bot.core import taker_fee\n", "from predict_bot.core import taker_fee\nimport hftbacktest_execution_shift_audit_v0 as hft_ex\nimport hftbacktest_execution_tape_feed_v1 as hft_tape\n")
s=s.replace("FILL_PROXY=os.environ.get('FILL_PROXY','QUEUECLEAR_PASS').upper()\nexecution_guard.require_legacy_optimistic_diagnostic(test_name=Path(__file__).name, fill_proxy=FILL_PROXY)\n", "FILL_PROXY='HFTBACKTEST_EXECUTION_TAPE_V1'\nHFT_ENTRY_LATENCY_MS=int(os.environ.get('HFT_ENTRY_LATENCY_MS','1092') or 1092)\nHFT_RESPONSE_LATENCY_MS=int(os.environ.get('HFT_RESPONSE_LATENCY_MS','273') or 273)\nHFT_QUEUE_MODEL=os.environ.get('HFT_QUEUE_MODEL','risk').lower()\nHFT_TRADE_OFFSET=os.environ.get('HFT_TRADE_OFFSET','mid').lower()\n_HFT=None\n")
start=s.index('def add_order(')
end=s.index('def pnl(', start)
replacement=r'''def _hft_open(market_id):
 events,_,meta=hft_tape.build_archive_events(int(market_id),trade_offset=HFT_TRADE_OFFSET)
 bt=hft_ex.new_bt(events,entry_latency_ms=HFT_ENTRY_LATENCY_MS,response_latency_ms=HFT_RESPONSE_LATENCY_MS,queue_model=HFT_QUEUE_MODEL)
 hft_ex.initialize_bt(bt)
 return {'bt':bt,'events':events,'meta':meta,'next_num':1,'key_to_num':{},'prev_cum':{},'pending_takers':{}}

def _hft_close():
 global _HFT
 if _HFT is not None:
  try:_HFT['bt'].close()
  except Exception:pass
 _HFT=None

def _hft_cancel(key):
 if _HFT is None:return
 num=_HFT['key_to_num'].get(key)
 if num is None:return
 cur=_HFT['bt'].orders(0).get(int(num))
 if cur is not None and bool(cur.cancellable):
  try:_HFT['bt'].cancel(0,int(num),False)
  except Exception:pass

def add_order(sim,snapshot,book_state,side,ns,now,places,meta,market_id,p,reason,allow_stack=True,bypass_guard=False):
 global _HFT
 same=[o for o in sim.orders.values() if o.side==side]; opp='DOWN' if side=='UP' else 'UP'; opp_orders=[o for o in sim.orders.values() if o.side==opp]
 occupied=bool(same)
 if same:
  if not allow_stack or len(same)>=2:return False
  latest=max(same,key=lambda o:int(o.placed_at_ms)); age=now-int(latest.placed_at_ms); sec=mod.snapshot_value(snapshot,'seconds_left','secondsLeft'); vol=str(snapshot.get('volatilityAlert') or 'NORMAL').upper()
  context=age<1500 or (sec is not None and 15<float(sec)<=60) or vol in ('WATCH','HIGH')
  if not context and not bypass_guard:return False
  if not bypass_guard:
   _,_,gross,net,pc=maker_totals(sim); dom='UP' if net>EPS else 'DOWN' if net<-EPS else None
   if dom==side and pc<.80:
    bias=str(snapshot.get('directionBias') or snapshot.get('direction_bias') or 'NEUTRAL').upper()
    if not (bias in ('UP','DOWN') and bias!=side):return False
 qr=quote(snapshot,side,opp_orders)
 if qr is None:return False
 tick,price=qr
 while (side,tick) in sim.orders:
  tick-=1
  if tick<int(round(mod.maker_ebm.MIN_PRICE/mod.maker_ebm.GRID)):return False
  price=round(tick*mod.maker_ebm.GRID,2)
 if opp_orders:
  mx=max(float(o.price) for o in opp_orders)
  while price+mx>mod.maker_ebm.MAX_PAIR_PRICE_SUM+1e-9:
   tick-=1
   if tick<int(round(mod.maker_ebm.MIN_PRICE/mod.maker_ebm.GRID)):return False
   price=round(tick*mod.maker_ebm.GRID,2)
 key=(side,tick)
 if now-int(sim.last_closed.get(key,0))<mod.maker_ebm.REFILL_COOLDOWN_MS:return False
 o=mod.SimOrder(key=key,side=side,price_tick=tick,price=price,shares=SHARES,placed_at_ms=now,placed_snapshot_ns=ns)
 if _HFT is None:raise RuntimeError('HFT context not initialized')
 num=int(_HFT['next_num']);_HFT['next_num']+=1;rc=int(hft_ex.submit_native(_HFT['bt'],num,side,price,SHARES))
 if rc!=0:return False
 sim.orders[key]=o;sim.placements+=1;_HFT['key_to_num'][key]=num;_HFT['prev_cum'][num]=0.0
 m={'market_id':market_id,'at_ms':now,'side':side,'price':price,'shares':SHARES,'pMaker':p,'reason':reason,'occupied_before':int(occupied),'pre_maker_net':maker_totals(sim)[3],'pre_maker_pc':maker_totals(sim)[4],'hft_order_num':num,'hft_submit_rc':rc};meta[key]=m;places.append(dict(m));return True

def update_depletion_meta(order_meta,changes):
 return

def harvest_hft(sim,order_meta,now):
 global _HFT
 rec=[]
 if _HFT is None:return rec
 hft_ex.advance_to(_HFT['bt'],int(now))
 for key,o in list(sim.orders.items()):
  num=_HFT['key_to_num'].get(key)
  if num is None:continue
  snap=hft_ex.order_snapshot(_HFT['bt'],int(num));cum=float(snap.get('cumExecQty') or 0.0);old=float(_HFT['prev_cum'].get(num,0.0))
  if cum>old+EPS:
   delta=cum-old; native=snap.get('execPrice');px=float(o.price)
   if native is not None and math.isfinite(float(native)):px=float(native) if o.side=='UP' else 1.0-float(native)
   fill_ms=int((snap.get('exchangeTs') or int(now)*1_000_000)//1_000_000)
   _,_,_,pre_net,pre_pc=maker_totals(sim)
   if o.side=='UP':sim.up_shares+=delta;sim.up_cost+=delta*px
   else:sim.down_shares+=delta;sim.down_cost+=delta*px
   sim.maker_fills.append({'side':o.side,'price':px,'shares':delta,'at_ms':fill_ms})
   _HFT['prev_cum'][num]=cum
   rec.append({'key':key,'meta':order_meta.get(key),'pre_net':pre_net,'pre_pc':pre_pc,'post_net':maker_totals(sim)[3],'proxy':'HFT_ACTUAL_FILL','delta':delta,'cum':cum,'status':snap.get('status')})
  if str(snap.get('status') or 'NONE') in {'FILLED','REJECTED','EXPIRED','CANCELED'}:
   sim.orders.pop(key,None);sim.last_closed[key]=int(now);_HFT['key_to_num'].pop(key,None)
 return rec

def submit_taker_hft(side,ask,now):
 global _HFT
 if _HFT is None:return None
 num=int(_HFT['next_num']);_HFT['next_num']+=1;native_side,native_px=hft_ex.native_order(side,min(.99,float(ask)+.02))
 if native_side=='BUY':rc=int(_HFT['bt'].submit_buy_order(0,num,native_px,SHARES,hft_ex.hbt.GTC,hft_ex.LIMIT,False))
 else:rc=int(_HFT['bt'].submit_sell_order(0,num,native_px,SHARES,hft_ex.hbt.GTC,hft_ex.LIMIT,False))
 if rc==0:_HFT['pending_takers'][num]={'side':side,'ask':float(ask),'prev':0.0,'submitted':int(now)}
 return num if rc==0 else None

def harvest_takers(inv,allfills,now):
 global _HFT
 fees=0.0;count=0;last_fill=None
 if _HFT is None:return fees,count,last_fill
 for num,m in list(_HFT['pending_takers'].items()):
  snap=hft_ex.order_snapshot(_HFT['bt'],int(num));cum=float(snap.get('cumExecQty') or 0.0);old=float(m.get('prev') or 0.0)
  if cum>old+EPS:
   q=cum-old; native=snap.get('execPrice');px=float(m['ask'])
   if native is not None and math.isfinite(float(native)):px=float(native) if m['side']=='UP' else 1.0-float(native)
   fm=int((snap.get('exchangeTs') or int(now)*1_000_000)//1_000_000);e={'event_ms':fm,'at_ms':fm,'role':'TAKER','side':m['side'],'price':px,'shares':q};inv.apply(e);allfills.append(e);fees+=taker_fee(q,px,FEE_BPS);count+=1;last_fill=fm;m['prev']=cum
  if str(snap.get('status') or 'NONE') in {'FILLED','REJECTED','EXPIRED','CANCELED'}:_HFT['pending_takers'].pop(num,None)
 return fees,count,last_fill

'''
s=s[:start]+replacement+s[end:]
# main: initialize/close HFT per market
s=s.replace("  for mi,(we,om) in enumerate(chosen,1):\n   tm=emap[we];", "  for mi,(we,om) in enumerate(chosen,1):\n   global _HFT\n   _HFT=_hft_open(om)\n   tm=emap[we];")
s=s.replace("    proxy_records=fill_proxy(sim,state,order_meta,now,FILL_PROXY)\n    if FILL_PROXY=='ASK_TOUCH':sim.fill_existing(snap,ns,now)\n", "    proxy_records=harvest_hft(sim,order_meta,now)\n")
s=s.replace("    newmf=sim.maker_fills[before_fill_n:]\n    for f in newmf:\n", "    newmf=sim.maker_fills[before_fill_n:]\n    for f in newmf:\n")
# harvest taker fills immediately after maker fills are copied to inventory
needle="    for f in newmf:\n     e={'event_ms':int(f['at_ms']),'role':'MAKER','side':str(f['side']),'price':float(f['price']),'shares':float(f['shares'])}; inv.apply(e); allfills.append({**e,'at_ms':e['event_ms']})\n"
repl=needle+"    tf,tc,tlast=harvest_takers(inv,allfills,now); taker_fee_total+=tf; taker_count+=tc; last_taker_ms=max(last_taker_ms,tlast if tlast is not None else last_taker_ms)\n"
s=s.replace(needle,repl)
# cancel must reach venue
s=s.replace("       if str(o.side)==es:\n        sim.orders.pop(key,None); sim.last_closed[key]=now; sim.cancels+=1; order_meta.pop(key,None)", "       if str(o.side)==es:\n        _hft_cancel(key); sim.orders.pop(key,None); sim.last_closed[key]=now; sim.cancels+=1; order_meta.pop(key,None)")
# replace both direct taker fill sequences with submit only
old="gd={'side':chosen_side,'price':float(ask),'shares':SHARES,'filled_at_ms':now}; sim.apply_seed(gd); inv.apply({'event_ms':now,'role':'TAKER','side':chosen_side,'price':float(ask),'shares':SHARES}); allfills.append({'event_ms':now,'at_ms':now,'role':'TAKER','side':chosen_side,'price':float(ask),'shares':SHARES}); fee=taker_fee(SHARES,float(ask),FEE_BPS); taker_fee_total+=fee; taker_count+=1; last_taker_ms=now"
new="submit_taker_hft(chosen_side,float(ask),now); last_taker_ms=now"
s=s.replace(old,new)
old2="gd={'side':chosen_side,'price':float(ask),'shares':SHARES,'filled_at_ms':now}; sim.apply_seed(gd); inv.apply({'event_ms':now,'role':'TAKER','side':chosen_side,'price':float(ask),'shares':SHARES}); allfills.append({'event_ms':now,'at_ms':now,'role':'TAKER','side':chosen_side,'price':float(ask),'shares':SHARES}); taker_fee_total+=taker_fee(SHARES,float(ask),FEE_BPS); taker_count+=1; last_taker_ms=now"
s=s.replace(old2,new)
# before settlement flush tape and takers, then close
settle="   winner=wins[we]; rawp,fees=pnl(allfills,winner,False);"
flush="   terminal=int(_HFT['meta']['lastReceivedMs']); harvest_hft(sim,order_meta,terminal); tf,tc,tlast=harvest_takers(inv,allfills,terminal); taker_fee_total+=tf; taker_count+=tc\n   _hft_close()\n   winner=wins[we]; rawp,fees=pnl(allfills,winner,False);"
s=s.replace(settle,flush)
# report semantics
s=s.replace("'execution':f'Maker public-book fill proxy={FILL_PROXY}; Taker current public ask'", "'execution':'HftBacktest Execution Tape V1 actual fills; entry latency 1092ms; response latency 273ms; risk queue; mid trade offset; Taker marketable-limit venue confirmation'",1)
s=s.replace("'Fixed 18-share size and public-book Maker fill logic remain execution proxies; depletion mixes trades and cancels.'", "'Fixed 18-share size is preserved; all realized Maker/Taker inventory comes from HftBacktest execution events. Dream-fill inventory is forbidden.'")
s=s.replace("PREFIX=OUT/f'target_blind_promoted_controller_closed_loop_v3{SUFFIX}'", "PREFIX=OUT/f'target_blind_promoted_controller_closed_loop_v3_hft_retrain_v1{SUFFIX}'")
s=s.replace("'reportVersion':'TARGET_BLIND_PROMOTED_CONTROLLER_CLOSED_LOOP_V3'", "'reportVersion':'TARGET_BLIND_PROMOTED_CONTROLLER_CLOSED_LOOP_V3_HFT_RETRAIN_V1'")
out.write_text(s,encoding='utf-8')
print(out)
