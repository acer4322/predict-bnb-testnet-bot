import argparse,json,os,sqlite3
from collections import defaultdict

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    c=sqlite3.connect(a.db);c.row_factory=sqlite3.Row
    mend={(r['asset'],r['market_id']):r['window_end_ms'] for r in c.execute("select asset,market_id,window_end_ms from target_markets where asset in ('BTC','ETH')")}
    rows=list(c.execute("select parent_id,asset,market_id,role,side,first_event_ms,average_price,shares from target_parent_orders where asset in ('BTC','ETH') order by asset,market_id,first_event_ms,parent_id"))
    events=[];cur=None;up=dn=0.;upc=dnc=0.;prev=None
    for r in rows:
        k=(r['asset'],r['market_id'])
        if k!=cur:
            cur=k;up=dn=upc=dnc=0.;prev=None
        total=up+dn;gap=abs(up-dn);paircov=2*min(up,dn)/total if total else 1.;weak=None
        if gap>1e-9: weak='UP' if up<dn else 'DOWN'
        rel='BAL' if weak is None else ('WEAK' if r['side']==weak else 'DOM')
        pre_abs=gap/total if total else 0.;pre_pair=paircov;pre_floor=min(up,dn)-(upc+dnc)
        sh=float(r['shares']);px=float(r['average_price'])
        if r['side']=='UP':up+=sh;upc+=sh*px
        else:dn+=sh;dnc+=sh*px
        post_total=up+dn;post_abs=abs(up-dn)/post_total if post_total else 0.;post_pair=2*min(up,dn)/post_total if post_total else 1.;post_floor=min(up,dn)-(upc+dnc)
        ev={'asset':r['asset'],'market':r['market_id'],'t':int(r['first_event_ms']),'role':r['role'],'side':r['side'],'rel':rel,'prePair':pre_pair,'preAbs':pre_abs,'dAbs':post_abs-pre_abs,'dPair':post_pair-pre_pair,'dFloor':post_floor-pre_floor}
        if prev is not None and rel in ('WEAK','DOM'):
            events.append({'asset':r['asset'],'market':r['market_id'],'pair':pre_pair,'gapMs':int(r['first_event_ms'])-prev['t'],'prevRole':prev['role'],'prevRel':prev['rel'],'prevEffect':'REPAIR' if prev['dAbs']<-1e-6 else 'EXPAND' if prev['dAbs']>1e-6 else 'FLAT','nextRole':r['role'],'nextRel':rel})
        prev=ev
    def pbin(x):
        if x<.5:return '<50%'
        if x<.75:return '50-75%'
        if x<.9:return '75-90%'
        return '>=90%'
    def gbin(ms):
        if ms<=1000:return '<=1s'
        if ms<=5000:return '1-5s'
        return '>5s'
    def summarize(filters):
        z=[e for e in events if all(e.get(k)==v for k,v in filters.items())]
        n=len(z);w=sum(e['nextRel']=='WEAK' for e in z)
        return {'n':n,'weakNext':w,'weakFrac':w/n if n else None}
    out={'version':'TARGET_ETH_BTC_REENTRY_TRANSITION_ANATOMY_V5','strictPastSequence':True,'actualParentFillsOnly':True,'placement18PriorUsed':False,'overall':{},'byPairCoverage':{},'byGap':{},'makerNext':{},'makerPrevAndNext':{}}
    for asset in ('BTC','ETH'):
        out['overall'][asset]={eff:summarize({'asset':asset,'prevEffect':eff}) for eff in ('REPAIR','EXPAND','FLAT')}
        out['byPairCoverage'][asset]={}
        for eff in ('REPAIR','EXPAND'):
            out['byPairCoverage'][asset][eff]={}
            for pb in ('<50%','50-75%','75-90%','>=90%'):
                z=[e for e in events if e['asset']==asset and e['prevEffect']==eff and pbin(e['pair'])==pb];n=len(z);w=sum(e['nextRel']=='WEAK' for e in z);out['byPairCoverage'][asset][eff][pb]={'n':n,'weakFrac':w/n if n else None}
        out['byGap'][asset]={}
        for eff in ('REPAIR','EXPAND'):
            out['byGap'][asset][eff]={}
            for gb in ('<=1s','1-5s','>5s'):
                z=[e for e in events if e['asset']==asset and e['prevEffect']==eff and gbin(e['gapMs'])==gb];n=len(z);w=sum(e['nextRel']=='WEAK' for e in z);out['byGap'][asset][eff][gb]={'n':n,'weakFrac':w/n if n else None}
        out['makerNext'][asset]={eff:summarize({'asset':asset,'prevEffect':eff,'nextRole':'MAKER'}) for eff in ('REPAIR','EXPAND')}
        out['makerPrevAndNext'][asset]={eff:summarize({'asset':asset,'prevEffect':eff,'prevRole':'MAKER','nextRole':'MAKER'}) for eff in ('REPAIR','EXPAND')}
    def diff(section,eff):
        a=out[section]['ETH'][eff]['weakFrac'];b=out[section]['BTC'][eff]['weakFrac'];return None if a is None or b is None else a-b
    out['keyDiffs']={'overallAfterExpand_ETHminusBTC':diff('overall','EXPAND'),'overallAfterRepair_ETHminusBTC':diff('overall','REPAIR'),'makerNextAfterExpand_ETHminusBTC':diff('makerNext','EXPAND'),'makerMakerAfterExpand_ETHminusBTC':diff('makerPrevAndNext','EXPAND')}
    os.makedirs(os.path.dirname(a.output),exist_ok=True);json.dump(out,open(a.output,'w',encoding='utf-8'),ensure_ascii=False,indent=2)
    print(json.dumps({'ok':True,'keyDiffs':out['keyDiffs'],'overall':out['overall'],'makerNext':out['makerNext'],'makerPrevAndNext':out['makerPrevAndNext'],'pairAfterExpand':{x:out['byPairCoverage'][x]['EXPAND'] for x in ('BTC','ETH')}},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
