"""后台运行转换流水线, 把 print 输出转成队列事件.

run.py 是纯命令行程序, 全程 print. 界面不能直接调用它, 否则主线程会被
几十秒的计算卡死, 所以这里起一个工作线程, 把 stdout 截流成事件放进队列,
界面用 after 轮询消费.
"""

from __future__ import annotations

import io
import queue
import sys
import threading
import traceback

import run as pipeline

EVT_LOG = "log"
EVT_STEP = "step"
EVT_DONE = "done"


class _Writer(io.TextIOBase):
    """把写入的文本塞进队列, 代替 sys.stdout."""

    def __init__(self, box: queue.Queue):
        self.box = box

    def write(self, text: str) -> int:
        if text:
            self.box.put((EVT_LOG, text))
        return len(text or "")

    def flush(self) -> None:
        return None

    @property
    def encoding(self) -> str:
        return "utf-8"


class PipelineTask:
    """一次转换任务. 同一时刻只允许一个实例在跑."""

    def __init__(self, argv: list[str]):
        self.argv = list(argv)
        self.queue: queue.Queue = queue.Queue()
        self.code: int | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="pipeline", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        old_out, old_err = sys.stdout, sys.stderr
        writer = _Writer(self.queue)
        sys.stdout = writer
        sys.stderr = writer
        code = 1
        try:
            code = int(pipeline.main(self.argv) or 0)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        except BaseException:
            self.queue.put((EVT_LOG, "\n" + traceback.format_exc()))
            code = 1
        finally:
            sys.stdout, sys.stderr = old_out, old_err
            self.code = code
            self.queue.put((EVT_DONE, code))


def parse_step(line: str) -> tuple[int, int] | None:
    """从 "[3/8] ..." 这样的行里取步骤进度."""
    left = line.find("[")
    if left < 0:
        return None
    right = line.find("]", left)
    if right < 0:
        return None
    body = line[left + 1:right]
    if "/" not in body:
        return None
    a, _, b = body.partition("/")
    if not (a.isdigit() and b.isdigit()):
        return None
    total = int(b)
    if total <= 0:
        return None
    return min(int(a), total), total
