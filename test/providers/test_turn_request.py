import unittest

from echo.providers.codex.turn_request import TurnContext, build_turn_params, rollback_count


class TurnRequestTest(unittest.TestCase):
    def test_plan_execution_explicitly_returns_to_default_mode(self):
        context = TurnContext("thread", "turn", "gpt", "rules", True)

        params = build_turn_params(context, "implement", proceed_plan=True)

        self.assertEqual("default", params["collaborationMode"]["mode"])
        self.assertEqual("turn", params["expectedTurnId"])

    def test_rollback_count_keeps_turns_before_target(self):
        self.assertEqual(3, rollback_count(5, 3))
        self.assertEqual(0, rollback_count(2, 4))


if __name__ == "__main__":
    unittest.main()
