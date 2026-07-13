"""Deterministic phase state machine for the multi-agent test-design runtime.

The state machine is deliberately unaware of agents and validators.  Callers run
the relevant work, run the deterministic gate, and only then call
``validate_phase``.  This keeps phase completion out of model control.

Normal progression is strictly::

    discovery -> plan -> risk -> cases -> review -> delivery

Rework is the only backward transition.  It invalidates the target phase and
every downstream phase so stale case or delivery artifacts cannot be reused.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping


class Phase(str, Enum):
    DISCOVERY = "discovery"
    PLAN = "plan"
    RISK = "risk"
    CASES = "cases"
    REVIEW = "review"
    DELIVERY = "delivery"


PHASE_ORDER: tuple[Phase, ...] = tuple(Phase)
_PHASE_INDEX = {phase: index for index, phase in enumerate(PHASE_ORDER)}


class StateTransitionError(ValueError):
    """Raised when a caller tries to skip, repeat, or bypass a phase gate."""


@dataclass(frozen=True)
class StateChange:
    """Auditable result of one state mutation."""

    previous_state: str
    state: str
    revision: int
    phase: str | None = None
    invalidated_phases: tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "previous_state": self.previous_state,
            "state": self.state,
            "revision": self.revision,
            "phase": self.phase,
            "invalidated_phases": list(self.invalidated_phases),
            "reason": self.reason,
        }


def _coerce_phase(value: Phase | str) -> Phase:
    try:
        return value if isinstance(value, Phase) else Phase(str(value).strip().lower())
    except ValueError as exc:
        raise StateTransitionError(f"unknown orchestration phase: {value!r}") from exc


def _running_state(phase: Phase) -> str:
    return f"{phase.value.upper()}_RUNNING"


def _validated_state(phase: Phase) -> str:
    return f"{phase.value.upper()}_VALIDATED"


class OrchestrationStateMachine:
    """Strict, serializable workflow state.

    Only this object should decide phase progression.  The validated phase list
    is cumulative and is intentionally included in the serialized checkpoint so
    a resumed process cannot infer success merely from files being present.
    """

    def __init__(self) -> None:
        self.state = "INIT"
        self.revision = 0
        self._validated: list[Phase] = []
        self._active_phase: Phase | None = None
        self._suspended_state: str | None = None
        self._suspended_phase: Phase | None = None
        self.failure_reason = ""

    @property
    def active_phase(self) -> Phase | None:
        return self._active_phase

    @property
    def validated_phases(self) -> tuple[Phase, ...]:
        return tuple(self._validated)

    @property
    def is_terminal(self) -> bool:
        return self.state in {"COMPLETE", "FAILED"}

    @property
    def next_phase(self) -> Phase | None:
        """Return the only phase that can be started without rework."""

        if self.state == "INIT":
            return Phase.DISCOVERY
        if self.state == "EXTERNAL_BLOCKED" or self.state.endswith("_RUNNING"):
            return None
        if self.state == "FAILED" or self.state == "COMPLETE":
            return None
        if self._validated:
            index = _PHASE_INDEX[self._validated[-1]] + 1
            return PHASE_ORDER[index] if index < len(PHASE_ORDER) else None
        return Phase.DISCOVERY

    def _change(
        self,
        previous: str,
        *,
        phase: Phase | None = None,
        invalidated: Iterable[Phase] = (),
        reason: str = "",
    ) -> StateChange:
        self.revision += 1
        return StateChange(
            previous_state=previous,
            state=self.state,
            revision=self.revision,
            phase=phase.value if phase else None,
            invalidated_phases=tuple(item.value for item in invalidated),
            reason=reason,
        )

    def start_phase(self, phase: Phase | str) -> StateChange:
        """Start exactly the next phase; phases cannot be skipped or repeated."""

        phase = _coerce_phase(phase)
        expected = self.next_phase
        if expected is None or phase != expected:
            expected_text = expected.value if expected else "none"
            raise StateTransitionError(
                f"cannot start {phase.value} from {self.state}; expected next phase is {expected_text}"
            )
        previous = self.state
        self._active_phase = phase
        self.state = _running_state(phase)
        self.failure_reason = ""
        return self._change(previous, phase=phase)

    # Friendly alias for orchestration callers.
    begin_phase = start_phase

    def validate_phase(self, phase: Phase | str) -> StateChange:
        """Promote a running phase after its deterministic gate has passed."""

        phase = _coerce_phase(phase)
        if self.state != _running_state(phase) or self._active_phase != phase:
            raise StateTransitionError(
                f"cannot validate {phase.value} from {self.state}; phase must be running"
            )
        index = _PHASE_INDEX[phase]
        expected_prefix = list(PHASE_ORDER[:index])
        if self._validated != expected_prefix:
            raise StateTransitionError(
                f"cannot validate {phase.value}; validated prefix is not complete"
            )
        previous = self.state
        self._validated.append(phase)
        self._active_phase = None
        self.state = _validated_state(phase)
        return self._change(previous, phase=phase)

    mark_phase_validated = validate_phase

    def request_rework(self, phase: Phase | str, reason: str) -> StateChange:
        """Downgrade to ``phase`` and invalidate it plus all downstream work.

        Rework may be requested while a phase is running, after validation, or
        even after completion.  It may not jump forward to a phase that has not
        been reached.  The returned invalidation list is always the complete
        target suffix, allowing cleanup code to behave identically on every run.
        """

        phase = _coerce_phase(phase)
        reason = str(reason).strip()
        if not reason:
            raise StateTransitionError("rework requires a non-empty reason")
        if self.state in {"INIT", "FAILED"}:
            raise StateTransitionError(f"cannot request rework from {self.state}")

        reached_index = -1
        if self._validated:
            reached_index = _PHASE_INDEX[self._validated[-1]]
        if self._active_phase is not None:
            reached_index = max(reached_index, _PHASE_INDEX[self._active_phase])
        if self.state == "COMPLETE":
            reached_index = len(PHASE_ORDER) - 1
        if self.state == "EXTERNAL_BLOCKED" and self._suspended_phase is not None:
            reached_index = max(reached_index, _PHASE_INDEX[self._suspended_phase])
        if _PHASE_INDEX[phase] > reached_index:
            raise StateTransitionError(
                f"cannot request forward rework to unreached phase {phase.value}"
            )

        previous = self.state
        target_index = _PHASE_INDEX[phase]
        self._validated = [
            item for item in self._validated if _PHASE_INDEX[item] < target_index
        ]
        self._active_phase = phase
        self._suspended_state = None
        self._suspended_phase = None
        self.state = _running_state(phase)
        self.failure_reason = ""
        invalidated = PHASE_ORDER[target_index:]
        return self._change(
            previous,
            phase=phase,
            invalidated=invalidated,
            reason=reason,
        )

    rework = request_rework

    def block_external(self, reason: str) -> StateChange:
        """Suspend the current running phase for a genuine external dependency."""

        reason = str(reason).strip()
        if not reason:
            raise StateTransitionError("external block requires a non-empty reason")
        if self._active_phase is None or not self.state.endswith("_RUNNING"):
            raise StateTransitionError("only a running phase can be externally blocked")
        previous = self.state
        self._suspended_state = self.state
        self._suspended_phase = self._active_phase
        self.state = "EXTERNAL_BLOCKED"
        self.failure_reason = reason
        return self._change(previous, phase=self._active_phase, reason=reason)

    def resume_external(self) -> StateChange:
        if self.state != "EXTERNAL_BLOCKED" or not self._suspended_state:
            raise StateTransitionError("workflow is not externally blocked")
        previous = self.state
        self.state = self._suspended_state
        phase = self._suspended_phase
        self._suspended_state = None
        self._suspended_phase = None
        self.failure_reason = ""
        return self._change(previous, phase=phase)

    def fail(self, reason: str) -> StateChange:
        reason = str(reason).strip()
        if not reason:
            raise StateTransitionError("failure requires a non-empty reason")
        if self.state in {"COMPLETE", "FAILED"}:
            raise StateTransitionError(f"cannot fail workflow from {self.state}")
        previous = self.state
        phase = self._active_phase
        self.state = "FAILED"
        self.failure_reason = reason
        return self._change(previous, phase=phase, reason=reason)

    def complete(self) -> StateChange:
        if self.state != _validated_state(Phase.DELIVERY):
            raise StateTransitionError(
                "workflow can complete only after DELIVERY_VALIDATED"
            )
        if tuple(self._validated) != PHASE_ORDER:
            raise StateTransitionError("workflow cannot complete with an incomplete phase prefix")
        previous = self.state
        self.state = "COMPLETE"
        self._active_phase = None
        return self._change(previous, phase=Phase.DELIVERY)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "state": self.state,
            "revision": self.revision,
            "active_phase": self._active_phase.value if self._active_phase else None,
            "validated_phases": [phase.value for phase in self._validated],
            "suspended_state": self._suspended_state,
            "suspended_phase": (
                self._suspended_phase.value if self._suspended_phase else None
            ),
            "failure_reason": self.failure_reason,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "OrchestrationStateMachine":
        if value.get("schema_version") != 1:
            raise StateTransitionError("unsupported state-machine schema_version")
        machine = cls()
        machine.state = str(value.get("state", ""))
        revision = value.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise StateTransitionError("state-machine revision must be a non-negative integer")
        machine.revision = revision

        raw_validated = value.get("validated_phases")
        if not isinstance(raw_validated, list):
            raise StateTransitionError("validated_phases must be a list")
        machine._validated = [_coerce_phase(item) for item in raw_validated]
        if machine._validated != list(PHASE_ORDER[: len(machine._validated)]):
            raise StateTransitionError("validated_phases must be a strict phase prefix")

        raw_active = value.get("active_phase")
        machine._active_phase = _coerce_phase(raw_active) if raw_active else None
        raw_suspended_phase = value.get("suspended_phase")
        machine._suspended_phase = (
            _coerce_phase(raw_suspended_phase) if raw_suspended_phase else None
        )
        raw_suspended_state = value.get("suspended_state")
        machine._suspended_state = (
            str(raw_suspended_state) if raw_suspended_state else None
        )
        machine.failure_reason = str(value.get("failure_reason", "") or "")
        machine._validate_restored_state()
        return machine

    def _validate_restored_state(self) -> None:
        allowed_states = {"INIT", "COMPLETE", "FAILED", "EXTERNAL_BLOCKED"}
        allowed_states.update(_running_state(phase) for phase in PHASE_ORDER)
        allowed_states.update(_validated_state(phase) for phase in PHASE_ORDER)
        if self.state not in allowed_states:
            raise StateTransitionError(f"unknown restored workflow state: {self.state!r}")

        if self.state == "INIT":
            valid = not self._validated and self._active_phase is None
        elif self.state.endswith("_RUNNING"):
            valid = (
                self._active_phase is not None
                and self.state == _running_state(self._active_phase)
                and self._validated == list(PHASE_ORDER[: _PHASE_INDEX[self._active_phase]])
            )
        elif self.state.endswith("_VALIDATED"):
            valid = (
                self._active_phase is None
                and bool(self._validated)
                and self.state == _validated_state(self._validated[-1])
            )
        elif self.state == "COMPLETE":
            valid = self._active_phase is None and tuple(self._validated) == PHASE_ORDER
        elif self.state == "EXTERNAL_BLOCKED":
            valid = (
                self._active_phase is not None
                and self._suspended_phase == self._active_phase
                and self._suspended_state == _running_state(self._active_phase)
            )
        else:  # FAILED preserves the last active phase for diagnostics.
            valid = True
        if not valid:
            raise StateTransitionError(
                f"restored state fields are inconsistent with {self.state}"
            )


# Short alias used by callers that already live in the orchestration package.
StateMachine = OrchestrationStateMachine


__all__ = [
    "PHASE_ORDER",
    "OrchestrationStateMachine",
    "Phase",
    "StateChange",
    "StateMachine",
    "StateTransitionError",
]
