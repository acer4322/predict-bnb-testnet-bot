"""BTC5M Strategy Playground V0: bounded research-only receipt-stress experiments.
No sockets, exchange clients, subprocesses, model training or native HFT dispatch.
The copied R78 kernel is a hypothetical receipt world, NOT a calibrated fill model.
"""
from __future__ import annotations
import ast
import copy
import hashlib
import json
import math
import operator
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCE = ROOT / 'data/research/v49_incremental_cycle_value_micro_20260921_r78'
STORE = ROOT / 'data/research/strategy_playground_v0_20260921'
VENDOR = HERE / 'vendor'
sys.path.insert(0, str(VENDOR))
import kernel as k
from grid_encoding import encode_proposals

VERSION = 'BTC5M_STRATEGY_PLAYGROUND_V0'
FORMULA = 'repair_weight * d_floor + add_weight * d_best - risk_weight * pending_added - cost_weight * cash'
DEFAULT = dict(add_weight=.25, repair_weight=1., risk_weight=.5, cost_weight=.05,
               best_trigger=70., max_loss=10., lock_mode='OFF', trigger='PENDING_SAFE',
               addition_mode='DYNAMIC', formula=FORMULA)
SCENARIOS = {
    'UP': '假想 UP 價格逐步上移／可成交量全額',
    'DOWN': '假想 UP 價格逐步下移／可成交量全額',
    'WHIPSAW': '假想來回震盪／半額成交',
    'NO_FILL': '假想零成交／回報 UNKNOWN',
    'CANCEL_RACE': '假想撤單與成交競速／延遲後才確認',
    'PARTIAL': '假想半額成交／價差擴大與滑價',
}
NAMES = {'add_weight','repair_weight','risk_weight','cost_weight','d_floor','d_best',
         'd_up','d_down','cash','pending_added','cancel_cash','floor','best','gap',
         'pending_cash','repair_gap','is_add','is_repair','is_active','is_cancel','is_keep'}
BIN = {ast.Add:operator.add, ast.Sub:operator.sub, ast.Mult:operator.mul,
       ast.Div:operator.truediv, ast.Mod:operator.mod}
CMP = {ast.Lt:operator.lt, ast.LtE:operator.le, ast.Gt:operator.gt,
       ast.GtE:operator.ge, ast.Eq:operator.eq, ast.NotEq:operator.ne}
FN = {'min':min, 'max':max, 'abs':abs}

class FormulaError(ValueError):
    pass

