import pandas as pd
from sklearn.metrics import log_loss
p='data/research/r4_v0/hourly/r4_management_meta_router_v1_oof_rows.csv'
d=pd.read_csv(p)
C=['CONTINUE_WEAK','HANDOFF_ALLOW','OBSERVE_NO_EVENT']
E=['EBM','LIGHTGBM','TINY_TRANSFORMER']
d['phase']=pd.cut(d.seconds_left,bins=[60,120,180,240,301],right=False)
print('rows',len(d),'markets',d.market_id.nunique())
for b in sorted(d.block.unique()):
    q=d[d.block==b]
    print('\nBLOCK',b)
    for ph,g in q.groupby('phase',observed=True):
        vals=[]
        for e in E:
            pp=g[[f'{e}_{c}' for c in C]].to_numpy()
            vals.append((e,round(log_loss(g.label,pp,labels=C),4)))
        print(ph,len(g),vals,'best_counts',g.best_expert.value_counts(normalize=True).round(3).to_dict())
