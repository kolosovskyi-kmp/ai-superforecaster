"""Read an explicit final binary probability without a second LLM inference."""

import re
from decimal import Decimal


def parse_final_probability(reasoning: str) -> float:
    """Parse the final ``Probability: ZZ%`` line, preserving its precision.

    Earlier percentages belong to the rationale. A range, missing units, or
    missing final answer is an error, not a reason to guess or average values.
    The caller retains its existing 1%-99% publication bounds.
    """
    lines = [line.strip() for line in reasoning.splitlines() if line.strip()]
    # Accept a closing Markdown fence around the requested final answer.
    while lines and lines[-1] == "```":
        lines.pop()
    final_line = lines[-1] if lines else ""
    final_line = re.sub(r"[*_`]", "", final_line).strip().strip("\"'")
    match = re.fullmatch(
        r"Probability\s*:\s*([+-]?(?:[0-9]+(?:[.,][0-9]+)?|[.,][0-9]+))\s*%\s*\.?",
        final_line,
        flags=re.IGNORECASE,
    )
    if not match:
        raise ValueError(
            "Expected a single final line 'Probability: ZZ%' with an explicit "
            "percentage; refusing to infer or round a probability from prose."
        )
    percent = Decimal(match.group(1).replace(",", "."))
    if not Decimal(0) <= percent <= Decimal(100):
        raise ValueError("Final probability must be between 0% and 100%")
    return float(percent / Decimal(100))
