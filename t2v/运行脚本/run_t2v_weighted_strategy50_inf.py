#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass(frozen=True)
class Case:
    name: str
    width: int
    height: int
    steps: int
    num_frames: int
    fps: int
    weight: float


@dataclass
class ReqResult:
    req_idx: int
    case_name: str
    width: int
    height: int
    steps: int
    num_frames: int
    fps: int
    ok: bool
    error: str | None
    video_id: str | None
    submit_ts: float
    accepted_ts: float | None
    completed_ts: float
    create_api_s: float
    e2e_s: float


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    arr = sorted(values)
    idx = (len(arr) - 1) * p
    lo = int(idx)
    hi = min(lo + 1, len(arr) - 1)
    frac = idx - lo
    return arr[lo] * (1 - frac) + arr[hi] * frac


async def wait_for_service(base_url: str, timeout_s: float = 1800.0) -> None:
    start = time.time()
    async with httpx.AsyncClient(timeout=5.0) as client:
        while True:
            try:
                resp = await client.get(f"{base_url}/health")
                if resp.status_code == 200:
                    return
            except Exception:
                pass
            if time.time() - start > timeout_s:
                raise TimeoutError(f"Service {base_url} not ready within {timeout_s}s")
            await asyncio.sleep(1.0)


async def create_and_wait_video(
    client: httpx.AsyncClient,
    base_url: str,
    case: Case,
    seed: int,
    req_idx: int,
    model: str,
    prompt: str,
    negative_prompt: str,
    poll_interval_s: float,
    ingress_bs: int,
) -> ReqResult:
    form = {
        "model": model,
        "prompt": prompt,
        "size": f"{case.width}x{case.height}",
        "num_frames": str(case.num_frames),
        "fps": str(case.fps),
        "num_inference_steps": str(case.steps),
        "negative_prompt": negative_prompt,
        "seed": str(seed),
        "ingress_bs": str(max(1, ingress_bs)),
    }

    submit_ts = time.time()
    t0 = time.perf_counter()
    try:
        create_resp = await client.post(f"{base_url}/v1/videos", data=form)
    except Exception as exc:
        completed_ts = time.time()
        return ReqResult(
            req_idx=req_idx,
            case_name=case.name,
            width=case.width,
            height=case.height,
            steps=case.steps,
            num_frames=case.num_frames,
            fps=case.fps,
            ok=False,
            error=f"create exception: {exc!r}",
            video_id=None,
            submit_ts=submit_ts,
            accepted_ts=None,
            completed_ts=completed_ts,
            create_api_s=0.0,
            e2e_s=max(0.0, completed_ts - submit_ts),
        )

    create_api_s = time.perf_counter() - t0
    accepted_ts = time.time()
    if create_resp.status_code != 200:
        completed_ts = time.time()
        return ReqResult(
            req_idx=req_idx,
            case_name=case.name,
            width=case.width,
            height=case.height,
            steps=case.steps,
            num_frames=case.num_frames,
            fps=case.fps,
            ok=False,
            error=f"create status={create_resp.status_code}",
            video_id=None,
            submit_ts=submit_ts,
            accepted_ts=accepted_ts,
            completed_ts=completed_ts,
            create_api_s=create_api_s,
            e2e_s=max(0.0, completed_ts - submit_ts),
        )

    try:
        body = create_resp.json()
    except Exception as exc:
        completed_ts = time.time()
        return ReqResult(
            req_idx=req_idx,
            case_name=case.name,
            width=case.width,
            height=case.height,
            steps=case.steps,
            num_frames=case.num_frames,
            fps=case.fps,
            ok=False,
            error=f"create json error: {exc!r}",
            video_id=None,
            submit_ts=submit_ts,
            accepted_ts=accepted_ts,
            completed_ts=completed_ts,
            create_api_s=create_api_s,
            e2e_s=max(0.0, completed_ts - submit_ts),
        )

    video_id = body.get("id")
    if not video_id:
        completed_ts = time.time()
        return ReqResult(
            req_idx=req_idx,
            case_name=case.name,
            width=case.width,
            height=case.height,
            steps=case.steps,
            num_frames=case.num_frames,
            fps=case.fps,
            ok=False,
            error="missing video id",
            video_id=None,
            submit_ts=submit_ts,
            accepted_ts=accepted_ts,
            completed_ts=completed_ts,
            create_api_s=create_api_s,
            e2e_s=max(0.0, completed_ts - submit_ts),
        )

    while True:
        try:
            poll_resp = await client.get(f"{base_url}/v1/videos/{video_id}")
        except Exception as exc:
            completed_ts = time.time()
            return ReqResult(
                req_idx=req_idx,
                case_name=case.name,
                width=case.width,
                height=case.height,
                steps=case.steps,
                num_frames=case.num_frames,
                fps=case.fps,
                ok=False,
                error=f"poll exception: {exc!r}",
                video_id=video_id,
                submit_ts=submit_ts,
                accepted_ts=accepted_ts,
                completed_ts=completed_ts,
                create_api_s=create_api_s,
                e2e_s=max(0.0, completed_ts - submit_ts),
            )
        if poll_resp.status_code != 200:
            completed_ts = time.time()
            return ReqResult(
                req_idx=req_idx,
                case_name=case.name,
                width=case.width,
                height=case.height,
                steps=case.steps,
                num_frames=case.num_frames,
                fps=case.fps,
                ok=False,
                error=f"poll status={poll_resp.status_code}",
                video_id=video_id,
                submit_ts=submit_ts,
                accepted_ts=accepted_ts,
                completed_ts=completed_ts,
                create_api_s=create_api_s,
                e2e_s=max(0.0, completed_ts - submit_ts),
            )
        try:
            poll_body = poll_resp.json()
        except Exception as exc:
            completed_ts = time.time()
            return ReqResult(
                req_idx=req_idx,
                case_name=case.name,
                width=case.width,
                height=case.height,
                steps=case.steps,
                num_frames=case.num_frames,
                fps=case.fps,
                ok=False,
                error=f"poll json error: {exc!r}",
                video_id=video_id,
                submit_ts=submit_ts,
                accepted_ts=accepted_ts,
                completed_ts=completed_ts,
                create_api_s=create_api_s,
                e2e_s=max(0.0, completed_ts - submit_ts),
            )

        status = str(poll_body.get("status", "")).lower()
        if status == "completed":
            completed_ts = time.time()
            return ReqResult(
                req_idx=req_idx,
                case_name=case.name,
                width=case.width,
                height=case.height,
                steps=case.steps,
                num_frames=case.num_frames,
                fps=case.fps,
                ok=True,
                error=None,
                video_id=video_id,
                submit_ts=submit_ts,
                accepted_ts=accepted_ts,
                completed_ts=completed_ts,
                create_api_s=create_api_s,
                e2e_s=max(0.0, completed_ts - submit_ts),
            )
        if status in {"failed", "cancelled"}:
            completed_ts = time.time()
            return ReqResult(
                req_idx=req_idx,
                case_name=case.name,
                width=case.width,
                height=case.height,
                steps=case.steps,
                num_frames=case.num_frames,
                fps=case.fps,
                ok=False,
                error=f"terminal status={status} err={poll_body.get('error')}",
                video_id=video_id,
                submit_ts=submit_ts,
                accepted_ts=accepted_ts,
                completed_ts=completed_ts,
                create_api_s=create_api_s,
                e2e_s=max(0.0, completed_ts - submit_ts),
            )
        await asyncio.sleep(poll_interval_s)


