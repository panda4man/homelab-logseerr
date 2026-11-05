import httpx

_PROMPT_TEMPLATE = """\
You are a log analysis assistant. Answer the user's question based only on the log excerpts below.
Be concise and specific. Use markdown formatting: bold for key terms, code blocks for log lines or \
values, and bullet lists when listing multiple items. If the logs contain no relevant information, \
say so clearly.

Search scope: {scope}
The excerpts are a filtered, de-duplicated sample of Loki logs for that scope only. Repeated lines \
are collapsed with a "(xN)" suffix meaning N occurrences. Do not claim anything about time \
ranges or containers outside the scope.
{guidance}

Log excerpts:
{context}

Question: {question}
Answer:"""

_GUIDANCE_MATCHED = (
    "Every line shown matched the {severity} filter (error-level or error keyword lines) and must "
    "be reported as such. Do NOT say that no errors or warnings were found."
)
_GUIDANCE_FALLBACK = (
    "No lines matched the error/warning filter, so these are other recent lines. The absence of "
    "error lines in this scope means none were found in this scope; it does NOT mean the service "
    "is healthy overall."
)
_GUIDANCE_PLAIN = (
    "These are recent lines, not filtered for errors. Absence of error lines in the excerpts "
    "does NOT mean the service is healthy overall."
)


async def _generate(
    ollama_url: str,
    model: str,
    prompt: str,
    num_ctx: int = 8192,
    format: str | None = None,
    timeout: float = 120,
) -> str | None:
    payload: dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {"num_ctx": num_ctx},
    }
    if format:
        payload["format"] = format
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{ollama_url}/api/generate", json=payload, timeout=timeout
            )
            resp.raise_for_status()
            return resp.json()["response"].strip()
    except (httpx.HTTPError, KeyError, ValueError):
        return None


async def generate_json(
    ollama_url: str, model: str, prompt: str, num_ctx: int = 8192, timeout: float = 60
) -> str | None:
    """Ask Ollama for a JSON-formatted response (used for query understanding)."""
    return await _generate(
        ollama_url, model, prompt, num_ctx=num_ctx, format="json", timeout=timeout
    )


async def generate_answer(
    ollama_url: str,
    model: str,
    query: str,
    sources: list[dict],
    scope: str = "all logs",
    num_ctx: int = 8192,
    severity: str = "all",
    fell_back: bool = False,
) -> str | None:
    context = "\n\n---\n\n".join(
        f"[{s['service']} · {s['time_range']}]\n{s['log_text']}" for s in sources
    )
    if severity in ("errors", "warnings"):
        guidance = _GUIDANCE_MATCHED.format(severity=severity)
    elif fell_back:
        guidance = _GUIDANCE_FALLBACK
    else:
        guidance = _GUIDANCE_PLAIN
    prompt = _PROMPT_TEMPLATE.format(
        context=context, question=query, scope=scope, guidance=guidance
    )
    return await _generate(ollama_url, model, prompt, num_ctx=num_ctx)
