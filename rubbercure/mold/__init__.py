"""模具登记与传感器映射。"""

from .generation import GenerationCounter
from .mapping import SensorMapBook, SensorMapping
from .registry import MoldRecord, MoldRegistry
from .summary import summarize_molds

__all__ = [
    "GenerationCounter",
    "SensorMapBook",
    "SensorMapping",
    "MoldRecord",
    "MoldRegistry",
    "summarize_molds",
]
