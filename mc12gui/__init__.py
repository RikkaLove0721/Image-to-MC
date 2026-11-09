"""MC 工程图 图形界面包.

把 `run.py` 的命令行流水线包成三个分页: 输入工作图片, 查看输出结果, 查看仓库内容.
本包只做界面与调度, 不复制任何转换逻辑, 所有重建都调用 `run.main`.
"""

from __future__ import annotations
