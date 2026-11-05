"""错误类型。

都继承 :class:`RavelError`，带上"是哪一段输入出了问题"的位置信息，
命令行最外层统一渲染成 `错误：...` 加一行上下文。
"""

from __future__ import annotations

from typing import Optional


class RavelError(Exception):
    """所有对外错误的基类。"""

    def __init__(self, message: str, position: Optional[int] = None,
                 text: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.position = position
        self.text = text

    def render(self) -> str:
        lines = [f"错误：{self.message}"]
        if self.text is not None and self.position is not None:
            lines.append(f"  {self.text}")
            lines.append("  " + " " * max(self.position, 0) + "^")
        elif self.text is not None:
            lines.append(f"  {self.text}")
        return "\n".join(lines)


class VersionError(RavelError):
    """版本号或约束写错了。"""


class RegistryError(RavelError):
    """registry / 需求文件格式不对。"""


class SolveError(RavelError):
    """求解失败（作为给用户看的错误）。"""


class LockError(RavelError):
    """lock 文件读不了，或者和当前需求对不上。"""
