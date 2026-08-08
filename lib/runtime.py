"""lib/runtime.py — 共享运行时状态 (P2-1, 机械拆分 + 共享运行时)

集中收敛 ashare_screener.py 的模块级可变全局，替代散落的 `global` 声明。
设计原则：
  - 本模块是**最底层叶子**：不反向 import 主脚本或 lib 内其他模块，避免循环依赖
    （已核实 lib → 主脚本为单向，新增 runtime 仍保持无环）。
  - 仅承载「真·共享可变状态」与「跨步骤日期上下文」；纯数据载体（如单步产出的
    index_data 局部消费）仍按参数/返回值传递，不进此处。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

# 全局仓位下限（原 ashare_screener.MIN_POSITION_PCT，@since v6.8.7）
MIN_POSITION_PCT = 20


def _load_builtin_version() -> str:
    """读取 VERSION 文件（SSOT）。与 ashare_screener._load_builtin_version 同源但独立实现，
    保持本模块为叶子（不 import 主脚本）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (
        os.path.join(here, "..", "VERSION"),
        "VERSION",
        os.path.join(here, "VERSION"),
    ):
        try:
            with open(cand, "r", encoding="utf-8") as f:
                return f.read().strip()
        except (FileNotFoundError, PermissionError):
            continue
    return "0.0.0"


BUILTIN_VERSION = _load_builtin_version()


@dataclass
class RuntimeState:
    # ---- 北京时间 / 日期上下文（step0 单点写，后续只读）----
    beijing_now: object = None
    beijing_date: str | None = None
    beijing_weekday: int | None = None
    _beijing_api_ok: bool = False
    data_date: str | None = None
    prediction_date: str | None = None
    pred_yyyymmdd: str | None = None

    # ---- 版本 / 参数 ----
    file_version: str = BUILTIN_VERSION
    params: dict = field(default_factory=dict)

    # ---- 策略状态（跨多 step 反复读改写）----
    market_condition: str = "震荡"
    position_pct: int = 55
    index_data: dict = field(default_factory=dict)
    _pl_sorted: list = field(default_factory=list)
    _step_status: list = field(default_factory=list)

    # ============================================================
    # 收敛写入口：未来所有 position_pct 变更统一经此处，强制下限
    # ============================================================
    def set_position(self, pct: int, reason: str = "") -> int:
        """设置仓位，强制不低于 MIN_POSITION_PCT。返回实际生效值。

        注：运行时日志由各调用方负责（本叶子模块不 import log_alert，避免环）。
        """
        clamped = max(MIN_POSITION_PCT, int(pct))
        self.position_pct = clamped
        return clamped

    def record_step(self, name: str, ok: bool, detail: str = "") -> None:
        """累积步骤执行状态（替代原模块级 _step_status.append）。"""
        self._step_status.append({"step": name, "ok": ok, "detail": detail})

    def step_summary(self) -> list:
        """返回步骤状态快照。"""
        return list(self._step_status)


# 模块级单例：迁移期兼容入口。
# 长期目标：由 orchestrator(main) 持有 ctx = RuntimeState() 并显式传递，
# 逐步移除对 RT 全局单例的直接引用。
RT = RuntimeState()
