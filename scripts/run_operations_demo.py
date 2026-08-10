from __future__ import annotations

import argparse
import json

import httpx


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the SRE Agent investigation and Observability Agent "
            "next-action demo workflow."
        )
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--mode", choices=["simulation", "live"], default="simulation")
    parser.add_argument(
        "--scenario",
        choices=["stale_policy", "slow_tool", "tool_failure"],
        default="tool_failure",
    )
    parser.add_argument("--trace-id")
    parser.add_argument("--timeout", type=float, default=180)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with httpx.Client(base_url=args.base_url, timeout=args.timeout) as client:
        response = client.post(
            "/api/operations/investigate",
            json={
                "mode": args.mode,
                "scenario": args.scenario,
                "trace_id": args.trace_id,
                "time_range_minutes": 30,
            },
        )
        print(f"HTTP {response.status_code}")
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))
        response.raise_for_status()


if __name__ == "__main__":
    main()
