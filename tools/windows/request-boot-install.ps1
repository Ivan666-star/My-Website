$ErrorActionPreference = 'Stop'
$project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$config = Join-Path $project 'data\boot\config.json'
if (-not (Test-Path -LiteralPath $config)) { throw 'Local boot configuration is missing.' }
$installer = Join-Path $PSScriptRoot 'install-boot.ps1'
$arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $installer + '" -ConfigPath "' + $config + '"'
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
Start-Process -FilePath $powershell -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -PassThru | Select-Object Id
