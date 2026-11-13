"""发布版桌面外壳.

双击 exe 做五件事:
  1. 单实例检查, 已有窗口就把它拉到前台, 不再起第二个
  2. 先开一个原生窗口显示启动画面, 再用随包的 node 起本地服务
  3. 服务就绪后把窗口切到界面, 之后一切交互都在这个窗口里
  4. 窗口内下载文件时弹系统另存为对话框, 而不是丢进浏览器的下载目录
  5. 关掉窗口就停掉服务与后端进程

窗口用系统的 Edge 内核 (WebView2) 渲染, 不跳浏览器, 不需要用户装任何东西.
这个文件只管窗口, 进程与端口, 不碰任何转换逻辑.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import webview

APP_NAME = "MC 工程图"
WINDOW_TITLE = APP_NAME
DEFAULT_PORT = 4310
PORT_SCAN = 30
READY_TIMEOUT = 70.0
WINDOW_W, WINDOW_H = 1380, 900
MIN_W, MIN_H = 1060, 720
MUTEX_NAME = "MC12.Desktop.SingleInstance"
CREATE_NO_WINDOW = 0x08000000
ERROR_ALREADY_EXISTS = 183

KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None

SPLASH = """<!doctype html>
<html><head><meta charset="utf-8"><style>
  html, body { margin: 0; height: 100%; background: #0f1419; color: #e6edf3;
    font: 14px/1.6 "Microsoft YaHei UI", "Segoe UI", system-ui, sans-serif;
    display: flex; align-items: center; justify-content: center;
    user-select: none; overflow: hidden; }
  .box { text-align: center; }
  .cube { width: 76px; height: 76px; margin: 0 auto 20px; display: block; }
  h1 { font-size: 17px; font-weight: 600; letter-spacing: .08em; margin: 0 0 7px; }
  p { margin: 0; color: #7d8590; font-size: 12.5px; letter-spacing: .02em; }
  .dots { margin-top: 20px; display: flex; gap: 7px; justify-content: center; }
  .dots i { width: 6px; height: 6px; border-radius: 50%; background: #3d7eff;
    animation: beat .9s infinite ease-in-out; }
  .dots i:nth-child(2) { animation-delay: .15s; }
  .dots i:nth-child(3) { animation-delay: .3s; }
  @keyframes beat {
    0%, 80%, 100% { opacity: .22; transform: translateY(0); }
    40% { opacity: 1; transform: translateY(-4px); }
  }
</style></head>
<body><div class="box">
  <svg class="cube" viewBox="0 0 100 100" fill="none">
    <path d="M50 5 93 30v40L50 95 7 70V30z" fill="#131b28" stroke="#3d7eff" stroke-width="3"/>
    <path d="M50 5 93 30 50 55 7 30z" fill="#2c3d5c"/>
    <path d="M50 55v40L7 70V30z" fill="#1a2434"/>
    <path d="M50 5v50M7 30l43 25M93 30L50 55" stroke="#3d7eff" stroke-width="1.6" opacity=".55"/>
  </svg>
  <h1>MC 工程图</h1>
  <p>正在启动本地服务 ...</p>
  <div class="dots"><i></i><i></i><i></i></div>
</div></body></html>"""


def base_dir() -> Path:
    """exe 所在目录; 直接跑脚本时用仓库根目录."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def version() -> str:
    for name in ("VERSION",):
        path = base_dir() / name
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8").strip() or "0.0.0"
            except OSError:
                break
    return "0.0.0"


def say(log, msg: str) -> None:
    """同时进日志与 stdout; windowed 模式下 stdout 可能不存在."""
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    try:
        if sys.stdout is not None:
            print(line, flush=True)
    except (OSError, ValueError):
        pass
    try:
        log.write(line + "\n")
    except (OSError, ValueError):
        pass


def open_log(base: Path):
    logs = base / "logs"
    try:
        logs.mkdir(parents=True, exist_ok=True)
        path = logs / "app.log"
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                path.replace(logs / "app.prev.log")
        except OSError:
            pass
        # 首次写带 BOM, 记事本与 PowerShell 都不会把中文读成乱码
        fresh = not path.exists() or path.stat().st_size == 0
        handle = open(path, "a", encoding="utf-8-sig" if fresh else "utf-8", buffering=1)
        return handle, path
    except OSError:
        return open(os.devnull, "w", encoding="utf-8"), Path(os.devnull)


def fail(msg: str, log=None) -> None:
    if log is not None:
        say(log, "错误: " + msg.replace("\n", " "))
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, msg, APP_NAME, 0x10)


