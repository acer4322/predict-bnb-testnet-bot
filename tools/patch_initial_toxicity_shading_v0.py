from pathlib import Path
p=Path('tools/hftbacktest_r2_initial_toxicity_shading_v0.py')
s=p.read_text(encoding='utf-8')
needle="""        if px is None:return
        num=int(a.next_num);a.next_num+=1;rc=ex.submit_native(a.bt,num,side,px,qty)
"""
repl="""        if px is None:return
        # Structural variant: price adverse-selection risk BEFORE joining the
        # queue. Existing working orders are never toxicity-cancelled/repriced.
        # The same strict-past fill-quality model is evaluated on a hypothetical
        # age-zero child at the base Frozen-R2 quote.
        bf0=mod.outcome_book(c.book.book,None) or {}
        _bid0=bf0.get('up_bid') if side=='UP' else bf0.get('down_bid')
        _ask0=bf0.get('up_ask') if side=='UP' else bf0.get('down_ask')
        pred0=None; extra0=0; base_px=float(px)
        if _bid0 is not None and _ask0 is not None:
            bid0=float(_bid0); ask0=float(_ask0)
            native_side0,native_px0=ex.native_order(side,float(px))
            depth_side0='bids' if native_side0=='BUY' else 'asks'
            init0=float(c.book.book.get(depth_side0,{}).get(float(native_px0),0.0) or 0.0)
            port0=actual.features(now)
            vals0={'side_is_up':float(side=='UP'),'order_age_ms':0.0,'quote_price':float(px),
                   'status_none':1.0,'status_new':0.0,'status_partial':0.0,'cum_exec_qty':0.0,
                   'remaining_qty':float(qty),'remaining_ratio':1.0,'partial_fill_ratio':0.0,
                   'active_same_count':0.0,'active_opp_count':float(active['DOWN' if side=='UP' else 'UP'] is not None),
                   'quote_offset_ticks':(bid0-float(px))/mod.GRID,'current_bid':bid0,'current_ask':ask0,
                   'current_spread_ticks':(ask0-bid0)/mod.GRID,'initial_depth':init0,
                   'public_cum_depletion':0.0,'public_depletion_ratio':0.0,'public_any_depletion':0.0}
            vals0.update(port0)
            frame0=pd.DataFrame([{f:vals0.get(f,math.nan) for f in tox_features}],columns=tox_features)
            pred0=float(tox_model.predict(frame0)[0])
            if pred0 < 0.0:
                extra0=max(1,int(math.ceil(-pred0)))
                base_tick=int(round(float(px)/mod.GRID))
                min_tick=int(round(mod.MIN_PRICE/mod.GRID))
                new_tick=max(min_tick,base_tick-extra0)
                px=round(new_tick*mod.GRID,2)
                if oppp is not None:
                    while px+float(oppp)>mod.MAX_PAIR_PRICE_SUM+EPS and new_tick>min_tick:
                        new_tick-=1; px=round(new_tick*mod.GRID,2)
                toxicity_events.append({'atMs':now,'side':side,'action':'INITIAL_TOXIC_SHADE','predictedMarkout1sTicks':pred0,'shadeTicks':int(base_tick-new_tick),'basePrice':base_px,'price':px,'targetRevision':int(target_revision[side])})
            else:
                toxicity_events.append({'atMs':now,'side':side,'action':'INITIAL_TOXIC_KEEP','predictedMarkout1sTicks':pred0,'shadeTicks':0,'basePrice':base_px,'price':px,'targetRevision':int(target_revision[side])})
        num=int(a.next_num);a.next_num+=1;rc=ex.submit_native(a.bt,num,side,px,qty)
"""
if needle not in s: raise SystemExit('submit insertion point missing')
s=s.replace(needle,repl,1)
# Disable post-placement toxicity withdrawal entirely; lifecycle/taker logic stays unchanged.
s=s.replace("            toxicity_step(t)\n", "            # initial-toxicity variant: never toxicity-cancel an existing queue\n")
s=s.replace("'version':'R2_ONE_SHOT_TOXICITY_SHADING_V2'", "'version':'R2_INITIAL_TOXICITY_SHADING_V0'")
s=s.replace("'version':'R2_ONE_SHOT_TOXICITY_SHADING_V2_REPORT'", "'version':'R2_INITIAL_TOXICITY_SHADING_V0_REPORT'")
p.write_text(s,encoding='utf-8')
print('patched',p)
