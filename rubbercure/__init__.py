"""橡胶硫化机群与模具调度控制平台。

对外只暴露装配入口 ``Platform`` 与工厂 ``build_platform``。其余组件包
（ns、store、audit、mold、lift、steam、temp、timer、cool、press、batch、
sequence、console）由 ``Platform`` 的真实调用链串联，组件之间不互相导入
对方的私有实现，只依赖对方暴露的状态查询与动作接口。
"""

from __future__ import annotations

import os
import sys

__version__ = "1.0.0"

# 离线 vendored 直接依赖：vendor/yaml 是 PyYAML 的纯 Python 源码，构建期与
# 运行期都不访问公网。基础镜像内不预装 PyYAML，因此这里显式把 vendor 目录
# 挂到模块搜索路径最前面。
_VENDOR_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor"
)
if os.path.isdir(_VENDOR_DIR) and _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)

from .app import Platform, build_platform  # noqa: E402

__all__ = ["Platform", "build_platform", "__version__"]
