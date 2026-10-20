param([string]$Preview, [switch]$PreviewSettings, [switch]$Validate)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework,PresentationCore,WindowsBase,System.Xaml
$taskRoot = $PSScriptRoot
$profilePath = Join-Path $taskRoot 'profile.json'
$credentialPath = Join-Path $taskRoot '.private\account.clixml'
$script:worker = $null
$script:startedAt = [DateTime]::MinValue
$script:kitLog = $null
$utf8 = New-Object Text.UTF8Encoding $false
$window = [Windows.Markup.XamlReader]::Parse([IO.File]::ReadAllText((Join-Path $taskRoot 'Launcher.xaml')))
$controls = @{}
@('HomeNav','SettingsNav','HomePage','SettingsPage','GraphicsSummary','AccountSummary','ServerSummary',
  'KitStatus','InstallPath','GamePath','GraphicsPath','PickInstall','PickGame','PickGraphics',
  'Shadnet','Webapi','Community','Relay','Account','Password','SaveSettings','StatusText','OpenLogs','Launch') |
  ForEach-Object { $controls[$_] = $window.FindName($_) }

function Show-Settings {
    $controls.HomePage.Visibility = 'Collapsed'
    $controls.SettingsPage.Visibility = 'Visible'
}
function Show-Home {
    $controls.HomePage.Visibility = 'Visible'
    $controls.SettingsPage.Visibility = 'Collapsed'
}
function Show-Error([string]$message) {
    $controls.StatusText.Text = $message
    [void][Windows.MessageBox]::Show($window, $message, 'BBSC Launcher', 'OK', 'Warning')
}
function Pick-File([string]$title, [string]$filter) {
    $dialog = New-Object Microsoft.Win32.OpenFileDialog
    $dialog.Title = $title
    $dialog.Filter = $filter
    if ($dialog.ShowDialog($window)) { return $dialog.FileName }
    return $null
}
function Update-Summary {
    $controls.AccountSummary.Text = if ($controls.Account.Text) { $controls.Account.Text } else { '尚未设置' }
    $controls.ServerSummary.Text = if ($controls.Shadnet.Text) { $controls.Shadnet.Text } else { '填写自己的 BBSC 服务器' }
    $source = $controls.GraphicsPath.Text
    if (Test-Path -LiteralPath $source -PathType Leaf) {
        $ini = @{}
        [IO.File]::ReadAllLines($source) | ForEach-Object {
            if ($_ -match '^\s*(output_res|upscaler)\s*=(.*)$') { $ini[$matches[1]] = $matches[2].Trim() }
        }
        $controls.GraphicsSummary.Text = $ini.output_res + ' · ' + ([string]$ini.upscaler).ToUpperInvariant()
    }
}
function Save-Profile {
    foreach ($name in @('InstallPath','GamePath','GraphicsPath')) {
        if (-not (Test-Path -LiteralPath $controls[$name].Text -PathType Leaf)) { throw '请先选择存在的游戏与配置文件。' }
    }
    foreach ($name in @('Shadnet','Webapi','Community','Relay','Account')) {
        if (-not $controls[$name].Text.Trim()) { throw '请填写完整的联机地址与账号。' }
    }
    $oldCredential = if (Test-Path -LiteralPath $credentialPath) { Import-Clixml -LiteralPath $credentialPath } else { $null }
    $password = $controls.Password.Password
    if (-not $password -and (-not $oldCredential -or $oldCredential.UserName -ne $controls.Account.Text.Trim())) {
        throw '首次使用或更换账号时需要填写密码。'
    }
    $profile = @{
        install = [IO.Path]::GetDirectoryName($controls.InstallPath.Text)
        game = [IO.Path]::GetDirectoryName($controls.GamePath.Text)
        graphics_source = $controls.GraphicsPath.Text
        network = @{ shadnet=$controls.Shadnet.Text.Trim(); webapi=$controls.Webapi.Text.Trim()
                     community=$controls.Community.Text.Trim(); relay=$controls.Relay.Text.Trim() }
    }
    if ($password) {
        $privatePath = Split-Path -Parent $credentialPath
        [void][IO.Directory]::CreateDirectory($privatePath)
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        & icacls.exe $privatePath /inheritance:r /grant:r ($identity + ':(OI)(CI)F') 'SYSTEM:(OI)(CI)F' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw '无法设置本地凭据目录权限，未保存密码。' }
        $secure = ConvertTo-SecureString $password -AsPlainText -Force
        $credential = New-Object System.Management.Automation.PSCredential($controls.Account.Text.Trim(), $secure)
        $credential | Export-Clixml -LiteralPath $credentialPath
        $controls.Password.Clear()
    }
    [IO.File]::WriteAllText($profilePath, ($profile | ConvertTo-Json -Depth 5), $utf8)
    Update-Summary
}

