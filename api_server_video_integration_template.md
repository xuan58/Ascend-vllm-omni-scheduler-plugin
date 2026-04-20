# `api_server.py` T2V Integration Template (Minimal Glue)

This template keeps DRR scheduling in `server_ingress_plugin` and leaves only
small reversible glue in framework source.

## 1) Import plugin module

Add in `vllm_omni/entrypoints/openai/api_server.py` imports:

```python
from server_ingress_plugin.video_ingress_dispatcher import (
    VideoIngressDispatcherService,
    VideoIngressPayload,
    VideoIngressResult,
)
from server_ingress_plugin.ingress_batch_drr_adapter import RequestType
```

## 2) Add one lazy per-process dispatcher

```python
_VIDEO_INGRESS_PLUGIN_DISPATCHER: VideoIngressDispatcherService | None = None
_VIDEO_INGRESS_PLUGIN_LOCK = asyncio.Lock()


async def _get_video_ingress_plugin_dispatcher(raw_request: Request) -> VideoIngressDispatcherService:
    global _VIDEO_INGRESS_PLUGIN_DISPATCHER
    if _VIDEO_INGRESS_PLUGIN_DISPATCHER is not None:
        return _VIDEO_INGRESS_PLUGIN_DISPATCHER

    async with _VIDEO_INGRESS_PLUGIN_LOCK:
        if _VIDEO_INGRESS_PLUGIN_DISPATCHER is not None:
            return _VIDEO_INGRESS_PLUGIN_DISPATCHER

        async def _batch_execute(
            req_type: RequestType,
            payloads: list[VideoIngressPayload],
        ) -> list[VideoIngressResult]:
            # Host callback:
            # 1) convert payloads -> batched prompt list
            # 2) call serving_video.generate_videos_batch(...)
            # 3) split result to one VideoIngressResult per payload
            handler = chat(raw_request)
            if handler is None:
                return [VideoIngressResult(created=int(time.time()), videos_b64=[], error="video handler unavailable")] * len(payloads)

            reqs = [p.request_obj for p in payloads]
            ref_ids = [p.request_id for p in payloads]
            refs = [p.reference_image for p in payloads]
            batch_resp = await handler.generate_videos_batch(reqs, ref_ids, reference_images=refs)

            out: list[VideoIngressResult] = []
            for r in batch_resp:
                b64s = [x.b64_json for x in r.data] if r and r.data else []
                out.append(VideoIngressResult(created=r.created if r else int(time.time()), videos_b64=b64s, error=None if b64s else "missing video payload"))
            return out

        svc = VideoIngressDispatcherService.from_env(batch_execute_fn=_batch_execute)
        await svc.start()
        _VIDEO_INGRESS_PLUGIN_DISPATCHER = svc
        return svc
```

## 3) Switch in `/v1/videos` path

In your video submission path (where `handler.generate_videos(...)` is called),
add a plugin branch before direct execution:

```python
if VideoIngressDispatcherService.enabled():
    dispatcher = await _get_video_ingress_plugin_dispatcher(raw_request)
    ingress_res = await dispatcher.submit(
        VideoIngressPayload(
            model=request.model or "/model",
            prompt=request.prompt,
            negative_prompt=getattr(request, "negative_prompt", None),
            width=request.width,
            height=request.height,
            steps=request.num_inference_steps,
            num_frames=request.num_frames,
            fps=request.fps,
            seed=request.seed,
            request_id=f"video_ingress-{random_uuid()}",
            reference_image=reference_image,
            request_obj=request,
        )
    )
    if ingress_res.error:
        raise HTTPException(status_code=500, detail=ingress_res.error)
    # Convert ingress_res to existing job/result format.
    # Fallback path remains unchanged below.
```

## 4) Runtime controls (no code rewrite per experiment)

- `OMNI_VIDEO_INGRESS_PLUGIN_ENABLE=1`: enable plugin branch
- `OMNI_VIDEO_INGRESS_BATCH_CAPS='{"854x480_3":6,"854x480_4":4,"1280x720_6":2}'`
- Existing DRR env vars continue to work through `IngressBatchDrrAdapter.from_env`

## 5) Rollback

- Set `OMNI_VIDEO_INGRESS_PLUGIN_ENABLE=0` (or unset) to return to original path.
- No scheduler logic is left inside framework after this wiring style.

