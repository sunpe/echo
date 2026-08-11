"""Explicit lifecycle for the background agent owned by a chat session."""

import enum


class RuntimePhase(enum.Enum):
    CREATED = "created"
    CONNECTING = "connecting"
    ACTIVE = "active"
    FAILED = "failed"
    STOPPED = "stopped"


class SessionRuntime:
    def __init__(self, on_phase_change=None):
        self._agent = None
        self._phase = RuntimePhase.CREATED
        self._on_phase_change = on_phase_change

    @property
    def agent(self):
        return self._agent

    @property
    def phase(self):
        return self._phase

    def _transition(self, phase):
        self._phase = phase
        if self._on_phase_change is not None:
            self._on_phase_change(phase)

    def launch(self, factory):
        """Replace the current agent and start the newly constructed one."""
        self.shutdown(notify=False)
        self._transition(RuntimePhase.CONNECTING)
        try:
            candidate = factory()
            self._agent = candidate
            candidate.start()
        except Exception:
            self._agent = None
            self._transition(RuntimePhase.FAILED)
            raise
        self._transition(RuntimePhase.ACTIVE)
        return candidate

    def shutdown(self, notify=True):
        current, self._agent = self._agent, None
        if current is not None:
            current.stop()
        if notify:
            self._transition(RuntimePhase.STOPPED)

    def resumable_session(self, override=None, preserve=True):
        if override is not None:
            return override
        if preserve and self._agent is not None:
            return self._agent.session_id
        return None
