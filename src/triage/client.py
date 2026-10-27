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
    """Cost of one call from the usage block the API returns."""
    return (usage.input_tokens * PRICE_INPUT_PER_MTOK
            + usage.output_tokens * PRICE_OUTPUT_PER_MTOK) / 1_000_000