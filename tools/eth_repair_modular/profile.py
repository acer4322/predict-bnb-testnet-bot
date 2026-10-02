from __future__ import annotations
from dataclasses import dataclass
from .completion import LegacyShareGapCompletionPolicy, EconomicResponsibilityCompletionPolicy
from .ownership import LegacyExistingThesisOnlyPolicy, RecoverablePreSafeOwnershipPolicy
from .handoff import LegacyAlwaysAllowActiveHandoffPolicy, RecoverabilityActiveHandoffPolicy
from .generation import LegacyRecursiveGenerationPolicy, SingleResponsibilityGenerationPolicy, PersistentDebtChildEpochGenerationPolicy
from .repair_execution import LegacyV36RepairExecutionPolicy, RecursiveCompositeRepairExecutionPolicy
from .scheduler import EventCompletedOnlySchedulerPolicy, ContinuousResponsibilitySchedulerPolicy
from .responsibility_frontier import AuthoritativeRepairResponsibilityFrontierV2
from .ownership_transition_guard import ProspectiveOwnershipTransitionGuardV1

@dataclass
class ModularPolicyProfile:
    name: str
    completion: object
    ownership: object
    handoff: object
    generation: object
    repair_execution: object | None = None
    scheduler: object | None = None
    responsibility_frontier: object | None = None
    ownership_transition_guard: object | None = None

    def describe(self):
        out = {
            'profile': self.name,
            'completion': getattr(self.completion,'name',type(self.completion).__name__),
            'ownership': getattr(self.ownership,'name',type(self.ownership).__name__),
            'handoff': getattr(self.handoff,'name',type(self.handoff).__name__),
            'generation': getattr(self.generation,'name',type(self.generation).__name__),
        }
        if self.repair_execution is not None:
            out['repairExecution'] = getattr(self.repair_execution,'name',type(self.repair_execution).__name__)
        if self.scheduler is not None:
            out['scheduler'] = getattr(self.scheduler,'name',type(self.scheduler).__name__)
        if self.responsibility_frontier is not None:
            out['responsibilityFrontier'] = getattr(self.responsibility_frontier,'name',type(self.responsibility_frontier).__name__)
        if self.ownership_transition_guard is not None:
            out['ownershipTransitionGuard'] = getattr(self.ownership_transition_guard,'name',type(self.ownership_transition_guard).__name__)
        return out

def legacy_v76_profile() -> ModularPolicyProfile:
    return ModularPolicyProfile(
        name='LEGACY_RESEARCH_CONTROL_V1',
        completion=LegacyShareGapCompletionPolicy(),
        ownership=LegacyExistingThesisOnlyPolicy(),
        handoff=LegacyAlwaysAllowActiveHandoffPolicy(),
        generation=LegacyRecursiveGenerationPolicy(),
        repair_execution=LegacyV36RepairExecutionPolicy(),
        scheduler=EventCompletedOnlySchedulerPolicy(),
    )

def economic_v1_profile() -> ModularPolicyProfile:
    return ModularPolicyProfile(
        name='ECONOMIC_MANAGEMENT_V1',
        completion=EconomicResponsibilityCompletionPolicy(),
        ownership=RecoverablePreSafeOwnershipPolicy(),
        handoff=RecoverabilityActiveHandoffPolicy(),
        generation=SingleResponsibilityGenerationPolicy(),
        repair_execution=LegacyV36RepairExecutionPolicy(),
        scheduler=EventCompletedOnlySchedulerPolicy(),
    )

def economic_recursive_execution_v2_profile() -> ModularPolicyProfile:
    return ModularPolicyProfile(
        name='ECONOMIC_MANAGEMENT_RECURSIVE_EXECUTION_V2',
        completion=EconomicResponsibilityCompletionPolicy(),
        ownership=RecoverablePreSafeOwnershipPolicy(),
        handoff=RecoverabilityActiveHandoffPolicy(),
        generation=SingleResponsibilityGenerationPolicy(),
        repair_execution=RecursiveCompositeRepairExecutionPolicy(),
        scheduler=EventCompletedOnlySchedulerPolicy(),
    )

def economic_continuous_scheduler_shadow_v1_profile() -> ModularPolicyProfile:
    """Shadow-only: changes Management reevaluation clock, not action authority."""
    return ModularPolicyProfile(
        name='ECONOMIC_CONTINUOUS_SCHEDULER_SHADOW_V1',
        completion=EconomicResponsibilityCompletionPolicy(),
        ownership=RecoverablePreSafeOwnershipPolicy(),
        handoff=RecoverabilityActiveHandoffPolicy(),
        generation=SingleResponsibilityGenerationPolicy(),
        repair_execution=RecursiveCompositeRepairExecutionPolicy(),
        scheduler=ContinuousResponsibilitySchedulerPolicy(),
    )

