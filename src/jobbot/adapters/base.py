from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class AdapterResult:
    ats_type: str
    normalized: dict[str, Any]


class Adapter(ABC):
    @abstractmethod
    def parse(self, raw_text: str) -> AdapterResult:
        raise NotImplementedError


class GenericAdapter(Adapter):
    def parse(self, raw_text: str) -> AdapterResult:
        return AdapterResult(ats_type="generic", normalized={"description": raw_text})


class GreenhouseAdapter(Adapter):
    def parse(self, raw_text: str) -> AdapterResult:
        return AdapterResult(ats_type="greenhouse", normalized={"description": raw_text})


class LeverAdapter(Adapter):
    def parse(self, raw_text: str) -> AdapterResult:
        return AdapterResult(ats_type="lever", normalized={"description": raw_text})


class WorkdayAdapter(Adapter):
    def parse(self, raw_text: str) -> AdapterResult:
        return AdapterResult(ats_type="workday", normalized={"description": raw_text})
