import json,random,time,math
N=500000; rng=random.Random(20260829); t0=time.perf_counter()
# Synthetic architecture stress only. Environment hazard is invariant; policy action frequency shifts by regime.
def env_p(age,live,load):
 p=0.03 + (0.28 if live else 0.0) + (0.08 if age<20 else 0.03 if age<80 else 0.0) - 0.08*max(0,load-1)
 return max(0.002,min(0.8,p))
def pol_p(reg,age,load):
 base={'LOW':0.05,'MID':0.20,'HIGH':0.40}[reg]
 return max(0.005,min(0.8,base + (0.10 if load<0.7 else -0.04) + (0.04 if age<20 else 0)))
def key(age,live,load):return (0 if age<20 else 1 if age<80 else 2,int(live),0 if load<.75 else 1 if load<1.5 else 2)
# train lookup on LOW regime: entangled predicts any(event), separated predicts environment(event)
stat={};train=160000
for _ in range(train):
 age=rng.uniform(0,180);live=rng.random()<.55;load=10**rng.uniform(-.6,.35);k=key(age,live,load);env=int(rng.random()<env_p(age,live,load));pol=int(rng.random()<pol_p('LOW',age,load));z=stat.setdefault(k,[0,0,0]);z[0]+=1;z[1]+=int(env or pol);z[2]+=env
res={}
for reg in ('LOW','MID','HIGH'):
 cmE=[0,0,0,0];cmS=[0,0,0,0];rows=(N-train)//3
 for _ in range(rows):
  age=rng.uniform(0,180);live=rng.random()<.55;load=10**rng.uniform(-.6,.35);k=key(age,live,load);env=int(rng.random()<env_p(age,live,load));pol=int(rng.random()<pol_p(reg,age,load));y=int(env or pol);z=stat.get(k,[1,0,0]);ent=int(z[1]/z[0]>=.5);sep=int(pol or (z[2]/z[0]>=.5))
  for pred,cm in ((ent,cmE),(sep,cmS)):
   if y and pred:cm[0]+=1
   elif y and not pred:cm[1]+=1
   elif not y and pred:cm[2]+=1
   else:cm[3]+=1
 def met(cm):
  tp,fn,fp,tn=cm;return {'balancedAccuracy':.5*(tp/max(1,tp+fn)+tn/max(1,tn+fp)),'positiveRecall':tp/max(1,tp+fn),'negativeRecall':tn/max(1,tn+fp),'n':sum(cm)}
 res[reg]={'entangledPolicyPredictedAsWorld':met(cmE),'policyWorldSeparated':met(cmS)}
print(json.dumps({'version':'R4_MANAGEMENT_POLICY_WORLD_SHIFT_MICROWORLD_V1','researchOnly':True,'promotionEvidence':False,'episodes':N,'trainRegime':'LOW','regimeResults':res,'elapsedSec':time.perf_counter()-t0,'guard':'Synthetic assumed hazards; discovery/stress only. Purpose is architecture sensitivity to policy-frequency shift, not market validation.'},indent=2))