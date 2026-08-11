import unittest
from unittest.mock import AsyncMock, MagicMock

from echo.domain.messages.message import CodexAgentOptions
from echo.providers.codex.client import CodexAgent


class ProtocolApprovalTest(unittest.IsolatedAsyncioTestCase):
    def agent(self, blocked=()):
        return CodexAgent(CodexAgentOptions(
            app_server_url="ws://127.0.0.1:4500",
            disallowed_tools=list(blocked),
        ))

    async def test_command_and_file_requests_use_common_decision_reply(self):
        agent = self.agent()
        agent._approvals.ask = AsyncMock(return_value=True)
        agent._rpc.respond = AsyncMock()
        agent._item_cache["change-1"] = {"changes": []}
        agent._generate_file_change_diff = MagicMock(
            return_value={"files": ["one.py"]}
        )

        await agent._handle_command_approval(10, {"command": "pwd"})
        await agent._handle_file_approval(11, {"itemId": "change-1"})

        self.assertEqual(
            ["command_execution", "fileChange"],
            [call.args[1] for call in agent._approvals.ask.await_args_list],
        )
        self.assertEqual(
            {"processed_diff": {"files": ["one.py"]}, "itemId": "change-1"},
            agent._approvals.ask.await_args_list[1].args[2],
        )
        self.assertEqual(
            [
                unittest.mock.call(10, {"decision": "accept"}),
                unittest.mock.call(11, {"decision": "accept"}),
            ],
            agent._rpc.respond.await_args_list,
        )

    async def test_blocked_questions_receive_noninteractive_answers(self):
        agent = self.agent(blocked=("AskUserQuestion",))
        agent._approvals.ask = AsyncMock()
        agent._rpc.respond = AsyncMock()

        await agent._handle_request_user_input(12, {
            "questions": [{"id": "choice", "question": "Choose?"}],
        })

        agent._approvals.ask.assert_not_awaited()
        reply = agent._rpc.respond.await_args.args[1]
        self.assertEqual(["choice"], list(reply["answers"]))
        self.assertTrue(reply["answers"]["choice"]["answers"][0])


if __name__ == "__main__":
    unittest.main()