def economic_continuous_scheduler_only_v1_profile() -> ModularPolicyProfile:
    """Single-module behavior candidate: ResponsibilityScheduler only."""
    return ModularPolicyProfile(
        name='ECONOMIC_CONTINUOUS_SCHEDULER_ONLY_V1',
        completion=EconomicResponsibilityCompletionPolicy(),
        ownership=RecoverablePreSafeOwnershipPolicy(),
        handoff=RecoverabilityActiveHandoffPolicy(),
        generation=SingleResponsibilityGenerationPolicy(),
        repair_execution=LegacyV36RepairExecutionPolicy(),
        scheduler=ContinuousResponsibilitySchedulerPolicy(),
    )

def economic_continuous_guarded_scheduler_candidate_v1_profile() -> ModularPolicyProfile:
    """Research candidate: continuous clock plus explicit Repair frontier and prospective birth ordering guard."""
    return ModularPolicyProfile(
        name='ECONOMIC_CONTINUOUS_GUARDED_SCHEDULER_CANDIDATE_V1',
        completion=EconomicResponsibilityCompletionPolicy(),
        ownership=RecoverablePreSafeOwnershipPolicy(),
        handoff=RecoverabilityActiveHandoffPolicy(),
        generation=SingleResponsibilityGenerationPolicy(),
        repair_execution=LegacyV36RepairExecutionPolicy(),
        scheduler=ContinuousResponsibilitySchedulerPolicy(),
        responsibility_frontier=AuthoritativeRepairResponsibilityFrontierV2(),
        ownership_transition_guard=ProspectiveOwnershipTransitionGuardV1(),
    )

def economic_partial_debt_child_epoch_only_v1_profile() -> ModularPolicyProfile:
    """Single-module behavior candidate: GenerationUnlockPolicy only."""
    return ModularPolicyProfile(
        name='ECONOMIC_PARTIAL_DEBT_CHILD_EPOCH_ONLY_V1',
        completion=EconomicResponsibilityCompletionPolicy(),
        ownership=RecoverablePreSafeOwnershipPolicy(),
        handoff=RecoverabilityActiveHandoffPolicy(),
        generation=PersistentDebtChildEpochGenerationPolicy(),
        repair_execution=RecursiveCompositeRepairExecutionPolicy(),
        scheduler=EventCompletedOnlySchedulerPolicy(),
    )

def economic_parallel_child_epoch_candidate_v1_profile() -> ModularPolicyProfile:
    """Research candidate: continuous clock + persistent-debt child epochs.

    AllocationLedger/ResponsibilityTransition remain external frozen modules.
    No runtime promotion is implied by constructing this profile.
    """
    return ModularPolicyProfile(
        name='ECONOMIC_PARALLEL_CHILD_EPOCH_CANDIDATE_V1',
        completion=EconomicResponsibilityCompletionPolicy(),
        ownership=RecoverablePreSafeOwnershipPolicy(),
        handoff=RecoverabilityActiveHandoffPolicy(),
        generation=PersistentDebtChildEpochGenerationPolicy(),
        repair_execution=RecursiveCompositeRepairExecutionPolicy(),
        scheduler=ContinuousResponsibilitySchedulerPolicy(),
    )


def economic_continuous_guarded_partial_debt_generation_candidate_v1_profile() -> ModularPolicyProfile:
    """Single-module extension of the guarded continuous scheduler candidate.

    Only GenerationUnlockPolicy differs: persistent old debt remains in the ledger,
    while a new child epoch may be considered after the equivalent physical Expand
    child is gone and confirmed Repair payment progress has occurred. Research only.
    """
    return ModularPolicyProfile(
        name='ECONOMIC_CONTINUOUS_GUARDED_PARTIAL_DEBT_GENERATION_CANDIDATE_V1',
        completion=EconomicResponsibilityCompletionPolicy(),
        ownership=RecoverablePreSafeOwnershipPolicy(),
        handoff=RecoverabilityActiveHandoffPolicy(),
        generation=PersistentDebtChildEpochGenerationPolicy(),
        repair_execution=LegacyV36RepairExecutionPolicy(),
        scheduler=ContinuousResponsibilitySchedulerPolicy(),
        responsibility_frontier=AuthoritativeRepairResponsibilityFrontierV2(),
        ownership_transition_guard=ProspectiveOwnershipTransitionGuardV1(),
    )
