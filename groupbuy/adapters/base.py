"""Platform interface: generic runner never knows coordinates or response fields."""
from typing import Protocol


class PlatformAdapter(Protocol):
    platform: str

    def collect_ui(self, task, runtime, driver, prior=None): ...
    def validate_evidence(self, task, evidence): ...
    def parse(self, task, entries, evidence): ...