if (Test-Path -LiteralPath $profilePath) {
    try {
        $profile = [IO.File]::ReadAllText($profilePath) | ConvertFrom-Json
        if ($profile.install) { $controls.InstallPath.Text = Join-Path $profile.install 'Bloodborne.exe' }
        if ($profile.game) { $controls.GamePath.Text = Join-Path $profile.game 'eboot.bin' }
        if ($profile.graphics_source) { $controls.GraphicsPath.Text = $profile.graphics_source }
        if ($profile.network) {
            $controls.Shadnet.Text = $profile.network.shadnet
            $controls.Webapi.Text = $profile.network.webapi
            $controls.Community.Text = $profile.network.community
            $controls.Relay.Text = $profile.network.relay
        }
        if (Test-Path -LiteralPath $credentialPath) {
            $controls.Account.Text = (Import-Clixml -LiteralPath $credentialPath).UserName
        }
        Update-Summary
        $controls.StatusText.Text = '已加载本机配置。准备好后开始狩猎。'
    } catch { $controls.StatusText.Text = '配置无法读取，请重新填写；原存档仍保存在 data 目录。' }
}
$controls.HomeNav.Add_Click({ Show-Home })
$controls.SettingsNav.Add_Click({ Show-Settings })
$controls.PickInstall.Add_Click({
    $path = Pick-File '选择普通版 Bloodborne.exe' 'Bloodborne.exe|Bloodborne.exe'
    if ($path) {
        $controls.InstallPath.Text = $path
        $ini = Join-Path (Split-Path -Parent $path) 'bbport.ini'
        if (Test-Path -LiteralPath $ini) { $controls.GraphicsPath.Text = $ini }
    }
})
$controls.PickGame.Add_Click({
    $path = Pick-File '选择 CUSA03173 1.09 eboot.bin' 'eboot.bin|eboot.bin'
    if ($path) { $controls.GamePath.Text = $path }
})
$controls.PickGraphics.Add_Click({
    $path = Pick-File '选择这台电脑普通版的 bbport.ini' 'bbport.ini|bbport.ini'
    if ($path) { $controls.GraphicsPath.Text = $path }
})
$controls.SaveSettings.Add_Click({
    try { Save-Profile; $controls.StatusText.Text = '设置已保存。'; Show-Home }
    catch { Show-Error $_.Exception.Message }
})
$controls.OpenLogs.Add_Click({
    $path = Join-Path $taskRoot 'data\user'
    if (-not (Test-Path -LiteralPath $path)) { $path = $taskRoot }
    # Explorer is intentionally visible because this button requests a folder window.
    [void][Diagnostics.Process]::Start('explorer.exe', ('"' + $path + '"'))
})
$controls.Launch.Add_Click({
    try {
        if (Get-Process bb-probe -ErrorAction SilentlyContinue) { throw '请先正常退出正在运行的游戏。' }
        if (-not (Test-Path -LiteralPath (Join-Path $taskRoot 'runtime\bin\python.exe'))) {
            throw '这里是源码目录。请下载完整的 Release 压缩包后再启动。'
        }
        Save-Profile
        $script:startedAt = Get-Date
        $script:kitLog = $null
        [IO.File]::WriteAllText((Join-Path $taskRoot 'launcher-state.json'), '{"phase":"preparing","message":"正在准备独立环境…"}', $utf8)
        $info = New-Object Diagnostics.ProcessStartInfo
        $info.FileName = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $info.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $taskRoot 'Run-Session.ps1') + '"'
        $info.WorkingDirectory = $taskRoot
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $info.WindowStyle = 'Hidden'
        $script:worker = [Diagnostics.Process]::Start($info)
        $controls.Launch.IsEnabled = $false
        $controls.SaveSettings.IsEnabled = $false
        $controls.SettingsNav.IsEnabled = $false
        $controls.Launch.Content = '狩猎进行中'
        $controls.KitStatus.Text = '正在等待模组加载…'
        Show-Home
    } catch { Show-Settings; Show-Error $_.Exception.Message }
})
$timer = New-Object Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromSeconds(1)
$timer.Add_Tick({
    if (-not $script:worker) { return }
    try {
        $path = Join-Path $taskRoot 'launcher-state.json'
        if (Test-Path -LiteralPath $path) {
            $state = [IO.File]::ReadAllText($path) | ConvertFrom-Json
            $controls.StatusText.Text = $state.message
        }
        if (-not $script:kitLog) {
            $script:kitLog = Get-ChildItem -LiteralPath (Join-Path $taskRoot 'mod\logs') -Filter 'bbsc-mod-*.log' -ErrorAction SilentlyContinue |
                Where-Object { $_.LastWriteTime -ge $script:startedAt } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        }
        if ($script:kitLog -and $controls.KitStatus.Text -notlike '五件*') {
            $text = [IO.File]::ReadAllText($script:kitLog.FullName)
            if ($text -match 'BBSC kit initialization: ready after (\d+) ms') {
                $controls.KitStatus.Text = '五件猎具已准备完成 · ' + ([math]::Round([double]$matches[1] / 1000, 1)) + ' 秒'
            } elseif ($text -match 'BBSC kit initialization: started') {
                $controls.KitStatus.Text = '正在准备猎具的分类、图标与效果…'
            }
        }
        if ($script:worker.HasExited) {
            if ($state.phase -notin @('exited','error')) { $controls.StatusText.Text = '启动进程已结束，请查看 startup.log。' }
            $script:worker.Dispose()
            $script:worker = $null
            $controls.Launch.IsEnabled = $true
            $controls.SaveSettings.IsEnabled = $true
            $controls.SettingsNav.IsEnabled = $true
            $controls.Launch.Content = '开始狩猎  →'
        }
    } catch { } # A partially-written status file is retried on the next tick.
})
$window.Add_Closing({
    if ($script:worker -and -not $script:worker.HasExited) {
        $_.Cancel = $true
        $controls.StatusText.Text = '请先从游戏菜单正常退出，再关闭启动器。'
    }
})
if ($Validate) {
    foreach ($name in $controls.Keys) { if (-not $controls[$name]) { throw "Missing UI element: $name" } }
    'WPF controls loaded successfully.'
    exit 0
}
if ($Preview) {
    # Render our own view for layout QA; does not start a game or read other apps.
    if ($PreviewSettings) { Show-Settings }
    $view = $window.Content
    $view.Background = $window.Background
    $view.Measure((New-Object Windows.Size(1040,740)))
    $view.Arrange((New-Object Windows.Rect(0,0,1040,740)))
    $view.UpdateLayout()
    $bitmap = New-Object Windows.Media.Imaging.RenderTargetBitmap(1040,740,96,96,[Windows.Media.PixelFormats]::Pbgra32)
    $bitmap.Render($view)
    $encoder = New-Object Windows.Media.Imaging.PngBitmapEncoder
    $encoder.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($bitmap))
    $stream = [IO.File]::Create([IO.Path]::GetFullPath($Preview))
    try { $encoder.Save($stream) } finally { $stream.Dispose() }
    exit 0
}
$hasher = [Security.Cryptography.SHA256]::Create()
try { $mutexName = 'Local\BBSCLauncher-' + [BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($taskRoot))) }
finally { $hasher.Dispose() }
$mutex = New-Object Threading.Mutex($false, $mutexName)
if (-not $mutex.WaitOne(0)) {
    [void][Windows.MessageBox]::Show('这个目录的 BBSC Launcher 已经打开，请从任务栏切换。', 'BBSC Launcher')
    exit 0
}
try { $timer.Start(); [void]$window.ShowDialog() }
finally { $timer.Stop(); $mutex.ReleaseMutex(); $mutex.Dispose() }
