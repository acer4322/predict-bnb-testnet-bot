"""Validated UI settings. Playback time is never a policy/exchange parameter."""
from __future__ import annotations
import math

VERSION = 'BTC5M_TIME_MODEL_PLAYGROUND_V1'
DEFAULT = {'seed':20260920, 'addition_mode':'DYNAMIC', 'clock_mode':'P50',
           'add_bias':0., 'repair_bias':0., 'active_bias':0., 'keep_bias':0.}
BIAS_FIELDS = ('add_bias','repair_bias','active_bias','keep_bias')
MODEL_PINS = {
    20260920: {'name':'ONPOLICY_W3_S20260920.npz','sha256':'b0f073ad7dc2f7fbe6be13c2ec4bcb74e71f1e352542e2abb4c0859521ba1d50'},
    20260921: {'name':'ONPOLICY_W3_S20260921.npz','sha256':'97bdd4d0a2a53b7405a0b749877187b32502fe7be2e8cabb427c01bb6bba8ac6'}}
REPAIR_IDS = (2,3,4,5,6,8,11,12,13)
ACTIVE_IDS = (3,4,5,6,8,11,13)

def validate(value):
    if not isinstance(value,dict) or set(value)-set(DEFAULT): raise ValueError('不支援的模型設定。')
    c={**DEFAULT,**value}
    if type(c['seed']) is not int or c['seed'] not in MODEL_PINS: raise ValueError('只能選擇已凍結的 R65 模型。')
    if c['addition_mode'] not in ('DYNAMIC','FIXED_UP'): raise ValueError('加倉方向設定錯誤。')
    if c['clock_mode'] not in ('P50','P90','STATIC3'): raise ValueError('時鐘設定錯誤。')
    for key in BIAS_FIELDS:
        n=c[key]
        if type(n) not in (int,float) or not math.isfinite(n) or not -5<=n<=5: raise ValueError(key+' 必須介於 -5 與 5。')
        c[key]=float(n)
    return c

def offsets(c, count=14):
    if count!=14: raise ValueError('凍結模型的動作數量不符。')
    y=[0.]*count;y[7]+=c['add_bias'];y[9]+=c['add_bias'];y[0]+=c['keep_bias']
    for i in REPAIR_IDS:y[i]+=c['repair_bias']
    for i in ACTIVE_IDS:y[i]+=c['active_bias']
    return y

def validate_request(body):
    if not isinstance(body,dict) or set(body)-{'market','baseline','candidate','apply_ms','note'}: raise ValueError('不支援的請求欄位。')
    market=body.get('market')
    if type(market) is not int or market<=0: raise ValueError('市場編號錯誤。')
    t=body.get('apply_ms',0)
    if type(t) not in (int,float) or not math.isfinite(t) or not 0<=t<300000: raise ValueError('套用時間必須介於 0 與 300 秒之前。')
    note=body.get('note','')
    if not isinstance(note,str) or len(note)>4000: raise ValueError('註記最多 4000 字。')
    return {'market':market,'baseline':validate(body.get('baseline',{})),
            'candidate':validate(body.get('candidate',{})),'apply_ms':int(t),'note':note}
