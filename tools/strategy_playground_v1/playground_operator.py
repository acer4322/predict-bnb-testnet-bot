"""Research-only inference preferences on the retained native R65 operator.
The canonical proposal/mask/reservation/receipt/gateway code is not replaced.
"""
from copy import deepcopy
import hashlib,json,os,time
from pathlib import Path
import numpy as np
from nn_bridge import NeuralOperator
import distribution_learning as dl
from settings import validate, offsets, MODEL_PINS

class PlaygroundOperator(NeuralOperator):
    def __init__(self,config):
        super().__init__(config)
        spec=config['playground']
        self.model_paths=dict(spec['model_paths'])
        self.base_settings=validate(spec['baseline'])
        self.next_settings=validate(spec['candidate'])
        self.switch_t=int(spec['market_start_ms'])+int(spec['apply_ms'])
        self.models={self.base_settings['seed']:self.policy}
        for seed in {self.base_settings['seed'],self.next_settings['seed']}:
            f=Path(spec['model_paths'][str(seed)])
            if hashlib.sha256(f.read_bytes()).hexdigest()!=MODEL_PINS[seed]['sha256']:raise ValueError('Candidate checkpoint hash mismatch')
            if seed not in self.models:self.models[seed]=dl.load(f)
        self.current_settings=self.base_settings
        self.frame_t=None;self.preference_log=[];self._last_progress=0.
        self.market_start=int(spec['market_start_ms']);self.market_end=int(spec['market_end_ms'])
    def scores(self,node,h):
        raw=super().scores(node,h)
        bias=offsets(self.current_settings,len(raw))
        # Exactly preserve values/dtype when all offsets are zero.
        return raw if not any(bias) else raw+np.asarray(bias,dtype=raw.dtype)
    def _produce(self,frame,producer,strong,validate_size,crossing):
        t=int(frame['t'])
        c=self.next_settings if t>=self.switch_t else self.base_settings
        self.current_settings=c;self.frame_t=t
        self.policy=self.models[c['seed']]
        if hasattr(self.policy,'arrays'):self.width=self.policy.arrays['w0'].shape[1]
        self.config['frozen_micro']['addition_mode']=c['addition_mode']
        self.config['frozen_micro']['clock_mode']=c['clock_mode']
        self.config['frozen_micro']['sha256']=MODEL_PINS[c['seed']]['sha256']
        self.config['frozen_micro']['path']=self.model_paths[str(c['seed'])]
        self.config['frozen_micro']['seed']=c['seed']
        self.config['frozen_micro']['id']='R65_S'+str(c['seed'])+'_'+c['addition_mode']
        self.config['frozen_micro']['source_model']='R65_S'+str(c['seed'])
        self.physical_clock.mode=c['clock_mode']
        before=len(self.nn_rows)
        ops=super()._produce(frame,producer,strong,validate_size,crossing)
        if len(self.nn_rows)>before and self.nn_rows[-1]['status']=='NN':
            row=self.nn_rows[-1]
            row['playground']={'settings':deepcopy(c),'preferences_active':t>=self.switch_t,'switch_t_ms':self.switch_t,
                'kind':'POST_NN_SCORE_OFFSET_WITH_NATIVE_LEGALITY','checkpoint_sha256':MODEL_PINS[c['seed']]['sha256']}
        now=time.monotonic()
        if now-self._last_progress>=1.:
            out=Path(os.environ['BTC5M_LAN_RESULT_DIR'])
            p=out/'PLAYBACK_PROGRESS.json';tmp=p.with_suffix('.tmp')
            tmp.write_text(json.dumps({'state':'RUNNING_NATIVE','source_t_ms':t,'elapsed_market_ms':max(0,t-self.market_start),
                'market_duration_ms':self.market_end-self.market_start,'source_index':int(frame['index']),
                'nn_decisions':len(self.nn_rows),'live_authority':False}),encoding='utf-8')
            os.replace(tmp,p);self._last_progress=now
        return ops
