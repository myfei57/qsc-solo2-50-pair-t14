"""追加型 JSONL 日志，带段切换。

生产审计与告警按时间顺序追加，读取方必须能连续读回整段历史。段达到容量
阈值后新建段，旧段不再追加，读取时按段序拼接，保证顺序与写入顺序一致。
"""

from __future__ import annotations

import json
import os
import threading

from ..errors import StorageError


class JsonlJournal:
    """按段追加的 JSONL 日志。"""

    def __init__(self, root: str, name: str, segment_limit: int = 512) -> None:
        if segment_limit < 1:
            raise StorageError("段容量必须为正", component="store")
        self._root = os.path.abspath(os.path.join(root, name))
        self._segment_limit = int(segment_limit)
        self._counts: dict[int, int] = {}
        self._lock = threading.RLock()
        os.makedirs(self._root, exist_ok=True)
        self._index_existing()

    def _index_existing(self) -> None:
        with self._lock:
            for entry in os.listdir(self._root):
                if not entry.endswith(".jsonl"):
                    continue
                try:
                    index = int(entry.split(".")[0])
                except ValueError:
                    continue
                with open(os.path.join(self._root, entry), "r", encoding="utf-8") as handle:
                    self._counts[index] = sum(1 for _ in handle)

    def _active_segment(self) -> int:
        if not self._counts:
            self._counts[0] = 0
            return 0
        index = max(self._counts)
        if self._counts[index] >= self._segment_limit:
            index += 1
            self._counts.setdefault(index, 0)
        return index

    def append(self, record: dict) -> int:
        """追加一条记录，返回它落到的段号。"""

        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with self._lock:
            index = self._active_segment()
            target = os.path.join(self._root, f"{index:05d}.jsonl")
            try:
                with open(target, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as exc:  # pragma: no cover - 依赖真实磁盘故障
                raise StorageError(f"追加审计日志失败: {exc}", component="store") from exc
            self._counts[index] = self._counts.get(index, 0) + 1
            return index

    def segment_count(self) -> int:
        with self._lock:
            return len(self._counts)

    def read_all(self) -> list[dict]:
        """按段号顺序读回全部记录。"""

        with self._lock:
            indexes = sorted(self._counts)
        records: list[dict] = []
        for index in indexes:
            records.extend(self.read_segment(index))
        return records

    def read_segment(self, index: int) -> list[dict]:
        """读回指定段。"""

        path = os.path.join(self._root, f"{index:05d}.jsonl")
        if not os.path.isfile(path):
            return []
        with open(path, "r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
