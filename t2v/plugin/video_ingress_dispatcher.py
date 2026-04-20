#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Awaitable, Callable

from server_ingress_plugin.ingress_batch_drr_adapter import IngressBatchDrrAdapter, RequestType


@dataclass
class VideoIngressPayload:
    model: str
    prompt: str
    negative_prompt: str | None
    width: int
    height: int
    steps: int
    num_frames: int
    fps: int
    seed: int | None
    request_id: str
    reference_image: object | None = None
    # Keep full original request for host callback flexibility.
    request_obj: object | None = None


@dataclass
class VideoIngressResult:
    created: int
    videos_b64: list[str]
    error: str | None = None


def _env_bool(name: str, default: bool) -> bool:
    import os

    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_caps(raw: str | None) -> dict[str, int]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, int] = {}
    for k, v in parsed.items():
        try:
            out[str(k)] = max(1, int(v))
        except Exception:
            continue
    return out


class VideoIngressDispatcherService:
    """Per-instance ingress dispatcher for /v1/videos.

    This service is plugin-only and keeps scheduling logic outside framework
    source files. Host `api_server` only provides callback glue:
    - parse request -> VideoIngressPayload
    - execute one dequeue-batch via host callback
    - map callback result back to API response / job store
    """

    def __init__(
        self,
        *,
        batch_execute_fn: Callable[[RequestType, list[VideoIngressPayload]], Awaitable[list[VideoIngressResult]]],
        batch_caps: dict[str, int],
    ) -> None:
        self._batch_execute_fn = batch_execute_fn
        self._adapter = IngressBatchDrrAdapter.from_env(
            execute_batch=self._execute_batch,
            batch_caps=batch_caps,
        )
        self._started = False
        self._start_lock = asyncio.Lock()

    @classmethod
    def from_env(
        cls,
        *,
        batch_execute_fn: Callable[[RequestType, list[VideoIngressPayload]], Awaitable[list[VideoIngressResult]]],
    ) -> "VideoIngressDispatcherService":
        import os

        caps = _parse_caps(os.environ.get("OMNI_VIDEO_INGRESS_BATCH_CAPS"))
        return cls(batch_execute_fn=batch_execute_fn, batch_caps=caps)

    @staticmethod
    def enabled() -> bool:
        import os

        return _env_bool("OMNI_VIDEO_INGRESS_PLUGIN_ENABLE", False)

    async def start(self) -> None:
        async with self._start_lock:
            if self._started:
                return
            await self._adapter.start()
            self._started = True

    async def stop(self) -> None:
        async with self._start_lock:
            if not self._started:
                return
            await self._adapter.stop()
            self._started = False

    async def submit(self, payload: VideoIngressPayload) -> VideoIngressResult:
        if not self._started:
            await self.start()
        return await self._adapter.submit(
            payload=payload,
            width=payload.width,
            height=payload.height,
            steps=payload.steps,
        )

    async def _execute_batch(
        self,
        req_type: RequestType,
        payloads: list[VideoIngressPayload],
    ) -> list[VideoIngressResult]:
        return await self._batch_execute_fn(req_type, payloads)

