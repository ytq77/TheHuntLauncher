param([string]$Preview, [switch]$PreviewSettings, [switch]$PreviewSaves, [switch]$PreviewFeatures, [switch]$PreviewServer, [switch]$Validate)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework,PresentationCore,WindowsBase,System.Xaml
$taskRoot = $PSScriptRoot
$profilePath = Join-Path $taskRoot 'profile.json'
$credentialPath = Join-Path $taskRoot '.private\account.clixml'
$script:worker = $null
$script:startedAt = [DateTime]::MinValue
$script:kitLog = $null
$script:saveTask = $null
$script:saveInspection = $null
$script:saveTaskApply = $false
$saveResultPath = Join-Path $taskRoot 'save-transfer-result.json'
$utf8 = New-Object Text.UTF8Encoding $false
$window = [Windows.Markup.XamlReader]::Parse([IO.File]::ReadAllText((Join-Path $taskRoot 'Launcher.xaml')))
$controls = @{}
@('HomeNav','SettingsNav','SaveNav','FeatureNav','HomePage','SettingsPage','SavePage','FeaturePage','GraphicsSummary','AccountSummary','ServerSummary',
  'KitStatus','InstallPath','GamePath','GraphicsPath','PickInstall','PickGame','PickGraphics',
  'Shadnet','Webapi','Community','Relay','Account','Password','SaveSettings','StatusText','OpenLogs','CheckUpdates','Launch',
  'SourceSavePath','DestinationSavePath','SaveSummary','RefreshSaves','ImportSaves','OpenSaveBackups',
  'ConversionNew','ConversionBaseline','ConversionInspect','ConversionConvert','ConversionVerify','ConversionExport',
  'ConversionStartExport','ConversionStartReplace',
  'ConversionSameRole','ConversionClearSlots','ConversionConfirmed','ConversionOpen','ConversionOpenOrdinaryBackup','ConversionStatus','ConversionPath',
  'FeatureItems','FeatureSummary','FeatureStatus','SaveFeatures','RecommendFeatures','ExpandFeatures',
  'ServerNav','ServerPage','AdminTarget','AdminPort','AdminDirectory','AdminIdentity','AdminKnownHosts','AdminSudo','PickAdminIdentity','PickAdminKnownHosts',
  'AdminRefresh','AdminCreate','AdminVerify','AdminName','AdminPassword','AdminEmail','AdminAccounts','AdminStatus','AdminFields') |
  ForEach-Object { $controls[$_] = $window.FindName($_) }

