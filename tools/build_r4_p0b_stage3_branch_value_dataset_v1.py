from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]; P=ROOT/'data/research/r4_v0/p0_provenance_v1'
BASE=P/'r4_p0b_stage3_group_context_dataset_v1.csv'; AUD=P/'r4_p0b_objective_counterfactual_audit_candidate_table_v2.json'; OUT=P/'r4_p0b_stage3_branch_value_dataset_v1.csv'; REP=P/'r4_p0b_stage3_branch_value_dataset_v1.json'
def main():
 d=pd.read_csv(BASE); a=json.loads(AUD.read_text(encoding='utf-8')); vals=[]
 for r in a['rows']:
  b=r['finalBranches']['REJECT_NO_ACTION']; ad=r['finalBranches']['ADDITIVE']; cr=r['finalBranches']['CREDIT']
  vals.append({'marketId':int(r['marketId']),'candidateKey':str(r['candidateKey']),'target_add_floor_gain':float(ad['floor'])-float(b['floor']),'target_add_absnet_gain':float(b['absNet'])-float(ad['absNet']),'target_credit_floor_gain':float(cr['floor'])-float(b['floor']),'target_credit_absnet_gain':float(b['absNet'])-float(cr['absNet']),'knownRole':r['knownRole']})
 v=pd.DataFrame(vals); x=d.drop(columns=['knownRole'],errors='ignore').merge(v,on=['marketId','candidateKey'],how='inner',validate='one_to_one'); x.to_csv(OUT,index=False)
 rep={'version':'R4_P0B_STAGE3_BRANCH_VALUE_DATASET_V1','researchOnly':True,'actionAuthority':False,'rows':len(x),'markets':x.marketId.nunique(),'targets':['target_add_floor_gain','target_add_absnet_gain','target_credit_floor_gain','target_credit_absnet_gain'],'targetSemantics':'Future counterfactual economics are regression labels only. Runtime X remains strict-past candidate/group/execution state. Positive floor_gain and absnet_gain are both economically favorable.','roleCounts':x.knownRole.value_counts().to_dict()}; REP.write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
