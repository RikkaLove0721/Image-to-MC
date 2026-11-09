@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
set "ROOT=%~dp0"
set "EXE=%ROOT%dist\MC工程图\MC工程图.exe"

rem 已经在跑就只把窗口调到前面, 不动文件
if exist "%EXE%" (
  tasklist /FI "IMAGENAME eq MC工程图.exe" 2>nul | find /I "MC工程图.exe" >nul
  if not errorlevel 1 (
    echo 软件已经在运行, 正在切换到现有窗口 ...
    start "" "%EXE%"
    exit /b 0
  )
)

where node >nul 2>nul
if errorlevel 1 (
  echo 没找到 Node.js. 请先安装 Node 22 或更新版本: https://nodejs.org
  pause
  exit /b 1
)

set NEED=2
for /f %%i in ('powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%pack\fresh.ps1" -Exe "%EXE%"') do set NEED=%%i
if not "%NEED%"=="0" set NEED=1

pushd "%ROOT%web"

if not exist "node_modules" (
  echo [1/4] 安装前端依赖, 首次会比较慢 ...
  call npm install --no-audit --no-fund
  if errorlevel 1 goto fail
) else (
  echo [1/4] 依赖已就位
)

if "%NEED%"=="1" (
  echo [2/4] 构建前端 ...
  call npm run build
  if errorlevel 1 goto fail

  if not exist "%ROOT%temp\venv\Scripts\python.exe" (
    echo 打包环境缺失: 需要 temp\venv 里的 Python 与 PyInstaller
    goto fail
  )

  echo [3/4] 组装桌面版, 这次要等两三分钟 ...
  popd
  "%ROOT%temp\venv\Scripts\python.exe" "%ROOT%pack\build_release.py"
  if errorlevel 1 goto fail
  pushd "%ROOT%web"
) else (
  echo [2/4] 发布包已是最新, 跳过构建
  echo [3/4] 无需组装
)

popd
echo [4/4] 启动 MC 工程图 ...
start "" "%EXE%"
exit /b 0

:fail
popd 2>nul
echo.
echo 出错了, 看看上面的日志.
pause
exit /b 1
