import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import audit_bot


class AuditTests(unittest.TestCase):
    def test_group_subquestions_are_separate_and_duplicate_posts_are_removed(self):
        post = {'id': 1, 'group_of_questions': {'questions': [
            {'id': 11, 'type': 'binary', 'my_forecasts': {'history': [{}]}},
            {'id': 12, 'type': 'numeric'}]}}
        rows = audit_bot.question_rows([post, post], {1})
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]['forecasted'])
        self.assertFalse(rows[1]['forecasted'])

    def test_conditional_audit_includes_both_submission_branches(self):
        post = {'id': 1, 'conditional': {'question_yes': {'id': 11, 'type': 'binary'},
                                       'question_no': {'id': 12, 'type': 'binary'}}}
        self.assertEqual(len(audit_bot.question_rows([post], set())), 2)

    def test_preflight_exposes_pending_queue_and_effective_credit(self):
        now = datetime.now(timezone.utc)
        post = {'id': 1, 'question': {'id': 11, 'type': 'binary', 'status': 'open',
                'open_time': (now-timedelta(minutes=1)).isoformat(),
                'scheduled_close_time': (now+timedelta(hours=2)).isoformat()}}
        with tempfile.TemporaryDirectory() as folder:
            output = str(Path(folder)/'audit.json')
            github_output = str(Path(folder)/'outputs')
            with patch.dict(os.environ, {'BOT_AUDIT_OUTPUT': output, 'GITHUB_OUTPUT': github_output, 'GITHUB_STEP_SUMMARY': ''}), \
                 patch.object(audit_bot, 'get_json', side_effect=[{'data': {'usage': 0, 'effective_budget': {'remaining_usd': 99.47, 'spend_usd': .53}}}, {'id': 308654}]), \
                 patch.object(audit_bot, 'list_posts', side_effect=[[post], [], [], []]), redirect_stdout(StringIO()):
                report = audit_bot.main(preflight=True)
            self.assertEqual(len(report['pending_open']), 1)
            self.assertEqual(json.loads(Path(output).read_text())['budget']['spend_usd'], .53)
            self.assertIn('has_pending=true', Path(github_output).read_text())

    def test_wrong_bot_token_is_a_failed_check(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {'BOT_AUDIT_OUTPUT': str(Path(folder)/'audit.json'), 'GITHUB_OUTPUT': '', 'GITHUB_STEP_SUMMARY': ''}), \
                 patch.object(audit_bot, 'get_json', side_effect=[{'data': {}}, {'id': 123}]), redirect_stdout(StringIO()):
                with self.assertRaises(SystemExit):
                    audit_bot.main(preflight=True)
