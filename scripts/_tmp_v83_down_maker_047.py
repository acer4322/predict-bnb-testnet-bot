import lzma,json,datetime
p=r'data/execution_tape_v1/markets/1916869.json.xz'
T0=datetime.datetime.fromtimestamp(1788450022673/1000,tz=datetime.timezone.utc)
with lzma.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
out=[]
for m in d.get('matches') or []:
 t=datetime.datetime.fromisoformat(m['executedAt'].replace('Z','+00:00'))
 if t < T0: continue
 for x in m.get('makers') or []:
  name=(x.get('outcome') or {}).get('name')
  px=int(x.get('price','0'))/1e18
  if name=='Down' and abs(px-0.47)<1e-12:
   out.append({'executedAt':m['executedAt'],'makerPrice':px,'makerAmount':int(x.get('amount','0'))/1e18,'priceExecuted':int(m.get('priceExecuted','0'))/1e18,'takerOutcome':((m.get('taker') or {}).get('outcome') or {}).get('name')})
print(json.dumps({'count':len(out),'first10':out[:10]},indent=2))
