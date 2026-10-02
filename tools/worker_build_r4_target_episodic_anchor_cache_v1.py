from pathlib import Path
import argparse,json,sys
import numpy as np,torch

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle-dir',required=True);ap.add_argument('--out',required=True);args=ap.parse_args()
 b=Path(args.bundle_dir).resolve();sys.path.insert(0,str(b))
 import train_r4_target_sequence_teacher_v1 as v1
 import train_r4_target_sequence_hazard_v1 as hz
 rep=json.loads((b/'r4_target_sequence_teacher_v1_report.json').read_text(encoding='utf-8'));train=set(map(int,rep['dataset']['trainMarketIds']))
 v1.D=b/'data';hz.D=b/'data'
 d=v1.build_rows();X,M,ya,yc,yf,ph,mids,times,sup=v1.build_sequences(d);actions=np.array([v1.ACTIONS[i] for i in ya]);keep=np.array([not a.endswith('_FLAT') for a in actions],bool)
 X=X[keep];M=M[keep];mids=mids[keep];actions=actions[keep];purpose=np.array([1 if a.endswith('_ADD') else 0 for a in actions],np.int64);role=np.array([1 if a.startswith('TAKER_') else 0 for a in actions],np.int64);ai=np.where(np.array([int(m) in train for m in mids],bool))[0]
 HX,HM,HY,HPH,HMIDS,HTIMES=hz.build_grid();HY=HY.astype(np.float32);hi=np.where(np.array([int(m) in train for m in HMIDS],bool))[0]
 out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True)
 torch.save({'version':'R4_TARGET_EPISODIC_ANCHOR_CACHE_V1','X':torch.from_numpy(X[ai]),'M':torch.from_numpy(M[ai]),'purpose':torch.from_numpy(purpose[ai]),'role':torch.from_numpy(role[ai]),'mids':torch.from_numpy(mids[ai]),'HX':torch.from_numpy(HX[hi]),'HM':torch.from_numpy(HM[hi]),'HY':torch.from_numpy(HY[hi]),'HMIDS':torch.from_numpy(HMIDS[hi])},out)
 print(json.dumps({'actionExamples':int(len(ai)),'hazardExamples':int(len(hi)),'out':str(out)},indent=2),flush=True)
if __name__=='__main__':main()