class Formula:
    """Whitelist AST interpreter. No eval/exec, attributes, imports, loops or powers."""
    def __init__(self, text: str):
        if not isinstance(text, str) or not 1 <= len(text) <= 600:
            raise FormulaError('公式須為 1–600 字元。')
        try:
            self.tree = ast.parse(text, mode='eval').body
        except (SyntaxError, RecursionError) as e:
            raise FormulaError('公式語法錯誤。') from e
        if sum(1 for _ in ast.walk(self.tree)) > 150:
            raise FormulaError('公式太複雜。')
        self.text = text
        self._validate(self.tree, 0)
    def _validate(self, n: ast.AST, depth: int):
        if depth > 24:
            raise FormulaError('公式巢狀深度超過上限。')
        if isinstance(n, ast.Constant):
            if type(n.value) not in (int, float, bool) or not math.isfinite(float(n.value)) or abs(n.value)>1e6:
                raise FormulaError('只接受有限數字，常數絕對值須小於等於 1000000。')
            return
        if isinstance(n, ast.Name):
            if n.id not in NAMES: raise FormulaError('未知變數：'+n.id)
            return
        children = []
        if isinstance(n, ast.BinOp) and type(n.op) in BIN: children=[n.left,n.right]
        elif isinstance(n, ast.UnaryOp) and isinstance(n.op,(ast.UAdd,ast.USub,ast.Not)): children=[n.operand]
        elif isinstance(n, ast.Compare) and all(type(o) in CMP for o in n.ops): children=[n.left]+n.comparators
        elif isinstance(n, ast.BoolOp) and isinstance(n.op,(ast.And,ast.Or)): children=n.values
        elif isinstance(n, ast.IfExp): children=[n.test,n.body,n.orelse]
        elif isinstance(n, ast.Call) and isinstance(n.func,ast.Name) and n.func.id in FN and not n.keywords:
            if not 1<=len(n.args)<=4 or (n.func.id=='abs' and len(n.args)!=1):
                raise FormulaError('函式參數數量不合法。')
            if n.func.id in ('min','max') and len(n.args)<2: raise FormulaError('min/max 至少需要兩個數值。')
            children=n.args
        else: raise FormulaError('不允許的公式語法：'+type(n).__name__)
        for c in children: self._validate(c,depth+1)
    def __call__(self, env: dict[str, float]) -> float:
        def go(n):
            if isinstance(n,ast.Constant): v=n.value
            elif isinstance(n,ast.Name): v=env[n.id]
            elif isinstance(n,ast.BinOp): v=BIN[type(n.op)](go(n.left),go(n.right))
            elif isinstance(n,ast.UnaryOp):
                z=go(n.operand);v=not z if isinstance(n.op,ast.Not) else -z if isinstance(n.op,ast.USub) else z
            elif isinstance(n,ast.Compare):
                a=go(n.left);v=True
                for op,b in zip(n.ops,n.comparators):
                    b=go(b)
                    if not CMP[type(op)](a,b): v=False;break
                    a=b
            elif isinstance(n,ast.BoolOp):
                v=all(bool(go(x)) for x in n.values) if isinstance(n.op,ast.And) else any(bool(go(x)) for x in n.values)
            elif isinstance(n,ast.IfExp): v=go(n.body if go(n.test) else n.orelse)
            else: v=FN[n.func.id](*[go(x) for x in n.args])
            if not math.isfinite(float(v)) or abs(v)>1e12: raise FormulaError('公式結果溢位或不是有限數值。')
            return v
        try: return float(go(self.tree))
        except (ArithmeticError,KeyError,TypeError) as e: raise FormulaError('公式計算失敗：'+str(e)) from e


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_roots() -> tuple[list[dict], dict]:
    source_file = STORE/'ROOTS.json'
    if not source_file.exists(): source_file=SOURCE/'ROOTS.json'
    d=json.loads(source_file.read_text(encoding='utf-8'))
    roots=d['roots']
    if not roots or len(roots)>100: raise ValueError('Invalid root count')
    pins={f:sha(VENDOR/f) for f in ('base_kernel.py','r58_kernel.py','r59_kernel.py','r60_kernel.py','kernel.py','grid_encoding.py')}
    manifest=HERE/'VENDOR_PINS.json'
    if manifest.exists():
        expected=json.loads(manifest.read_text(encoding='utf-8'))
        if pins != expected['files']: raise ValueError('Vendor hash changed; refuse to run.')
        if sha(source_file) != expected['roots_sha256']: raise ValueError('Root snapshot hash changed; refuse to run.')
    return roots, {'root_sha256':sha(source_file),'vendor_sha256':pins,'source_line':'R78 / consumed native diagnostic roots',
                   'zero_fee':True,'market_impact_mode':'NONE','fill_model':'HYPOTHETICAL_RECEIPT_STRESS',
                   'training_eligible':False,'native_hft':False,'live_authority':False}


def config(value: Any) -> dict:
    if not isinstance(value,dict): raise ValueError('參數必須是物件。')
    if set(value)-set(DEFAULT): raise ValueError('不支援的參數。')
    c={**DEFAULT,**value}
    for name,limit in [('add_weight',10),('repair_weight',10),('risk_weight',10),('cost_weight',10),('best_trigger',1000),('max_loss',1000)]:
        v=c[name]
        if type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=limit: raise ValueError('參數範圍錯誤：'+name)
        c[name]=float(v)
    if c['lock_mode'] not in ('OFF','STOP_ALL','REPAIR_ONLY'): raise ValueError('Invalid lock mode')
    if c['trigger'] not in ('CONFIRMED','PENDING_SAFE'): raise ValueError('Invalid trigger')
    if c['addition_mode'] not in ('DYNAMIC','FIXED_UP'): raise ValueError('Invalid addition mode')
    Formula(c['formula'])
    return c


def prepare(root: dict, c: dict) -> dict:
    n=copy.deepcopy(root['node']);n['addition_mode']=c['addition_mode']
    n.setdefault('attempt_memory',{'failed_signatures':[],'confirmed_zero':0,'confirmed_partial':0,'confirmed_full':0})
    n.setdefault('observed_progress',{s:{'qty':0.,'cash':0.,'events':0} for s in k.SIDES})
    n.setdefault('last_fill',{s:0. for s in k.SIDES});n.setdefault('serial',0)
    for o in n['state']['owners']:
        for key,v in dict(age=0,cancel_requested=o['state']=='CANCEL_PENDING',requested=o['qty'],filled_total=0.,attempt_signature=None,origin='EXISTING').items():o.setdefault(key,v)
    k.refresh(n)
    return n


