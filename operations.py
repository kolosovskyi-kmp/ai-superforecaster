"""Deadline selection and account-level cost evidence; no forecasting logic."""

import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def question_key(question):
    qid = getattr(question, "id_of_question", None)
    if qid is not None:
        return ("question", qid)
    yes = getattr(question, "question_yes", None)
    no = getattr(question, "question_no", None)
    if yes is not None and no is not None:
        return ("conditional", getattr(yes, "id_of_question", None), getattr(no, "id_of_question", None))
    raise ValueError("Question has no API identity; refusing ambiguous deduplication")


def describe_question(question):
    return {
        "question_key": list(question_key(question)),
        "post_id": getattr(question, "id_of_post", None),
        "url": getattr(question, "page_url", None),
        "type": getattr(question, "question_type", type(question).__name__),
        "close_time": utc(getattr(question, "close_time", None)).isoformat()
        if getattr(question, "close_time", None) else None,
    }


def select_questions(questions, limit, *, now=None, enforce_window=True, skip_forecasted=True):
    """Select unique unforecasted questions by earliest closing deadline.

    Forecast history from either copy wins when a post is in both projects.
    Different subquestions of a group are preserved by their question IDs.
    """
    if limit < 1:
        raise ValueError("Question limit must be positive")
    now = utc(now or datetime.now(timezone.utc))
    grouped = {}
    for question in questions:
        grouped.setdefault(question_key(question), []).append(question)
    eligible, skipped = [], []
    for copies in grouped.values():
        question = copies[0]
        reason = None
        if skip_forecasted and any(getattr(q, "already_forecasted", None) is True for q in copies):
            reason = "already_forecasted"
        elif skip_forecasted and getattr(question, "already_forecasted", None) is None:
            reason = "forecast_history_unknown"
        elif enforce_window:
            opened = utc(getattr(question, "open_time", None))
            closed = utc(getattr(question, "close_time", None))
            if opened and opened > now:
                reason = "not_open_yet"
            elif closed is None:
                reason = "missing_close_time"
            elif closed <= now:
                reason = "deadline_passed"
        if reason:
            skipped.append({**describe_question(question), "reason": reason})
        else:
            eligible.append(question)
    eligible.sort(key=lambda q: (utc(getattr(q, "close_time", None)) or datetime.max.replace(tzinfo=timezone.utc), str(question_key(q))))
    selected, deferred = eligible[:limit], eligible[limit:]
    return selected, {
        "discovered": len(questions), "duplicates_removed": len(questions) - len(grouped),
        "eligible": len(eligible), "selected": [describe_question(q) for q in selected],
        "deferred": [describe_question(q) for q in deferred], "skipped": skipped,
    }


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def normalize_budget(data):
    """Prefer a sponsored key's effective guardrail over misleading usage=0."""
    effective = data.get("effective_budget") or {}
    remaining = effective.get("remaining_usd")
    limit = effective.get("limit_usd")
    spend = effective.get("spend_usd")
    source = "effective_budget"
    if not number(remaining):
        remaining, limit, spend = data.get("limit_remaining"), data.get("limit"), None
        source = "key_limit"
        if number(limit) and number(remaining):
            spend = limit - remaining
    return {
        "observed_utc": datetime.now(timezone.utc).isoformat(), "source": source,
        "remaining_usd": remaining if number(remaining) else None,
        "limit_usd": limit if number(limit) else None,
        "spend_usd": spend if number(spend) else None,
        "reset_interval": effective.get("reset_interval") if source == "effective_budget" else data.get("limit_reset"),
        "resets_at": effective.get("resets_at"),
    }


def read_budget():
    request = Request("https://openrouter.ai/api/v1/key", headers={
        "Authorization": "Bearer " + os.environ.get("OPENROUTER_API_KEY", ""),
        "Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=20) as response:
            return normalize_budget(json.load(response)["data"])
    except (HTTPError, URLError, TimeoutError, KeyError, ValueError) as exc:
        # Do not dump response bodies, headers or tokens into artifacts/logs.
        logger.warning("OpenRouter balance unavailable (%s); recorded as unknown", type(exc).__name__)
        return {"observed_utc": datetime.now(timezone.utc).isoformat(), "error": type(exc).__name__}


def budget_delta(before, after):
    """Account charge delta, not an invented per-question price.

    It may lag provider billing or include another user of the same key.
    Changes in funding or reset period cannot be treated as request charges.
    """
    if before.get("error") or after.get("error"):
        return None
    if any(before.get(k) != after.get(k) for k in ("source", "limit_usd", "reset_interval", "resets_at")):
        return None
    old, new = before.get("spend_usd"), after.get("spend_usd")
    if not number(old) or not number(new) or new < old:
        return None
    return round(new - old, 9)


def save_run(report):
    folder = Path("forecast_reports")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "run_metrics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary and report.get("status") != "started":
        selection = report.get("selection", {})
        text = ("\n### Forecast operations\n\n"
                f"Status: **{report.get('status')}**. Eligible: {selection.get('eligible', 0)}; "
                f"selected: {len(selection.get('selected', []))}; deferred: {len(selection.get('deferred', []))}.\n\n"
                f"Completed reports: {report.get('completed', 0)}; failed: {report.get('failed', 0)}; "
                f"verified posted: {report.get('verified_posted', 0)}.\n\n"
                f"Observed account charge delta (may lag billing): {report.get('observed_account_charge_usd')}. "
                f"Remaining credit: {report.get('budget_after', {}).get('remaining_usd')}. "
                "Full evidence: `run_metrics.json`.\n")
        with open(summary, "a", encoding="utf-8") as file:
            file.write(text)
