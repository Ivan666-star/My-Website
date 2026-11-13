param([Parameter(Mandatory=$true)][string]$ConfigPath)
$ErrorActionPreference = 'Stop'
$cfg = [IO.File]::ReadAllText($ConfigPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
$runtime = [IO.Path]::GetFullPath($cfg.runtime)
$siteDir = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
if ($siteDir -ne [IO.Path]::GetFullPath($cfg.project)) { throw 'Project path mismatch' }
if (-not $runtime.StartsWith((Join-Path $siteDir 'data') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Runtime path mismatch' }
$resultFile = Join-Path $runtime 'install-result.json'
$taskName = 'Prism-Boot'
$probeName = 'Prism-Boot-Preflight'
$backup = Join-Path $runtime ('backup-' + (Get-Date -Format yyyyMMdd-HHmmss))
New-Item -ItemType Directory -Path $backup -Force | Out-Null
$moved = @()
$changedAcl = @()

function Save-Result($value) {
    [IO.File]::WriteAllText($resultFile, ($value | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
}
function Read-Status {
    $path = Join-Path $runtime 'status.json'
    if (Test-Path -LiteralPath $path) {
        try { return [IO.File]::ReadAllText($path) | ConvertFrom-Json } catch { }
    }
}
function Wait-Health {
    for ($i=0; $i -lt 45; $i++) {
        $status = Read-Status
        if ($status.local_ok -and $status.backend_pid -and $status.tunnel_pid) { return $status }
        Start-Sleep -Seconds 2
    }
    throw 'New background task did not become healthy. See data/boot logs.'
}
function Grant-Path([string]$path, [string]$permission) {
    if (-not (Test-Path -LiteralPath $path)) { throw ('Missing path: ' + $path) }
    $script:changedAcl += @{path=$path; sddl=(Get-Acl -LiteralPath $path).Sddl}
    & icacls.exe $path /grant ('*S-1-5-19:' + $permission) /Q | Out-Null
    if ($LASTEXITCODE -ne 0) { throw ('Cannot grant service access: ' + $path) }
}
try {
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator authorization is required once.' }
    # LOCAL SERVICE 权限低于 SYSTEM；只开放网站运行实际需要的文件。
    foreach ($path in $cfg.read_directories) { Grant-Path $path '(OI)(CI)RX' }
    Grant-Path (Join-Path $siteDir 'data') '(OI)(CI)M'
    Grant-Path $cfg.tunnel_config 'R'
    Grant-Path $cfg.tunnel_credentials 'R'
    $changedAcl | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $backup 'acl.json') -Encoding UTF8
    $account = New-ScheduledTaskPrincipal -UserId 'S-1-5-19' -LogonType ServiceAccount
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
    $supervisor = Join-Path $PSScriptRoot 'boot_supervisor.py'
    $arguments = '"' + $supervisor + '" --config "' + $ConfigPath + '"'
    # 先在真实服务账号下做预检；预检失败不会停止旧网站。
    $checkFile = Join-Path $runtime 'preflight.json'
    Remove-Item -LiteralPath $checkFile -ErrorAction SilentlyContinue
    $probe = New-ScheduledTaskAction -Execute $cfg.python -Argument ($arguments + ' --check') -WorkingDirectory $siteDir
    Register-ScheduledTask -TaskName $probeName -Action $probe -Principal $account -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $probeName
    for ($i=0; $i -lt 30 -and -not (Test-Path -LiteralPath $checkFile); $i++) { Start-Sleep -Seconds 1 }
    if (-not (Test-Path -LiteralPath $checkFile)) { throw 'Service-account preflight timed out' }
    $check = [IO.File]::ReadAllText($checkFile) | ConvertFrom-Json
    if (-not $check.ok) { throw ('Service-account preflight failed: ' + $check.error) }
    Unregister-ScheduledTask -TaskName $probeName -Confirm:$false

    # 备份旧任务定义，以便回退。
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) {
        Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath (Join-Path $backup 'task.xml') -Encoding Unicode
        New-Item -ItemType File -Path (Join-Path $runtime 'stop.flag') -Force | Out-Null
        Start-Sleep -Seconds 5
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    }
    # 旧守护进程先正常退出；只处理已核对的 Prism 网站监听端口。
    New-Item -ItemType File -Path $cfg.legacy_stop_flag -Force | Out-Null
    for ($i=0; $i -lt 15; $i++) {
        $watchers = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -and $_.CommandLine.IndexOf($cfg.legacy_watchdog,[StringComparison]::OrdinalIgnoreCase) -ge 0 })
        if (-not $watchers.Count) { break }
        Start-Sleep -Seconds 1
    }
    $listeners = @(Get-NetTCPConnection -LocalPort $cfg.port -State Listen -ErrorAction SilentlyContinue)
    foreach ($entry in $listeners) {
        $process = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $entry.OwningProcess)
        if ($process.CommandLine -notmatch 'uvicorn app\.main:app.*--port 8848') { throw 'Port 8848 is occupied by an unexpected process; installation stopped.' }
        Stop-Process -Id $entry.OwningProcess
    }
    $oldTunnels = @(Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" | Where-Object {
        $_.ExecutablePath -eq $cfg.tunnel_exe -and $_.CommandLine.IndexOf($cfg.tunnel_config,[StringComparison]::OrdinalIgnoreCase) -ge 0
    })
    foreach ($process in $oldTunnels) { Stop-Process -Id $process.ProcessId }
    Remove-Item -LiteralPath (Join-Path $runtime 'stop.flag') -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $runtime 'tunnel.disabled') -ErrorAction SilentlyContinue
    $action = New-ScheduledTaskAction -Execute $cfg.python -Argument $arguments -WorkingDirectory $siteDir
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $trigger.Delay = 'PT15S'
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $account -Settings $settings -Description 'Prism website and tunnel before Windows sign-in' -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
    $status = Wait-Health
    # 新任务已接管后才移走两个登录启动项，不触碰其他启动项。
    foreach ($path in $cfg.legacy_startup_files) {
        if (Test-Path -LiteralPath $path) {
            $destination = Join-Path $backup ([IO.Path]::GetFileName($path))
            Move-Item -LiteralPath $path -Destination $destination
            $moved += @{source=$path; backup=$destination}
        }
    }
    New-Item -ItemType File -Path (Join-Path $runtime 'enabled.flag') -Force | Out-Null
    Save-Result @{ok=$true; task=$taskName; principal='S-1-5-19'; preflight=$check; status=$status; backup=$backup; startup_files=$moved}
} catch {
    Save-Result @{ok=$false; error=$_.Exception.Message; backup=$backup; startup_files=$moved}
    # 不删除数据库、凭据或旧启动项，失败信息留给前台恢复旧启动方式。
    exit 1
}