def hold_mutex() -> bool:
    """拿住单实例互斥体; 返回 False 表示已经有别的实例在跑."""
    global _MUTEX
    if KERNEL32 is None:
        return True
    handle = KERNEL32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return True
    _MUTEX = handle
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


_MUTEX = None


def focus_existing(title: str) -> bool:
    """把已运行的窗口还原并切到前台."""
    if os.name == "nt":
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return False
        user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
        return True
    return False


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def request_json(url: str, timeout: float = 1.5) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def pick_projects_dir(base: Path) -> Path:
    """优先放 exe 同级的 projects, 不可写时退到用户本地目录."""
    fixed = os.environ.get("MC12_PROJECTS_DIR")
    if fixed:
        path = Path(fixed)
        path.mkdir(parents=True, exist_ok=True)
        return path
    beside = base / "projects"
    try:
        beside.mkdir(parents=True, exist_ok=True)
        probe = beside / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return beside
    except OSError:
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
        fallback = Path(local) / "MC工程图" / "projects"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def storage_dir(base: Path) -> Path:
    """窗口缓存放用户目录, 不塞进程序文件夹.

    这里是 Chromium 的缓存, 几十兆, 对用户没有备份价值;
    放程序目录还会让整个文件夹拷来拷去时白白变大一倍.
    """
    local = os.environ.get("LOCALAPPDATA")
    if os.name == "nt" and local:
        path = Path(local) / "MC工程图" / "webview"
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            pass
    return base / "data" / "webview"


def same_project_dir(served: str, projects: Path) -> bool:
    return served.replace("\\", "/").lower() == str(projects).replace("\\", "/").lower()


def wait_ready(port: int, child: subprocess.Popen, deadline: float) -> bool:
    url = f"http://127.0.0.1:{port}/api/watch"
    while time.time() < deadline:
        if child.poll() is not None:
            return False
        if request_json(url, timeout=1.0):
            return True
        time.sleep(0.35)
    return False


