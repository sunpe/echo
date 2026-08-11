import unittest

from echo.domain.conversation.permission_flow import PermissionFlow, PermissionRoute


class PermissionFlowTest(unittest.TestCase):
    def setUp(self):
        self.replies = []
        self.dismissed = []
        self.implemented = []
        self.flow = PermissionFlow(
            lambda *value: self.replies.append(value),
            self.dismissed.append,
            lambda: self.implemented.append(True),
        )

    def test_local_confirmation_rule_wins_over_auto_approve(self):
        route = self.flow.open(
            "one",
            "local_workspace.write",
            {"path": "a.py"},
            "allow-edit",
            {"always_confirm": ["write"], "auto_approve": ["write"]},
        )

        self.assertIs(PermissionRoute.CONFIRM, route)
        self.assertEqual([], self.replies)

    def test_accept_all_replies_without_opening_a_card(self):
        route = self.flow.open(
            "two", "command_execution", {"command": "pwd"}, "accept-all"
        )

        self.assertIs(PermissionRoute.SILENT_ALLOW, route)
        self.assertEqual("allow", self.replies[0][1]["behavior"])

    def test_allow_chat_changes_policy_for_later_requests(self):
        self.flow.open("first", "command_execution", {}, "default")
        self.flow.decide("first", "allow_chat")
        route = self.flow.open("second", "unknown", {}, "default")

        self.assertIs(PermissionRoute.SILENT_ALLOW, route)
        self.assertEqual(["first"], self.dismissed)

    def test_question_answers_are_merged_with_original_arguments(self):
        self.flow.open(
            "question", "AskUserQuestion", {"questions": [1]}, "default"
        )
        self.flow.answer("question", {"language": "Python"})

        updated = self.replies[0][1]["updatedInput"]
        self.assertEqual([1], updated["questions"])
        self.assertEqual({"language": "Python"}, updated["answers"])


if __name__ == "__main__":
    unittest.main()
