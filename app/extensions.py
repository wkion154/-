"""Capability contracts for later stages. Online adapters declare capabilities; AI components remain inactive."""
from dataclasses import dataclass
from typing import Protocol, Iterable

@dataclass(frozen=True)
class ProviderPolicy:
    name: str
    capabilities: frozenset[str]
    metadata_license: str
    image_license: str
    analysis_allowed: bool = False

class Provider(Protocol):
    policy: ProviderPolicy
    def discover(self, cursor: str | None, cancelled) -> tuple[list[dict], str | None]: ...

class Analyzer(Protocol):
    name: str
    version: str
    def analyze(self, image_path: str, cancelled) -> dict: ...

class Upscaler(Protocol):
    name: str
    version: str
    scales: tuple[int, ...]
    def run(self, original: str, references: list[str], scale: int, cancelled) -> str: ...

class Registry:
    def __init__(self):
        self._items = {}
    def register(self, name, component):
        if name in self._items:
            raise ValueError('이미 등록된 확장입니다: '+name)
        self._items[name] = component
    def get(self, name):
        return self._items[name]
