"""Exercise paid-run orchestration with every network/model operation mocked."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import superforecaster


def question():
    return SimpleNamespace(id_of_question=9001, id_of_post=9000,
                           page_url='https://www.metaculus.com/questions/9000',
                           already_forecasted=False,
                           open_time=datetime.now(timezone.utc)-timedelta(minutes=1),
                           close_time=datetime.now(timezone.utc)+timedelta(hours=1))


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_dir = os.getcwd()
        os.chdir(self.tmp.name)
        self.env = patch.dict(os.environ, {'PUBLISH_TO_METACULUS': 'true', 'MAX_QUESTIONS_PER_RUN': '5'}, clear=True)
        self.env.start()
        self.q = question()
        self.client = MagicMock()
        self.client.get_all_open_questions_from_tournament.side_effect = [[self.q], [self.q]]
        self.client.get_question_by_post_id.return_value = SimpleNamespace(**{**vars(self.q), 'already_forecasted': True})
        self.bot = MagicMock()
        self.bot.skip_previously_forecasted_questions = True
        self.bot.forecast_questions = AsyncMock(return_value=[SimpleNamespace(question=self.q)])
        self.budget = {'source': 'effective_budget', 'limit_usd': 100, 'spend_usd': 1, 'remaining_usd': 99}
        self.patches = [
            patch.object(superforecaster, 'MetaculusClient', return_value=self.client),
            patch.object(superforecaster, 'EnsembleFutureEvalBot', return_value=self.bot),
            patch.object(superforecaster, 'GeneralLlm'),
            patch.object(superforecaster, 'read_budget', return_value=self.budget),
            patch.object(superforecaster, 'print_run_summary_banner'),
            patch.object(superforecaster, 'print_startup_banner'),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.env.stop()
        os.chdir(self.old_dir)
        self.tmp.cleanup()

    def metrics(self):
        return json.loads(Path('forecast_reports/run_metrics.json').read_text())

    async def test_overlapping_projects_produce_one_verified_submission(self):
        await superforecaster.run('tournament')
        self.bot.forecast_questions.assert_awaited_once_with([self.q], return_exceptions=True)
        report = self.metrics()
        self.assertEqual(report['verified_posted'], 1)
        self.assertEqual(report['selection']['duplicates_removed'], 1)
        self.assertEqual(report['status'], 'completed')

    async def test_disabled_publishing_never_previews_a_live_question(self):
        os.environ['PUBLISH_TO_METACULUS'] = 'false'
        await superforecaster.run('tournament')
        self.bot.forecast_questions.assert_not_awaited()
        superforecaster.read_budget.assert_not_called()
        self.assertEqual(self.metrics()['status'], 'publishing_disabled_no_model_calls')

    async def test_empty_queue_makes_no_paid_calls(self):
        self.client.get_all_open_questions_from_tournament.side_effect = [[], []]
        await superforecaster.run('tournament')
        self.bot.forecast_questions.assert_not_awaited()
        superforecaster.read_budget.assert_not_called()
        self.assertEqual(self.metrics()['status'], 'no_eligible_questions')

    async def test_failed_prediction_still_records_final_balance(self):
        self.bot.forecast_questions.return_value = [ValueError('synthetic failure')]
        self.client.get_question_by_post_id.return_value = self.q
        self.bot.log_report_summary.side_effect = RuntimeError('failed forecast')
        with self.assertRaises(RuntimeError):
            await superforecaster.run('tournament')
        self.assertEqual(self.metrics()['failed'], 1)
        self.assertEqual(self.metrics()['budget_after'], self.budget)
        self.assertEqual(superforecaster.read_budget.call_count, 2)

    async def test_missing_publication_is_an_error_without_automatic_resubmission(self):
        self.client.get_question_by_post_id.return_value = self.q
        with self.assertRaisesRegex(RuntimeError, 'Publication not fully verified'):
            await superforecaster.run('tournament')
        self.bot.forecast_questions.assert_awaited_once()
        self.assertEqual(self.metrics()['verified_posted'], 0)

    async def test_unexpected_runner_error_still_saves_cost_evidence(self):
        self.bot.forecast_questions.side_effect = RuntimeError('synthetic crash')
        with self.assertRaisesRegex(RuntimeError, 'synthetic crash'):
            await superforecaster.run('tournament')
        self.assertEqual(self.metrics()['status'], 'runner_failed')
        self.assertIn('budget_after', self.metrics())

    async def test_sponsored_runner_rejects_cup_before_any_requests(self):
        with self.assertRaisesRegex(ValueError, 'reserved'):
            await superforecaster.run('metaculus_cup')
        self.client.get_all_open_questions_from_tournament.assert_not_called()
        self.bot.forecast_questions.assert_not_awaited()

    async def test_test_area_cannot_accidentally_publish(self):
        with self.assertRaisesRegex(ValueError, 'testing area'):
            await superforecaster.run('test_questions')
        self.bot.forecast_questions.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
