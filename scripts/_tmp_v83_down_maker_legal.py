import lzma,json,datetime
p=r'data/execution_tape_v1/markets/1916869.json.xz'
T0MS=1788450022673
T0=datetime.datetime.fromtimestamp(T0MS/1000,tz=datetime.timezone.utc)
Q=2.1649963710093214
minp=1.0/Q
with lzma.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
out=[]
for m in d.get('matches') or []:
 t=datetime.datetime.fromisoformat(m['executedAt'].replace('Z','+00:00'))
 if t < T0: continue
 for x in m.get('makers') or []:
  name=(x.get('outcome') or {}).get('name'); px=int(x.get('price','0'))/1e18
  if name=='Down' and px+1e-12>=minp:
   ms=int(t.timestamp()*1000)
   out.append({'executedAt':m['executedAt'],'delayMs':ms-T0MS,'makerPrice':px,'makerAmount':int(x.get('amount','0'))/1e18,'takerOutcome':((m.get('taker') or {}).get('outcome') or {}).get('name'),'priceExecuted':int(m.get('priceExecuted','0'))/1e18})
out.sort(key=lambda z:z['delayMs'])
print(json.dumps({'minLegalPrice':minp,'count':len(out),'first20':out[:20]},indent=2))
