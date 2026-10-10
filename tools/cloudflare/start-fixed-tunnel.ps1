param(
    [string]$RuntimeDir = (Join-Path $PSScriptRoot '..\..\data\tunnel'),
    [string]$AddressFile = (Join-Path $RuntimeDir 'Prism网站地址.txt'),
    [string]$Hostname = 'myprism.click',
    [string]$ConfigPath = (Join-Path $env:USERPROFILE '.cloudflared\config.yml')
)
$ErrorActionPreference = 'Stop'
$siteDir = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path $AddressFile -Parent) -Force | Out-Null
$exe = Join-Path $RuntimeDir 'cloudflared.exe'
$stateFile = Join-Path $RuntimeDir 'named-state.json'
$stdout = Join-Path $RuntimeDir 'named-tunnel.stdout.log'
$stderr = Join-Path $RuntimeDir 'named-tunnel.stderr.log'
. (Join-Path $PSScriptRoot 'fixed-tunnel-common.ps1')
$control = Enter-FixedTunnelControl
$state = $null
try {
    # 1) 从配置文件读取命名隧道 UUID
    if (-not (Test-Path -LiteralPath $ConfigPath)) {
        throw ('没有找到隧道配置：' + $ConfigPath + '（需先完成 cloudflared tunnel login 与 tunnel create）')
    }
    $match = [regex]::Match([string](Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8), '(?m)^\s*tunnel:\s*([0-9a-fA-F-]{36})\s*$')
    if (-not $match.Success) { throw '隧道配置文件里没有读到 tunnel UUID。' }
    $tunnelId = $match.Groups[1].Value

    # 2) 校验 cloudflared.exe 与数字签名
    if (-not (Test-Path -LiteralPath $exe)) { throw ('没有找到 cloudflared.exe：' + $exe) }
    Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
    if ((Get-AuthenticodeSignature -LiteralPath $exe).Status -ne 'Valid') { throw 'cloudflared 数字签名验证失败。' }

    # 3) 确认本机网站已就绪（未运行时按既有方式启动）
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

    # 4) 复用已在运行的隧道进程，否则启动新的
    $state = Read-FixedTunnelState
    if (Get-FixedTunnelProcess $state) {
        Write-Host '固定域名隧道已在运行，正在验证访问，请稍候……'
    } else {
        $state = $null
        $process = Start-Process -FilePath $exe -ArgumentList @('--config', $ConfigPath, '--no-autoupdate', 'tunnel', 'run', $tunnelId) -WorkingDirectory $RuntimeDir -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
        $state = [ordered]@{
            pid       = $process.Id
            startedAt = $process.StartTime.ToUniversalTime().ToString('o')
            tunnel    = $tunnelId
            hostname  = $Hostname
            url       = ('https://' + $Hostname)
        }
        $state | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding UTF8
        Write-Host '正在连接固定域名隧道，请稍候……'
    }
    @('正在确认固定地址可访问，请等待窗口显示结果。') | Set-Content -LiteralPath $AddressFile -Encoding UTF8
    $result = Wait-FixedAddress $Hostname
    if ($result.verified) {
        Write-FixedAddress $result.url
        Write-Host ('固定地址已就绪：' + $result.url)
    } else {
        Write-FixedAddress $result.url '隧道已连接 Cloudflare，但本机自检未通过（本机 DNS 缓存或证书吊销检查常导致误报）。请用浏览器打开上面的地址确认。'
        Write-Host ('隧道已连接 Cloudflare，但本机自检未通过。请用浏览器打开 ' + $result.url + ' 确认。')
    }
    Write-Host ('地址说明已更新：' + $AddressFile)
} catch {
    $failure = $_.Exception.Message
    # 固定域名隧道不因一次验证失败而被杀：网络抖动、Cloudflare 短暂异常都会自行恢复。
    @('Prism 固定域名隧道启动异常。', $failure, '本机网站不受影响。隧道进程可能仍在运行并自行恢复，稍后可再次点击启动确认。') | Set-Content -LiteralPath $AddressFile -Encoding UTF8
    Write-Host ('启动异常：' + $failure)
    exit 1
} finally { $control.Dispose() }
