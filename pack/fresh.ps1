# 判断发布包是否需要重建. 输出 1 表示需要, 0 表示不用.
# 任何一步判断失灵都输出 1, 宁可多等两分钟也不跑旧产物.
param([string]$Exe)

$root = Split-Path -Parent $PSScriptRoot

if ([string]::IsNullOrWhiteSpace($Exe)) { Write-Output 1; exit }
if (-not (Test-Path -LiteralPath $Exe)) { Write-Output 1; exit }

$targets = @(
  (Join-Path $root 'web\app'),
  (Join-Path $root 'web\server'),
  (Join-Path $root 'web\nuxt.config.ts'),
  (Join-Path $root 'web\package.json'),
  (Join-Path $root 'mc12'),
  (Join-Path $root 'run.py'),
  (Join-Path $root 'pack'),
  (Join-Path $root 'VERSION')
)

$files = New-Object System.Collections.ArrayList
foreach ($t in $targets) {
  if (Test-Path -LiteralPath $t) {
    foreach ($f in (Get-ChildItem -LiteralPath $t -Recurse -File -ErrorAction SilentlyContinue)) {
      [void]$files.Add($f)
    }
  }
}

if ($files.Count -eq 0) { Write-Output 1; exit }

$newest = ($files | Sort-Object LastWriteTime -Descending | Select-Object -First 1)
$exeTime = (Get-Item -LiteralPath $Exe).LastWriteTime

if ($newest.LastWriteTime -gt $exeTime) { Write-Output 1 } else { Write-Output 0 }
