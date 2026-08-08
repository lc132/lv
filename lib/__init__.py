# lib/__init__.py — P2-1 共享运行时包初始化
# 仅导出叶子模块 runtime 的符号；不触发任何其他 lib 子模块 import，避免 import 副作用。
from .runtime import RuntimeState, RT, MIN_POSITION_PCT, BUILTIN_VERSION

__all__ = ["RuntimeState", "RT", "MIN_POSITION_PCT", "BUILTIN_VERSION"]
