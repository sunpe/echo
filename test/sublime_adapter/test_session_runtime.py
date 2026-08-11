import unittest

from echo.domain.conversation.session_runtime import RuntimePhase, SessionRuntime


class FakeAgent:
    def __init__(self, session_id="session-1", fail=False):
        self.session_id = session_id
        self.fail = fail
        self.started = False
        self.stopped = False

    def start(self):
        if self.fail:
            raise RuntimeError("cannot start")
        self.started = True

    def stop(self):
        self.stopped = True


class SessionRuntimeTest(unittest.TestCase):
    def test_replacement_stops_previous_agent_before_start(self):
        phases = []
        runtime = SessionRuntime(phases.append)
        first = runtime.launch(FakeAgent)
        second = runtime.launch(lambda: FakeAgent("session-2"))

        self.assertTrue(first.stopped)
        self.assertTrue(second.started)
        self.assertIs(second, runtime.agent)
        self.assertEqual(RuntimePhase.ACTIVE, runtime.phase)

    def test_failed_launch_has_no_live_agent(self):
        runtime = SessionRuntime()

        with self.assertRaises(RuntimeError):
            runtime.launch(lambda: FakeAgent(fail=True))

        self.assertIsNone(runtime.agent)
        self.assertEqual(RuntimePhase.FAILED, runtime.phase)

    def test_resume_choice_is_owned_by_runtime(self):
        runtime = SessionRuntime()
        runtime.launch(lambda: FakeAgent("stored"))

        self.assertEqual("override", runtime.resumable_session("override"))
        self.assertEqual("stored", runtime.resumable_session())
        self.assertIsNone(runtime.resumable_session(preserve=False))


if __name__ == "__main__":
    unittest.main()
