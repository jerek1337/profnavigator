import unittest

from core import content as C
from core.matching import answer_label, match_option, match_professions, parse_grade, pick_profile
from core.text import sanitize_ai, split_text


class MatchingTest(unittest.TestCase):
    def test_rules_table_from_spec(self):
        cases = {
            ("math", "analysis"): ["fin_analyst", "data_scientist"],
            ("cs", "create"): ["fintech_dev", "blockchain_eng"],
            ("social", "people"): ["economist", "fin_lawyer"],
            ("other", "analysis"): ["risk_manager", "esg_analyst"],
        }
        for (subject, skills), expected in cases.items():
            self.assertEqual(match_professions({"subject": subject, "skills": skills}), expected)

    def test_every_combination_gives_two_different_professions(self):
        for s_code, _ in C.QUESTIONS[0].options:
            for k_code, _ in C.QUESTIONS[1].options:
                for i_code, _ in C.QUESTIONS[2].options + (("custom", ""),):
                    result = match_professions({"subject": s_code, "skills": k_code, "interest": i_code})
                    self.assertEqual(len(result), 2)
                    self.assertEqual(len(set(result)), 2)
                    self.assertTrue(all(p in C.PROFESSIONS for p in result))

    def test_profile(self):
        self.assertEqual(pick_profile(["fintech_dev", "blockchain_eng"], {}), 2)
        self.assertEqual(pick_profile(["economist", "fin_lawyer"], {}), 1)
        self.assertEqual(pick_profile(["fin_analyst", "data_scientist"], {"interest": "tech"}), 2)
        self.assertEqual(pick_profile(["fin_analyst", "data_scientist"], {"interest": "money"}), 1)

    def test_match_option_and_grade(self):
        q = C.QUESTIONS[1]
        self.assertEqual(match_option(q, "анализировать данные"), "analysis")
        self.assertEqual(match_option(q, "  Решать задачи!! 🧩"), "tasks")
        self.assertEqual(match_option(q, "созда"), "create")
        self.assertIsNone(match_option(q, "танцевать"))
        self.assertEqual(parse_grade("я в 10 классе"), 10)
        self.assertIsNone(parse_grade("5"))
        self.assertIsNone(parse_grade("не знаю"))

    def test_answer_label_prefers_own_text(self):
        self.assertEqual(answer_label("subject", {"subject": "other", "subject_text": "История"}), "История")
        self.assertEqual(answer_label("subject", {"subject": "math"}), "Математика")

    def test_text_utils(self):
        self.assertEqual(sanitize_ai("## Итог\n**Важно**: `код`\n- пункт"), "Итог\nВажно: код\n• пункт")
        parts = split_text("строка\n" * 2000, limit=500)
        self.assertTrue(all(len(p) <= 500 for p in parts))
        self.assertEqual("\n".join(parts).count("строка"), 2000)


if __name__ == "__main__":
    unittest.main()
