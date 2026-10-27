"""Step 0: one cheap call to prove the key, the model id and block reading all work."""
from triage.client import MODEL, get_client, text_of, timed_create


def main() -> None:
    client = get_client()
    response, ms = timed_create(
        client,
        model=MODEL,
        max_tokens=200,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": "Reply with exactly: pong"}],
    )

    block_types = [block.type for block in response.content]
    print(f"model:        {response.model}")
    print(f"stop_reason:  {response.stop_reason}")
    print(f"block types:  {block_types}")
    print(f"answer:       {text_of(response)!r}")
    print(f"tokens:       in={response.usage.input_tokens} out={response.usage.output_tokens}")
    print(f"latency:      {ms:.0f} ms")


if __name__ == "__main__":
    main()