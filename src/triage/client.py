"""Shared Anthropic client helpers used by every step."""
import os
import time

import anthropic
from dotenv import load_dotenv

load_dotenv()

MODEL = os.getenv("TRIAGE_MODEL", "claude-haiku-5-5")

# USD per million tokens for claude-haiku-5-5, prompts up to 100K tokens.
# Source: platform.claude.com/docs/en/about-claude/pricing (checked in Week 1).
PRICE_INPUT_PER_MTOK = 0.10
PRICE_OUTPUT_PER_MTOK = 0.50

# Prompt caching multipliers on the input price (5-minute cache).
# Source: platform.claude.com/docs/en/build-with-claude/prompt-caching
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10


def get_client() -> anthropic.Anthropic:
    """Create the API client. Reads ANTHROPIC_API_KEY from the environment (.env)."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is missing. Add it to the .env file in the repo root.")
    return anthropic.Anthropic()


def text_of(response) -> str:
    """Join all text blocks. Never read content[0]: on Haiku 5.5 it can be a thinking block."""
    return "".join(block.text for block in response.content if block.type == "text")


def timed_create(client: anthropic.Anthropic, **kwargs):
    """Call messages.create and return (response, elapsed_ms)."""
    start = time.perf_counter()
    response = client.messages.create(**kwargs)
    elapsed_ms = (time.perf_counter() - start) * 1000
    return response, elapsed_ms


def cost_usd(usage) -> float:
    """Cost of one call from the usage block the API returns.

    With caching, input_tokens is only the uncached part; cache writes and reads are billed separately.
    """
    cache_write = usage.cache_creation_input_tokens or 0
    cache_read = usage.cache_read_input_tokens or 0
    return (usage.input_tokens * PRICE_INPUT_PER_MTOK
            + cache_write * PRICE_INPUT_PER_MTOK * CACHE_WRITE_MULTIPLIER
            + cache_read * PRICE_INPUT_PER_MTOK * CACHE_READ_MULTIPLIER
            + usage.output_tokens * PRICE_OUTPUT_PER_MTOK) / 1_000_000