def stop_child(child: subprocess.Popen | None) -> None:
    """连进程树一起收掉, 免得转换进程变孤儿."""
    if child is None or child.poll() is not None:
        return
    if os.name == "nt":
        subprocess.call(
            ["taskkill", "/PID", str(child.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
    else:
        child.terminate()
    try:
        child.wait(timeout=8)
    except subprocess.TimeoutExpired:
        child.kill()


class _ViewerApi:
    """查看器窗口的 JS 桥：只负责"置顶"一件事.

    pywebview 会把 js_api 上的公开方法暴露成 window.pywebview.api.xxx()。
    这个类故意只留一个方法 —— 查看器页面是完全独立的 HTML，不该顺手拿到
    主窗口的保存文件、打开目录那套能力。
    """

    def __init__(self, shell: "Bridge") -> None:
        self._shell = shell

    def set_on_top(self, on: bool = True) -> dict:
        """按住 R 置顶 / 松开还原（由查看器页面里的 keydown/keyup 调）.  """
        win = getattr(self._shell, "_viewer", None)
        if win is None:
            return {"ok": False, "error": "查看器窗口已关闭"}
        try:
            win.on_top = bool(on)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "on_top": bool(on)}


class Bridge:
    """暴露给页面脚本的少量桌面能力."""

    def __init__(self, port: int, base: Path, projects: Path, log=None):
        # 下划线开头: pywebview 会把公开属性当 api 递归展开, Path 对象会污染接口表
        self._port = port
        self._base = base
        self._projects = projects
        self._log = log
        # 查看器窗口的引用（浮在最前面的那个），关掉后置 None
        self._viewer = None
        # 主窗口引用：更新装完后要用它自杀（页面里调 quit_app）
        self._window = None

    def _note(self, msg: str) -> None:
        if self._log is not None:
            say(self._log, msg)

    def app_info(self) -> dict:
        return {
            "desktop": True,
            "version": version(),
            "projectsDir": str(self._projects),
            "port": self._port,
        }

    def quit_app(self) -> dict:
        """让软件自己退出（应用内更新装完后由页面调用）。

        必须**先回包再关窗**：pywebview 的 JS 桥是同步等待返回值的，
        在同一个调用里 destroy 窗口，页面会永远等不到结果。
        所以起一个 0.5 秒的定时器，把销毁推到回包之后。
        """
        win = self._window
        if win is None:
            return {"ok": False, "error": "窗口还没准备好"}
        self._note("页面请求退出（准备覆盖更新）")

        def _bye() -> None:
            try:
                win.destroy()
            except Exception as exc:  # noqa: BLE001
                self._note(f"关窗失败: {type(exc).__name__}: {exc}")

        threading.Timer(0.5, _bye).start()
        return {"ok": True}

    def save_file(self, url: str, filename: str) -> dict:
        """把产物另存到用户选的位置. url 只接受本机的 /api/ 路径."""
        if not isinstance(url, str) or not url.startswith("/api/"):
            return {"ok": False, "error": "只允许保存本程序的产物"}
        name = Path(str(filename or "download.bin")).name or "download.bin"
        self._note(f"保存请求 {name}")
        target = self._ask_path(name)
        if not target:
            return {"ok": False, "canceled": True}
        path = Path(target)
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self._port}{url}", timeout=60
            ) as resp:
                data = resp.read()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return {"ok": False, "error": f"写入失败: {exc}"}
        return {"ok": True, "path": str(path)}

    def reveal_file(self, path: str) -> dict:
        """在资源管理器里选中刚保存的文件."""
        target = Path(str(path or ""))
        if not target.exists():
            return {"ok": False, "error": "文件不存在"}
        subprocess.Popen(["explorer", "/select,", str(target)])
        return {"ok": True}

    def open_viewer(self, url: str, title: str = "") -> dict:
        """3D 查看器开成应用内的第二个窗口, 而不是跳浏览器.

        窗口带一个只有置顶能力的小 API: 查看器页面里按住 R 会调
        `set_on_top(True)`, 松开调 False —— 玩家想一边开着游戏一边照着盖,
        窗口必须能浮在游戏上面, 松手又别一直压着别的窗口.
        """
        if not isinstance(url, str) or not url.startswith("/api/"):
            return {"ok": False, "error": "只允许打开本程序的页面"}
        try:
            win = webview.create_window(
                str(title or "3D 查看器"),
                f"http://127.0.0.1:{self._port}{url}",
                width=1040,
                height=820,
                min_size=(720, 520),
                text_select=True,
                js_api=_ViewerApi(self),
            )
            self._viewer = win
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def _ask_path(self, name: str) -> str | None:
        label = desktop_label(name)
        window = webview.windows[0] if webview.windows else None
        if window is None:
            return None
        # pywebview 6 用 FileDialog 枚举, 旧常量已标记废弃
        dialog = getattr(webview, "FileDialog", None)
        kind = dialog.SAVE if dialog is not None else webview.SAVE_DIALOG
        chosen = window.create_file_dialog(
            kind,
            directory=str(Path.home() / "Downloads"),
            save_filename=name,
            file_types=(label, "所有文件 (*.*)"),
        )
        if not chosen:
            return None
        if isinstance(chosen, (list, tuple)):
            return str(chosen[0]) if chosen else None
        return str(chosen)


def desktop_label(name: str) -> str:
    suffix = Path(name).suffix.lower()
    table = {
        ".schematic": "Minecraft 结构文件 (*.schematic)",
        ".txt": "文本文件 (*.txt)",
        ".png": "图片 (*.png)",
        ".json": "JSON 文件 (*.json)",
    }
    return table.get(suffix, "文件 (*" + (suffix or ".*") + ")")


