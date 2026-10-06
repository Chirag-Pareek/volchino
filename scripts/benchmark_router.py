#!/usr/bin/env python3
"""Benchmark Multi-Tier Intent Router for Volchino Agent.

Measures routing latency and token usage across all 6 tiers:
    Tier 1: Local Cache (0 tokens)
    Tier 2: SQLite Memory (0 tokens)
    Tier 3: Learned Skills (0 tokens)
    Tier 4: Deterministic Regex (0 tokens)
    Tier 5: Groq Router (<300ms target)
    Tier 6: OpenCode Deep Reasoning

Usage:
    python scripts/benchmark_router.py [--iterations N] [--live-groq]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import AsyncMock, MagicMock

from server.config import Settings
from server.db import connect
from server.llm.groq import ChatClient, ChatResponse, GroqClient
from server.pipeline import cache as cache_stage
from server.pipeline.cache import Cache
from server.pipeline.types import Outcome
from server.reasoning.opencode import OpenCodeReasoningClient
from server.router.multi_tier import MultiTierRouter
from server.router.router_groq import GroqRouter
from server.skills.engine import approve_skill, save_draft_skill, synthesize_skill
from server.tools import REGISTRY


async def setup_bench_env(tmp_dir: Path):
    db_path = tmp_dir / "bench.db"
    db = await connect(db_path)
    cache = Cache(db)

    # Populate Tier 1: Cache
    outcome = Outcome(
        text="Cached battery status",
        stage="cache",
        intent="get_battery_status",
        tool="get_battery_status",
        tokens_used=0,
    )
    await cache_stage.store(cache, "battery level", outcome, ttl_s=3600)

    # Populate Tier 3: Approved Skill
    skill_def = synthesize_skill(
        task_description="Work mode",
        steps=[{"tool": "switch_workspace", "args": {"workspace": 1}}],
        trigger="work mode",
        name="work_mode",
    )
    name = await save_draft_skill(db, skill_def)
    await approve_skill(db, name)

    return db, cache


async def run_benchmark(iterations: int, use_live_groq: bool):
    print("=" * 78)
    print(" Volchino Multi-Tier Router Benchmark")
    print(f" Iterations: {iterations} per tier | Live Groq: {use_live_groq}")
    print("=" * 78)

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_dir = Path(tmp_str)
        db, cache = await setup_bench_env(tmp_dir)

        # Setup Groq router (live or mock)
        settings = Settings.from_env()
        if use_live_groq and settings.groq_api_key:
            print("[INFO] Using live Groq API connection")
            groq_client = GroqClient(settings.groq_api_key, settings.groq_model)
        else:
            if use_live_groq and not settings.groq_api_key:
                print("[WARN] GROQ_API_KEY not found in env; falling back to simulated Groq")
            mock_client = MagicMock(spec=ChatClient)
            mock_client.complete = AsyncMock(
                return_value=ChatResponse(
                    content='{"target":"tool","name":"open_app","args":{"app":"firefox"},"confidence":0.99}',
                    total_tokens=14,
                )
            )
            groq_client = mock_client

        groq_router = GroqRouter(groq_client, cache, REGISTRY)
        reasoning = OpenCodeReasoningClient()
        router = MultiTierRouter(db, cache, REGISTRY, groq_router, reasoning)

        test_cases = [
            ("Tier 1 (Cache)", "battery level", "battery level"),
            ("Tier 2 (Memory)", "how is volchino", "how is volchino"),
            ("Tier 3 (Skill)", "work mode", "work mode"),
            ("Tier 4 (Regex)", "volume 50%", "volume 50%"),
            ("Tier 5 (Groq)", "browse the web please", "browse the web please"),
            (
                "Tier 6 (Reason)",
                "organize my complex dev workspace",
                "organize my complex dev workspace",
            ),
        ]

        header = (
            f"{'Tier':<18} | {'Input Prompt':<28} | {'Latency (ms)':<12} | {'Tokens':<6} | Target"
        )
        print(header)
        print("-" * 78)

        for tier_label, raw_prompt, norm_prompt in test_cases:
            latencies = []
            tokens = 0
            target = "?"

            for _ in range(iterations):
                # Clear route cache for Tier 5 test to measure routing latency
                if "Tier 5" in tier_label:
                    from server.pipeline.cache import route_key

                    await cache.set(route_key(norm_prompt), None, 0)

                t0 = time.perf_counter()
                dec = await router.decide(raw_prompt, norm_prompt)
                t1 = time.perf_counter()

                latencies.append((t1 - t0) * 1000)
                tokens = dec.tokens_used
                target = dec.target

            avg_ms = sum(latencies) / len(latencies)
            row = (
                f"{tier_label:<18} | {raw_prompt[:26]:<28} | {avg_ms:9.3f} ms | "
                f"{tokens:6d} | {target}"
            )
            print(row)

        if hasattr(groq_client, "aclose"):
            await groq_client.aclose()
        await db.close()

    print("=" * 78)
    print(" Benchmark completed successfully.")


def main():
    parser = argparse.ArgumentParser(description="Volchino Intent Router Benchmark")
    parser.add_argument("--iterations", type=int, default=10, help="Number of runs per test")
    parser.add_argument("--live-groq", action="store_true", help="Use live Groq API key from .env")
    args = parser.parse_args()

    asyncio.run(run_benchmark(args.iterations, args.live_groq))


if __name__ == "__main__":
    main()
