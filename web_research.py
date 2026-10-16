"""OpenRouter web research with API-supplied citations, using the existing key.

Reference: https://openrouter.ai/docs/guides/features/server-tools/web-search
No plain-text fallback: a failed or ungrounded response must stop the forecast.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

DEFAULT_RESEARCHER_MODEL = "openrouter/openai/gpt-5-mini"
logger = logging.getLogger(__name__)


async def search_web(prompt: str, *, model: str, question_url: str) -> str:
    """Run blocking HTTP outside the event loop; make only one billable request."""
    return await asyncio.to_thread(
        _search_web, prompt, model=model, question_url=question_url
    )


def _search_web(prompt: str, *, model: str, question_url: str) -> str:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key or api_key == "REPLACE_ME":
        raise ValueError("Web research requires OPENROUTER_API_KEY")
    model = model.removeprefix("openrouter/")
    if not model.startswith("openai/") or ":" in model:
        raise ValueError(
            "RESEARCHER_MODEL must be a native-search-capable OpenAI model, "
            "e.g. openrouter/openai/gpt-5-mini. The sponsored key does not "
            "allow Perplexity/Sonar."
        )

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        # OpenRouter executes this server tool; no client-side tool loop is needed.
        "tools": [{"type": "openrouter:web_search", "parameters": {
            "engine": "native", "max_results": 5, "max_uses": 2,
        }}],
        "max_tool_calls": 2,
        "max_tokens": 6000,
        # Stay on a provider allowed by the sponsored key; do not route elsewhere.
        "provider": {"only": ["openai"], "allow_fallbacks": False},
        "stream": False,
    }
    request = Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=180) as response:
            result = json.load(response)
    except HTTPError as exc:
        # Keep actionable provider errors, without dumping request headers/key.
        error_body = exc.read().decode("utf-8", errors="replace")
        try:
            message = json.loads(error_body)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            message = exc.reason
        raise RuntimeError(
            f"OpenRouter web research HTTP {exc.code}: {message}"
        ) from None

    if result.get("error"):
        raise RuntimeError(f"OpenRouter web research failed: {result['error']}")
    usage = result.get("usage") or {}
    searches = (usage.get("server_tool_use") or {}).get("web_search_requests")
    choices = result.get("choices") or []
    choice = choices[0] if choices else {}
    message = choice.get("message") or {}
    text = message.get("content") or ""

    # A URL written by the model is not proof of retrieval. Only trust URL
    # citations supplied separately by OpenRouter in response annotations.
    sources = {}
    for annotation in message.get("annotations") or []:
        if annotation.get("type") != "url_citation":
            continue
        citation = annotation.get("url_citation") or {}
        url = citation.get("url")
        if (isinstance(url, str) and urlsplit(url).scheme in {"https", "http"}
                and urlsplit(url).netloc):
            sources[url] = citation.get("title") or url

    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "question_url": question_url,
        "generation_id": result.get("id"),
        "model": result.get("model", model),
        "cost_usd": usage.get("cost"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "web_search_requests": searches,
        "source_urls": list(sources),
    }
    # Save even rejected responses: an unsuccessful research call can cost money.
    folder = Path("forecast_reports")
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "research_usage.jsonl").open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
    logger.info("OpenRouter research usage (research only): %s", json.dumps(record))

    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("Web research returned no text; forecast blocked")
    if choice.get("finish_reason") == "length":
        raise RuntimeError("Web research was truncated; forecast blocked")
    if not sources or searches == 0:
        raise RuntimeError(
            "Web research returned no verified search evidence/API citations; "
            "forecast blocked. Do not enable publishing until a dry run passes."
        )
    citations = "\n".join(f"- {title}: {url}" for url, title in sources.items())
    return f"{text.strip()}\n\nSources returned by the search API:\n{citations}"
