"""Cost-aware ensemble forecaster for Metaculus FutureEval.

Binary questions use four genuinely independent perspectives and a transparent
log-odds ensemble. Other question types retain the official Metaculus template
implementation until we have enough resolved forecasts to justify changes.
"""

import argparse
import asyncio
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Literal

import dotenv
from forecasting_tools import (
    BinaryPrediction,
    BinaryQuestion,
    GeneralLlm,
    MetaculusClient,
    ReasonedPrediction,
    clean_indents,
    structure_output,
)

from bot_helpers import (
    check_environment,
    print_run_summary_banner,
    print_startup_banner,
    silence_noisy_dependencies,
)
from main import SummerTemplateBot2026

dotenv.load_dotenv()
silence_noisy_dependencies()
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ForecastRole:
    name: str
    weight: float
    instructions: str


ROLES = (
    ForecastRole(
        "base-rate",
        1.25,
        "Start from the closest defensible reference class and historical base rate. "
        "Move away from it only for concrete differences in this case.",
    ),
    ForecastRole(
        "inside-view",
        1.0,
        "Build a causal timeline from the current facts to the resolution date. "
        "Estimate the probability of each necessary step and bottleneck.",
    ),
    ForecastRole(
        "red-team",
        1.0,
        "Challenge the obvious narrative. Look for missing evidence, selection bias, "
        "strategic behavior, and plausible surprise paths to both YES and NO.",
    ),
    ForecastRole(
        "resolution-auditor",
        0.9,
        "Read the resolution criteria literally. Separate whether the real-world event "
        "happens from whether the stated evidence will make the question resolve YES.",
    ),
)


def weighted_log_odds(probabilities: list[float], weights: list[float]) -> float:
    """Pool probabilities geometrically in odds space."""
    if not probabilities or len(probabilities) != len(weights):
        raise ValueError("probabilities and weights must be non-empty and equal length")
    logits = []
    for probability in probabilities:
        p = min(0.99, max(0.01, probability))
        logits.append(math.log(p / (1 - p)))
    pooled_logit = sum(w * x for w, x in zip(weights, logits)) / sum(weights)
    return 1 / (1 + math.exp(-pooled_logit))


def calibrate_probability(probability: float, shrinkage: float = 0.85) -> float:
    """Conservatively shrink log-odds toward 50%; tune on resolved forecasts later."""
    p = min(0.99, max(0.01, probability))
    logit = math.log(p / (1 - p)) * shrinkage
    return min(0.99, max(0.01, 1 / (1 + math.exp(-logit))))


class EnsembleFutureEvalBot(SummerTemplateBot2026):
    async def _role_forecast(
        self, question: BinaryQuestion, research: str, role: ForecastRole
    ) -> tuple[ForecastRole, float, str]:
        prompt = clean_indents(
            f"""
            You are the {role.name} member of an independent forecasting team.
            Do not imitate a consensus and do not assume other forecasters' answers.

            Your method:
            {role.instructions}

            Question: {question.question_text}
            Background: {question.background_info}
            Resolution criteria: {question.resolution_criteria}
            Fine print: {question.fine_print}
            Research packet: {research}
            Today: {datetime.now().strftime('%Y-%m-%d')}

            Give a compact rationale. Explicitly state your prior, strongest evidence,
            strongest counterargument, and what would change your estimate. Avoid 0% and
            100% unless the outcome is logically settled.
            End with exactly: Probability: ZZ%
            """
        )
        reasoning = await self.get_llm("default", "llm").invoke(prompt)
        parsed: BinaryPrediction = await structure_output(
            reasoning,
            BinaryPrediction,
            model=self.get_llm("parser", "llm"),
            num_validation_samples=self._structure_output_validation_samples,
        )
        probability = min(0.99, max(0.01, parsed.prediction_in_decimal))
        return role, probability, reasoning

    async def _run_forecast_on_binary(
        self, question: BinaryQuestion, research: str
    ) -> ReasonedPrediction[float]:
        results = await asyncio.gather(
            *(self._role_forecast(question, research, role) for role in ROLES)
        )
        probabilities = [result[1] for result in results]
        weights = [result[0].weight for result in results]
        pooled = weighted_log_odds(probabilities, weights)
        shrinkage = float(os.getenv("CALIBRATION_SHRINKAGE", "0.85"))
        final_probability = calibrate_probability(pooled, shrinkage)

        spread = max(probabilities) - min(probabilities)
        role_lines = "\n".join(
            f"- {role.name}: {probability:.1%}" for role, probability, _ in results
        )
        reasoning = clean_indents(
            f"""
            Ensemble forecast from independent roles:
            {role_lines}

            Median: {median(probabilities):.1%}; disagreement spread: {spread:.1%}.
            Weighted log-odds pool before calibration: {pooled:.1%}.
            Final probability after {shrinkage:.2f} log-odds shrinkage: {final_probability:.1%}.

            Individual rationales:
            """
        ) + "\n\n" + "\n\n".join(
            f"## {role.name}\n{text}" for role, _, text in results
        )
        logger.info("Binary ensemble for %s: %.3f", question.page_url, final_probability)
        return ReasonedPrediction(
            prediction_value=final_probability,
            reasoning=reasoning,
        )


