# Learning Mall Core 课件同步工具 — 一键安装（Windows）
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
Write-Host "== 1/4 检查 Python" -ForegroundColor Cyan
$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
if (-not $py) {
    Write-Host "没有找到 Python，尝试用 winget 安装 Python 3.12 ..."
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    Write-Host "安装完后请关闭此窗口，重新双击 install.cmd。" -ForegroundColor Yellow
    pause; exit 1
}
Write-Host "== 2/4 创建独立环境 .venv" -ForegroundColor Cyan
& $py.Source -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if ($LASTEXITCODE -ne 0) { throw "需要 Python 3.10 或更新版本，请更新后重试。" }
if (-not (Test-Path ".venv\Scripts\python.exe")) { & $py.Source -m venv .venv }
if (-not (Test-Path ".venv\Scripts\python.exe")) { throw "创建虚拟环境失败。" }
$vpy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $vpy -m pip install --upgrade pip -q
if ($LASTEXITCODE -ne 0) { throw "更新 pip 失败，请检查网络。" }
& $vpy -m pip install -r requirements.txt -q
if ($LASTEXITCODE -ne 0) { throw "安装依赖失败，请检查网络。" }
Write-Host "== 3/4 检查浏览器" -ForegroundColor Cyan
$edge = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
if (-not (Test-Path $edge)) { $edge = "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe" }
if (Test-Path $edge) { Write-Host "使用系统自带的 Microsoft Edge" }
else {
    Write-Host "没有 Edge，下载 Playwright 自带的 Chromium ..."
    & $vpy -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw "浏览器安装失败。" }
    if (-not (Test-Path config.json)) { '{"base_url":"https://core.xjtlu.edu.cn","browser":"chromium","courses":[]}' | Out-File -Encoding utf8 config.json }
}
$filee = "$env:LOCALAPPDATA\Programs\Filee\filee-cli.exe"
if (Test-Path $filee) { Write-Host "检测到 Filee：用它把 PDF/PPT/Word 转成 Markdown" }
else {
    Write-Host "没有检测到 Filee，安装备用转换器 markitdown（也可以之后自己装 Filee）"
    & $vpy -m pip install "markitdown[pdf,docx,pptx,xlsx]" -q
    if ($LASTEXITCODE -ne 0) { throw "转换器安装失败。" }
}
Write-Host "== 4/4 首次设置：登录 → 选本学期课程 → 在桌面建课程文件夹 → 第一次同步" -ForegroundColor Cyan
& $vpy -m lmcore_sync setup
if ($LASTEXITCODE -ne 0) { throw "首次设置未完成，请重试。" }
Write-Host "`n安装完成！课程文件夹已经在桌面上了。以后双击『同步课件.cmd』更新，双击『查看作业DDL.cmd』看截止时间。" -ForegroundColor Green
pause
