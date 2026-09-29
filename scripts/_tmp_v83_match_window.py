import lzma,json,datetime
p=r'data/execution_tape_v1/markets/1916869.json.xz'
with lzma.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
start=datetime.datetime.fromtimestamp(1788450022673/1000, tz=datetime.timezone.utc)
end=start+datetime.timedelta(seconds=15)
out=[]
for m in d.get('matches') or []:
    t=datetime.datetime.fromisoformat(m['executedAt'].replace('Z','+00:00'))
    if start <= t <= end:
        makers=[]
        for x in m.get('makers') or []:
            makers.append({'outcome':(x.get('outcome') or {}).get('name'),'price':int(x.get('price','0'))/1e18,'amount':int(x.get('amount','0'))/1e18,'quoteType':x.get('quoteType')})
        taker=m.get('taker') or {}
        out.append({'executedAt':m['executedAt'],'priceExecuted':int(m.get('priceExecuted','0'))/1e18,'amountFilled':int(m.get('amountFilled','0'))/1e18,'makers':makers,'takerOutcome':(taker.get('outcome') or {}).get('name'),'takerPrice':int(taker.get('price','0'))/1e18})
print(json.dumps({'start':start.isoformat(),'end':end.isoformat(),'matches':out},indent=2))
