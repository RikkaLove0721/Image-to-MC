# MC 工程图 · Image to MC

把一张建筑照片转成 **Minecraft Java 1.12.2** 方块模型（`.schematic`），并给出方块清单、等轴测预览与分层视图。

支持上传 **正视图 / 侧视图 / 俯视图 / 自由视角图** 最多四张，多视角时按各方向的尺寸与轮廓共同推断体量，单图则退化为 2.5D 浮雕。

![界面预览](input/house.png)

## 功能

- **多视角建模**：正/侧/俯/自由四种角色，最多四张；缺哪个方向就只从有的方向推断
- **一键出模型**：照片 → 透视矫正 → 光照归一化 → 降采样到 1 像素 = 1 方块 → 配色 → 体素化 → 空心壳
- **方块表**：按数量排序的方块清单，直接对着清单备料
- **三种预览**：等轴测渲染图、HTML 三维查看器、逐层平面图
- **项目化管理**：每个项目独立文件夹，可命名、记录创建时间，随时切回继续改图
- **桌面版**：不依赖浏览器，双击 exe 即开原生窗口；磁盘上的项目文件夹改动会自动同步回界面
- **旧版本兼容**：只用 1.12.2 存在的方块（数字 ID + metadata），导出的 `.schematic` 可直接丢进 WorldEdit / MCEdit

## 快速开始

### 桌面版（推荐）

双击 `启动界面.bat`。首次运行会自动装前端依赖、构建并组装发布包（约两三分钟），之后直接开窗口。

产物在 `dist/MC工程图/`，其中 `MC工程图.exe` 可单独拷走使用，不需要另装 Python 或 Node。

### 从源码跑

```bash
# 1. 前端依赖
cd web && npm install

# 2. 后端依赖（Python 3.10+，需要 numpy 与 Pillow）
pip install numpy pillow pywebview

# 3. 开发模式（Nuxt 热更新，浏览器访问 http://localhost:4310）
cd web && npm run dev
```

回到项目根目录执行 `python run.py` 是本项目的命令行入口，也是后端进程的实际工作方式。

### 命令行

```bash
python run.py -i 正视图.jpg --view side=侧面.jpg --view top=俯视.jpg -o output --name 我的楼
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--preset xs/s/m/l/xl` | 尺寸预设，宽度 24 / 32 / 40 / 56 / 80 格，默认 `m` |
| `--mode building/synth/hall/flat/relief/terrain` | 生成策略，默认 `building` |
| `--clean 0/1/2` | 规整强度，2 会统一窗尺寸并等间距重排 |
| `--dedupe 10` | 颜色过于接近的方块合并阈值（CIEDE2000），调大则配色更统一 |
| `--no-hollow` | 保留实心体（默认输出空心壳） |

完整参数见 `python run.py -h`。

## 目录结构

```
mc12/         建模核心：矫正、降采样、聚类配色、体素化、schematic 导出
mc12gui/      旧版 Tk 界面
pack/         桌面外壳（pywebview + WebView2）与发布包构建脚本
web/          Nuxt 4 前端 + Nitro 后端
run.py        命令行入口 / 后端进程
```

## 打包发布

```bash
python pack/build_release.py
```

会依次构建前端、打包 Python 后端、下载 Node 运行时并组装 `dist/MC工程图/`。加 `--skip-core` 可只用已有后端包重新组装。

## 环境

- 目标游戏版本：**Minecraft Java 1.12.2**
- 运行时：Windows 10/11，自带 WebView2（系统 Edge 即可）
- 开发：Node 22+、Python 3.10+

## 许可证

尚未指定。
