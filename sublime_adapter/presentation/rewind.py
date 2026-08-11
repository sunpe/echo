"""Conversation checkpoint forking independent of the session facade."""

import logging

import sublime


LOG = logging.getLogger("echo")


class ConversationFork:
    def __init__(self, session, transcript_prefix):
        self._session = session
        self._prefix_width = len(transcript_prefix)

    def request(self, checkpoint_index):
        session = self._session
        if not session.provider.capabilities.rewind:
            sublime.status_message(
                "{} does not support conversation rewind".format(
                    session.provider.name
                )
            )
            return False

        target = self._target(checkpoint_index)
        if target is None:
            return False
        agent, message_id = target
        session.end_activity()
        agent.fork(
            message_id,
            on_done=lambda fork_id: self._receive_fork(
                checkpoint_index, fork_id
            ),
        )
        return True

    def _target(self, checkpoint_index):
        session = self._session
        if checkpoint_index not in range(len(session.checkpoints)):
            return None
        agent = session.agent_thread
        session_id = agent.session_id if agent is not None else None
        if not session_id:
            LOG.warning("Cannot fork a conversation without an active session")
            sublime.status_message("Rewind: no active session ID found")
            return None
        message_id = session.checkpoints.at(checkpoint_index).message_id
        if not message_id:
            sublime.status_message(
                "Rewind: message UUID not yet available for this prompt"
            )
            return None
        return agent, message_id

    def _receive_fork(self, checkpoint_index, fork_id):
        if not fork_id:
            sublime.status_message("Rewind failed: see console for details")
            return
        self._replace_tail(checkpoint_index, fork_id)

    def _replace_tail(self, checkpoint_index, fork_id):
        session = self._session
        checkpoint = session.checkpoints.at(checkpoint_index)
        prompt_region = checkpoint.region
        cut_at = prompt_region.begin() - self._prefix_width
        prompt_text = session.chat_view.substr(prompt_region)

        session.checkpoints.discard_from(checkpoint_index)
        session._redraw_prompt_highlights()
        session.artifact.truncate(cut_at)
        session.permissions.reset()
        session.mark_conversation_started()
        session.set_view_session_id(session.chat_view, fork_id)

        command = "echo_chat_rewind_truncate" if cut_at >= 0 \
            else "echo_chat_input_prompt"
        arguments = (
            {"cut_point": cut_at, "rewind_text": prompt_text}
            if cut_at >= 0 else {"text": prompt_text}
        )
        session.chat_view.run_command(command, arguments)
        session.chat_view.run_command(
            "echo_chat_output_append",
            {"text": "\n■ Rewind conversation to earlier checkpoint\n"},
        )
        session.restart_provider(session_id_override=fork_id, quiet=True)
