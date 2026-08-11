"""Commands that open Echo's user-editable package settings."""

import sublime
import sublime_plugin


def _open_resource(file_name, seed, user_file=None):
    arguments = {
        "base_file": "${packages}/echo/" + file_name,
        "default": seed,
    }
    if user_file is not None:
        arguments["user_file"] = user_file
    sublime.run_command("edit_settings", arguments)


class EchoEditSettingsCommand(sublime_plugin.ApplicationCommand):
    def run(self):
        _open_resource(
            "echo.sublime-settings",
            '{\n\t"provider": "codex",\n\t"providers": {\n'
            '\t\t"codex": {"app_server": '
            '{"url": "ws://127.0.0.1:4500"}}\n\t}\n}\n',
        )


class EchoEditKeyBindingsCommand(sublime_plugin.ApplicationCommand):
    def run(self):
        _open_resource(
            "Default.sublime-keymap",
            '[\n\t{ "keys": ["primary+alt+g"], '
            '"command": "echo_chat_cli" }\n]\n',
            "${packages}/User/Default (${platform}).sublime-keymap",
        )
