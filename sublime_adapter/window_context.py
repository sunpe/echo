"""Window-scoped access to Echo views and live sessions."""

from ..shared.settings import ECHO_VIEW_FLAG
from ..runtime.session_store import echo_clients


class EchoWindowContext:
    def __init__(self, window):
        self.window = window

    @property
    def key(self):
        return self.window.id()

    @property
    def session(self):
        return echo_clients.get(self.key)

    def bind(self, session):
        echo_clients[self.key] = session
        return session

    def release(self, stop=False):
        session = echo_clients.pop(self.key, None)
        if stop and session is not None:
            session.stop()
        return session

    def echo_view(self):
        for view in self.window.views():
            if view.settings().get(ECHO_VIEW_FLAG, False):
                return view
        return None
