"""原子 JSON 文档存储。

模温确认、闩锁状态和分析仪基线必须"落盘成功才算生效"：控制器重启后按
落盘内容恢复，所以写入必须原子替换，不能在原地截断重写——中途断电会留下
半份 JSON，重启时反而丢掉上一份有效状态，导致硫化计时在没有确认的情况下
放行。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading

from ..errors import StorageError


class JsonStore:
    """以目录为根的 JSON 文档存储。"""

    def __init__(self, root: str) -> None:
        self._root = os.path.abspath(root)
        self._lock = threading.RLock()
        os.makedirs(self._root, exist_ok=True)

    @property
    def root(self) -> str:
        return self._root

    def path_for(self, name: str) -> str:
        """把逻辑名映射成 ``<root>/<name>.json``。"""

        if not name or os.path.sep in name or "/" in name:
            raise StorageError(f"非法的存储键: {name!r}", component="store")
        return os.path.join(self._root, f"{name}.json")

    def exists(self, name: str) -> bool:
        return os.path.isfile(self.path_for(name))

    def write(self, name: str, payload: dict) -> str:
        """原子写入一份文档，返回落盘后的 SHA256。"""

        target = self.path_for(name)
        data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        with self._lock:
            handle = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._root,
                prefix=f".{name}.",
                suffix=".tmp",
                delete=False,
            )
            try:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
                handle.close()
                os.replace(handle.name, target)
            except OSError as exc:  # pragma: no cover - 依赖真实磁盘故障
                raise StorageError(f"写入 {name} 失败: {exc}", component="store") from exc
            finally:
                if not handle.closed:
                    handle.close()
            return self.digest(name)

    def read(self, name: str, default: dict | None = None) -> dict | None:
        """读取文档；不存在时返回 ``default``。"""

        target = self.path_for(name)
        with self._lock:
            if not os.path.isfile(target):
                return default
            try:
                with open(target, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, ValueError) as exc:
                raise StorageError(f"读取 {name} 失败: {exc}", component="store") from exc
        if not isinstance(payload, dict):
            raise StorageError(f"{name} 的内容不是 JSON 对象", component="store")
        return payload

    def digest(self, name: str) -> str:
        """返回文档内容的 SHA256；文件不存在时返回空串。"""

        target = self.path_for(name)
        with self._lock:
            if not os.path.isfile(target):
                return ""
            with open(target, "rb") as handle:
                return hashlib.sha256(handle.read()).hexdigest()

    def remove(self, name: str) -> bool:
        """删除文档，返回是否真的删掉了文件。"""

        target = self.path_for(name)
        with self._lock:
            if not os.path.isfile(target):
                return False
            os.remove(target)
        return True
