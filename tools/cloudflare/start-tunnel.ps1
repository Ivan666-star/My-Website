param(
    [string]$RuntimeDir = (Join-Path $PSScriptRoot '..\..\data\tunnel'),
    [string]$AddressFile = (Join-Path $RuntimeDir 'Prism公网测试地址.txt')
)
$ErrorActionPreference = 'Stop'
$siteDir = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path $AddressFile -Parent) -Force | Out-Null
$exe = Join-Path $RuntimeDir 'cloudflared.exe'
$stateFile = Join-Path $RuntimeDir 'state.json'
$stdout = Join-Path $RuntimeDir 'tunnel.stdout.log'
$stderr = Join-Path $RuntimeDir 'tunnel.stderr.log'
. (Join-Path $PSScriptRoot 'tunnel-common.ps1')
$control = Enter-TunnelControl
$state = $null
try {
    $state = Read-TunnelState
    try { $health = Invoke-RestMethod 'http://127.0.0.1:8848/api/health' -TimeoutSec 5 } catch { $health = $null }
    if (-not $health.ok) {
        $python = Join-Path $siteDir '.venv\Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $python)) { $python = 'C:\Users\Ivan\.workbuddy\binaries\python\envs\default\Scripts\python.exe' }
        if (-not (Test-Path -LiteralPath $python)) { throw '没有找到 Python，请先启动本机网站。' }
        Start-Process -FilePath $python -ArgumentList '-m uvicorn app.main:app --host 0.0.0.0 --port 8848 --no-use-colors' -WorkingDirectory $siteDir -WindowStyle Hidden -RedirectStandardOutput (Join-Path $RuntimeDir 'website.stdout.log') -RedirectStandardError (Join-Path $RuntimeDir 'website.stderr.log') | Out-Null
        for ($i = 0; $i -lt 20; $i++) {
            Start-Sleep -Seconds 1
            try { $health = Invoke-RestMethod 'http://127.0.0.1:8848/api/health' -TimeoutSec 2 } catch { $health = $null }
            if ($health.ok) { break }
        }
        if (-not $health.ok) { throw '本机网站启动失败，请查看 website 日志。' }
    }
    $gate = [System.Net.HttpWebRequest]::Create('http://127.0.0.1:8848/api/bootstrap')
    $gate.AllowAutoRedirect = $false
    $gate.Timeout = 5000
    try { $response = $gate.GetResponse() } catch [System.Net.WebException] { $response = $_.Exception.Response }
    if (-not $response) { throw '无法确认网站登录保护。' }
    $status = [int]$response.StatusCode
    $response.Close()
    if ($status -ne 401 -and $status -ne 403) { throw '请先创建管理员账号并启用登录保护。' }
    if (Get-TunnelProcess $state) {
        Write-Host '已有 Tunnel 进程，正在恢复网址并验证访问，请稍候……'
    } else {
        $state = $null
        Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
        if ((Get-AuthenticodeSignature -LiteralPath $exe).Status -ne 'Valid') { throw 'cloudflared 数字签名验证失败。' }
        Write-Host '正在申请公网测试地址，请等待启动完成……'
        $process = Start-Process -FilePath $exe -ArgumentList 'tunnel --no-autoupdate --protocol http2 --url http://127.0.0.1:8848' -WorkingDirectory $RuntimeDir -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
        $state = [ordered]@{ pid = $process.Id; startedAt = $process.StartTime.ToUniversalTime().ToString('o'); url = ''; origin = 'http://127.0.0.1:8848' }
        $state | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding UTF8
    }
    @('正在连接 Prism 公网测试入口，请等待启动窗口显示完整网址。', '若启动失败，请按窗口提示检查网络并重新启动。') | Set-Content -LiteralPath $AddressFile -Encoding UTF8
    $url = Wait-TunnelAddress $state
    Write-Host ('公网测试已就绪：' + $url)
    Write-Host ('地址说明已更新：' + $AddressFile)
} catch {
    $failure = $_.Exception.Message
    $process = Get-TunnelProcess $state
    if ($process) {
        Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
        $process.WaitForExit(5000) | Out-Null
    }
    if (Test-Path -LiteralPath $stateFile) { Remove-Item -LiteralPath $stateFile -ErrorAction SilentlyContinue }
    @('Prism 公网测试启动失败。', $failure, '本机网站不受影响。请检查网络后重新点击启动。') | Set-Content -LiteralPath $AddressFile -Encoding UTF8
    Write-Host ('启动失败：' + $failure)
    exit 1
} finally { $control.Dispose() }
