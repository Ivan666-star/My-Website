param(
    [string]$RuntimeDir = (Join-Path $PSScriptRoot '..\..\data\tunnel'),
    [string]$AddressFile = (Join-Path $RuntimeDir 'Prism网站地址.txt')
)
$ErrorActionPreference = 'Stop'
$siteDir = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$bootRuntime = Join-Path $siteDir 'data\boot'
if (Test-Path -LiteralPath (Join-Path $bootRuntime 'enabled.flag')) {
    New-Item -ItemType File -Path (Join-Path $bootRuntime 'tunnel.disabled') -Force | Out-Null
    Write-Host '已请求后台任务关闭公网隧道，本机网站继续运行。'
    return
}
New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path $AddressFile -Parent) -Force | Out-Null
$exe = Join-Path $RuntimeDir 'cloudflared.exe'
$stateFile = Join-Path $RuntimeDir 'named-state.json'
. (Join-Path $PSScriptRoot 'fixed-tunnel-common.ps1')
$control = Enter-FixedTunnelControl
try {
    $state = Read-FixedTunnelState
    $process = Get-FixedTunnelProcess $state
    if ($process) {
        Stop-Process -Id $process.Id
        if (-not $process.WaitForExit(5000)) { throw '隧道尚未完全退出，请稍后重试停止。' }
    }
    if (Test-Path -LiteralPath $stateFile) { Remove-Item -LiteralPath $stateFile }
    @('Prism 固定域名隧道已停止。', '固定域名本身没有变化，但此刻公网无法访问网站。', '双击“运行工具”中的“启动Prism域名隧道.cmd”可以恢复。', '本机网站继续运行。') | Set-Content -LiteralPath $AddressFile -Encoding UTF8
    Write-Host '固定域名隧道已停止。本机网站继续运行。'
} finally { $control.Dispose() }
