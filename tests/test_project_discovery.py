import unittest
from unittest.mock import MagicMock

from project_discovery import discover_open_questions


class ProjectDiscoveryTests(unittest.TestCase):
    def test_either_series_can_fail_without_losing_other_questions(self):
        for values in [[['fall-question'], RuntimeError()], [RuntimeError(), ['mini-question']]]:
            client = MagicMock()
            client.get_all_open_questions_from_tournament.side_effect = values
            questions, errors = discover_open_questions(client, ['fall', 'mini'])
            self.assertEqual(len(questions), 1)
            self.assertEqual(len(errors), 1)

    def test_both_failures_are_not_a_successful_empty_queue(self):
        client = MagicMock()
        client.get_all_open_questions_from_tournament.side_effect = RuntimeError()
        with self.assertRaisesRegex(RuntimeError, 'All tournament'):
            discover_open_questions(client, ['fall', 'mini'])

    def test_same_project_is_not_requested_twice(self):
        client = MagicMock()
        client.get_all_open_questions_from_tournament.return_value = ['question']
        self.assertEqual(discover_open_questions(client, ['fall', 'fall']), (['question'], []))
        client.get_all_open_questions_from_tournament.assert_called_once_with('fall')
