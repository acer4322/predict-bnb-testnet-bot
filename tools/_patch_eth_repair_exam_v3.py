from pathlib import Path
p=Path('tools/run_eth_repair_functional_exam_v3_objective_handoff.py')
s=p.read_text(encoding='utf-8')

s=s.replace("self.activeObjective=None\n        self.objectiveSwitches=0;self.objectiveCompletions=0;self.reauthBlocks=0;self.remainingCapBlocks=0;self.globalOwnershipBlocks=0\n        self.lifecyclePredictions=[]",
"self.activeObjective=None\n        self.nextObjectiveId=1\n        self.awaitingReentry=False\n        self.submitRoleAuthorized={}\n        self.authorizedSubmitWithTruthRoleMismatch=0\n        self.objectiveSwitches=0;self.objectiveCompletions=0;self.objectiveInvalidations=0;self.reauthBlocks=0;self.remainingCapBlocks=0;self.globalOwnershipBlocks=0\n        self.lifecyclePredictions=[]")

needle="    def auth_cost(self):\n        return float(self.cost)+sum(float(x['qty'])*float(x['price']) for x in self.pendingObs)\n"
insert="""    def auth_cost(self):
        return float(self.cost)+sum(float(x['qty'])*float(x['price']) for x in self.pendingObs)
    def reserved_authoritative(self,side):
        # One responsibility, one count: local pre-ack and venue-live are two views of the same order.
        total=0.0
        for key,o in self.orders.items():
            if o.get('side')!=side:continue
            snap=self.snap(o);status=snap.get('status')
            if v1.live(status):
                total+=max(0.0,float(snap.get('leavesQty') or 0.0))
            elif status is None and key in self.localPending[side]:
                total+=max(0.0,float(self.localPending[side][key].get('remaining',0.0)))
        return total
    def _new_objective(self,role,side):
        obj={'id':self.nextObjectiveId,'role':role,'side':side};self.nextObjectiveId+=1;return obj
    def _cancel_objective_carriers(self,t,objective_id):
        for key,o in self.orders.items():
            if o.get('objective_id')!=objective_id:continue
            snap=self.snap(o)
            if not v1.live(snap.get('status')):continue
            cur=self.bt.orders(0).get(o['n'])
            if cur is not None and bool(cur.cancellable):
                self.cancelRequestedAt.setdefault(key,int(t))
                try:self.bt.cancel(0,o['n'],False)
                except Exception:pass
    def _reconcile_objective(self,t):
        obj=self.activeObjective
        if not obj or obj.get('role')!='REPAIR':return
        ai=self.auth_inv();side=obj.get('side');opp='DOWN' if side=='UP' else 'UP'
        # Structural completion/invalidation: the side this objective was repairing is no longer weak.
        if side is None or ai[side]>=ai[opp]-EPS:
            oid=obj['id'];self.objectiveCompletions+=1
            if ai[side]>ai[opp]+EPS:self.objectiveInvalidations+=1
            self.activeObjective=None;self.awaitingReentry=True
            self._cancel_objective_carriers(t,oid)
    def submit(self,t,side,p,q):
        n_before=self.n
        truth_role=ex1.role_from_inv(self.truthInv,side)
        auth_role=getattr(self,'_pendingAuthorizedRole',None) or truth_role
        auth_oid=getattr(self,'_pendingAuthorizedObjectiveId',None)
        ok=super().submit(t,side,p,q)
        if not ok:return False
        key=f'{side}_{n_before}'
        self.submitRoleAuthorized[key]=auth_role
        if key in self.orders:
            self.orders[key]['objective_role']=auth_role;self.orders[key]['objective_id']=auth_oid
        if auth_role!=truth_role:self.authorizedSubmitWithTruthRoleMismatch+=1
        self._pendingAuthorizedRole=None;self._pendingAuthorizedObjectiveId=None
        return True
"""
if needle not in s: raise SystemExit('auth_cost needle missing')
s=s.replace(needle,insert)

s=s.replace("submit_role=self.submitRoleObserved.get(key)","submit_role=self.submitRoleAuthorized.get(key,self.submitRoleObserved.get(key))")

s=s.replace("        self._apply_due_observations(t)\n    def _visible_history", "        self._apply_due_observations(t)\n        self._reconcile_objective(t)\n    def _visible_history")

