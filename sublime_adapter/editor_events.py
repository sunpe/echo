"""Decision helpers for Sublime editor events in Echo views."""


def is_plain_word_click(command_name, args):
    if command_name != "drag_select" or not args:
        return False
    return (
        args.get("by") == "words"
        and not args.get("extend")
        and not args.get("additive")
    )


def click_point(view, args):
    event = (args or {}).get("event") or {}
    x, y = event.get("x"), event.get("y")
    return view.window_to_text((x, y)) if x is not None and y is not None \
        else None


def history_move(view, command_name, args, editable_start, region_factory):
    if (
        command_name != "move"
        or not args
        or args.get("by") != "lines"
        or view.is_auto_complete_visible()
        or not view.sel()
    ):
        return None
    caret = view.sel()[0]
    if not caret.empty():
        return None
    if not args.get("forward", True):
        caret_row = view.rowcol(caret.begin())[0]
        prompt_row = view.rowcol(editable_start)[0]
        return "echo_chat_history_up" if caret_row == prompt_row else None
    tail = view.substr(region_factory(caret.end(), view.size()))
    return "echo_chat_history_down" if not tail.strip() else None
