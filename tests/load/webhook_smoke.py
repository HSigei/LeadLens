"""Synthetic smoke/load probe for a non-production deployment only.

Run only against a sandbox URL after setting LOAD_TEST_TOKEN to an approved
gateway token. This deliberately does not generate real Twilio calls.
"""
from __future__ import annotations

import asyncio
import os
import sys

import httpx


async def probe(client: httpx.AsyncClient, url: str) -> int:
    response = await client.get(f"{url.rstrip('/')}/healthz", headers={"X-Load-Test-Token": os.environ["LOAD_TEST_TOKEN"]})
    return response.status_code


async def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Usage: python tests/load/webhook_smoke.py https://sandbox.example.com 25")
    if not os.getenv("LOAD_TEST_TOKEN"):
        raise SystemExit("LOAD_TEST_TOKEN is required.")
    url, count = sys.argv[1], int(sys.argv[2])
    async with httpx.AsyncClient(timeout=10) as client:
        results = await asyncio.gather(*(probe(client, url) for _ in range(count)))
    failures = sum(result != 200 for result in results)
    print(f"requests={count} failures={failures}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())