def view(n: dict) -> dict:
    s=n['state'];inv=s['inv'];cost=s['cost'];owners=s['owners']
    pay={a:float(inv[a]-cost) for a in k.SIDES}
    pending={a:sum(float(o['qty']*o['limit']) for o in owners if o['side']==a) for a in k.SIDES}
    bound={a:pay[a]-pending['DOWN' if a=='UP' else 'UP'] for a in k.SIDES}
    return {'inventory':dict(inv),'cost':cost,'payoff':pay,'floor':min(pay.values()),'best':max(pay.values()),
            'pending_cash':pending,'pending_bound':bound,'pending_floor':min(bound.values()),
            'owners':copy.deepcopy(owners),'owner_count':len(owners),'gap':abs(inv['UP']-inv['DOWN']),
            'public':dict(n['public']),'depth':dict(n['depth']),
            'receipts':copy.deepcopy(n.get('event_receipts',[]))}


def actions(n: dict) -> list[dict]:
    ps,_=encode_proposals(k.proposals(n),.01)
    return ps


def features(n: dict, p: dict, c: dict) -> dict:
    v=view(n);q={s:sum(o['qty'] for o in p['orders'] if o['side']==s) for s in k.SIDES}
    cash=sum(o['qty']*o['price'] for o in p['orders'])
    after={s:v['payoff'][s]+q[s]-cash for s in k.SIDES}
    major,minor,*_=k.context(n)
    return {**{x:c[x] for x in ('add_weight','repair_weight','risk_weight','cost_weight')},
            'd_floor':min(after.values())-v['floor'],'d_best':max(after.values())-v['best'],
            'd_up':after['UP']-v['payoff']['UP'],'d_down':after['DOWN']-v['payoff']['DOWN'],
            'cash':cash,'pending_added':cash,'cancel_cash':sum(o['qty']*o['limit'] for o in n['state']['owners'] if o['side']==p['cancel_side']),
            'floor':v['floor'],'best':v['best'],'gap':v['gap'],'pending_cash':sum(v['pending_cash'].values()),
            'repair_gap':max(0.,v['gap']-n['state']['pending_qty'][minor]),
            'is_add':float(p['id'] in (7,9)), 'is_repair':float(bool(p['orders']) and all(o['side']==minor for o in p['orders']) and p['id'] not in (7,9)),
            'is_active':float(any(o['route']=='ACTIVE' for o in p['orders'])),'is_cancel':float(p['cancel_side'] is not None),
            'is_keep':float(p['id']==0)}


def proposals_view(n: dict, c: dict) -> list[dict]:
    f=Formula(c['formula']);out=[]
    for p in actions(n):
        feat=features(n,p,c)
        out.append({**p,'geometry':{x:feat[x] for x in ('d_floor','d_best','cash')},'score':f(feat) if p['legal'] else None})
    return out


def stress(name: str, i: int) -> dict:
    if name not in SCENARIOS: raise ValueError('Invalid scenario')
    if name=='UP': return dict(shift=.02,fraction=1.,ack='LATE',slip=0.)
    if name=='DOWN': return dict(shift=-.02,fraction=1.,ack='LATE',slip=0.)
    if name=='NO_FILL': return dict(shift=0.,fraction=0.,ack='UNKNOWN',slip=0.)
    if name=='CANCEL_RACE': return dict(shift=-.02 if i%2==0 else .02,fraction=1.,ack='LATE' if i%3==0 else 'EARLY',slip=0.)
    return dict(shift=(.01 if i%2 else -.01),fraction=.5,ack='LATE',slip=.01 if name=='PARTIAL' else 0.,widen=.02 if name=='PARTIAL' and i%3==0 else 0.)


def stop_action(n: dict) -> dict:
    # No synthetic terminal acknowledgement. UNKNOWN owners are kept by the kernel.
    pending=[o for o in n['state']['owners'] if not o.get('cancel_requested') and o['state']!='UNKNOWN']
    side=max(pending,key=lambda o:o['qty']*o['limit'])['side'] if pending else None
    return dict(id=-1,name='STOP_CANCEL_ONE_SIDE' if side else 'STOP_WAIT_TERMINAL',orders=[],cancel_side=side,legal=True,reason='OPERATOR_STOP')


def apply(n: dict, p: dict, scenario: str, index: int) -> dict:
    if not p['legal']: raise ValueError('拒絕不合法動作：'+p['reason'])
    z=k.step(n,p,stress(scenario,index))
    for s in k.SIDES:
        if z['state']['inv'][s] < n['state']['inv'][s]-1e-7: raise ValueError('Inventory moved without BUY receipt')
    receipt_cash=sum(r['qty']*r['price'] for r in z.get('event_receipts',[]))
    if abs(z['state']['cost']-n['state']['cost']-receipt_cash)>1e-6: raise ValueError('Receipt/cost mismatch')
    for s in k.SIDES:
        qty=sum(r['qty'] for r in z.get('event_receipts',[]) if r['side']==s)
        if abs(z['state']['inv'][s]-n['state']['inv'][s]-qty)>1e-6: raise ValueError('Receipt/inventory mismatch')
    return z


