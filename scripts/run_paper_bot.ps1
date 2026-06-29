$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"
$LogDir = Join-Path $ProjectRoot "logs"
$TranscriptPath = Join-Path $LogDir "paper_bot_task.log"

if (-not (Test-Path -LiteralPath $PythonExe)) {
    $PythonExe = "python"
}

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
Set-Location -LiteralPath $ProjectRoot

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
"[$stamp] starting Polymarket paper bot from $ProjectRoot" | Out-File -FilePath $TranscriptPath -Append -Encoding utf8

& $PythonExe paper_trading_bot.py *>> $TranscriptPath
$exitCode = $LASTEXITCODE

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"
"[$stamp] paper bot exited with code $exitCode" | Out-File -FilePath $TranscriptPath -Append -Encoding utf8
exit $exitCode
