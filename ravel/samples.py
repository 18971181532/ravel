"""内置的示例生态。

不是真 PyPI——是十来个手写的小包，版本和依赖都刻意安排成几个经典场景：
一次成功求解、一次需要回退的成功求解、一次确实无解的冲突、一条深依赖链。
固定数据的好处是 README 里的每条输出都能复现。
"""

from __future__ import annotations

import random
from typing import Dict, List, Tuple

from .registry import Registry, parse_requirements

_PACKAGES: Dict[str, dict] = {
    "core": {
        "description": "基础设施",
        "releases": [
            {"version": "1.0.0"},
            {"version": "1.4.0"},
            {"version": "2.0.0"},
        ],
    },
    "json": {
        "description": "序列化",
        "releases": [
            {"version": "1.0.0"},
            {"version": "1.2.0"},
            {"version": "2.0.0"},
        ],
    },
    "tls": {
        "description": "传输层安全",
        "releases": [
            {"version": "1.0.0"},
            {"version": "1.6.0"},
            {"version": "2.0.0"},
            {"version": "2.1.0-rc.1"},
        ],
    },
    "http": {
        "description": "HTTP 客户端",
        "releases": [
            {"version": "1.0.0"},
            {"version": "1.4.0"},
            {"version": "1.8.0"},
            {"version": "1.9.0", "yanked": True},
            {"version": "2.0.0", "dependencies": {"tls": "^2.0"}},
            {"version": "2.1.0", "dependencies": {"tls": "^2.0"}},
        ],
    },
    "log": {
        "description": "日志",
        "releases": [
            {"version": "1.0.0"},
            {"version": "1.2.0"},
            {"version": "2.0.0"},
        ],
    },
    "auth": {
        "description": "认证",
        "releases": [
            {"version": "1.0.0", "dependencies": {"http": "^1.0", "json": "^1.0"}},
            {"version": "1.4.0", "dependencies": {"http": "^1.8", "core": "^1.0"}},
            {"version": "2.0.0", "dependencies": {"http": "^2.0", "core": "^2.0"}},
        ],
    },
    "crypto": {
        "description": "加解密",
        "releases": [
            {"version": "1.0.0"},
            {"version": "1.2.0", "dependencies": {"tls": "^1.0"}},
            {"version": "2.0.0", "dependencies": {"tls": "^2.0"}},
        ],
    },
    "web": {
        "description": "Web 框架",
        "releases": [
            {"version": "1.0.0", "dependencies": {"http": "^1.0", "log": "^1.0"}},
            {"version": "2.0.0", "dependencies": {"http": "^2.0", "auth": "^2.0"}},
            {"version": "2.1.0", "dependencies": {"http": "^2.0", "auth": "^2.0", "tls": "^2.0"}},
        ],
    },
    "metrics": {
        "description": "指标采集",
        "releases": [
            {"version": "1.0.0", "dependencies": {"log": "^1.0"}},
            {"version": "2.0.0", "dependencies": {"log": "^2.0"}},
        ],
    },
    "cache": {
        "description": "缓存",
        "releases": [
            {"version": "1.0.0", "dependencies": {"core": "^1.0"}},
            {"version": "2.0.0", "dependencies": {"core": "^2.0", "json": "^2.0"}},
        ],
    },
    "api": {
        "description": "业务接口层",
        "releases": [
            {"version": "1.0.0", "dependencies": {"web": "^1.0"}},
            {"version": "2.0.0", "dependencies": {"web": "^2.0", "cache": "^2.0"}},
        ],
    },
}


def build() -> Registry:
    """返回内置 registry（每次都是同一份数据）。"""
    return Registry.from_dict({"packages": _PACKAGES}, source="内置示例")


#: 演示用的场景：(名字, 需求文本, 一句话说明)
SCENARIOS: List[Tuple[str, str, str]] = [
    ("basic", "web ^1.0\nmetrics ^1.0", "一次平常的求解：约束不打架，各取最新"),
    ("backtrack", "api *\nhttp ^1.0", "最新的 api 2.0 要 http 2.x，只能回退到 api 1.0"),
    ("conflict", "web ^2.0\nhttp ^1.0", "web 2.x 要 http ^2.0，和 http ^1.0 没有交集"),
    ("unknown", "nope ^1.0", "registry 里没有这个包"),
    ("chain", "api ^2.0", "一路往下带出六七个包"),
    ("prerelease", "tls ^2.0", "2.1.0-rc.1 虽然落在区间里，但没点名就不装"),
]


def _series(count: int) -> List[str]:
    """`1.0.0, 1.1.0, 1.2.0, 2.0.0, ...` —— 每三位进一个大版本。"""
    out: List[str] = []
    major, minor = 1, 0
    for _ in range(count):
        out.append(f"{major}.{minor}.0")
        minor += 1
        if minor == 3:
            major, minor = major + 1, 0
    return out


def synthetic(packages: int = 60, versions: int = 4, seed: int = 7) -> Registry:
    """造一个确定性的合成生态，用来测规模。

    只依赖编号更大的包，所以不可能成环；约束一半是 `*`、一半是指向某个版本的
    `^`，既有宽松的边，也有真会互相卡的边。同一个 seed 永远是同一份数据。
    """
    rng = random.Random(seed)
    names = [f"pkg{index:02d}" for index in range(packages)]
    series = _series(versions)
    body: Dict[str, dict] = {}
    for index, name in enumerate(names):
        releases: List[dict] = []
        for version in series:
            deps: Dict[str, str] = {}
            for other_index in range(index + 1, packages):
                if rng.random() > 0.08:
                    continue
                other = names[other_index]
                deps[other] = "*" if rng.random() < 0.5 else "^" + series[rng.randrange(len(series))]
            item: dict = {"version": version}
            if deps:
                item["dependencies"] = deps
            releases.append(item)
        body[name] = {"releases": releases}
    return Registry.from_dict(
        {"packages": body},
        source=f"合成生态（{packages} 个包 × {versions} 个版本）")


def scenario_requirements(name: str):
    for scenario_name, text, _ in SCENARIOS:
        if scenario_name == name:
            return parse_requirements(text, source=f"场景 {name}")
    raise KeyError(name)
