from tools.eth_repair_modular.joint_multislot_recoverability import *
p=JointMultiSlotCurrentCoordinateRecoverabilityPolicyV1();cases=[]
def add(name,ctx,pred):
 d=p.evaluate(ctx);cases.append((name,bool(pred(d)),d))
base=lambda slots,ub=.25,db=.55,maxc=4:JointMultiSlotContext(5,5,4,tuple(slots),ub,db,maxc,12)
q=1/.45
add('single_slot_recoverable',base([{'side':'UP','price':.45,'qty':q}]),lambda d:d.recoverable)
add('two_slots_jointly_blocked_correlated_avalanche',base([{'side':'UP','price':.45,'qty':q},{'side':'UP','price':.45,'qty':q}]),lambda d:not d.recoverable and 'DID_NOT_RESTORE' in d.reason)
add('joint_recoverable_good_frontier',base([{'side':'UP','price':.45,'qty':q},{'side':'UP','price':.45,'qty':q}],ub=.2,db=.2),lambda d:d.recoverable)
add('mixed_side_rejected_v1',base([{'side':'UP','price':.45,'qty':q},{'side':'DOWN','price':.45,'qty':q}]),lambda d:not d.recoverable and d.reason=='MIXED_SIDE_NOT_SUPPORTED_V1')
add('invalid_slot_rejected',base([{'side':'UP','price':0,'qty':2}]),lambda d:not d.recoverable and d.reason=='INVALID_SLOT')
add('no_slots_rejected',base([]),lambda d:not d.recoverable and d.reason=='NO_SLOTS')
# Repair-like balanced improvement should pass immediate floor if the candidate itself improves the floor.
add('immediate_nonworse_short_circuit',JointMultiSlotContext(5,8,3,({'side':'UP','price':.2,'qty':3},),.5,.5,4,12),lambda d:d.recoverable and d.reason=='JOINT_FILL_IMMEDIATE_FLOOR_NONWORSE')
# Same two-slot avalanche becomes recoverable if recovery budget is expanded enough.
add('relay_budget_is_explicit',base([{'side':'UP','price':.45,'qty':q},{'side':'UP','price':.45,'qty':q}],maxc=8),lambda d:d.recoverable)
import json
out={'version':'JOINT_MULTISLOT_CURRENT_COORDINATE_RECOVERABILITY_MICROWORLD_V1','passed':sum(x[1] for x in cases),'total':len(cases),'allPass':all(x[1] for x in cases),'cases':[{'name':n,'pass':ok,'reason':d.reason,'floorBefore':d.floor_before,'floorAfterJointFill':d.floor_after_joint_fill,'jointRepairDebt':d.joint_repair_debt,'debtSide':d.debt_side,'recursiveRecoveredStep':None if d.recursive is None else d.recursive.recovered_step,'recursiveTerminalFloor':None if d.recursive is None else d.recursive.terminal_floor} for n,ok,d in cases]};print(json.dumps(out,ensure_ascii=False))
