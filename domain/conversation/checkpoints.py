"""Conversation checkpoint state independent from Sublime rendering."""

from dataclasses import dataclass


@dataclass
class PromptCheckpoint:
    region: object
    message_id: str = None
    phantom: object = None


class PromptCheckpointLedger:
    def __init__(self):
        self._items = []

    def __len__(self):
        return len(self._items)

    def add(self, region, phantom):
        self._items.append(PromptCheckpoint(region, phantom=phantom))
        return len(self._items) - 1

    def at(self, index):
        return self._items[index]

    def attach_to_latest(self, message_id):
        if not self._items:
            return None
        index = len(self._items) - 1
        self._items[index].message_id = message_id
        return index

    def regions(self):
        return [item.region for item in self._items]

    def snapshot(self):
        return [
            (item.region, item.message_id, index)
            for index, item in enumerate(self._items)
        ]

    def discard_from(self, index):
        removed = self._items[index:]
        self._items = self._items[:index]
        for item in removed:
            if item.phantom is not None:
                item.phantom.update([])
        return removed

    def clear(self):
        self.discard_from(0)
