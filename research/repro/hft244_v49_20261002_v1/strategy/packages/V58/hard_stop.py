"""Universal research cutoff; does not mutate expiry, ledger, or cancel receipts."""
import ast,atexit,gzip,json,os
from pathlib import Path
MODE=os.environ.get('V12G_STOP290','ON')
assert MODE in ('OFF','ON')
CUTOFF_MS=290000
ROWS=[]

def active(frame,mode=None):
    return (MODE if mode is None else mode)=='ON' and int(frame['t'])>=int(frame['start'])+CUTOFF_MS

def accounts(ledger):
    return {str(i):{k:float(v) for k,v in ledger.account(i).items() if isinstance(v,(int,float))} for i in (1,2)}

def verify(frame,producer,operations):
    if not active(frame):return
    ledger=frame['ledger'];owners=[];expected=[]
    before=accounts(ledger)
    for key,c in ledger.carriers.items():
        if c.state=='TERMINAL':continue
        can=bool(frame['cancellable'].get(key,False))
        owners.append(dict(key=key,state=c.state,route=c.route,qty=float(c.qty),filled=float(c.filled),
            remaining=max(0.,float(c.qty)-float(c.filled)),limit=float(c.limit),cancellable=can))
        if c.state!='CANCEL_PENDING' and can:expected.append(key)
    actual=[o['key'] for o in operations if o['kind']=='CANCEL']
    row=dict(t=int(frame['t']),index=int(frame['index']),start=int(frame['start']),end=int(frame['end']),
        elapsed_ms=int(frame['t'])-int(frame['start']),owners=owners,expected_cancels=sorted(expected),
        operations=[dict(o) for o in operations],reservations_before=before,reservations_after=accounts(ledger))
    ROWS.append(row)
    assert all(o['kind']=='CANCEL' for o in operations),'STOP290_NEW_OR_KEEP_ESCAPE'
    assert len(actual)==len(set(actual)) and sorted(actual)==sorted(expected),'STOP290_CANCEL_COVERAGE'
    assert row['reservations_before']==row['reservations_after'],'STOP290_SYNTHETIC_RESERVATION_RELEASE'

def instrument(source,mode=None):
    if (MODE if mode is None else mode)=='OFF':return source
    tree=ast.parse(source);counts=dict(produce=0,envelope=0,appenders=0,guard=0)
    gate="__import__('hard_stop').active(f)"
    class Rewrite(ast.NodeTransformer):
        def visit_ClassDef(self,node):
            if node.name!='Policy':return self.generic_visit(node)
            for fn in node.body:
                if isinstance(fn,ast.FunctionDef) and fn.name=='produce':
                    for branch in fn.body:
                        if isinstance(branch,ast.If) and ast.unparse(branch.test)=="f['t'] >= f['end']":
                            counts['produce']+=1
                            branch.test=ast.BoolOp(ast.Or(),[branch.test,ast.parse(gate,mode='eval').body])
                            class Reason(ast.NodeTransformer):
                                def visit_Constant(self,x):
                                    if x.value=='ACTUAL_MARKET_END':
                                        return ast.parse("'USER_STOP290_CANCEL_ALL' if "+gate+" else 'ACTUAL_MARKET_END'",mode='eval').body
                                    return x
                            branch.body=[Reason().visit(x) for x in branch.body]
            return self.generic_visit(node)
        def visit_FunctionDef(self,node):
            if node.name!='envelope':return self.generic_visit(node)
            counts['envelope']+=1;body=[]
            for stmt in node.body:
                if isinstance(stmt,ast.Assign) and isinstance(stmt.value,ast.Call):
                    name=ast.unparse(stmt.value.func)
                    if name in ('producer.bridge.service','producer.general_finite_active.apply'):
                        assert len(stmt.targets)==1 and isinstance(stmt.targets[0],ast.Name) and stmt.targets[0].id=='ops'
                        body.append(ast.If(test=ast.UnaryOp(ast.Not(),ast.parse(gate,mode='eval').body),body=[stmt],orelse=[]))
                        counts['appenders']+=1;continue
                if isinstance(stmt,ast.Expr) and isinstance(stmt.value,ast.Call) and ast.unparse(stmt.value.func)=='govern_plan':
                    body.extend(ast.parse("__import__('hard_stop').verify(f, producer, ops)").body);counts['guard']+=1
                body.append(stmt)
            node.body=body;return node
    tree=Rewrite().visit(tree)
    assert counts==dict(produce=1,envelope=1,appenders=4,guard=1),counts
    ast.fix_missing_locations(tree)
    result=ast.unparse(tree);compile(result,'stop290_policy','exec')
    return result

def finish(out):
    with gzip.GzipFile(filename=str(Path(out)/'stop290_trace.json.gz'),mode='wb',mtime=0) as f:
        f.write(json.dumps(dict(mode=MODE,cutoff_ms=CUTOFF_MS,rows=ROWS),sort_keys=True,separators=(',',':')).encode())
    return dict(mode=MODE,cutoff_ms=CUTOFF_MS,frames=len(ROWS),cancel_requests=sum(len(x['operations']) for x in ROWS),
        first_elapsed_ms=ROWS[0]['elapsed_ms'] if ROWS else None,all_routes=True,repair_exemptions=False,live_eligible=False)

def save_exit():
    out=os.environ.get('BTC5M_LAN_RESULT_DIR')
    if out and Path(out).is_dir():finish(out)
atexit.register(save_exit)