async def run(mode: Literal["tournament", "metaculus_cup", "test_questions"]) -> None:
    publish = os.getenv("PUBLISH_TO_METACULUS", "false").lower() == "true"
    max_questions = int(os.getenv("MAX_QUESTIONS_PER_RUN", "1"))
    if max_questions < 1:
        raise ValueError("MAX_QUESTIONS_PER_RUN must be at least 1")
    print_startup_banner(mode, will_publish=publish)
    bot = EnsembleFutureEvalBot(
        research_reports_per_question=1,
        predictions_per_research_report=1,
        use_research_summary_to_forecast=False,
        publish_reports_to_metaculus=publish,
        folder_to_save_reports_to="forecast_reports",
        skip_previously_forecasted_questions=True,
        extra_metadata_in_explanation=True,
        llms={
            "default": GeneralLlm(
                model=os.getenv("FORECAST_MODEL", "openrouter/openai/gpt-5-mini"),
                temperature=0.35,
                timeout=120,
                allowed_tries=2,
            ),
            "researcher": os.getenv(
                "RESEARCHER_MODEL", "openrouter/perplexity/sonar"
            ),
            "parser": os.getenv("PARSER_MODEL", "openrouter/openai/gpt-5-mini"),
        },
    )
    client = MetaculusClient()
    if mode == "tournament":
        questions = client.get_all_open_questions_from_tournament(
            client.CURRENT_AI_COMPETITION_ID
        )
        questions += client.get_all_open_questions_from_tournament(
            client.CURRENT_MINIBENCH_ID
        )
        url = "https://www.metaculus.com/tournament/fall-futureeval-2026/"
    elif mode == "metaculus_cup":
        bot.skip_previously_forecasted_questions = False
        questions = client.get_all_open_questions_from_tournament(
            client.CURRENT_METACULUS_CUP_ID
        )
        url = "https://www.metaculus.com/tournament/"
    else:
        bot.skip_previously_forecasted_questions = False
        questions = client.get_all_open_questions_from_tournament(
            "bot-testing-area"
        )
        url = "https://www.metaculus.com/tournament/bot-testing-area/"

    if bot.skip_previously_forecasted_questions:
        questions = [question for question in questions if not question.already_forecasted]
    selected_questions = questions[:max_questions]
    logger.info(
        "Selected %d of %d eligible question(s); MAX_QUESTIONS_PER_RUN=%d",
        len(selected_questions),
        len(questions),
        max_questions,
    )
    reports = await bot.forecast_questions(
        selected_questions, return_exceptions=True
    )
    bot.log_report_summary(reports)
    print_run_summary_banner(reports, will_publish=publish, tournament_url=url)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["tournament", "metaculus_cup", "test_questions"],
        default="test_questions",
    )
    arguments = parser.parse_args()
    check_environment(strict=True)
    asyncio.run(run(arguments.mode))
