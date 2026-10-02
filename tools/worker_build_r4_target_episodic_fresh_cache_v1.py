from pathlib import Path
import argparse,sys,json
import numpy as np,torch

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle-dir',required=True);ap.add_argument('--fresh-dir',required=True);ap.add_argument('--out',required=True);args=ap.parse_args()
 b=Path(args.bundle_dir).resolve();f=Path(args.fresh_dir).resolve();sys.path.insert(0,str(b))
 import train_r4_target_sequence_teacher_v1 as v1
 import train_r4_target_sequence_hazard_v1 as hz
 v1.D=f;hz.D=f
 d=v1.build_rows();X,M,ya,yc,yf,ph,mids,times,sup=v1.build_sequences(d);actions=np.array([v1.ACTIONS[i] for i in ya]);keep=np.array([not a.endswith('_FLAT') for a in actions],bool)
 X=X[keep];M=M[keep];ph=ph[keep];mids=mids[keep];times=times[keep];actions=actions[keep];purpose=np.array([1 if a.endswith('_ADD') else 0 for a in actions],np.int64);role=np.array([1 if a.startswith('TAKER_') else 0 for a in actions],np.int64)
 HX,HM,HY,HPH,HMIDS,HTIMES=hz.build_grid();HY=HY.astype(np.float32)
 out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True);torch.save({'version':'R4_TARGET_EPISODIC_FRESH_TENSOR_CACHE_V1','X':torch.from_numpy(X),'M':torch.from_numpy(M),'purpose':torch.from_numpy(purpose),'role':torch.from_numpy(role),'phase':torch.from_numpy(ph),'mids':torch.from_numpy(mids),'times':torch.from_numpy(times),'HX':torch.from_numpy(HX),'HM':torch.from_numpy(HM),'HY':torch.from_numpy(HY),'HPH':torch.from_numpy(HPH),'HMIDS':torch.from_numpy(HMIDS),'HTIMES':torch.from_numpy(HTIMES)},out)
 print(json.dumps({'rows':int(len(d)),'actionExamples':int(len(X)),'hazardExamples':int(len(HX)),'markets':int(len(set(map(int,mids)))),'out':str(out)},indent=2),flush=True)
if __name__=='__main__':main()