def choose_port(projects: Path) -> tuple[int, bool]:
    """返回 (端口, 是否由本进程起服务).

    只有在跑着同一版本、且数据目录相同的情况下才复用, 否则另起一个,
    免得新外壳连到旧版本还在跑的服务上.
    """
    mine = version()
    for candidate in range(DEFAULT_PORT, DEFAULT_PORT + PORT_SCAN):
        info = request_json(f"http://127.0.0.1:{candidate}/api/watch", timeout=0.8)
        if (
            info
            and same_project_dir(str(info.get("dir") or ""), projects)
            and str(info.get("version") or "") == mine
        ):
            return candidate, False
        if port_free(candidate):
            return candidate, True
    return 0, False


def screen_size() -> tuple[int, int]:
    """逻辑像素的屏幕尺寸, 用来把窗口压在屏幕里."""
    if os.name != "nt":
        return WINDOW_W, WINDOW_H
    user32 = ctypes.windll.user32
    try:
        dpi = user32.GetDpiForSystem() or 96
    except (AttributeError, OSError):
        dpi = 96
    scale = dpi / 96.0
    try:
        return int(user32.GetSystemMetrics(0) / scale), int(user32.GetSystemMetrics(1) / scale)
    except OSError:
        return WINDOW_W, WINDOW_H


def window_box() -> tuple[int, int, tuple[int, int], bool]:
    """返回 (宽, 高, 最小尺寸, 是否直接最大化).

    小屏幕 (笔记本 1280x720 这类) 直接最大化, 免得界面被挤成一条;
    大屏幕上给一个居中的固定尺寸, 看起来更像正经软件.
    """
    sw, sh = screen_size()
    if sw < 1500 or sh < 900:
        return max(880, sw - 40), max(600, sh - 80), (880, 600), True
    return WINDOW_W, WINDOW_H, (MIN_W, MIN_H), False


