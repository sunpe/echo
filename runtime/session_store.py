"""Shared mutable state for active chat sessions."""

echo_clients = {}
_chat_session_type = None


def register_chat_session_type(session_type):
    """Register the concrete Sublime session after its module is loaded."""
    global _chat_session_type
    _chat_session_type = session_type


def chat_session_type():
    if _chat_session_type is None:
        raise RuntimeError("Echo chat session type is not registered")
    return _chat_session_type


def create_chat_session(*args, **kwargs):
    """Create a session without making commands import the view module."""
    return chat_session_type()(*args, **kwargs)
