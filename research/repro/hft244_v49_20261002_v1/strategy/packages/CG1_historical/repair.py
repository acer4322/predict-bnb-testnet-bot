"""Post-observed-FLIP repair proposal only. Original ADD direction and caps stay outside."""
import atexit,gzip,json,math,os
from pathlib import Path
from economics import proposal

ENABLED=os.environ.get('V12G_POSTFLIP_MONEY_REPAIR','OFF')=='ON'
assert os.environ.get('V12G_POSTFLIP_MONEY_REPAIR','OFF') in ('OFF','ON')
ROWS=[]

def select(original,state,strong,weak,quotes,book,peak,step,past_flip,enabled=ENABLED):
    # A proposal becomes eligible only through past observed runtime events,
    # never terminal market labels, baseline events, or a future flip count.
    if not enabled or not past_flip or original['reason']!='NO_RESTORABLE_NEGATIVE_BRANCH':return original,weak,False
    if state['payoff'][strong]>=-1e-8 or state['payoff'][weak]<=1e-8:return original,weak,False
    side=strong;F=weak;ask=(quotes.get(side) or {}).get('ask')
    ladder=(book.get('asks') if side=='UP' else book.get('bids')) or {}
    depth=float(ladder[min(ladder) if side=='UP' else max(ladder)]) if ladder else 0.
    row=proposal(state,F,ask,depth,peak,step)
    row.update(postflip_money_repair=True,original_strong=strong,original_weak=weak,
               physical_F=F,physical_L=side,original_reason=original['reason'],full_target_reason=row['reason'])
    if row['reason']=='FULL_RESTORATION_NOT_AFFORDABLE':
        q=max(0.,round(math.floor((min(row['needed_qty'],row['total_cap'],depth)+1e-9)/step)*step,8))
        if q>1e-8 and q*float(ask)>=1.-1e-8:
            row.update(eligible=True,reason='POSTFLIP_PARTIAL_MONEY_REPAIR',quantity=q,quoted_cost=q*float(ask),
                after_G_if_filled=row['G']-q*float(ask),after_H_if_filled=row['H']+q*(1-float(ask)),
                depth_limited=q<row['needed_qty']-step,partial=True,not_a_fill=True)
            assert row['worst_G']-q*float(ask)>=row['retained_G_floor']-1e-8
        else:row.update(eligible=False,reason='POSTFLIP_PARTIAL_BELOW_ORIGINAL_MINIMUM',quantity=q)
    return row,side,True

def record(frame,row,state,operations):
    if ENABLED and row.get('postflip_money_repair'):
        ROWS.append(dict(t=int(frame['t']),index=int(frame['index']),row=dict(row),state=state,operations_before=operations))

def finish():
    target=os.environ.get('BTC5M_LAN_RESULT_DIR')
    if not target:return
    p=Path(target);p.mkdir(parents=True,exist_ok=True)
    payload=dict(enabled=ENABLED,rows=ROWS)
    with gzip.GzipFile(filename=str(p/'postflip_repair_trace.json.gz'),mode='wb',mtime=0) as f:f.write(json.dumps(payload,sort_keys=True,separators=(',',':')).encode())
atexit.register(finish)