def replay_prefix(root: dict, c: dict, scenario: str, history: list) -> tuple[dict,bool,list]:
    if not isinstance(history,list) or len(history)>12: raise ValueError('手動歷史最多 12 個事件。')
    n=prepare(root,c);latched=False;rows=[{'event':0,'action':'REAL_ROOT','state':view(n)}]
    for i,choice in enumerate(history):
        if choice=='STOP': latched=True
        if latched: p=stop_action(n)
        else:
            if type(choice)!=int or not 0<=choice<len(actions(n)): raise ValueError('Invalid manual action')
            p=actions(n)[choice]
        n=apply(n,p,scenario,i)
        rows.append({'event':i+1,'action':p['name'],'state':view(n),'assumption':stress(scenario,i)})
    return n,latched,rows


def eligible_stop(n: dict,c: dict) -> bool:
    v=view(n);pay=v['pending_bound'] if c['trigger']=='PENDING_SAFE' else v['payoff']
    return max(pay.values())>=c['best_trigger'] and min(pay.values())>=-c['max_loss']


def choose(n: dict,c: dict,latched: bool) -> tuple[dict,bool]:
    latched=latched or (c['lock_mode']!='OFF' and eligible_stop(n,c))
    if latched and c['lock_mode']=='STOP_ALL': return stop_action(n),True
    ps=proposals_view(n,c)
    if latched and c['lock_mode']=='REPAIR_ONLY':
        # Conservative hypothetical filter, NOT a claim that pending orders are gone.
        ps=[p for p in ps if not p['orders'] or (features(n,p,c)['is_repair'] and features(n,p,c)['d_floor']>=-1e-8)]
    ps=[p for p in ps if p['legal']]
    return max(ps,key=lambda p:(p['score'],-p['id'])),latched


def experiment(root: dict,c: dict,scenario: str,history: list,horizon: int=8) -> dict:
    if type(horizon)!=int or not 1<=horizon<=8: raise ValueError('分支長度為 1–8 個事件。')
    n,manual_stop,prefix=replay_prefix(root,c,scenario,history)
    a=copy.deepcopy(n);b=copy.deepcopy(n);locked=manual_stop
    if manual_stop: c={**c,'lock_mode':'STOP_ALL'}
    ar=[{'event':len(history),'action':'BRANCH_ROOT','state':view(a)}];br=copy.deepcopy(ar)
    for j in range(horizon):
        i=len(history)+j
        pa=stop_action(a) if manual_stop else actions(a)[0]
        pb,locked=choose(b,c,locked)
        a=apply(a,pa,scenario,i);b=apply(b,pb,scenario,i)
        ar.append({'event':i+1,'action':pa['name'],'state':view(a)})
        br.append({'event':i+1,'action':pb['name'],'state':view(b),'stop_latched':locked})
    av,bv=view(a),view(b)
    return {'version':VERSION,'evidence':'HYPOTHETICAL_RECEIPT_STRESS_NOT_NATIVE_HFT',
            'root_id':root['id'],'market':root['market'],'config':c,'scenario':scenario,'horizon':horizon,'history':history,
            'baseline_label':'KEEP：保留既有掛單，非 V49/R65 神經控制器',
            'candidate_label':'你的公式控制器：當前狀態幾何評分，非市場預測',
            'baseline':ar,'candidate':br,'prefix':prefix,
            'delta':{s:bv['payoff'][s]-av['payoff'][s] for s in k.SIDES},
            'floor_delta':bv['floor']-av['floor'],'pending_floor_delta':bv['pending_floor']-av['pending_floor'],
            'training_eligible':False,'realized_pnl_claim':False,'native_hft':False,'live_authority':False}


def resolve_request(roots: list[dict], body: dict) -> tuple[dict,dict,str,list]:
    if not isinstance(body,dict): raise ValueError('Invalid JSON request')
    root=next((r for r in roots if r['id']==body.get('root_id')),None)
    if root is None: raise ValueError('找不到起點。')
    c=config(body.get('config',{}));scenario=body.get('scenario','WHIPSAW')
    if scenario not in SCENARIOS: raise ValueError('找不到情境。')
    history=body.get('history',[])
    return root,c,scenario,history
