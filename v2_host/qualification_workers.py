"""In-process fake-provider worker factory used only by signed qualification E2Es."""

from __future__ import annotations

from collections.abc import Mapping

from v2_host.composition import V2Container, V2Role
from v2_host.settings import V2Settings
from v2_host.worker_main import OneShotWorker, WorkerCycle, WorkerQueue


def install_qualification_worker_set(
    container: V2Container,
    workers: Mapping[WorkerQueue, OneShotWorker],
) -> None:
    if type(container) is not V2Container or container.role is not V2Role.WORKER:
        raise TypeError("qualification workers require an exact worker container")
    if not container.settings.all_real_effect_gates_closed:
        raise ValueError("qualification workers require every real-effect gate closed")
    validated = WorkerCycle(workers).workers
    if hasattr(container, "_qualification_worker_set"):
        raise RuntimeError("qualification worker set is immutable once installed")
    container._qualification_worker_set = dict(validated)


def build_worker_set(
    *,
    container: V2Container,
    settings: V2Settings,
) -> Mapping[WorkerQueue, OneShotWorker]:
    if type(container) is not V2Container or container.role is not V2Role.WORKER:
        raise TypeError("qualification factory requires an exact worker container")
    if type(settings) is not V2Settings or settings is not container.settings:
        raise TypeError("qualification factory settings must be container-owned")
    if not settings.all_real_effect_gates_closed:
        raise ValueError("qualification factory refuses open real-effect gates")
    workers = getattr(container, "_qualification_worker_set", None)
    if not isinstance(workers, Mapping):
        raise RuntimeError("qualification worker set was not installed in-process")
    return dict(WorkerCycle(workers).workers)


__all__ = ["build_worker_set", "install_qualification_worker_set"]
