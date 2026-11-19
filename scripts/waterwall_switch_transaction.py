"""Switch transaction orchestration; no command-line interface or system calls.

This module is a development component, not a deployable switch tool. A future
backend must provide exclusive inter-process locking, durable atomic snapshots,
a systemd rollback timer and real end-to-end health checks. Those facilities are
not implemented here. The methods and tests do not make production switching
safe by themselves.
"""
from dataclasses import dataclass
from typing import Protocol

VALID_MODES = frozenset(('reality-mux', 'reality', 'tls-mux', 'wstunnel'))


class SwitchFailure(Exception):
    pass


class RollbackFailure(SwitchFailure):
    pass


@dataclass(frozen=True)
class Snapshot:
    """Opaque durable identifier and old mode, supplied by the backend."""
    token: str
    old_mode: str


class Backend(Protocol):
    def preflight(self, target: str) -> None: ...
    def snapshot(self) -> Snapshot: ...
    def arm_rollback(self, snapshot: Snapshot) -> None: ...
    def set_live(self, target: str) -> None: ...
    def verify_connectivity(self, target: str) -> bool: ...
    def persist(self, target: str) -> None: ...
    def restore(self, snapshot: Snapshot) -> None: ...
    def disarm_rollback(self, snapshot: Snapshot) -> None: ...
    def discard_snapshot(self, snapshot: Snapshot) -> None: ...


def switch_under_lock(backend: Backend, target: str) -> None:
    """Perform a trial switch; caller must hold the backend's exclusive lock.

    Contract: snapshot is durable before mutation, and arm_rollback must return
    only after an independent recovery job is scheduled. restore restores both
    saved and live states and is idempotent. The recovery job takes the same
    lock and checks the snapshot's active token before acting. A stale recovery
    job must never change a newer selection. The backend must refuse conflicting
    or ambiguous live/saved config, multiple peers or an existing transaction.

    If the calling process is killed, the independent timer remains armed. This
    pure function cannot enforce or test that guarantee without a real backend.
    """
    if target not in VALID_MODES:
        raise ValueError('invalid target mode')
    backend.preflight(target)
    snap = backend.snapshot()
    try:
        backend.arm_rollback(snap)
    except Exception as error:
        # Arming may have partially succeeded; retain the snapshot until the
        # backend establishes whether a timer exists and can safely clean up.
        raise SwitchFailure('recovery timer setup failed; snapshot retained') from error
    try:
        backend.set_live(target)
        if not backend.verify_connectivity(target):
            raise SwitchFailure('new transport failed the connectivity check')
        backend.persist(target)
    except Exception as error:
        try:
            backend.restore(snap)
        except Exception as rollback_error:
            # Keep the snapshot and timer: do not conceal a failed restoration.
            raise RollbackFailure('live or saved rollback failed; recovery remains armed') from rollback_error
        try:
            backend.disarm_rollback(snap)
            backend.discard_snapshot(snap)
        except Exception as cleanup_error:
            # Endpoint is restored; preserve snapshot for a possibly live timer.
            raise RollbackFailure('restored but recovery cleanup failed') from cleanup_error
        raise SwitchFailure('switch failed; prior transport restored') from error
    # Once persistence succeeds, failure to disarm may cause a safe but
    # unexpected later rollback. Never throw away its snapshot on failure.
    try:
        backend.disarm_rollback(snap)
        backend.discard_snapshot(snap)
    except Exception as error:
        raise SwitchFailure('switch saved, but recovery cleanup failed; inspect state') from error
