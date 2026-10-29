"""Offline checks for search activation and blocking ungrounded forecasts."""

import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from web_research import DEFAULT_RESEARCHER_MODEL, search_web


class WebResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.paths = patch("web_research.Path", side_effect=lambda name: Path(self.temp.name) / name)
        self.paths.start()
        self.addCleanup(self.paths.stop)
        self.http = patch("web_research.urlopen")
        self.urlopen = self.http.start()
        self.addCleanup(self.http.stop)
        self.result = {
            "id": "test-generation", "model": "openai/gpt-5-mini",
            "choices": [{"finish_reason": "stop", "message": {
                "content": "The latest observation is 42.",
                "annotations": [{"type": "url_citation", "url_citation": {
                    "url": "https://example.org/observations", "title": "Observations",
                }}],
            }}],
            "usage": {"cost": 0.012, "server_tool_use": {"web_search_requests": 1}},
        }

    def run_research(self, model=DEFAULT_RESEARCHER_MODEL):
        self.urlopen.return_value = io.BytesIO(json.dumps(self.result).encode())
        return asyncio.run(search_web("Research this question", model=model,
                                      question_url="https://www.metaculus.com/questions/45517"))

    def usage_record(self):
        file = Path(self.temp.name) / "forecast_reports" / "research_usage.jsonl"
        return json.loads(file.read_text())

    def test_search_is_explicit_bounded_and_uses_allowed_provider(self):
        research = self.run_research()
        request = self.urlopen.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(body["model"], "openai/gpt-5-mini")
        self.assertEqual(body["provider"], {"only": ["openai"], "allow_fallbacks": False})
        self.assertEqual(body["tools"][0]["type"], "openrouter:web_search")
        self.assertEqual(body["tools"][0]["parameters"]["engine"], "native")
        self.assertLessEqual(body["max_tool_calls"], 2)
        self.assertIn("https://example.org/observations", research)
        self.assertEqual(self.usage_record()["cost_usd"], 0.012)
        self.assertNotIn("test-key", json.dumps(self.usage_record()))

    def test_reasoning_is_bounded_and_original_prompt_preserved(self):
        self.run_research()
        body = json.loads(self.urlopen.call_args.args[0].data)
        self.assertEqual(body["reasoning"], {"effort": "low"})
        self.assertEqual(body["max_tokens"], 6000)
        self.assertEqual(body["messages"][-1]["content"], "Research this question")
        self.assertIn("conflicting evidence", body["messages"][0]["content"])
        self.assertEqual(self.urlopen.call_count, 1)

    def test_reasoning_exhaustion_is_logged_and_blocked_without_retry(self):
        self.result["choices"][0]["finish_reason"] = "length"
        self.result["choices"][0]["message"]["content"] = ""
        self.result["usage"]["completion_tokens_details"] = {"reasoning_tokens": 6000}
        with self.assertRaisesRegex(RuntimeError, "truncated"):
            self.run_research()
        record = self.usage_record()
        self.assertEqual(record["reasoning_tokens"], 6000)
        self.assertEqual(record["finish_reason"], "length")
        self.assertEqual(record["cost_usd"], 0.012)
        self.assertEqual(self.urlopen.call_count, 1)

    def test_model_written_url_does_not_substitute_for_api_citations(self):
        self.result["choices"][0]["message"] = {
            "content": "Sources: https://example.org/observations"
        }
        with self.assertRaisesRegex(RuntimeError, "forecast blocked"):
            self.run_research()
        self.assertEqual(self.usage_record()["cost_usd"], 0.012)
        self.assertEqual(self.urlopen.call_count, 1)  # No paid/plain-LLM fallback.

    def test_explicit_zero_searches_is_rejected_even_with_citation(self):
        self.result["usage"]["server_tool_use"]["web_search_requests"] = 0
        with self.assertRaisesRegex(RuntimeError, "forecast blocked"):
            self.run_research()

    def test_citations_work_when_provider_omits_search_counter(self):
        del self.result["usage"]["server_tool_use"]
        self.assertIn("Sources returned by the search API", self.run_research())
        self.assertIsNone(self.usage_record()["web_search_requests"])

    def test_missing_cost_remains_unknown_not_zero(self):
        del self.result["usage"]["cost"]
        self.run_research()
        self.assertIsNone(self.usage_record()["cost_usd"])

    def test_empty_or_truncated_response_is_blocked(self):
        self.result["choices"][0]["message"]["content"] = ""
        with self.assertRaisesRegex(RuntimeError, "no text"):
            self.run_research()
        self.result["choices"][0]["message"]["content"] = "Incomplete"
        self.result["choices"][0]["finish_reason"] = "length"
        with self.assertRaisesRegex(RuntimeError, "truncated"):
            self.run_research()

    def test_sonar_is_rejected_before_billable_request(self):
        with self.assertRaisesRegex(ValueError, "does not allow Perplexity/Sonar"):
            self.run_research("openrouter/perplexity/sonar")
        self.urlopen.assert_not_called()

    def test_missing_key_is_rejected_before_request(self):
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": ""}):
            with self.assertRaisesRegex(ValueError, "requires OPENROUTER_API_KEY"):
                self.run_research()
        self.urlopen.assert_not_called()

    def test_provider_error_is_actionable_and_not_retried(self):
        body = json.dumps({"error": {"message": "No allowed providers"}}).encode()
        self.urlopen.side_effect = HTTPError("https://openrouter.ai", 404, "Not Found", {}, io.BytesIO(body))
        with self.assertRaisesRegex(RuntimeError, "HTTP 404: No allowed providers"):
            self.run_research()
        self.assertEqual(self.urlopen.call_count, 1)


if __name__ == "__main__":
    unittest.main()
