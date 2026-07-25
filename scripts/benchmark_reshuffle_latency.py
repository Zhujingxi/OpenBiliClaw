#!/usr/bin/env python3
"""Measure reshuffle latency and event-loop health against a running daemon.

Example:
    python scripts/benchmark_reshuffle_latency.py --requests 100 --trigger-refresh
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from contextlib import suppress
from dataclasses import dataclass, field

import httpx


@dataclass
class Samples:
    reshuffle_ms: list[float] = field(default_factory=list)
    health_ms: list[float] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def percentile(values: list[float], fraction: float) -> float:
    """Return a nearest-rank percentile without third-party statistics code."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


async def timed_request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    **kwargs: object,
) -> tuple[httpx.Response, float]:
    started = time.perf_counter()
    response = await client.request(method, path, **kwargs)
    return response, (time.perf_counter() - started) * 1000.0


async def poll_health(
    client: httpx.AsyncClient,
    samples: Samples,
    stop: asyncio.Event,
    interval: float,
) -> None:
    while not stop.is_set():
        try:
            response, elapsed_ms = await timed_request(client, "GET", "/api/health")
            if response.is_success:
                samples.health_ms.append(elapsed_ms)
            else:
                samples.failures.append(f"health HTTP {response.status_code}")
        except Exception as exc:  # benchmark must keep collecting after one probe failure
            samples.failures.append(f"health {type(exc).__name__}: {exc}")
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=max(0.01, interval))


async def benchmark(args: argparse.Namespace) -> tuple[Samples, dict[str, float | int]]:
    timeout = httpx.Timeout(args.timeout)
    samples = Samples()
    base_url = args.base_url.rstrip("/")
    # Keep health probes on their own warmed connection. Sharing one client
    # with the long request can make HTTP/1.1 connection reuse serialize the
    # first probe behind reshuffle and report client-pool wait as server-loop
    # latency.
    async with (
        httpx.AsyncClient(base_url=base_url, timeout=timeout) as client,
        httpx.AsyncClient(base_url=base_url, timeout=timeout) as health_client,
    ):
        warm_health = await health_client.get("/api/health")
        warm_health.raise_for_status()
        if args.trigger_refresh:
            response = await client.post("/api/recommendations/refresh")
            response.raise_for_status()

        stop = asyncio.Event()
        health_task = asyncio.create_task(
            poll_health(health_client, samples, stop, args.health_interval)
        )
        visible_bvids: list[str] = []
        try:
            total = max(0, args.warmup) + max(1, args.requests)
            for index in range(total):
                try:
                    response, elapsed_ms = await timed_request(
                        client,
                        "POST",
                        "/api/recommendations/reshuffle",
                        json={"excluded_bvids": visible_bvids},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    items = payload.get("items", []) if isinstance(payload, dict) else []
                    visible_bvids = [
                        str(item.get("bvid", ""))
                        for item in items
                        if isinstance(item, dict) and str(item.get("bvid", ""))
                    ]
                    if index >= args.warmup:
                        samples.reshuffle_ms.append(elapsed_ms)
                except Exception as exc:
                    samples.failures.append(f"reshuffle {type(exc).__name__}: {exc}")
        finally:
            stop.set()
            await health_task

    summary: dict[str, float | int] = {
        "requests": len(samples.reshuffle_ms),
        "reshuffle_p50_ms": round(percentile(samples.reshuffle_ms, 0.50), 1),
        "reshuffle_p95_ms": round(percentile(samples.reshuffle_ms, 0.95), 1),
        "reshuffle_p99_ms": round(percentile(samples.reshuffle_ms, 0.99), 1),
        "reshuffle_max_ms": round(max(samples.reshuffle_ms, default=0.0), 1),
        "health_samples": len(samples.health_ms),
        "health_p99_ms": round(percentile(samples.health_ms, 0.99), 1),
        "health_max_ms": round(max(samples.health_ms, default=0.0), 1),
        "failures": len(samples.failures),
    }
    return samples, summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8420")
    parser.add_argument("--requests", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--health-interval", type=float, default=0.05)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument(
        "--trigger-refresh",
        action="store_true",
        help="Queue one forced refresh before measurement to exercise background work.",
    )
    parser.add_argument(
        "--no-fail",
        action="store_true",
        help="Print results without enforcing the documented latency SLOs.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    samples, summary = asyncio.run(benchmark(args))
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    if samples.failures:
        print(json.dumps({"failure_samples": samples.failures[:10]}, ensure_ascii=False, indent=2))
    if args.no_fail:
        return 0
    failed = bool(samples.failures) or not samples.reshuffle_ms or not samples.health_ms
    failed = failed or percentile(samples.reshuffle_ms, 0.50) > 800.0
    failed = failed or percentile(samples.reshuffle_ms, 0.95) > 1_500.0
    failed = failed or percentile(samples.reshuffle_ms, 0.99) > 2_500.0
    failed = failed or max(samples.reshuffle_ms, default=0.0) > 3_000.0
    failed = failed or percentile(samples.health_ms, 0.99) > 100.0
    failed = failed or max(samples.health_ms, default=0.0) > 300.0
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
