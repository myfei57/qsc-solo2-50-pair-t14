"""命令行入口。

五个子命令覆盖日常使用：

``serve``   启动控制台 HTTP 服务，可选地在启动后探测页面；
``demo``    在本地跑一遍完整流程（硫化顺序 → 控制周期 → 并发探测 → 开模与
            调度 → 停运顺序），用于交付前的真实运行检查；
``status``  打印当前状态快照；
``config``  打印生效配置；
``reset``   清掉各台硫化机的落盘状态。
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from .app import CycleInputs, Platform, build_platform
from .clock import ManualClock, SystemClock
from .config import config_to_mapping, default_config, load_config
from .console.server import ConsoleServer, probe_pages
from .errors import RubberCureError

DEFAULT_DEMO_CYCLES = 12


def _build(args: argparse.Namespace) -> Platform:
    config = load_config(args.config) if getattr(args, "config", None) else default_config()
    if getattr(args, "storage_root", None):
        return build_platform(config, storage_root=args.storage_root)
    return Platform(config=config)


def _dump(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _cmd_serve(args: argparse.Namespace) -> int:
    platform = _build(args)
    server = ConsoleServer(platform, host=args.host, port=args.port)
    base_url = server.start()
    payload: dict = {"listening": base_url, "pages": list(platform.config.pages)}
    if args.probe:
        payload["probe"] = probe_pages(base_url, platform.config.pages)
    _dump(payload)
    if args.duration > 0:
        try:
            time.sleep(args.duration)
        except KeyboardInterrupt:  # pragma: no cover - 交互路径
            pass
        server.stop()
        return 0
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:  # pragma: no cover - 交互路径
        server.stop()
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    clock = ManualClock() if args.fast else SystemClock()
    config = load_config(args.config) if args.config else default_config()
    platform = build_platform(config, clock=clock, storage_root=args.storage_root)
    # 演示从干净基线起步：先清掉上一轮落盘状态，避免继承半程工况。
    platform.reset_state()

    cure = platform.run_cure("demo-mold", cavity_count=4)
    cycles = [
        platform.cycle(
            CycleInputs(
                inlet_c=26.0 + index * 0.3,
                steam_target_kpa=config.steam.target_pressure_kpa,
                dt=1.0,
            )
        )
        for index in range(args.cycles)
    ]
    setpoint_probe = platform.concurrency_setpoint_probe(rounds=args.rounds)
    report_probe = platform.concurrency_report_probe(rounds=args.report_rounds)
    platform.register_mold("next-mold", cavity_count=4)
    platform.enqueue_mold("next-mold")
    openmold = platform.run_open("press-01")
    dispatch = platform.run_dispatch("press-01")
    stop = platform.run_stop("press-01")

    server = ConsoleServer(platform)
    base_url = server.start()
    probe = probe_pages(base_url, platform.config.pages)
    server.stop()

    report = {
        "cure": cure,
        "cycles_run": len(cycles),
        "last_cycle": cycles[-1],
        "setpoint_probe": setpoint_probe,
        "report_probe": report_probe,
        "open": openmold,
        "dispatch": dispatch,
        "stop": stop,
        "probe": probe,
        "audit": platform.audit.summary(),
    }
    _dump(report)
    failures = list(cure["violations"])
    failures += list(openmold["violations"]) + list(dispatch["violations"]) + list(stop["violations"])
    failures += [name for name, item in probe.items() if not item["ok"]]
    if failures:
        print(f"检查未通过: {failures}", file=sys.stderr)
        return 1
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    platform = _build(args)
    payload = platform.status()
    payload["topology"] = platform.topology_summary()
    payload["namespaces"] = platform.namespaces.describe()
    _dump(payload)
    return 0


def _cmd_config(args: argparse.Namespace) -> int:
    config = load_config(args.config) if args.config else default_config()
    _dump(config_to_mapping(config))
    return 0


def _cmd_reset(args: argparse.Namespace) -> int:
    platform = _build(args)
    cleared = platform.reset_state()
    _dump({"storage_root": platform.store.root, "cleared": cleared["cleared"]})
    return 0


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""

    parser = argparse.ArgumentParser(
        prog="rubbercure", description="橡胶硫化机群与模具调度控制平台"
    )
    parser.add_argument("--config", help="YAML 配置文件路径")
    parser.add_argument("--storage-root", help="覆盖状态存储目录")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="启动控制台服务")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--duration", type=float, default=0.0, help="运行多少秒后退出，0 表示常驻")
    serve.add_argument("--probe", action="store_true", help="启动后探测页面")
    serve.set_defaults(func=_cmd_serve)

    demo = sub.add_parser("demo", help="跑一遍完整流程")
    demo.add_argument("--cycles", type=int, default=DEFAULT_DEMO_CYCLES)
    demo.add_argument("--rounds", type=int, default=24, help="目标值并发探测轮数")
    demo.add_argument("--report-rounds", type=int, default=12, help="完成上报并发探测轮数")
    demo.add_argument("--fast", action="store_true", help="使用可推进时钟，跳过真实等待")
    demo.set_defaults(func=_cmd_demo)

    status = sub.add_parser("status", help="打印状态快照")
    status.set_defaults(func=_cmd_status)

    config = sub.add_parser("config", help="打印生效配置")
    config.set_defaults(func=_cmd_config)

    reset = sub.add_parser("reset", help="清掉各台硫化机的落盘状态")
    reset.set_defaults(func=_cmd_reset)
    return parser


def main(argv: list[str] | None = None) -> int:
    """命令行主入口。"""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except RubberCureError as error:
        print(json.dumps(error.as_record(), ensure_ascii=False), file=sys.stderr)
        return 2