function Show-Settings {
    $controls.ServerPage.Visibility = 'Collapsed'
    $controls.FeaturePage.Visibility = 'Collapsed'
    $controls.HomePage.Visibility = 'Collapsed'
    $controls.SettingsPage.Visibility = 'Visible'
    $controls.SavePage.Visibility = 'Collapsed'
    $controls.SettingsNav.IsChecked = $true
}
function Show-Home {
    $controls.ServerPage.Visibility = 'Collapsed'
    $controls.FeaturePage.Visibility = 'Collapsed'
    $controls.HomePage.Visibility = 'Visible'
    $controls.SettingsPage.Visibility = 'Collapsed'
    $controls.SavePage.Visibility = 'Collapsed'
    $controls.HomeNav.IsChecked = $true
}
function Show-Saves {
    $controls.ServerPage.Visibility = 'Collapsed'
    $controls.FeaturePage.Visibility = 'Collapsed'
    $controls.HomePage.Visibility = 'Collapsed'
    $controls.SettingsPage.Visibility = 'Collapsed'
    $controls.SavePage.Visibility = 'Visible'
    $controls.SaveNav.IsChecked = $true
}
function Show-Features {
    $controls.ServerPage.Visibility = 'Collapsed'
    $controls.HomePage.Visibility = 'Collapsed'
    $controls.SettingsPage.Visibility = 'Collapsed'
    $controls.SavePage.Visibility = 'Collapsed'
    $controls.FeaturePage.Visibility = 'Visible'
    $controls.FeatureNav.IsChecked = $true
}
function Show-Server {
    foreach ($name in @('HomePage','SettingsPage','SavePage','FeaturePage')) { $controls[$name].Visibility = 'Collapsed' }
    $controls.ServerPage.Visibility = 'Visible'
    $controls.ServerNav.IsChecked = $true
}
function Show-Error([string]$message) {
    $controls.StatusText.Text = $message
    [void][Windows.MessageBox]::Show($window, $message, 'The Hunt', 'OK', 'Warning')
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
        $controls.StatusText.Text = '已加载本机配置，可以启动游戏。'
    } catch { $controls.StatusText.Text = '配置无法读取，请重新填写；原存档仍保存在 data 目录。' }
}
function Set-BusyControls {
    $busy = ($null -ne $script:worker) -or ($null -ne $script:saveTask) -or ($null -ne $script:adminTask) -or ($null -ne $script:conversionTask)
    foreach ($name in @('Launch','SaveSettings','SettingsNav','RefreshSaves','SaveFeatures','RecommendFeatures','FeatureItems','AdminFields','AdminRefresh','AdminCreate','AdminVerify')) {
        $controls[$name].IsEnabled = -not $busy
    }
    $controls.ImportSaves.IsEnabled = -not $busy -and $null -ne $script:saveInspection
    Update-ConversionButtons $busy
}
function Start-SaveTransfer([bool]$apply) {
    if ($script:worker -or $script:saveTask -or $script:adminTask -or $script:conversionTask -or (Get-Process bb-probe -ErrorAction SilentlyContinue)) {
        throw '请先正常退出游戏，并等待当前操作完成。'
    }
    $exe = $controls.InstallPath.Text
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
        throw '请先在「游戏与联机」选择本机普通版 Bloodborne.exe。'
    }
    $python = Join-Path $taskRoot 'runtime\bin\python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw '请使用完整安装包中的启动器。' }
    $install = [IO.Path]::GetDirectoryName($exe)
    $expected = if ($script:saveInspection) { $script:saveInspection.source } else { $null }
    $script:saveInspection = $null
    if (Test-Path -LiteralPath $saveResultPath) { Remove-Item -LiteralPath $saveResultPath }
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $python
    $info.Arguments = '"' + (Join-Path $taskRoot 'save_transfer.py') + '" --install "' + $install + '" --result "' + $saveResultPath + '"'
    if ($apply) {
        if (-not $expected) { throw '请先读取普通版存档信息。' }
        $info.Arguments += ' --apply --expected-source "' + $expected + '"'
    }
    $info.WorkingDirectory = $taskRoot
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.WindowStyle = 'Hidden'
    $info.EnvironmentVariables['PYTHONHOME'] = Join-Path $taskRoot 'runtime'
    $info.EnvironmentVariables['PYTHONUTF8'] = '1'
    $info.EnvironmentVariables['PATH'] = (Join-Path $taskRoot 'runtime\bin') + ';' + $env:PATH
    $script:saveTaskApply = $apply
    $script:saveTask = [Diagnostics.Process]::Start($info)
    $controls.SaveSummary.Text = if ($apply) { '正在校验并导入存档，请保持游戏关闭…' } else { '正在读取存档位置与文件信息…' }
    $controls.StatusText.Text = $controls.SaveSummary.Text
    Set-BusyControls
}
$controls.HomeNav.Add_Click({ Show-Home })
$controls.SettingsNav.Add_Click({ Show-Settings })
$controls.FeatureNav.Add_Click({ Show-Features })
$controls.ServerNav.Add_Click({ Show-Server })
$controls.SaveNav.Add_Click({
    Show-Saves
    if (-not $script:worker -and -not $script:saveTask -and -not $script:conversionTask) {
        try { Start-SaveTransfer $false }
        catch { $controls.SaveSummary.Text = $_.Exception.Message; Set-BusyControls }
    }
})
$controls.RefreshSaves.Add_Click({
    try { Start-SaveTransfer $false }
    catch { Show-Error $_.Exception.Message; Set-BusyControls }
})
$controls.ImportSaves.Add_Click({
    try {
        if (-not $script:saveInspection) { throw '请先读取普通版存档信息。' }
        $message = "将普通版的全部角色进度导入无缝版。`n`n来源：`n" + $script:saveInspection.source +
                   "`n`n目标：`n" + $script:saveInspection.destination +
                   "`n`n现有无缝存档及已完成的联机辅助记录会一起备份、隔离，之后整体替换，不合并进度。未完成的世界恢复或共享进度会阻止导入。普通版存档保持原样。`n`n继续导入？"
        if ([Windows.MessageBox]::Show($window, $message, '从普通版导入存档', 'YesNo', 'Question', 'No') -eq 'Yes') {
            Start-SaveTransfer $true
        }
    } catch { Show-Error $_.Exception.Message; Set-BusyControls }
})
$controls.OpenSaveBackups.Add_Click({
    $path = Join-Path $taskRoot 'save-backups'
    if (Test-Path -LiteralPath $path) {
        [void][Diagnostics.Process]::Start('explorer.exe', ('"' + $path + '"'))
    } else {
        $controls.SaveSummary.Text = '还没有导入备份。首次替换现有无缝存档时会自动创建。'
    }
})
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
$controls.CheckUpdates.Add_Click({
    try {
        $library = Join-Path $taskRoot 'HuntUpdates.dll'
        if (-not (Test-Path -LiteralPath $library)) { throw '请使用完整更新包；缺少更新模块 HuntUpdates.dll。' }
        [void][Reflection.Assembly]::LoadFrom($library)
        $build = [IO.File]::ReadAllText((Join-Path $taskRoot 'BUILD-INFO.json')) | ConvertFrom-Json
        $canInstall = [Func[bool]] { return -not ($script:worker -or $script:saveTask -or $script:adminTask -or $script:conversionTask -or (Get-Process bb-probe -ErrorAction SilentlyContinue)) }
        [HuntUpdates]::Show($window, $taskRoot, 'TheHuntLauncher', [string]$build.version, $canInstall)
    } catch { Show-Error $_.Exception.Message }
})
$controls.Launch.Add_Click({
    if ($script:featuresDirty) {
        Show-Features
        Show-Error '请先保存功能页中的变更，再启动游戏。'
        return
    }
    try {
        if ($script:saveTask -or $script:adminTask -or $script:conversionTask) { throw '请等待存档或服务器操作完成。' }
        if (Test-Path -LiteralPath (Join-Path $taskRoot '.save-conversion.lock')) { throw '转换测试尚未结束，请先回到存档页处理测试状态。' }
        if (Test-Path -LiteralPath (Join-Path $taskRoot '.save-import.lock')) {
            throw '存档导入正在进行或上次未完整结束，请先检查存档备份。'
        }
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
        Set-BusyControls
        $controls.Launch.Content = '游戏运行中'
        $controls.KitStatus.Text = '正在等待模组加载…'
        Show-Home
    } catch { Show-Settings; Show-Error $_.Exception.Message }
})
$timer = New-Object Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromSeconds(1)
$timer.Add_Tick({
    Update-AdminTask
    Update-ConversionTask
    if ($script:saveTask -and $script:saveTask.HasExited) {
        try {
            if (-not (Test-Path -LiteralPath $saveResultPath)) { throw '存档工具未返回结果，请重新读取存档信息。' }
            $result = [IO.File]::ReadAllText($saveResultPath) | ConvertFrom-Json
            if ($result.source) { $controls.SourceSavePath.Text = $result.source }
            if ($result.destination) { $controls.DestinationSavePath.Text = $result.destination }
            if ($script:saveTask.ExitCode -ne 0 -or -not $result.ok) {
                if ($result.error) { throw $result.error }
                throw '存档操作失败，请检查文件权限后重新读取。'
            }
            if ($script:saveTaskApply) {
                $controls.SaveSummary.Text = '导入完成。无缝版现在使用普通版的这份进度。'
                if ($result.backup) { $controls.SaveSummary.Text += "`n原无缝存档备份：" + $result.backup }
                $controls.StatusText.Text = '存档已导入，可以启动游戏。'
            } else {
                $script:saveInspection = $result
                $mb = [math]::Round([double]$result.total_bytes / 1MB, 1)
                $controls.SaveSummary.Text = ('共 {0} 个文件 · {1} MB · 最近保存：{2}' -f $result.file_count,$mb,$result.latest_write)
                $controls.StatusText.Text = '已读取存档信息。导入会替换无缝版的全部角色进度。'
            }
        } catch {
            $controls.SaveSummary.Text = $_.Exception.Message
            $controls.StatusText.Text = '存档操作未完成，请查看「存档」页面。'
        } finally {
            $script:saveTask.Dispose()
            $script:saveTask = $null
            Set-BusyControls
        }
    }
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
            Set-BusyControls
            $controls.Launch.Content = '启动游戏  →'
        }
    } catch { } # A partially-written status file is retried on the next tick.
})
$window.Add_Closing({
    if ($script:conversionTask -and -not $script:conversionTask.HasExited) { $_.Cancel = $true; $controls.StatusText.Text = '请先正常退出测试游戏，并等待转换检查完成。' }
    if ($script:adminTask) { $_.Cancel = $true; $controls.StatusText.Text = '请等待服务器管理操作完成后关闭。' }
    if ($script:saveTask -and -not $script:saveTask.HasExited) {
        $_.Cancel = $true
        $controls.StatusText.Text = '请等待存档操作完成，再关闭启动器。'
    }
    if ($script:worker -and -not $script:worker.HasExited) {
        $_.Cancel = $true
        $controls.StatusText.Text = '请先从游戏菜单正常退出，再关闭启动器。'
    }
})
Show-Home
. (Join-Path $taskRoot 'Features.ps1')
. (Join-Path $taskRoot 'Server.ps1')
. (Join-Path $taskRoot 'Conversion.ps1')
$controls.ImportSaves.IsEnabled = $false
$controls.DestinationSavePath.Text = Join-Path $taskRoot 'data\user\savedata'
$iconPath = Join-Path $taskRoot 'TheHunt.ico'
if (-not (Test-Path -LiteralPath $iconPath)) { $iconPath = Join-Path $taskRoot '..\.build\TheHunt.ico' }
if (Test-Path -LiteralPath $iconPath) { $window.Icon = New-Object Windows.Media.Imaging.BitmapImage([Uri][IO.Path]::GetFullPath($iconPath)) }
if ($Validate) {
    foreach ($name in $controls.Keys) { if (-not $controls[$name]) { throw "Missing UI element: $name" } }
    'WPF controls loaded successfully.'
    exit 0
}
if ($Preview) {
    # Render our own view for layout QA; does not start a game or read other apps.
    if ($PreviewSettings) { Show-Settings }
    if ($PreviewSaves) { Show-Saves }
    if ($PreviewFeatures) { Show-Features; $script:featureExpanders[0].IsExpanded = $true }
    if ($PreviewServer) { Show-Server }
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
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class HuntWindow {
    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr window, int attribute, ref int value, int size);
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    public static extern int SetCurrentProcessExplicitAppUserModelID(string id);
    public static void Apply(IntPtr window) {
        int dark = 1;
        if (DwmSetWindowAttribute(window, 20, ref dark, 4) != 0)
            DwmSetWindowAttribute(window, 19, ref dark, 4);
        // Windows 11 supports explicit caption/text colors; Windows 10 keeps dark mode.
        int caption = 0x001A1817;
        int text = 0x00CED5D7;
        DwmSetWindowAttribute(window, 35, ref caption, 4);
        DwmSetWindowAttribute(window, 36, ref text, 4);
    }
}
'@
[void][HuntWindow]::SetCurrentProcessExplicitAppUserModelID('iytq.TheHunt')
$window.Add_SourceInitialized({
    $handle = (New-Object Windows.Interop.WindowInteropHelper($window)).Handle
    [HuntWindow]::Apply($handle)
})
$hasher = [Security.Cryptography.SHA256]::Create()
try { $mutexName = 'Local\BBSCLauncher-' + [BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($taskRoot))) }
finally { $hasher.Dispose() }
$mutex = New-Object Threading.Mutex($false, $mutexName)
if (-not $mutex.WaitOne(0)) {
    [void][Windows.MessageBox]::Show('这个目录的 The Hunt 已经打开，请从任务栏切换。', 'The Hunt')
    exit 0
}
try {
    $library = Join-Path $taskRoot 'HuntUpdates.dll'
    [void][Reflection.Assembly]::LoadFrom($library)
    $build = [IO.File]::ReadAllText((Join-Path $taskRoot 'BUILD-INFO.json')) | ConvertFrom-Json
    [HuntUpdates]::WatchStartup($window, $controls.CheckUpdates, $taskRoot, 'TheHuntLauncher', [string]$build.version)
} catch { $controls.CheckUpdates.ToolTip = '自动检查暂不可用，仍可点击手动检查更新。' }
try { $timer.Start(); [void]$window.ShowDialog() }
finally { $timer.Stop(); $mutex.ReleaseMutex(); $mutex.Dispose() }
