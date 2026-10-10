# Prism 固定域名（Cloudflare 命名隧道）共用函数
# 由 start-fixed-tunnel.ps1 / stop-fixed-tunnel.ps1 点源加载。
# 调用方需先定义：$RuntimeDir $AddressFile $exe $stateFile $stderr

# 国内网络常常访问不到证书吊销列表（CRL/OCSP），Windows 的 HTTPS 校验会抛
# CRYPT_E_REVOCATION_OFFLINE，把"吊销服务器不可达"误判成"网站不可访问"。
# 这里放宽本进程的策略，避免假报警。
try { [Net.ServicePointManager]::CheckCertificateRevocationList = $false } catch { }
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

function Get-FixedTunnelProcess($state) {
    if (-not $state -or -not $state.pid -or -not $state.startedAt) { return }
    $process = Get-Process -Id $state.pid -ErrorAction SilentlyContinue
    if ($process -and $process.Path -eq $exe -and $process.StartTime.ToUniversalTime().ToString('o') -eq $state.startedAt) { return $process }
}

function Read-FixedTunnelState {
    if (Test-Path -LiteralPath $stateFile) {
        try { return Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json }
        catch { Write-Warning '固定域名状态文件无法读取，将重新检查隧道。' }
    }
}

function Test-FixedHealth([string]$url) {
    # 优先用系统自带 curl：它能跳过证书吊销检查，结论最贴近真实浏览器。
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        $body = (& $curl.Source --ssl-no-revoke -s --max-time 15 $url 2>$null) -join ''
        if ($body -match '"ok"\s*:\s*true') { return $true }
        if ($body) { return $false }
    }
    try {
        $health = Invoke-RestMethod $url -TimeoutSec 15
        return [bool]$health.ok
    } catch { return $false }
}

function Write-FixedAddress([string]$url, [string]$note = '') {
    $lines = @(
        'Prism 网站地址（固定域名，永久有效）', '',
        $url, '',
        '这是固定地址：重启电脑、重启隧道都不会改变；带 www 前缀（www.' + ($url -replace '^https://', '') + '）同样可以访问。',
        '使用原有网站账号登录；新用户注册后需要管理员批准。',
        '必须保持电脑开机、联网，且本机网站服务与隧道程序都在运行，网站才能访问。',
        '“运行工具”里的停止按钮只关闭公网入口，本机网站继续运行。',
        '域名解析由 Cloudflare 托管（NS：ada.ns.cloudflare.com、corey.ns.cloudflare.com），域名本身仍在阿里云，续费与实名不受影响。'
    )
    if ($note) { $lines += ''; $lines += ('提示：' + $note) }
    $lines | Set-Content -LiteralPath $AddressFile -Encoding UTF8
}

function Enter-FixedTunnelControl {
    try { return [IO.File]::Open((Join-Path $RuntimeDir 'fixed-control.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None) }
    catch { throw '另一个启动或停止操作尚未完成，请等待该窗口结束后再试。' }
}

function Wait-FixedAddress([string]$hostname) {
    $url = 'https://' + $hostname
    $deadline = [DateTime]::UtcNow.AddSeconds(120)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (-not (Get-FixedTunnelProcess (Read-FixedTunnelState))) { throw '隧道进程已退出，请查看 named-tunnel.stderr.log。' }
        if (Test-FixedHealth ($url + '/api/health')) { return [ordered]@{ url = $url; verified = $true } }
        Start-Sleep -Seconds 3
    }
    # 自检超时：若日志显示隧道已与 Cloudflare 建立连接，说明地址本身是通的，
    # 问题多半出在本机自检环境（DNS 或证书吊销检查），此时不能报"启动失败"。
    $log = [string](Get-Content -LiteralPath $stderr -Raw -Encoding UTF8 -ErrorAction SilentlyContinue)
    if ($log -match 'Registered tunnel connection') { return [ordered]@{ url = $url; verified = $false } }
    throw ((('120 秒内 ' + $url) + ' 仍无法访问；隧道进程继续运行，请检查网络与 Cloudflare 状态。'))
}
