# 构建两套独立 onedir 程序。只复用本机已有的 PyInstaller / PySide6，不安装依赖。
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

python -c "import PyInstaller, PySide6, sys; print('Python', sys.version.split()[0]); print('PyInstaller', PyInstaller.__version__); print('PySide6', PySide6.__version__)"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller/PySide6 不可用；请先检查环境，不要自动安装。" }

if (-not (Test-Path "assets\brand\app_icon.ico")) {
    python -X utf8 "packaging\make_placeholder_icon.py"
    if ($LASTEXITCODE -ne 0) { throw "占位图标生成失败。" }
}

python -m PyInstaller --noconfirm --clean `
    --distpath "dist\reader" `
    --workpath "build\reader" `
    "packaging\dao_yuan_reader.spec"
if ($LASTEXITCODE -ne 0) { throw "普通用户端构建失败。" }

python -m PyInstaller --noconfirm --clean `
    --distpath "dist\admin" `
    --workpath "build\admin" `
    "packaging\dao_yuan_admin.spec"
if ($LASTEXITCODE -ne 0) { throw "维护端构建失败。" }

Copy-Item "packaging\README_reader.txt" "dist\reader\道源知识库\使用说明.txt" -Force
Copy-Item "packaging\README_admin.txt" "dist\admin\道源采集管理\使用说明.txt" -Force
Copy-Item "assets\brand\app_icon.ico" "dist\reader\道源知识库\app_icon.ico" -Force
Copy-Item "assets\brand\app_icon.ico" "dist\admin\道源采集管理\app_icon.ico" -Force

Write-Host ""
Write-Host "构建完成："
Get-Item "dist\reader\道源知识库\道源知识库.exe", "dist\admin\道源采集管理\道源采集管理.exe" |
    Select-Object FullName, Length, LastWriteTime
