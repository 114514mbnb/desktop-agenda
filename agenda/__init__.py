"""桌面日程：QQ 群通知 → 结构化日程 → 桌面常驻面板。

模块划分：
  models     数据模型与常量
  parsing    中文日期/时间表达式解析（确定性规则）
  extract    通知文本 → 候选事务
  aggregate  一周视图聚合（供面板与流水线使用）
  store      JSON 存储 + 去重 + 一周记忆
  panel      桌面右侧常驻面板（tkinter）
  pipeline   一次完整跑批：扫描 inbox → 解析 → 合并 → 归档
"""

__all__ = ["models", "parsing", "extract", "aggregate", "store", "panel", "pipeline"]
#: 版本号。以前一直停在 0.1.0（谁也没读它），发布到 v1.3 时对齐一下，
#: 免得有人查版本时对着一个跟 Release 对不上的数字发懵。
__version__ = "1.3.0"