def per_case_summary(req_results: list[ReqResult]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[ReqResult]] = {}
    for r in req_results:
        grouped.setdefault(r.case_name, []).append(r)
    out: dict[str, dict[str, Any]] = {}
    for case_name, rows in grouped.items():
        e2e_all = [x.e2e_s for x in rows]
        out[case_name] = {
            "requests": len(rows),
            "success_rate": sum(1 for x in rows if x.ok) / len(rows) if rows else 0.0,
            "mean_e2e_s": statistics.mean(e2e_all) if e2e_all else 0.0,
            "p99_e2e_s": percentile(e2e_all, 0.99),
            "errors": sum(1 for x in rows if not x.ok),
            "error_samples": [x.error for x in rows if not x.ok][:3],
        }
    return out


def save_outputs(reqs: list[ReqResult], summary: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    req_json = out_dir / f"t2v_weighted_strategy50_inf_reqs_{ts}.json"
    req_csv = out_dir / f"t2v_weighted_strategy50_inf_reqs_{ts}.csv"
    summary_json = out_dir / f"t2v_weighted_strategy50_inf_summary_{ts}.json"

    payload = [asdict(x) for x in reqs]
    req_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if payload:
        with req_csv.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(payload[0].keys()))
            writer.writeheader()
            writer.writerows(payload)

    print(f"[OUT] req_json={req_json}", flush=True)
    print(f"[OUT] req_csv={req_csv}", flush=True)
    print(f"[OUT] summary_json={summary_json}", flush=True)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Weighted T2V strategy benchmark: 50 concurrent burst, rps=inf.")
    parser.add_argument("--base-url", type=str, default="http://127.0.0.1:18191")
    parser.add_argument("--requests", type=int, default=50)
    parser.add_argument("--seed", type=int, default=20260415)
    parser.add_argument("--request-timeout-s", type=float, default=36000.0)
    parser.add_argument("--poll-interval-s", type=float, default=1.0)
    parser.add_argument("--model", type=str, default="/model")
    parser.add_argument("--ingress-bs", type=int, default=1)
    parser.add_argument("--out-dir", type=str, default="/docker/aixuan/vllm-omni-v0.18.0-test/t2v_reports")
    args = parser.parse_args()

    cases = [
        Case("case1_854x480_s3_f80_fps16", 854, 480, 3, 80, 16, 0.15),
        Case("case2_854x480_s4_f120_fps24", 854, 480, 4, 120, 24, 0.25),
        Case("case3_1280x720_s6_f80_fps16", 1280, 720, 6, 80, 16, 0.60),
    ]
    prompt = "A cinematic shot of ocean waves at sunset, realistic motion, high detail."
    negative_prompt = "blurry, artifacts, low quality, text, watermark"

    await wait_for_service(args.base_url)
    print(f"[RUN] service ready at {args.base_url}", flush=True)
    print(f"[RUN] requests={args.requests}, concurrency={args.requests}, rps=inf (burst submit)", flush=True)
    print(f"[RUN] ingress_bs={max(1, args.ingress_bs)}", flush=True)

    rng = random.Random(args.seed)
    selected_cases = rng.choices(cases, weights=[c.weight for c in cases], k=args.requests)
    sampled_counts = {c.name: sum(1 for x in selected_cases if x.name == c.name) for c in cases}
    print(f"[RUN] sampled case counts: {json.dumps(sampled_counts, ensure_ascii=False)}", flush=True)

    req_results: list[ReqResult] = []
    lock = asyncio.Lock()
    bench_start = time.perf_counter()
    timeout = httpx.Timeout(args.request_timeout_s)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async def submit_one(i: int, c: Case) -> None:
            rec = await create_and_wait_video(
                client=client,
                base_url=args.base_url,
                case=c,
                seed=args.seed + i,
                req_idx=i,
                model=args.model,
                prompt=prompt,
                negative_prompt=negative_prompt,
                poll_interval_s=args.poll_interval_s,
                ingress_bs=max(1, args.ingress_bs),
            )
            async with lock:
                req_results.append(rec)

        tasks = [asyncio.create_task(submit_one(i, c)) for i, c in enumerate(selected_cases)]
        await asyncio.gather(*tasks)

    req_results.sort(key=lambda x: x.req_idx)
    total_elapsed = max(1e-9, time.perf_counter() - bench_start)
    ok_count = sum(1 for x in req_results if x.ok)
    e2e_all = [x.e2e_s for x in req_results]

    summary = {
        "round_name": "strategy_weighted_50req_conc50_rps_inf",
        "total_requests": len(req_results),
        "success_rate": ok_count / len(req_results) if req_results else 0.0,
        "mean_e2e_s": statistics.mean(e2e_all) if e2e_all else 0.0,
        "p99_e2e_s": percentile(e2e_all, 0.99),
        "throughput_rps": ok_count / total_elapsed,
        "mean_create_api_s": statistics.mean([x.create_api_s for x in req_results]) if req_results else 0.0,
        "errors": len(req_results) - ok_count,
        "error_samples": [x.error for x in req_results if not x.ok][:5],
        "sampled_counts": sampled_counts,
        "ingress_bs": max(1, args.ingress_bs),
        "per_case": per_case_summary(req_results),
    }
    print(f"[SUMMARY] {json.dumps(summary, ensure_ascii=False)}", flush=True)
    save_outputs(req_results, summary, Path(args.out_dir))


if __name__ == "__main__":
    asyncio.run(main())

