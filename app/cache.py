"""Byte-budgeted decoded-image LRU; accessed exclusively by the GUI thread."""
from collections import OrderedDict


class ImageCache:
    def __init__(self, max_bytes=128 * 1024 * 1024, max_items=12):
        self.max_bytes = max_bytes
        self.max_items = max_items
        self.bytes_used = 0
        self.items = OrderedDict()
        self.hits = 0

    def get(self, key):
        value = self.items.get(key)
        if value is not None:
            self.items.move_to_end(key)
            self.hits += 1
        return value

    def put(self, key, image):
        size = image.sizeInBytes()
        if image.isNull() or size > self.max_bytes:
            return False
        if key in self.items:
            self.bytes_used -= self.items.pop(key).sizeInBytes()
        while self.items and (self.bytes_used + size > self.max_bytes or len(self.items)>=self.max_items):
            _, previous = self.items.popitem(last=False)
            self.bytes_used -= previous.sizeInBytes()
        self.items[key] = image
        self.bytes_used += size
        return True

    def clear(self):
        self.items.clear()
        self.bytes_used = 0
