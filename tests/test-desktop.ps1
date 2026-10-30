param([Parameter(Mandatory=$true)][string]$AssemblyPath,[string]$FixtureDirectory)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot
# FontCache can retain private fonts beyond process exit. Use the immutable
# parent assets directly, keeping font files out of the disposable fixture.
# The parent owns cleanup.
if(-not $FixtureDirectory) {
    $fixture = Join-Path $repo ('the-hunt-desktop-' + [Guid]::NewGuid().ToString('N'))
    [void][IO.Directory]::CreateDirectory($fixture)
    $testExit = 1
    try {
        & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -STA -ExecutionPolicy Bypass -File $PSCommandPath -AssemblyPath ([IO.Path]::GetFullPath($AssemblyPath)) -FixtureDirectory $fixture
        $testExit = $LASTEXITCODE
    } finally {
        $full=[IO.Path]::GetFullPath($fixture); $base=[IO.Path]::GetFullPath($repo).TrimEnd('\')+'\'
        if(-not $full.StartsWith($base,[StringComparison]::OrdinalIgnoreCase) -or (Split-Path -Leaf $full) -notlike 'the-hunt-desktop-*') { throw 'Unsafe fixture cleanup path' }
        try { Remove-Item -LiteralPath $full -Recurse -Force } catch { if($testExit -eq 0) { throw }; Write-Warning "Fixture cleanup also failed: $_" }
    }
    exit $testExit
}
Add-Type -AssemblyName PresentationFramework,PresentationCore,WindowsBase,System.Xaml,System.Web.Extensions
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class ToastStyleCheck {
    [DllImport("user32.dll",EntryPoint="GetWindowLongW")] public static extern int Style(IntPtr handle,int index);
}
'@
[void][Reflection.Assembly]::LoadFrom((Join-Path (Split-Path $AssemblyPath) 'HuntUpdates.dll'))
[void][Reflection.Assembly]::LoadFrom($AssemblyPath)
$fixture = $FixtureDirectory
$utf8 = New-Object Text.UTF8Encoding $false
$flags = [Reflection.BindingFlags]'Instance,NonPublic'
function Invoke-Private($object, [string]$name, [object[]]$arguments=@()) { $raw = New-Object object[] $arguments.Length; for($i=0;$i -lt $arguments.Length;$i++) { $raw[$i]=$arguments[$i].PSObject.BaseObject }; return $object.GetType().GetMethod($name,$flags).Invoke($object,$raw) }
function Assert($value,[string]$message) { if(-not $value) { throw $message } }
try {
    foreach($name in @('Launcher.xaml','feature-catalog.json','trophy-catalog.json')) { Copy-Item -LiteralPath (Join-Path $repo ('launcher\'+$name)) -Destination $fixture }
    Assert ([HuntTypography]::Assets($fixture) -eq (Join-Path $repo 'assets')) 'Fixture must use the immutable source assets'
    $credentials = Join-Path $fixture '.private\account.clixml'
    [void][IO.Directory]::CreateDirectory((Split-Path $credentials))
    $user = '猎人_x0041_ & < >'
    $password = 'p ä 雪 " _x0042_'
    (New-Object Management.Automation.PSCredential($user,(ConvertTo-SecureString $password -AsPlainText -Force))) | Export-Clixml -LiteralPath $credentials
    $old = [DesktopCredentials]::Read($credentials)
    Assert ($old[0] -ceq $user -and $old[1] -ceq $password) 'Existing PowerShell credential read failed'
    [DesktopCredentials]::Save($credentials,$user,$password)
    $roundtrip=Import-Clixml -LiteralPath $credentials
    Assert ($roundtrip.UserName -ceq $user -and $roundtrip.GetNetworkCredential().Password -ceq $password) 'PowerShell credential compatibility failed'
    Assert ((Get-Acl -LiteralPath (Split-Path $credentials)).AreAccessRulesProtected) 'Private ACL inheritance was not protected'
    $desktop = New-Object DesktopWindow($fixture)
    foreach($page in @('Home','Settings','Feature','Save','Server','Trophy')) { $desktop.Validate($null,$page) }
    $window=$desktop.Window
    $privateHash=(Get-FileHash -LiteralPath $credentials -Algorithm SHA256).Hash
    $window.FindName('PublicAccount').Text='Public_fixture'
    $window.FindName('PublicPassword').Password='public-fixture-password'
    Invoke-Private $desktop 'SavePublic'
    $publicPath=Join-Path $fixture '.private\public-online.clixml'
    $public=[DesktopCredentials]::Read($publicPath)
    Assert ($public[0] -ceq 'Public_fixture' -and $public[1] -ceq 'public-fixture-password') 'Public credential roundtrip failed'
    Assert ((Get-FileHash -LiteralPath $credentials -Algorithm SHA256).Hash -ceq $privateHash) 'Public login overwrote private credentials'
    Assert ($window.FindName('PublicPassword').Password.Length -eq 0) 'Public password remained visible in the form'
    $window.FindName('PublicAccount').Text=''
    Invoke-Private $desktop 'LoadProfile'
    Assert ($window.FindName('PublicAccount').Text -ceq 'Public_fixture') 'Saved public account was lost before the first full profile save'
    $reused=Invoke-Private $desktop 'PublicCredential'
    Assert ($reused[1] -ceq 'public-fixture-password') 'Saved public credentials were not reused without typing'
    $window.FindName('PublicAccount').Text='Other_fixture'; $rejected=$false
    try { Invoke-Private $desktop 'PublicCredential' } catch { $rejected=$true }
    Assert $rejected 'Changed public account reused a different account password'
    $window.FindName('PublicAccount').Text='Public_fixture'
    Assert ($window.Title -eq 'The Hunt') 'Extra title attribution remains'
    Assert ($null -eq $window.FindName('FontStylePicker')) 'Font selection option remains'
    Assert ($window.FontFamily.Source -eq ([HuntTypography]::Body($fixture)).Source) 'Default font is not fixed Sans'
    Assert ([Windows.Documents.TextElement]::GetFontFamily($window.FindName('HomePage')).Source -eq ([HuntTypography]::Home($fixture)).Source) 'Home page is not fixed WenKai'
    Assert ($window.FindName('HomeTrophies').FontFamily.Source -eq ([HuntTypography]::Home($fixture)).Source) 'Home button did not inherit WenKai'
    Assert ($window.FindName('SaveSettings').FontFamily.Source -eq ([HuntTypography]::Body($fixture)).Source) 'Settings did not inherit Sans'
    $toast=$desktop.GetType().GetField('toast',$flags).GetValue($desktop)
    $toast.Validate((Join-Path $fixture 'toast.png'))
    Assert ($toast.Window.Width -eq 540 -and $toast.Window.Height -eq 180) 'Readable trophy overlay dimensions regressed'
    $toast.Validate((Join-Path $fixture 'toast-720p.png'),$null,1280,720)
    Assert ($toast.Window.Width -eq 270 -and $toast.Window.Height -eq 90) '720p trophy overlay did not scale with the client area'
    Assert ($toast.Window.Content -is [Windows.Controls.Viewbox]) 'Trophy card does not scale its typography and icon together'
    $toast.Validate((Join-Path $fixture 'toast-1080p.png'),$null,1920,1080)
    Assert ($toast.Window.Width -eq 405 -and $toast.Window.Height -eq 135) '1080p trophy overlay sizing regressed'
    $toast.Validate((Join-Path $fixture 'toast-high-dpi.png'),$null,1024,576)
    Assert ($toast.Window.Width -eq 216 -and $toast.Window.Height -eq 72) 'DPI-converted trophy viewport sizing regressed'
    $toast.Validate((Join-Path $fixture 'toast.png'))
    Assert (-not $toast.Window.ShowActivated -and -not $toast.Window.ShowInTaskbar -and -not $toast.Window.IsHitTestVisible) 'Trophy toast can steal focus or input'
    $handle=(New-Object Windows.Interop.WindowInteropHelper($toast.Window)).EnsureHandle()
    $style=[ToastStyleCheck]::Style($handle,-20)
    Assert (($style -band 0x080000A0) -eq 0x080000A0) 'Native no-activate/click-through/tool-window flags missing'
    $window.WindowStartupLocation='Manual'; $window.Left=-10000; $window.Top=-10000
    $window.ShowInTaskbar=$false; $window.ShowActivated=$false; $window.Show()
    $controls=$desktop.GetType().GetField('featureControls',$flags).GetValue($desktop)
    Assert ($controls.Count -eq 31) 'Feature catalogue incomplete'
    Assert (-not $controls['hooks'].IsEnabled -and -not $controls['shared_progress'].IsChecked) 'Feature recommendations/required gates changed'
    $controls['scale_boss_hp'].Text='60'; $controls['invasions'].IsChecked=$false
    Invoke-Private $desktop 'SaveFeatures'
    $saved=Get-Content -LiteralPath (Join-Path $fixture 'features.json') -Raw | ConvertFrom-Json
    Assert ($saved.values.scale_boss_hp -eq 60 -and -not $saved.values.invasions) 'Native feature persistence failed'
    $before=[IO.File]::ReadAllText((Join-Path $fixture 'features.json'))
    $controls['scale_boss_hp'].Text='301'; $rejected=$false
    try { Invoke-Private $desktop 'SaveFeatures' } catch { $rejected=$true }
    Assert ($rejected -and [IO.File]::ReadAllText((Join-Path $fixture 'features.json')) -ceq $before) 'Invalid feature value was written'
    $desktop.GetType().GetField('operation',$flags).SetValue($desktop,$true)
    Invoke-Private $desktop 'SetBusy'
    foreach($name in @('Launch','SaveSettings','RefreshSaves','ImportSaves','UseLocalServer','SaveFeatures','AdminCreate','ConversionStartReplace','PublicVerify','PublicForget')) { Assert (-not $window.FindName($name).IsEnabled) ('Busy gate failed: '+$name) }
    Assert (-not $desktop.CanUpdate()) 'Updates permitted during operation'
    $closing=New-Object ComponentModel.CancelEventArgs
    # Closing is tested through the actual event while a task is active.
    $window.Close()
    Assert ($window.FindName('StatusText').Text -like '*等待*') 'Close did not explain active operation'
    $desktop.GetType().GetField('operation',$flags).SetValue($desktop,$false)
    Invoke-Private $desktop 'SetBusy'
    [void][IO.Directory]::CreateDirectory((Join-Path $fixture 'server'))
    [IO.File]::WriteAllText((Join-Path $fixture 'server\server.json'),'{"address":"192.0.2.7"}',$utf8)
    Invoke-Private $desktop 'UseLocalServer'
    Assert ($window.FindName('Shadnet').Text -eq '192.0.2.7:31413' -and $window.FindName('Relay').Text -eq '192.0.2.7:20444') 'Local addresses incorrect'
    Assert (-not (Test-Path -LiteralPath (Join-Path $fixture 'profile.json'))) 'Local address shortcut saved without user action'
    $backup=Join-Path $fixture 'old-save'; [void][IO.Directory]::CreateDirectory($backup)
    $job=New-Object 'Collections.Generic.Dictionary[string,object]'; $job['state']='applied'; $job['next']=$null; $job['ordinary_backup']=$backup
    Invoke-Private $desktop 'ShowConversion' @($job); Invoke-Private $desktop 'SetBusy'
    Assert ($window.FindName('ConversionOpenOrdinaryBackup').IsEnabled) 'Conversion backup browse lost'
    $parser=New-Object Web.Script.Serialization.JavaScriptSerializer
    $session=New-Object TrophySession
    $session.Accept($parser.DeserializeObject('{"trophies":[{"id":14,"unlocked":100}]}'))
    Assert ($session.Added.Length -eq 0) 'History was reported as session unlocks'
    $session.Begin($true); $session.Exited(); $session.Failed('test unavailable')
    Assert ($session.Snapshot -ne $null -and $session.Warning) 'Failed refresh erased previous snapshot'
    $session.Accept($parser.DeserializeObject('{"trophies":[{"id":14,"unlocked":100},{"id":36,"unlocked":200}]}'))
    Assert ($session.Added.Length -eq 1 -and $session.Added[0] -eq 36) 'Exit delta incorrect'
    $session.Accept($parser.DeserializeObject('{"trophies":[{"id":14,"unlocked":100}]}'))
    Assert ([TrophySession]::Earned($session.Snapshot).Count -eq 1) 'Older branch was merged into cache'
    Assert ($session.Added.Length -eq 0) 'Session summary retained an unlock absent from the current branch'
    $session.Begin($false); $session.Exited(); $session.Accept($parser.DeserializeObject('{"trophies":[{"id":14,"unlocked":100}]}'))
    Assert ($session.Added.Length -eq 0) 'Failed baseline produced fabricated new unlocks'
    $session.Accept($parser.DeserializeObject('{"missing":true,"trophies":[]}'))
    $session.Begin($true); $session.Exited(); $session.Accept($parser.DeserializeObject('{"missing":false,"trophies":[{"id":14,"unlocked":100}]}'))
    Assert ($session.Added.Length -eq 0) 'First-launch imported history was called a new in-game unlock'
    [void][IO.Directory]::CreateDirectory((Join-Path $fixture 'data\user'))
    [IO.File]::WriteAllText((Join-Path $fixture 'data\user\ui-trophy-events.json'),('{"session":"'+('a'*32)+'","events":[{"seq":1,"id":14,"when":100}]}'),$utf8)
    $desktop.GetType().GetField('noticeSession',$flags).SetValue($desktop,('b'*32))
    Invoke-Private $desktop 'PollTrophyNotices'
    Assert ($toast.PendingCount -eq 0) 'A previous game session replayed its notification'
    $desktop.GetType().GetField('noticeSession',$flags).SetValue($desktop,('a'*32))
    Invoke-Private $desktop 'PollTrophyNotices'; Invoke-Private $desktop 'PollTrophyNotices'
    Assert ($toast.PendingCount -eq 1) 'Trophy notification was lost or replayed on polling'
    $toast.Stop()
    $window.Close()
    'PASS: native views, credential compatibility, feature persistence, busy/close/update guards, conversion backups, local addresses, trophy session states.'
} finally {
    if($toast) { $toast.Stop() }
    if($window) { $window.Close() }
}
