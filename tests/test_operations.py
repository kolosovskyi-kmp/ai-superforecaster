import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from operations import budget_delta, normalize_budget, select_questions

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


def question(qid, minutes=60, forecasted=False, post=100):
    return SimpleNamespace(id_of_question=qid, id_of_post=post,
                           page_url=f"https://www.metaculus.com/questions/{post}",
                           open_time=NOW-timedelta(hours=1),
                           close_time=NOW+timedelta(minutes=minutes),
                           already_forecasted=forecasted)


class SelectionTests(unittest.TestCase):
    def test_earliest_deadline_wins_and_cap_deferrals_are_explicit(self):
        selected, ledger = select_questions([question(1, 90), question(2, 10)], 1, now=NOW)
        self.assertEqual([q.id_of_question for q in selected], [2])
        self.assertEqual(ledger['deferred'][0]['question_key'], ['question', 1])
        self.assertEqual(ledger['eligible'], 2)

    def test_overlap_does_not_erase_group_subquestions(self):
        selected, ledger = select_questions([question(1), question(1), question(2)], 5, now=NOW)
        self.assertEqual(len(selected), 2)
        self.assertEqual(ledger['duplicates_removed'], 1)

    def test_forecast_history_on_either_project_copy_prevents_reposting(self):
        selected, ledger = select_questions([question(1), question(1, forecasted=True)], 5, now=NOW)
        self.assertEqual(selected, [])
        self.assertEqual(ledger['skipped'][0]['reason'], 'already_forecasted')

    def test_expired_future_and_unknown_history_are_not_forecasted(self):
        future = question(3)
        future.open_time = NOW + timedelta(minutes=5)
        selected, ledger = select_questions([question(1, 0), question(2, forecasted=None), future], 5, now=NOW)
        self.assertEqual(selected, [])
        self.assertEqual({q['reason'] for q in ledger['skipped']}, {'deadline_passed', 'forecast_history_unknown', 'not_open_yet'})

    def test_testing_area_can_repeat_practice_questions(self):
        selected, _ = select_questions([question(1, -5, True)], 1, now=NOW,
                                       enforce_window=False, skip_forecasted=False)
        self.assertEqual(len(selected), 1)

    def test_missing_ids_fail_instead_of_merging_unrelated_questions(self):
        q = question(None)
        with self.assertRaises(ValueError):
            select_questions([q], 5, now=NOW)


class BudgetTests(unittest.TestCase):
    def test_effective_budget_overrides_misleading_zero_usage(self):
        budget = normalize_budget({'usage': 0, 'limit_remaining': 100,
                                   'effective_budget': {'limit_usd': 100, 'remaining_usd': 99.47, 'spend_usd': .53}})
        self.assertEqual(budget['remaining_usd'], 99.47)
        self.assertEqual(budget['spend_usd'], .53)

    def test_fallback_does_not_treat_zero_usage_as_free(self):
        budget = normalize_budget({'limit': 100, 'limit_remaining': 99.47, 'usage': 0})
        self.assertAlmostEqual(budget['spend_usd'], .53)
        self.assertIsNone(normalize_budget({'usage': 0})['remaining_usd'])

    def test_actual_charge_unknown_after_reset_topup_or_failed_read(self):
        before = normalize_budget({'limit': 100, 'limit_remaining': 99.5})
        after = normalize_budget({'limit': 100, 'limit_remaining': 99.4})
        self.assertAlmostEqual(budget_delta(before, after), .1)
        self.assertIsNone(budget_delta(before, {**after, 'limit_usd': 200}))
        self.assertIsNone(budget_delta(before, {'error': 'TimeoutError'}))
        self.assertIsNone(budget_delta(after, before))


if __name__ == '__main__':
    unittest.main()
