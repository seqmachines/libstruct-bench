from __future__ import annotations

import fcntl
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Callable, Iterator, ParamSpec, TypeVar

from .artifacts import CapabilityImprovementError


P = ParamSpec("P")
R = TypeVar("R")

MUTATION_LOCK_SUFFIX = ".capability-mutation.lock"
SPLIT_JOURNAL_SUFFIX = ".split-freeze-journal.json"
SINGLE_BRANCH_MIGRATION_JOURNAL_SUFFIX = ".single-branch-migration-journal.json"
EXEMPLAR_ADOPTION_JOURNAL_SUFFIX = ".exemplar-adoption-journal.json"


@dataclass(frozen=True)
class ExperimentMutationLease:
    experiment_root: Path
    lock_path: Path
    operation: str
    reentrant: bool
    split_journal_recovery_authorized: bool


@dataclass
class _ProcessLockState:
    process_id: int
    gate: threading.RLock = field(default_factory=threading.RLock)
    depth: int = 0
    descriptor: int | None = None
    outer_operation: str | None = None
    split_journal_recovery_authorized: bool = False


_STATE_GUARD = threading.Lock()
_LOCK_STATES: dict[Path, _ProcessLockState] = {}


def experiment_mutation_lock_path(experiment_root: Path) -> Path:
    root = experiment_root.expanduser().resolve()
    return root.parent / f".{root.name}{MUTATION_LOCK_SUFFIX}"


def split_freeze_journal_path(experiment_root: Path) -> Path:
    root = experiment_root.expanduser().resolve()
    return root.parent / f".{root.name}{SPLIT_JOURNAL_SUFFIX}"


def single_branch_migration_journal_path(experiment_root: Path) -> Path:
    root = experiment_root.expanduser().resolve()
    return root.parent / f".{root.name}{SINGLE_BRANCH_MIGRATION_JOURNAL_SUFFIX}"


def exemplar_adoption_journal_path(experiment_root: Path) -> Path:
    root = experiment_root.expanduser().resolve()
    return root.parent / f".{root.name}{EXEMPLAR_ADOPTION_JOURNAL_SUFFIX}"


@contextmanager
def experiment_mutation_lock(
    experiment_root: Path,
    *,
    operation: str,
    authorize_split_journal_recovery: bool = False,
) -> Iterator[ExperimentMutationLease]:
    """Exclusively serialize every mutation of one capability experiment.

    The lock is re-entrant only for the owning thread. Other threads in the
    process and all other processes fail closed rather than waiting behind an
    interactive or agent-backed mutation.
    """

    root = experiment_root.expanduser().resolve()
    lock_path = experiment_mutation_lock_path(root)
    state = _lock_state(lock_path)
    if not state.gate.acquire(blocking=False):
        raise CapabilityImprovementError(
            f"another capability experiment mutation is already running: {lock_path}"
        )
    entered = False
    try:
        reentrant = state.depth > 0
        if not reentrant:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                os.close(descriptor)
                raise CapabilityImprovementError(
                    "another capability experiment mutation is already running: "
                    f"{lock_path}"
                ) from error
            state.descriptor = descriptor
            state.outer_operation = operation
            state.split_journal_recovery_authorized = authorize_split_journal_recovery
        elif (
            authorize_split_journal_recovery
            and not state.split_journal_recovery_authorized
        ):
            raise CapabilityImprovementError(
                "split-journal recovery cannot be authorized by a nested mutation"
            )
        state.depth += 1
        entered = True
        yield ExperimentMutationLease(
            experiment_root=root,
            lock_path=lock_path,
            operation=operation,
            reentrant=reentrant,
            split_journal_recovery_authorized=(state.split_journal_recovery_authorized),
        )
    finally:
        if entered:
            state.depth -= 1
            if state.depth == 0:
                descriptor = state.descriptor
                state.descriptor = None
                state.outer_operation = None
                state.split_journal_recovery_authorized = False
                if descriptor is not None:
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                    finally:
                        os.close(descriptor)
        state.gate.release()


