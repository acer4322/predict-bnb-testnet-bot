from pathlib import Path
import argparse,json

def load(p):return json.loads(Path(p).read_text(encoding='utf-8'))

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--state-v1',required=True);ap.add_argument('--dual-result',required=True);ap.add_argument('--out',required=True);args=ap.parse_args()
 s=load(args.state_v1);d=load(args.dual_result);caps=s['capabilityRegistry'];decisions={}
 # Repair
 rr=d['repairDemand5s'];rd=rr['delta'];base=caps['REPAIR_DEMAND']
 if base['reversal']>=2:
  decisions['REPAIR_DEMAND']={'decision':'SPECIALIZE','reason':'Cross-stream context reversals exist; keep specialist capability but prohibit global directional update.','evidence':{'finalAucDelta':rd['auc'],'finalBADelta':rd['balancedAccuracy']}}
 elif rd['auc']>0 and rd['balancedAccuracy']>0:
  decisions['REPAIR_DEMAND']={'decision':'ACCEPT_SHADOW','reason':'Independent specialist improved fresh final AUC and BA.','evidence':{'finalAucDelta':rd['auc'],'finalBADelta':rd['balancedAccuracy']}}
 else:
  decisions['REPAIR_DEMAND']={'decision':'ROLLBACK','reason':'Independent specialist did not improve both fresh final AUC and BA.'}
 # State shaping
 ar=d['stateShapingOpportunity5s'];ad=ar['delta'];basea=caps['STATE_SHAPING_OPPORTUNITY']
 if ad['auc']>0 and ad['balancedAccuracy']>0:
  if ad['negativeRecall'] < -0.05 or ad['positiveRecall'] < -0.05:
   decisions['STATE_SHAPING_OPPORTUNITY']={'decision':'ACCEPT_SHADOW_WITH_GUARD','reason':'Independent specialist repairs prior capability deficit and improves AUC/BA, but one recall side falls materially; require false-positive/class-balance guard.','evidence':{'finalAucDelta':ad['auc'],'finalBADelta':ad['balancedAccuracy'],'positiveRecallDelta':ad['positiveRecall'],'negativeRecallDelta':ad['negativeRecall']}}
  else:
   decisions['STATE_SHAPING_OPPORTUNITY']={'decision':'ACCEPT_SHADOW','reason':'Independent specialist improved AUC/BA without material class-side degradation.'}
 else:
  decisions['STATE_SHAPING_OPPORTUNITY']={'decision':'ROLLBACK','reason':'Independent specialist failed fresh final AUC+BA improvement.'}
 # Role
 rc=caps['ROLE_MAKER_TAKER']
 decisions['ROLE_MAKER_TAKER']={'decision':'SPECIALIZE' if rc['reversal']>=2 else 'COLLECT_MORE','reason':'Maker/Taker direction changes across chronology/context; no global role bias permitted.' if rc['reversal']>=2 else 'Insufficient stable role evidence.'}
 # Timing
 tc=caps['TIMING_HAZARD']
 decisions['TIMING_HAZARD']={'decision':'COLLECT_MORE' if tc['status']=='INSUFFICIENT_CROSS_STREAM_EVIDENCE' else 'KEEP_FROZEN','reason':'Need repeated cross-stream timing evidence before continual adaptation.'}
 # Structural capabilities
 for k in ['OBJECTIVE_LIFECYCLE','EXECUTION_REFRESH','PROTECTION']:
  decisions[k]={'decision':'KEEP_FROZEN','reason':'Structural/safety capability remains governed by existing validated rules; no learned update authorized.'}
 out={'version':'R4_ADAPTIVE_CYCLE_SUPERVISOR_V2_STATE','researchOnly':True,'actionAuthority':False,'objective':'OUR_OWN_TARGET_INSPIRED_ADAPTIVE_SYSTEM','targetRole':'AUXILIARY_TEACHER_REFERENCE_ONLY','decisions':decisions,'cycleNextActions':[
  'Use independent Repair and State-Shaping checkpoints, never a shared binary Purpose head.',
  'Attach class-balance/false-positive guard to State-Shaping before any later promotion.',
  'Route Role to context/regime specialization rather than global episodic updates.',
  'Continue collecting Timing evidence without aggressive adaptation.',
  'On every new episode: attribute capability -> train challenger only -> fresh guard -> behavior ledger -> supervisor decision -> shadow checkpoint update or rollback.'
 ],'safetyInvariants':['no <=180s NEW learned exposure','Protection precedence absolute','Stage0 venue feasibility','authoritative carrier reconciliation','no duplicate responsibility','strict-past runtime','no live 8781/R3/R3.1 mutation']}
 p=Path(args.out);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'out':str(p),'decisions':{k:v['decision'] for k,v in decisions.items()}},indent=2))
if __name__=='__main__':main()