def main() -> int:
    base = base_dir()
    log, log_path = open_log(base)

    # 窗口模式没有控制台, 把依赖库的日志也引到文件里, 出问题才有据可查
    level = logging.DEBUG if os.environ.get("MC12_DEBUG") else logging.WARNING
    handler = logging.StreamHandler(log)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(handler)
    # pywebview 自己挂过 NullHandler, 显式接管一遍, 否则它的异常写不进日志
    for name in ("pywebview", "webview"):
        sub = logging.getLogger(name)
        sub.setLevel(level)
        sub.addHandler(handler)
        sub.propagate = True

    say(log, f"=== {APP_NAME} {version()} 启动 ===")
    say(log, f"程序目录 {base}")

    if not hold_mutex():
        if focus_existing(WINDOW_TITLE):
            say(log, "已有实例在运行, 已把窗口切到前台")
            return 0
        say(log, "已有实例在运行, 但没找到窗口, 本次不再启动")
        return 0

    runtime = base / "runtime"
    node = runtime / ("node.exe" if os.name == "nt" else "node")
    server = runtime / "server" / "index.mjs"
    if not node.is_file() or not server.is_file():
        fail(f"运行时不完整, 缺少 runtime/node.exe 或 runtime/server/index.mjs\n\n请重新解压完整的发布包到:\n{base}", log)
        return 2

    projects = pick_projects_dir(base)
    say(log, f"项目目录 {projects}")

    # 注入失败会让页面里没有 window.pywebview, 但异常在 .NET 事件回调里会被吞掉,
    # 这里包一层把真实原因写进日志
    try:
        import webview.platforms.edgechromium as edge

        original_inject = edge.inject_pywebview

        def traced_inject(platform_name, target):
            try:
                return original_inject(platform_name, target)
            except Exception as exc:  # noqa: BLE001
                say(log, f"注入脚本失败: {type(exc).__name__}: {exc}")
                raise

        edge.inject_pywebview = traced_inject
        say(log, "注入脚本已挂钩")
    except Exception as exc:  # noqa: BLE001
        say(log, f"注入脚本挂钩失败: {exc}")

    port, own = choose_port(projects)
    if not port:
        fail(f"从 {DEFAULT_PORT} 起连续 {PORT_SCAN} 个端口都被占用, 请先关掉占用端口的程序", log)
        return 3

    core = base / "core" / ("mc12-core.exe" if os.name == "nt" else "mc12-core")
    say(log, f"服务端口 {port} ({'新起' if own else '复用已有'})")
    say(log, f"转换后端 {core if core.is_file() else '系统 Python'}")

    bridge = Bridge(port, base, projects, log)
    win_w, win_h, win_min, maximize = window_box()
    window = webview.create_window(
        WINDOW_TITLE,
        html=SPLASH,
        js_api=bridge,
        width=win_w,
        height=win_h,
        min_size=win_min,
        text_select=True,
        background_color="#0f1419",
        maximized=maximize,
    )
    bridge._window = window

    state: dict = {"child": None, "error": ""}

    def boot() -> None:
        child = None
        if own:
            env = dict(os.environ)
            env.update(
                {
                    "PORT": str(port),
                    "HOST": "127.0.0.1",
                    "MC12_ROOT": str(base),
                    "MC12_PROJECTS_DIR": str(projects),
                    "NODE_ENV": "production",
                }
            )
            if core.is_file():
                env["MC12_CORE"] = str(core)
            try:
                child = subprocess.Popen(
                    [str(node), str(server)],
                    cwd=str(base),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except OSError as exc:
                state["error"] = f"没能启动本地服务: {exc}"
                window.destroy()
                return
            state["child"] = child
            if not wait_ready(port, child, time.time() + READY_TIMEOUT):
                state["error"] = f"本地服务没能正常启动, 详细日志见:\n{log_path}"
                window.destroy()
                return
        else:
            deadline = time.time() + 20
            while time.time() < deadline:
                if request_json(f"http://127.0.0.1:{port}/api/watch", timeout=0.8):
                    break
                time.sleep(0.3)

        say(log, f"就绪, 打开窗口 http://127.0.0.1:{port}")
        window.load_url(f"http://127.0.0.1:{port}")

        if os.environ.get("MC12_SELFTEST"):
            def wait_dom(timeout: float = 25.0) -> bool:
                """等前端真正挂载完再自检。

                原来固定 sleep 6 秒，页面一复杂（0.22 的前端包更大）就会在
                渲染前取到空 DOM，报告 nodes:24 / footer:false，看着像坏了。
                """
                end = time.time() + timeout
                while time.time() < end:
                    try:
                        if window.evaluate_js("!!document.querySelector('.app-footer')"):
                            return True
                    except Exception:  # noqa: BLE001
                        pass
                    time.sleep(0.4)
                return False

            def check() -> None:
                wait_dom()
                try:
                    info = window.evaluate_js(
                        "JSON.stringify({"
                        "pywebview: typeof window.pywebview,"
                        "keys: window.pywebview ? Object.keys(window.pywebview.api || {}) : [],"
                        "nodes: document.querySelectorAll('*').length,"
                        "footer: !!document.querySelector('.app-footer'),"
                        "w: window.innerWidth, dpr: window.devicePixelRatio"
                        "})"
                    )
                    say(log, f"自检 {info}")
                    if os.environ.get("MC12_SELFTEST") == "2":
                        listing = request_json(f"http://127.0.0.1:{port}/api/projects") or {}
                        rows = listing.get("projects") or []
                        pid = rows[0].get("id") if rows else ""
                        say(log, f"自检: 触发另存为 pid={pid}")
                        window.evaluate_js(
                            "window.pywebview.api.save_file("
                            f"'/api/projects/{pid}/file/m01.schematic','m01.schematic');'fired'"
                        )
                except Exception as exc:  # noqa: BLE001
                    say(log, f"自检失败 {exc}")

            threading.Thread(target=check, daemon=True).start()

    try:
        webview.start(
            boot,
            gui="edgechromium",
            private_mode=False,
            storage_path=str(storage_dir(base)),
        )
    except Exception as exc:  # noqa: BLE001
        state["error"] = (
            f"窗口没能打开: {exc}\n\n"
            "这台电脑可能缺少 WebView2 运行时.\n"
            "Windows 11 与新版 Windows 10 自带, 旧系统可从微软官网装一次:\n"
            "https://developer.microsoft.com/microsoft-edge/webview2/"
        )
    finally:
        stop_child(state.get("child"))
        if state.get("error"):
            fail(state["error"], log)
            say(log, "退出: " + state["error"].replace("\n", " "))
            log.close()
            return 4
        say(log, "窗口已关闭, 服务已停止")
        log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
