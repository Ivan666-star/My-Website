function Get-TunnelProcess($state) {
    if (-not $state -or -not $state.pid -or -not $state.startedAt) { return }
    $process = Get-Process -Id $state.pid -ErrorAction SilentlyContinue
    if ($process -and $process.Path -eq $exe -and $process.StartTime.ToUniversalTime().ToString('o') -eq $state.startedAt) { return $process }
}

function Read-TunnelState {
    if (Test-Path -LiteralPath $stateFile) {
        try { return Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json }
        catch { Write-Warning '状态文件无法读取，将重新检查启动。' }
    }
}

function Write-TunnelAddress([string]$url) {
    @('Prism 公网测试地址', '', $url, '',
      '使用原有网站账号登录。新用户注册后需要管理员批准。',
      '电脑和网站服务必须保持运行；关闭 Tunnel 后链接失效，重新启动通常会换地址。',
      '重复点击启动会恢复并显示仍在运行的地址，不会强制换网址。',
      '需要换网址时，先点击停止，再点击启动；把新地址发给测试者。',
      '“运行工具”中的“停止Prism公网测试.cmd”只停止公网入口，保留本机网站运行。'
    ) | Set-Content -LiteralPath $AddressFile -Encoding UTF8
}

function Wait-TunnelAddress($state) {
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    $nextProbe = [DateTime]::MinValue
    while ([DateTime]::UtcNow -lt $deadline) {
        if (-not (Get-TunnelProcess $state)) { throw 'Tunnel 进程已退出，请查看 tunnel.stderr.log。' }
        $log = [string](Get-Content -LiteralPath $stderr -Raw -Encoding UTF8 -ErrorAction SilentlyContinue)
        $match = [regex]::Match($log, 'https://[a-z0-9-]+\.trycloudflare\.com')
        $url = [string]$state.url
        if ($url -notmatch '^https://[a-z0-9-]+\.trycloudflare\.com$') {
            $url = if ($match.Success) { $match.Value } else { '' }
        }
        if ($url -and $log.Contains('Registered tunnel connection') -and [DateTime]::UtcNow -ge $nextProbe) {
            try {
                $health = Invoke-RestMethod ($url + '/api/health') -TimeoutSec 5
                if ($health.ok) {
                    $state.url = $url
                    $state | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding UTF8
                    Write-TunnelAddress $url
                    return $url
                }
            } catch { }
            $nextProbe = [DateTime]::UtcNow.AddSeconds(3)
        }
        Start-Sleep -Seconds 1
    }
    throw '60 秒内未取得可访问的网址，请检查网络后再次启动。'
}

function Enter-TunnelControl {
    try { return [IO.File]::Open((Join-Path $RuntimeDir 'control.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None) }
    catch { throw '另一个启动或停止操作尚未完成，请等待该窗口结束后再试。' }
}
