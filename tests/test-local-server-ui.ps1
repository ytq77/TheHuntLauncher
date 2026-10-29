$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework,PresentationCore,WindowsBase,System.Xaml
$repo = Split-Path -Parent $PSScriptRoot
$taskRoot = Join-Path ([IO.Path]::GetTempPath()) ('thehunt-local-' + [Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($taskRoot)
$window = [Windows.Markup.XamlReader]::Parse([IO.File]::ReadAllText((Join-Path $repo 'launcher\Launcher.xaml')))
$controls = @{}
foreach ($name in @('OpenLocalServer','UseLocalServer','Shadnet','Webapi','Community','Relay','Account','Password','StatusText')) {
    $controls[$name] = $window.FindName($name)
    if (-not $controls[$name]) { throw ('Missing local server control: ' + $name) }
}
$script:worker = $null; $script:saveTask = $null; $script:adminTask = $null; $script:conversionTask = $null
function Show-Settings { $script:settingsShown = $true }
function Show-Error([string]$text) { throw $text }
. (Join-Path $repo 'launcher\Local-Server.ps1')
$controls.Account.Text = 'existing-player'; $controls.Password.Password = 'existing-secret'
$profile = Join-Path $taskRoot 'profile.json'; [IO.File]::WriteAllText($profile,'unchanged-profile')
Use-LocalServer
if ($controls.Shadnet.Text -ne '127.0.0.1:31413' -or $controls.Webapi.Text -ne 'http://127.0.0.1:31415' -or $controls.Community.Text -ne 'http://127.0.0.1:20443' -or $controls.Relay.Text -ne '127.0.0.1:20444') { throw 'Incorrect local endpoints' }
if ($controls.Account.Text -ne 'existing-player' -or $controls.Password.Password -ne 'existing-secret' -or [IO.File]::ReadAllText($profile) -ne 'unchanged-profile' -or -not $script:settingsShown) { throw 'Endpoint selection changed credentials/profile or failed navigation' }
$controls.Shadnet.Text = 'remote.example:31413'; $script:worker = New-Object Object
$refused = $false
try { Use-LocalServer } catch { $refused = $true }
if (-not $refused -or $controls.Shadnet.Text -ne 'remote.example:31413') { throw 'Active session endpoint switch was not blocked' }
$script:worker = $null
$refused = $false
try { Open-LocalServer } catch { $refused = $true }
if (-not $refused) { throw 'Missing server component was accepted' }
[void][IO.Directory]::CreateDirectory((Join-Path $taskRoot 'server'))
[IO.File]::WriteAllText((Join-Path $taskRoot 'server\server.json'),'{"address":"192.0.2.10"}')
Use-LocalServer
if ($controls.Shadnet.Text -ne '192.0.2.10:31413' -or $controls.Webapi.Text -ne 'http://192.0.2.10:31415' -or $controls.Community.Text -ne 'http://192.0.2.10:20443' -or $controls.Relay.Text -ne '192.0.2.10:20444') { throw 'Configured listener was ignored in favor of loopback' }
[IO.File]::WriteAllText((Join-Path $taskRoot 'server\server.json'),'{"address":"0.0.0.0"}')
$refused = $false
try { Use-LocalServer } catch { $refused = $true }
if (-not $refused -or $controls.Shadnet.Text -ne '192.0.2.10:31413') { throw 'Invalid listener changed endpoint fields' }
[IO.File]::WriteAllText((Join-Path $taskRoot 'server\Server.exe'),'not-executed-test-marker')
function Start-Process([string]$FilePath,[string]$WorkingDirectory,[string]$WindowStyle) { $script:opened = @($FilePath,$WorkingDirectory,$WindowStyle) }
Open-LocalServer
if ($script:opened[0] -ne (Join-Path $taskRoot 'server\Server.exe') -or $script:opened[1] -ne (Join-Path $taskRoot 'server') -or $script:opened[2] -ne 'Normal') { throw 'Local server launches wrong program/directory/window mode' }
function Get-Process { return [pscustomobject]@{Path=(Join-Path $taskRoot 'unrelated\Server.exe')} }
if (Test-LocalServerOpen) { throw 'Unrelated server window blocked this installation' }
function Get-Process { return [pscustomobject]@{Path=(Join-Path $taskRoot 'server\Server.exe')} }
if (-not (Test-LocalServerOpen)) { throw 'Bundled server window was not detected for update guard' }
Write-Output 'PASS: local entry controls, exact endpoints, credential/profile preservation, active-session guard and independent server launch contract.'
