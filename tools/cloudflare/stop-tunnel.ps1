param(
    [string]$RuntimeDir = (Join-Path $PSScriptRoot '..\..\data\tunnel'),
    [string]$AddressFile = (Join-Path $RuntimeDir 'Prism公网测试地址.txt')
)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path $AddressFile -Parent) -Force | Out-Null
$exe = Join-Path $RuntimeDir 'cloudflared.exe'
$stateFile = Join-Path $RuntimeDir 'state.json'
. (Join-Path $PSScriptRoot 'tunnel-common.ps1')
$control = Enter-TunnelControl
try {
    $state = Read-TunnelState
    $process = Get-TunnelProcess $state
    if ($process) {
        Stop-Process -Id $process.Id
        if (-not $process.WaitForExit(5000)) { throw 'Tunnel 尚未完全退出，请稍后重试停止。' }
    }
    if (Test-Path -LiteralPath $stateFile) { Remove-Item -LiteralPath $stateFile }
    @('Prism 公网测试入口已停止。', '双击“运行工具”中的“启动Prism公网测试.cmd”可以重新创建测试链接。', '旧链接已失效，重新启动通常会生成新地址。') | Set-Content -LiteralPath $AddressFile -Encoding UTF8
    Write-Host '公网入口已停止。本机网站继续运行。'
} finally { $control.Dispose() }