old="""        # Persistent objective: role transition requires the corresponding learned semantic authorization.
        if self.activeObjective is None:self.activeObjective={'role':desired_role,'side':weak if desired_role=='REPAIR' else dom}
        elif self.activeObjective['role']!=desired_role:
            allow=False
            if self.activeObjective['role']=='REPAIR' and desired_role=='EXPAND':allow=(p['switch']>=0.5 and p['reentry']>=0.5)
            elif self.activeObjective['role']=='EXPAND' and desired_role=='REPAIR':allow=(p['switch']>=0.5 and p['repair']>=0.5)
            if allow:self.objectiveSwitches+=1;self.activeObjective={'role':desired_role,'side':weak if desired_role=='REPAIR' else dom}
            else:self.reauthBlocks+=1;desired_role=self.activeObjective['role']
"""
new="""        # Persistent objective: completion is structural; a new EXPAND after completed REPAIR still needs learned re-entry authorization.
        self._reconcile_objective(t)
        if self.activeObjective is None:
            if desired_role=='EXPAND' and self.awaitingReentry:
                if p['reentry']<0.5:self.reauthBlocks+=1;return None
                self.awaitingReentry=False
            self.activeObjective=self._new_objective(desired_role,weak if desired_role=='REPAIR' else dom)
        elif self.activeObjective['role']!=desired_role:
            allow=False
            if self.activeObjective['role']=='REPAIR' and desired_role=='EXPAND':allow=(p['switch']>=0.5 and p['reentry']>=0.5)
            elif self.activeObjective['role']=='EXPAND' and desired_role=='REPAIR':allow=(p['switch']>=0.5 and p['repair']>=0.5)
            if allow:
                self.objectiveSwitches+=1;self.activeObjective=self._new_objective(desired_role,weak if desired_role=='REPAIR' else dom)
            else:self.reauthBlocks+=1;desired_role=self.activeObjective['role']
"""
if old not in s: raise SystemExit('objective transition needle missing')
s=s.replace(old,new)

s=s.replace("            if qty<=EPS:self.objectiveCompletions+=1;self.activeObjective=None;self.remainingCapBlocks+=1;return None",
            "            if qty<=EPS:self._reconcile_objective(t);self.remainingCapBlocks+=1;return None")

s=s.replace("        return side,qty,p\n", "        return side,qty,p,desired_role,self.activeObjective.get('id') if self.activeObjective else None\n")
s=s.replace("            side,qty,_=z;p=float(qv[side]['bid']);legal=1/p if p>EPS else 1e9",
            "            side,qty,_,authorized_role,objective_id=z;p=float(qv[side]['bid']);legal=1/p if p>EPS else 1e9")
s=s.replace("            qty=max(qty,legal);qty=min(qty,12.)\n            self.submit(t,side,p,qty)",
            "            qty=max(qty,legal);qty=min(qty,12.)\n            self._pendingAuthorizedRole=authorized_role;self._pendingAuthorizedObjectiveId=objective_id\n            self.submit(t,side,p,qty)")

s=s.replace("'objectiveSwitches':self.objectiveSwitches,'objectiveCompletions':self.objectiveCompletions,'reauthBlocks':self.reauthBlocks",
            "'objectiveSwitches':self.objectiveSwitches,'objectiveCompletions':self.objectiveCompletions,'objectiveInvalidations':self.objectiveInvalidations,'authorizedSubmitWithTruthRoleMismatch':self.authorizedSubmitWithTruthRoleMismatch,'observedSubmitWithTruthRoleMismatchDiagnostic':self.acceptedSubmitWithObservedTruthRoleMismatch,'reauthBlocks':self.reauthBlocks")

s=s.replace("'objectiveSwitches':int(sm('objectiveSwitches')),'objectiveCompletions':int(sm('objectiveCompletions')),'reauthBlocks':int(sm('reauthBlocks'))",
            "'objectiveSwitches':int(sm('objectiveSwitches')),'objectiveCompletions':int(sm('objectiveCompletions')),'objectiveInvalidations':int(sm('objectiveInvalidations')),'authorizedSubmitWithTruthRoleMismatch':int(sm('authorizedSubmitWithTruthRoleMismatch')),'observedSubmitWithTruthRoleMismatchDiagnostic':int(sm('observedSubmitWithTruthRoleMismatchDiagnostic')),'reauthBlocks':int(sm('reauthBlocks'))")

oldgate="'allScenariosNoObservedTruthRoleMismatch':all(s['acceptedSubmitWithObservedTruthRoleMismatch']==0 for s in summaries.values()),'ackLagDoesNotCollapseActivity'"
newgate="'allScenariosNoAuthorizedTruthRoleMismatch':all(s['authorizedSubmitWithTruthRoleMismatch']==0 for s in summaries.values()),'objectiveLifecycleCanComplete':control['objectiveCompletions']>0,'ackLagDoesNotCollapseActivity'"
if oldgate not in s: raise SystemExit('gate needle missing')
s=s.replace(oldgate,newgate)

s=s.replace("'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V2_RESPONSIBILITY_LEDGER'","'version':'ETH_REPAIR_FUNCTIONAL_EXAM_V3_OBJECTIVE_HANDOFF'")
s=s.replace("'learned switch/reentry reauthorization']","'learned switch/reentry reauthorization','deduplicated local/venue ownership','structural repair completion + carrier cancellation','authorized-role first-fill scoring']")
s=s.replace("'Consumed Fresh101 development-only'","'Consumed Fresh101 development-only','V2 DAgger cache reused unchanged; no retraining required when cache is present'")

p.write_text(s,encoding='utf-8')
print('patched-v3',p.stat().st_size)
