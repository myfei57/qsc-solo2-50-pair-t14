"""硫化车间命名空间注册表。

现场设备按"车间／工位／设备"三级命名，控制台、审计和趋势都以同一套名字
索引对象。注册表在装配阶段一次性建立，运行期不再改名；重复注册同一个
名字直接报错，避免同一台模具或同一个传感器被两条控制链各自定义。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from ..errors import NamespaceError


@dataclass(frozen=True)
class Namespace:
    """一个硫化车间命名空间。"""

    unit: str
    area: str
    device: str
    kind: str

    @property
    def path(self) -> str:
        return f"rubbercure/{self.unit}/{self.area}/{self.device}"

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "unit": self.unit,
            "area": self.area,
            "device": self.device,
            "kind": self.kind,
        }


class NamespaceRegistry:
    """线程安全的命名空间注册表。"""

    def __init__(self, unit: str) -> None:
        if not unit:
            raise NamespaceError("车间名不能为空", component="ns")
        self._unit = unit
        self._entries: dict[str, Namespace] = {}
        self._lock = threading.RLock()

    @property
    def unit(self) -> str:
        return self._unit

    def register(self, area: str, device: str, kind: str) -> Namespace:
        """登记一个设备命名空间。"""

        namespace = Namespace(unit=self._unit, area=area, device=device, kind=kind)
        with self._lock:
            if namespace.path in self._entries:
                raise NamespaceError(f"命名空间重复注册: {namespace.path}", component="ns")
            self._entries[namespace.path] = namespace
        return namespace

    def resolve(self, path: str) -> Namespace:
        """按路径解析命名空间。"""

        with self._lock:
            namespace = self._entries.get(path)
        if namespace is None:
            raise NamespaceError(f"命名空间未注册: {path}", component="ns")
        return namespace

    def in_area(self, area: str) -> list[Namespace]:
        """返回某个工位下的全部命名空间，按路径排序。"""

        with self._lock:
            entries = list(self._entries.values())
        return sorted(
            (entry for entry in entries if entry.area == area), key=lambda item: item.path
        )

    def describe(self) -> list[dict]:
        with self._lock:
            entries = sorted(self._entries.values(), key=lambda item: item.path)
        return [entry.as_dict() for entry in entries]
