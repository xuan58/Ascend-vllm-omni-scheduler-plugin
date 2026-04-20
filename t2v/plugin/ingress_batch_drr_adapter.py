#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, TypeVar

BASE_COST = 512 * 512 * 20

TPayload = TypeVar("TPayload")
TResult = TypeVar("TResult")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_json_dict(name: str) -> dict[str, float]:
    raw = os.environ.get(name)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return {}
        out: dict[str, float] = {}
        for k, v in parsed.items():
            out[str(k)] = float(v)
        return out
    except Exception:
        return {}


@dataclass(frozen=True)
class RequestType:
    width: int
    height: int
    steps: int

    @property
    def key(self) -> str:
        return f"{self.width}x{self.height}_{self.steps}"

    @property
    def cost(self) -> float:
        return (self.width * self.height * self.steps) / BASE_COST


@dataclass
class _Pending(Generic[TPayload, TResult]):
    payload: TPayload
    req_type: RequestType
    enqueue_ts: float
    future: asyncio.Future[TResult]


class IngressBatchDrrAdapter(Generic[TPayload, TResult]):
    """Server-side ingress batching adapter (per-instance).

    This component is designed to run inside one API server instance, before
    the core diffusion scheduler/executor. The adapter owns:
    - per-type waiting queues
    - DRR queue selection
    - max-wait tail flush

    It emits a *batched* payload list into `execute_batch`, then maps batch
    results back to each original request future.
    """

    def __init__(
        self,
        *,
        execute_batch: Callable[[RequestType, list[TPayload]], Awaitable[list[TResult]]],
        batch_caps: dict[str, int],
        max_wait_ms: int = 250,
        strict_batching: bool = False,
        q_base: int = 12,
        age_threshold_ms: int = 1200,
        age_bonus_factor: float = 1.0,
        queue_budget_overrides: dict[str, float] | None = None,
        max_queues: int = 0,
    ) -> None:
        self._execute_batch = execute_batch
        self.batch_caps = {k: max(1, int(v)) for k, v in batch_caps.items()}
        self.max_wait_ms = max(0, int(max_wait_ms))
        self.strict_batching = bool(strict_batching)
        self.q_base = max(1, int(q_base))
        self.age_threshold_ms = max(1, int(age_threshold_ms))
        self.age_bonus_factor = max(0.0, float(age_bonus_factor))
        self.queue_budget_overrides = {str(k): max(1.0, float(v)) for k, v in (queue_budget_overrides or {}).items()}
        self.max_queues = max(0, int(max_queues))

        self._queues: dict[str, deque[_Pending[TPayload, TResult]]] = {}
        self._types: dict[str, RequestType] = {}
        self._deficits: dict[str, float] = {}
        self._order: list[str] = []
        self._rr_idx = 0
        self._lock = asyncio.Lock()
        self._cv = asyncio.Condition(self._lock)
        self._running = False
        self._loop_task: asyncio.Task | None = None
        self._closed = False

    @classmethod
    def from_env(
        cls,
        *,
        execute_batch: Callable[[RequestType, list[TPayload]], Awaitable[list[TResult]]],
        batch_caps: dict[str, int],
    ) -> "IngressBatchDrrAdapter[TPayload, TResult]":
        return cls(
            execute_batch=execute_batch,
            batch_caps=batch_caps,
            max_wait_ms=_env_int("OMNI_INGRESS_DRR_MAX_WAIT_MS", 250),
            strict_batching=_env_bool("OMNI_INGRESS_DRR_STRICT_BATCHING", False),
            q_base=_env_int("OMNI_INGRESS_DRR_Q_BASE", 12),
            age_threshold_ms=_env_int("OMNI_INGRESS_DRR_AGE_THRESHOLD_MS", 1200),
            age_bonus_factor=_env_float("OMNI_INGRESS_DRR_AGE_BONUS_FACTOR", 1.0),
            queue_budget_overrides=_env_json_dict("OMNI_INGRESS_DRR_QUEUE_BUDGET_OVERRIDES"),
            max_queues=_env_int("OMNI_INGRESS_DRR_MAX_QUEUES", 0),
        )

    def _quantum(self, req_type: RequestType) -> int:
        override = self.queue_budget_overrides.get(req_type.key)
        if override is not None:
            return max(1, round(override))
        return max(1, round(self.q_base / req_type.cost))

    async def start(self) -> None:
        async with self._lock:
            if self._running:
                return
            self._running = True
            self._closed = False
            self._loop_task = asyncio.create_task(self._run_loop(), name="ingress-batch-drr-loop")

    async def stop(self) -> None:
        async with self._lock:
            self._running = False
            self._closed = True
            self._cv.notify_all()
        if self._loop_task is not None:
            await self._loop_task
            self._loop_task = None

    async def submit(
        self,
        *,
        payload: TPayload,
        width: int,
        height: int,
        steps: int,
    ) -> TResult:
        incoming_req_type = RequestType(width=width, height=height, steps=steps)
        req_type = incoming_req_type
        key = req_type.key
        fut: asyncio.Future[TResult] = asyncio.get_running_loop().create_future()
        async with self._lock:
            if self._closed:
                raise RuntimeError("IngressBatchDrrAdapter is closed")
            if key not in self._queues and self.max_queues > 0 and len(self._queues) >= self.max_queues:
                nearest_key = min(self._types.keys(), key=lambda k: abs(self._types[k].cost - req_type.cost))
                key = nearest_key
                req_type = self._types[nearest_key]
            if key not in self._queues:
                self._queues[key] = deque()
                self._types[key] = req_type
                self._deficits[key] = 0.0
                self._order.append(key)
                self._order.sort(key=lambda k: self._types[k].cost)
            node = _Pending(payload=payload, req_type=req_type, enqueue_ts=time.perf_counter(), future=fut)
            self._queues[key].append(node)
            self._cv.notify()
        return await fut

    def _select_queue_locked(self) -> tuple[str | None, bool]:
        active = [k for k in self._order if self._queues.get(k)]
        if not active:
            return None, False

        for key in active:
            self._deficits[key] += self._quantum(self._types[key])

        n = len(active)
        for offset in range(n):
            key = active[(self._rr_idx + offset) % n]
            q = self._queues[key]
            req_type = self._types[key]
            oldest_wait_ms = (time.perf_counter() - q[0].enqueue_ts) * 1000.0
            bonus = self.age_bonus_factor * self._quantum(req_type) if oldest_wait_ms >= self.age_threshold_ms else 0.0
            if self._deficits[key] + bonus >= req_type.cost:
                self._rr_idx = (self._rr_idx + offset + 1) % max(1, n)
                return key, False

        oldest_key = min(active, key=lambda k: self._queues[k][0].enqueue_ts)
        return oldest_key, True

    async def _run_loop(self) -> None:
        while True:
            async with self._lock:
                while self._running and not any(self._queues.get(k) for k in self._order):
                    await self._cv.wait()
                if not self._running:
                    break

                key, force = self._select_queue_locked()
                if key is None:
                    await self._cv.wait()
                    continue

                queue = self._queues[key]
                req_type = self._types[key]
                cap = self.batch_caps.get(key, 1)
                oldest_wait_ms = (time.perf_counter() - queue[0].enqueue_ts) * 1000.0

                if len(queue) < cap:
                    if self.strict_batching:
                        await self._cv.wait()
                        continue
                    if oldest_wait_ms < self.max_wait_ms:
                        wait_s = max((self.max_wait_ms - oldest_wait_ms) / 1000.0, 0.001)
                        try:
                            await asyncio.wait_for(self._cv.wait(), timeout=wait_s)
                        except asyncio.TimeoutError:
                            pass
                        continue

                batch_size = min(cap, len(queue))
                batch = [queue.popleft() for _ in range(batch_size)]
                if not force:
                    self._deficits[key] = max(0.0, self._deficits[key] - req_type.cost * batch_size)

            await self._dispatch_batch(req_type, batch)

    async def _dispatch_batch(self, req_type: RequestType, batch: list[_Pending[TPayload, TResult]]) -> None:
        if not batch:
            return
        payloads = [x.payload for x in batch]
        try:
            outputs = await self._execute_batch(req_type, payloads)
            if len(outputs) != len(batch):
                raise RuntimeError(f"batch result size mismatch: expected {len(batch)}, got {len(outputs)}")
            for node, output in zip(batch, outputs, strict=True):
                if not node.future.done():
                    node.future.set_result(output)
        except Exception as exc:
            for node in batch:
                if not node.future.done():
                    node.future.set_exception(exc)
