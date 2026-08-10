from __future__ import annotations

import argparse
import asyncio
from itertools import cycle, islice

import httpx

SCENARIOS = ("healthy", "stale_policy", "slow_tool", "tool_failure")


async def generate(args: argparse.Namespace) -> None:
    scenarios = list(islice(cycle(SCENARIOS), args.requests))
    async with httpx.AsyncClient(
        base_url=args.base_url, timeout=args.timeout
    ) as client:
        for index, scenario in enumerate(scenarios, start=1):
            response = await client.post(
                "/api/ask",
                json={
                    "question": (
                        "国内出張の精算期限と申請 REQ-2026-0042 "
                        "の状況を教えてください。"
                    ),
                    "request_id": "REQ-2026-0042",
                    "mode": args.mode,
                    "scenario": scenario,
                    "conversation_id": f"conv_traffic_{index:04d}",
                },
            )
            payload = response.json()
            print(
                f"{index:03d} {scenario:13s} HTTP {response.status_code} "
                f"{payload['elapsed_ms']:8.2f} ms trace={payload['trace_id']}"
            )
            if args.interval:
                await asyncio.sleep(args.interval)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate repeatable LLM operations demo traffic."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--mode", choices=["simulation", "live"], default="simulation")
    parser.add_argument("--requests", type=int, default=16)
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument("--timeout", type=float, default=30)
    return parser.parse_args()


def main() -> None:
    asyncio.run(generate(parse_args()))


if __name__ == "__main__":
    main()
