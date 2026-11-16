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
if (-not (Test-Path ".venv\Scripts\python.exe")) { & $py.Source -m venv .venv }
$vpy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $vpy -m pip install --upgrade pip -q
& $vpy -m pip install -r requirements.txt -q
Write-Host "== 3/4 检查浏览器" -ForegroundColor Cyan
$edge = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
if (-not (Test-Path $edge)) { $edge = "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe" }
if (Test-Path $edge) { Write-Host "使用系统自带的 Microsoft Edge" }
else {
    Write-Host "没有 Edge，下载 Playwright 自带的 Chromium ..."
    & $vpy -m playwright install chromium
    if (-not (Test-Path config.json)) { '{"base_url":"https://core.xjtlu.edu.cn","browser":"chromium","courses":[]}' | Out-File -Encoding utf8 config.json }
}
$filee = "$env:LOCALAPPDATA\Programs\Filee\filee-cli.exe"
if (Test-Path $filee) { Write-Host "检测到 Filee：用它把 PDF/PPT/Word 转成 Markdown" }
else {
    Write-Host "没有检测到 Filee，安装备用转换器 markitdown（也可以之后自己装 Filee）"
    & $vpy -m pip install "markitdown[pdf,docx,pptx,xlsx]" -q
}
Write-Host "== 4/4 首次设置：登录并选择课程" -ForegroundColor Cyan
& $vpy -m lmcore_sync setup
Write-Host "`n安装完成。以后双击『同步课件.cmd』即可。" -ForegroundColor Green
pause
