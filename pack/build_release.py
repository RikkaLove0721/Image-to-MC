"""组装发布版目录 dist/MC工程图/.

做四件事:
  1. 把 run.py 打成 core/mc12-core.exe (Python 后端, 含 numpy 与 Pillow)
  2. 拷 web/.output 与 node.exe 到 runtime/
  3. 把 pack/launcher.py 打成 dist/MC工程图/MC工程图.exe
  4. 写一份使用说明

必须用带 PyInstaller 的 Python 运行, 也就是 temp/venv 里那个 3.10:
    temp/venv/Scripts/python.exe pack/build_release.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DIST = ROOT / "dist"
OUT = DIST / "MC工程图"
TEMP = ROOT / "temp"
"""每次构建用一个独立目录: PyInstaller 收尾时会整目录删除旧产物,
落在固定的 dist/_core 上会被系统的批量删除保护拦下."""
CORE_BUILD = TEMP / f"_core_{os.getpid()}"
NAME = "MC工程图"

NODE_CANDIDATES = [
    Path(os.environ.get("MC12_NODE", "")),
    Path(sys.executable).parent / "node.exe",
    Path(r"C:\Program Files\nodejs\node.exe"),
]


def run(args: list[str]) -> int:
    print("  $", " ".join(str(a) for a in args))
    return subprocess.call([str(a) for a in args])


def find_node() -> Path:
    for path in NODE_CANDIDATES:
        if str(path) and path.is_file():
            return path
    found = shutil.which("node")
    if found:
        exe = Path(found)
        if exe.suffix.lower() != ".exe" and (exe.parent / "node.exe").is_file():
            exe = exe.parent / "node.exe"
        if exe.is_file():
            return exe
    raise SystemExit("找不到 node.exe, 用环境变量 MC12_NODE 指一个")


def icon() -> Path:
    """等轴测立方体图标, 与打包脚本共用一套画法."""
    from build_icon import make_icon

    return make_icon(TEMP / "app.ico")


def build_core() -> Path:
    print("[1/4] 打包 Python 后端 ...")
    code = run(
        [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm",
            "--onedir",
            "--console",
            "--name", "mc12-core",
            "--collect-submodules", "mc12",
            "--hidden-import", "mcp_server",
            "--exclude-module", "tkinter",
            "--exclude-module", "matplotlib",
            "--exclude-module", "scipy",
            "--distpath", CORE_BUILD,
            "--workpath", CORE_BUILD / "work",
            "--specpath", CORE_BUILD,
            ROOT / "run.py",
        ]
    )
    if code != 0:
        raise SystemExit(f"PyInstaller 打包后端失败, 退出码 {code}")
    built = CORE_BUILD / "mc12-core"
    if not built.is_dir():
        raise SystemExit(f"没找到 {built}")
    target = OUT / "core"
    shutil.copytree(built, target, dirs_exist_ok=True)
    return target / "mc12-core.exe"


def _sync_tree(src: Path, dst: Path) -> None:
    """把 src 同步到 dst：覆盖同名文件，并删掉 dst 里 src 已经没有的文件。

    不能直接 rmtree 再 copytree：目录上千个文件会触发系统的批量删除保护；
    而 dirs_exist_ok 只覆盖不删除，删掉的路由会一直留在包里。
    """
    dst.mkdir(parents=True, exist_ok=True)
    keep: set[str] = set()
    for item in src.rglob("*"):
        if not item.is_file():
            continue
        rel = item.relative_to(src)
        keep.add(rel.as_posix())
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
    for item in sorted(dst.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        rel = item.relative_to(dst).as_posix()
        if item.is_file() and rel not in keep:
            item.unlink()
        elif item.is_dir():
            try:
                item.rmdir()
            except OSError:
                pass


def copy_runtime(node: Path) -> Path:
    print("[2/4] 布置 Web 运行时 ...")
    output = WEB / ".output"
    if not (output / "server" / "index.mjs").is_file():
        raise SystemExit("web/.output 不存在, 先在 web 目录跑 npm run build")
    runtime = OUT / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    _sync_tree(output / "server", runtime / "server")
    _sync_tree(output / "public", runtime / "public")
    shutil.copy2(node, runtime / "node.exe")
    return runtime


def build_launcher(ico: Path) -> Path:
    print("[3/4] 打包桌面外壳 ...")
    work = TEMP / f"_launcher_{os.getpid()}"
    code = run(
        [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm",
            "--onefile",
            "--windowed",
            "--name", NAME,
            "--icon", ico,
            "--distpath", OUT,
            "--workpath", work,
            "--specpath", work,
            "--exclude-module", "numpy",
            "--exclude-module", "PIL",
            "--exclude-module", "tkinter",
            "--exclude-module", "unittest",
            "--collect-all", "webview",
            "--collect-all", "pythonnet",
            "--collect-all", "clr_loader",
            "--hidden-import", "clr",
            "--hidden-import", "webview.platforms.edgechromium",
            "--hidden-import", "webview.platforms.winforms",
            ROOT / "pack" / "launcher.py",
        ]
    )
    if code != 0:
        raise SystemExit(f"PyInstaller 打包桌面外壳失败, 退出码 {code}")
    exe = OUT / f"{NAME}.exe"
    if not exe.is_file():
        raise SystemExit(f"没找到 {exe}")
    return exe


README = """MC 工程图 {version}
========================================

双击「MC工程图.exe」直接进软件窗口, 不用浏览器, 也不用装 Python 或 Node.

