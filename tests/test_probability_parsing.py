import unittest

from probability_parsing import parse_final_probability


class ProbabilityParsingTests(unittest.TestCase):
    def test_reported_failure_keeps_17_point_1_distinct_from_17(self):
        self.assertEqual(parse_final_probability("Probability: 17.1%"), 0.171)
        self.assertEqual(parse_final_probability("Probability: 17%"), 0.17)

    def test_prior_and_intermediate_probabilities_do_not_change_final_answer(self):
        self.assertEqual(parse_final_probability(
            "Prior: 50%. Conditional estimate: 17%.\nProbability: 17.1%"
        ), 0.171)

    def test_markdown_and_blank_lines_are_supported(self):
        for text in [
            "**Probability: 17.1%**\n\n",
            "**Probability:** **17.1%**",
            '"Probability: 17.1%"',
            "```text\nProbability: 17.1%\n```",
        ]:
            with self.subTest(text=text):
                self.assertEqual(parse_final_probability(text), 0.171)

    def test_explicit_final_label_at_end_of_sentence_is_accepted(self):
        self.assertEqual(parse_final_probability(
            "The evidence is limited. Probability: 12%"
        ), 0.12)
        self.assertEqual(parse_final_probability(
            "Prior was 50%; final assessment. **Probability: 17.1%**."
        ), 0.171)

    def test_multiple_final_labels_and_trailing_qualifiers_stay_ambiguous(self):
        for text in ["Probability: 12% or Probability: 15%",
                     "Conclusion. Probability: 12–15%",
                     "Conclusion. Probability: 12%, perhaps higher"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_final_probability(text)

    def test_decimal_comma_and_precision(self):
        self.assertEqual(parse_final_probability("Probability: 17,125%"), 0.17125)
        self.assertEqual(parse_final_probability("Probability: 0.171%"), 0.00171)
        self.assertEqual(parse_final_probability("Probability: .5%"), 0.005)

    def test_endpoints_are_valid_before_callers_apply_existing_bounds(self):
        self.assertEqual(parse_final_probability("Probability: 0%"), 0)
        self.assertEqual(parse_final_probability("Probability: 100%"), 1)

    def test_ambiguous_ranges_or_missing_percent_unit_are_rejected(self):
        for text in ["Probability: 17-20%", "Probability: 0.171", "17.1%",
                     "Probability: 17% or 17.1%", "Probability: approximately 17%"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_final_probability(text)

    def test_no_fallback_to_a_percentage_in_prose(self):
        for text in ["", "My estimate is 17.1%.",
                     "Probability: 17%\nActually I am unsure whether 17% or 17.1%."]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_final_probability(text)

    def test_invalid_probabilities_are_rejected(self):
        for text in ["Probability: -1%", "Probability: 100.1%",
                     "Probability: NaN%", "Probability: Infinity%"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_final_probability(text)


if __name__ == "__main__":
    unittest.main()