def guard_experiment_mutation(
    operation: str,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Guard a keyword-only orchestration function with the shared lock."""

    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        @wraps(function)
        def guarded(*args: P.args, **kwargs: P.kwargs) -> R:
            root_value = kwargs.get("experiment_root")
            if root_value is None:
                raise TypeError(
                    f"{function.__name__} requires keyword argument experiment_root"
                )
            root = Path(root_value).expanduser().resolve()
            with experiment_mutation_lock(root, operation=operation) as lease:
                if not lease.split_journal_recovery_authorized:
                    assert_no_interrupted_split_freeze(root)
                return function(*args, **kwargs)

        return guarded

    return decorate


def assert_no_interrupted_split_freeze(experiment_root: Path) -> None:
    journal = split_freeze_journal_path(experiment_root)
    if journal.exists():
        raise CapabilityImprovementError(
            "an interrupted authorized split freeze must be recovered before "
            f"another experiment mutation: {journal}"
        )
    migration_journal = single_branch_migration_journal_path(experiment_root)
    if migration_journal.exists():
        raise CapabilityImprovementError(
            "an interrupted single-branch migration must be recovered before "
            f"another experiment mutation: {migration_journal}"
        )
    adoption_journal = exemplar_adoption_journal_path(experiment_root)
    if adoption_journal.exists():
        raise CapabilityImprovementError(
            "an interrupted exemplar-memory adoption must be recovered before "
            f"another experiment mutation: {adoption_journal}"
        )


def resolve_experiment_root_from_paths(*paths: Path) -> Path | None:
    """Resolve the active experiment owning low-level mutation artifacts."""

    resolved_roots: set[Path] = set()
    for value in paths:
        path = value.expanduser().resolve()
        start = path if path.is_dir() else path.parent
        candidates = [
            ancestor
            for ancestor in (start, *start.parents)
            if (ancestor / "design" / "experiment_manifest.json").is_file()
        ]
        if candidates:
            resolved_roots.add(candidates[-1].resolve())
            continue
        inferred = _root_from_persistent_lock(start)
        if inferred is not None:
            resolved_roots.add(inferred)
    if len(resolved_roots) > 1:
        raise CapabilityImprovementError(
            "low-level mutation artifacts resolve to different experiments"
        )
    return next(iter(resolved_roots), None)


def assert_no_nearby_split_freeze_journal(*paths: Path) -> None:
    """Fail closed when an experiment root is temporarily undiscoverable."""

    journals: set[Path] = set()
    for value in paths:
        path = value.expanduser().resolve()
        start = path if path.is_dir() else path.parent
        for ancestor in (start, *start.parents):
            for journal in ancestor.glob(f".*{SPLIT_JOURNAL_SUFFIX}"):
                name = journal.name
                root_name = name[1 : -len(SPLIT_JOURNAL_SUFFIX)]
                candidate = (ancestor / root_name).resolve()
                if start == candidate or start.is_relative_to(candidate):
                    journals.add(journal)
    if journals:
        rendered = ", ".join(path.as_posix() for path in sorted(journals))
        raise CapabilityImprovementError(
            "an interrupted authorized split freeze blocks this low-level "
            f"mutation: {rendered}"
        )


def _lock_state(lock_path: Path) -> _ProcessLockState:
    process_id = os.getpid()
    with _STATE_GUARD:
        state = _LOCK_STATES.get(lock_path)
        if state is None or state.process_id != process_id:
            state = _ProcessLockState(process_id=process_id)
            _LOCK_STATES[lock_path] = state
        return state


def _root_from_persistent_lock(start: Path) -> Path | None:
    candidates: set[Path] = set()
    for ancestor in (start, *start.parents):
        for lock_path in ancestor.glob(f".*{MUTATION_LOCK_SUFFIX}"):
            name = lock_path.name
            root_name = name[1 : -len(MUTATION_LOCK_SUFFIX)]
            candidate = (ancestor / root_name).resolve()
            if start == candidate or start.is_relative_to(candidate):
                candidates.add(candidate)
    if len(candidates) > 1:
        raise CapabilityImprovementError(
            "low-level mutation path has multiple experiment mutation locks"
        )
    return next(iter(candidates), None)


def _reset_lock_state_after_fork() -> None:
    global _STATE_GUARD
    for state in _LOCK_STATES.values():
        if state.descriptor is not None:
            try:
                os.close(state.descriptor)
            except OSError:
                pass
    _LOCK_STATES.clear()
    _STATE_GUARD = threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_lock_state_after_fork)