目录说明
  MC工程图.exe    软件本体, 就是入口
  runtime/        内置服务与界面资源
  core/           转换后端 mc12-core.exe, 已带 numpy 与 Pillow
  projects/       你的项目都在这里, 一个项目一个文件夹
                  拷走这个文件夹就是备份, 拷进来软件会自动发现
  logs/app.log    运行日志, 出问题时先看这个
  VERSION         版本号

项目文件夹
  每个项目里有 project.json / images/ / output/.
  output 里的 .schematic 直接拿去 MCEdit 或 WorldEdit 导入 Minecraft 1.12.2.
  结果页的下载按钮会弹系统另存为对话框, 存哪儿由你定.
  首页右上角「项目文件夹」按钮可以直接打开这个目录.

多视角
  正视图必传, 侧视 / 俯视 / 自由视角可选, 最多四张.
  侧视图量进深, 俯视图给平面轮廓与屋顶颜色, 自由视角只用来校白平衡.

三个滑条
  比例尺: 1 米 = 多少格. 决定模型整体多大; AI 先判断建筑的实物尺寸(米),
          再用它换算成格数, 所以窄高的楼不会被硬铺成一块长板.
  细节等级: 1~4. 管的是"贴面"——勒脚、腰线、窗套、檐口, 4 会再补一层
          小件与材质做旧(观赏性细节).
  结构数量: 1~5. 管的是"体量"——角塔、侧翼、门厅、凸窗、楼梯间、屋面设备.
          照片里的楼常有几十个部分, 蓝图只写得到三五个, 拉到 4~5 由构建器
          按规则长出来. 附属体量会自动避开入口, 不会把门廊埋掉.
  游戏建筑技巧性 0/1/2: 越高越按 MC 社区的"好看"规则来(收分、框景、腰线),
          而不是死抠照片的逐像素还原.
          界面上这一项叫「还原偏好」, 滑条往右 = 严格照照片.

AI 方案
  上传照片后可以「让 AI 出蓝图」: AI 只看分析结果与蓝图, 不读像素、也不吐方块.
  生成后会本地量一遍(层高、开窗率、比例、构件是否齐全), 有偏差再让它修,
  最多三轮, 取分最高的一版.
  结果不满意点「修改这一版」, 写一句"入口想改成几根柱子围起来"就行 ——
  只改你点到的部分, 比例、层数、种子都钉住不变.
  修改版是独立模型(m09 → m09_r2 → m09_r3), 原版不会被覆盖.

3D 查看器
  结果页最上方进去, 可以自由旋转、缩放, 点方块能看它是什么料、在哪个高度.

小C
  右下角那只 Q 版角色是界面助手, 会眨眼、摆腿、跟着鼠标看.
  左键点她可以调大小或关掉, 关掉不影响任何功能.

更新
  首页右上角「设置」→ 左边「检测更新」:
    上面一栏是当前版本与安装目录, 中间一栏查 GitHub 上的最新版本,
    下面一栏是自动更新 —— 点「下载并安装」, 下完解压到 update 暂存区,
    再点「重启并完成更新」, 软件会自己退出、覆盖、重新打开.
  项目、模型、日志、AI 密钥都不会被覆盖或删除, 只换程序本体.
  下载失败或包坏了会停在"什么都没变"的状态, 旧版本照常能用.
  也可以自己去发布页下载 zip, 解压覆盖整个文件夹(同样别删 projects).

方块库范围与自定义
  本软件以 Minecraft Java 1.12.2 的方块体系(数字 ID + metadata)为准,
  只收录了建筑常用的 177 种方块 —— 它是子集, 不是 1.12.2 的全部方块.
  1.13 以后新增的方块(深板岩 / 方解石 / 凝灰岩 / 玄武岩 / 黑石 / 铜块 /
  樱花木等)这里没有, 提示词里写了也会兜底成石头.
  需要别的版本或想加方块:
    改 mc12/palette.py 的 _rows() 增加 (id, meta, 英文名, 中文名, 颜色) 行,
    再跑 tools/export_library.py 刷新界面的方块库. 其余代码不用动.
  导出的 .schematic 是经典格式, 只有 1.12.2 及更早版本能直接读;
  1.13+ 需要先用 WorldEdit 之类的工具转换.

常见问题
  1. 双击没反应: 看看是不是被杀毒软件拦了, 把整个文件夹加白名单.
  2. 提示缺少 WebView2: 到微软官网装一次 WebView2 Runtime, Win11 与新 Win10 自带.
  3. 想挪位置: 整个文件夹一起挪, 别只拷 exe.
  4. 已经在运行: 再双击一次只会把原窗口调到前面, 不会开第二个.
"""


def write_readme() -> None:
    print("[4/4] 写使用说明 ...")
    version = "0.8.0"
    version_file = ROOT / "VERSION"
    if version_file.is_file():
        version = version_file.read_text(encoding="utf-8").strip()
        shutil.copy2(version_file, OUT / "VERSION")
    (OUT / "使用说明.txt").write_text(README.format(version=version), encoding="utf-8")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    node = find_node()
    ico = icon()

    # 只改了界面时可以用 --skip-core 跳过 Python 后端, 省一分多钟
    skip_core = "--skip-core" in sys.argv[1:]
    core = OUT / "core" / "mc12-core.exe"
    if skip_core and core.is_file():
        print("[1/4] 沿用已有的转换后端, 跳过重新打包")
    else:
        core = build_core()

    copy_runtime(node)
    exe = build_launcher(ico)
    write_readme()

    total = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print()
    print(f"后端 {core}  {core.stat().st_size / 1048576:.1f} MB")
    print(f"启动器 {exe}  {exe.stat().st_size / 1048576:.1f} MB")
    print(f"整个目录 {total / 1048576:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
