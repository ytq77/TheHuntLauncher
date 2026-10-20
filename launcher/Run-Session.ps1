$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
try {
    $credential = Import-Clixml -LiteralPath (Join-Path $taskRoot '.private\account.clixml')
    $env:BBSC_ACCOUNT = $credential.UserName
    $env:BBSC_PASSWORD = $credential.GetNetworkCredential().Password
    $env:PYTHONHOME = Join-Path $taskRoot 'runtime'
    $env:PATH = (Join-Path $taskRoot 'runtime\bin') + ';' + $env:PATH
    & (Join-Path $taskRoot 'runtime\bin\python.exe') (Join-Path $taskRoot 'client.py') *> (Join-Path $taskRoot 'startup.log')
    exit $LASTEXITCODE
} catch {
    $message = @{ phase='error'; message='启动失败，请检查账号保存状态与 startup.log。' } | ConvertTo-Json
    [IO.File]::WriteAllText((Join-Path $taskRoot 'launcher-state.json'), $message, (New-Object Text.UTF8Encoding $false))
    exit 1
} finally {
    Remove-Item Env:BBSC_PASSWORD,Env:BBSC_ACCOUNT -ErrorAction SilentlyContinue
}
