"""Shell-like prompt history without editor dependencies."""


class PromptHistory:
    def __init__(self):
        self._items = []
        self._cursor = 0
        self._draft = ""

    def record(self, text):
        self._items.append(text)
        self._cursor = len(self._items)
        self._draft = ""

    def older(self, current_text):
        if self._cursor == len(self._items):
            self._draft = current_text
        if self._cursor == 0:
            return None
        self._cursor -= 1
        return self._items[self._cursor]

    def newer(self):
        if self._cursor >= len(self._items):
            return None
        self._cursor += 1
        return (
            self._draft
            if self._cursor == len(self._items)
            else self._items[self._cursor]
        )
