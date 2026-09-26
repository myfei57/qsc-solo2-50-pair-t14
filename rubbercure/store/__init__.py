"""文件型持久化。"""

from .jsonfile import JsonStore
from .journal import JsonlJournal

__all__ = ["JsonStore", "JsonlJournal"]
