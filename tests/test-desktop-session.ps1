param([Parameter(Mandatory=$true)][string]$PackageRoot,[Parameter(Mandatory=$true)][string]$OutputDirectory,[string]$GameDirectory)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName PresentationFramework,PresentationCore,WindowsBase,System.Xaml
[void][IO.Directory]::CreateDirectory($OutputDirectory)
$OutputDirectory=[IO.Path]::GetFullPath($OutputDirectory)
$fixture=Join-Path $OutputDirectory ('fixture-'+[Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($fixture)
$utf8=New-Object Text.UTF8Encoding $false
$flags=[Reflection.BindingFlags]'Instance,NonPublic'
function Assert($v,[string]$m) { if(-not $v) { throw $m } }
function Await-Task($task) {
    $deadline=[DateTime]::UtcNow.AddSeconds(30)
    while(-not $task.IsCompleted) {
        if($script:noticeProbe) { $script:observedNotices=[Math]::Max($script:observedNotices,$script:noticeProbe.PendingCount) }
        if([DateTime]::UtcNow -gt $deadline) { throw 'Async desktop test timeout' }
        $frame=New-Object Windows.Threading.DispatcherFrame
        $tick=New-Object Windows.Threading.DispatcherTimer
        $tick.Interval=[TimeSpan]::FromMilliseconds(20)
        $tick.Tag=$frame
        $tick.Add_Tick({ $this.Stop(); $this.Tag.Continue=$false })
        $tick.Start(); [Windows.Threading.Dispatcher]::PushFrame($frame)
    }
    $task.GetAwaiter().GetResult()
}
try {
    Copy-Item -LiteralPath (Join-Path $PackageRoot 'runtime') -Destination $fixture -Recurse
    Copy-Item -LiteralPath (Join-Path $PackageRoot 'assets') -Destination $fixture -Recurse
    foreach($name in @('Launcher.xaml','feature-catalog.json','trophy-catalog.json','trophy_view.py','trophy_sync.py','save_transfer.py','home_save_proof.py')) {
        if(Test-Path (Join-Path $PackageRoot $name)) { Copy-Item -LiteralPath (Join-Path $PackageRoot $name) -Destination $fixture }
    }
    # Reuse the complete backend modules for lazy imports, without copying user data.
    foreach($file in Get-ChildItem -LiteralPath $PackageRoot -Filter '*.py') { Copy-Item -LiteralPath $file.FullName -Destination $fixture }
    [void][IO.Directory]::CreateDirectory((Join-Path $fixture 'engine\launcher'))
    Copy-Item -LiteralPath (Join-Path $PackageRoot 'engine\launcher\bbport_trophy_icons.py') -Destination (Join-Path $fixture 'engine\launcher')
    [IO.File]::WriteAllText((Join-Path $fixture 'Bloodborne.exe'),'fixture',$utf8)
    [IO.File]::WriteAllText((Join-Path $fixture 'bbport.ini'),"output_res=2560x1440`nupscaler=dlss",$utf8)
    if(-not $GameDirectory) { $GameDirectory=Join-Path $fixture 'game'; [void][IO.Directory]::CreateDirectory($GameDirectory); [IO.File]::WriteAllText((Join-Path $GameDirectory 'eboot.bin'),'fixture',$utf8) }
    $profile=@{ install=$fixture; game=$GameDirectory; graphics_source=(Join-Path $fixture 'bbport.ini'); network=@{shadnet='127.0.0.1:31413';webapi='http://127.0.0.1:31415';community='http://127.0.0.1:20443';relay='127.0.0.1:20444'} }
    [IO.File]::WriteAllText((Join-Path $fixture 'profile.json'),($profile|ConvertTo-Json -Depth 4),$utf8)
    [void][Reflection.Assembly]::LoadFrom((Join-Path $PackageRoot 'HuntUpdates.dll'))
    [void][Reflection.Assembly]::LoadFrom((Join-Path $PackageRoot 'BBSC-Launcher.exe'))
    [DesktopCredentials]::Save((Join-Path $fixture '.private\account.clixml'),'fixture-hunter','fixture-password')
    [void][IO.Directory]::CreateDirectory((Join-Path $fixture 'data\user'))
    [IO.File]::WriteAllText((Join-Path $fixture 'data\user\trophies.log'),"7 1700000000`n14 1700000001`n21 1700000002`n",$utf8)
    [IO.File]::WriteAllText((Join-Path $fixture 'client.py'),@'
from pathlib import Path
import json, os, sys, time
from trophy_notices import TrophyNotices
root=Path(__file__).resolve().parent
notices=TrophyNotices(root,os.environ['THEHUNT_UI_SESSION'])
assert os.environ['BBSC_ACCOUNT']=='fixture-hunter'
assert os.environ['BBSC_PASSWORD']=='fixture-password'
time.sleep(.15)
with (root/'data/user/trophies.log').open('a') as f: f.write('36 1700000100\n')
notices.poll()
time.sleep(2.2)
(root/'launcher-state.json').write_text(json.dumps({'phase':'error','message':'fixture recovery error'}))
(root/'session-proof.json').write_text(json.dumps({'authenticated':True,'exit':9}))
print('fixture worker completed')
sys.exit(9)
'@,$utf8)
    [Threading.SynchronizationContext]::SetSynchronizationContext((New-Object Windows.Threading.DispatcherSynchronizationContext))
    $desktop=New-Object DesktopWindow($fixture)
    Assert (Await-Task ($desktop.RefreshTrophies($true))) 'Initial real provider refresh failed'
    Assert ($desktop.Window.FindName('TrophySummary').Text -like '*3 / 40*') 'Real parser was not connected to UI'
    $desktop.Validate((Join-Path $OutputDirectory 'launcher-home.png'),'Home')
    $script:noticeProbe=$desktop.GetType().GetField('toast',$flags).GetValue($desktop)
    $script:observedNotices=0
    $pollTimer=$desktop.GetType().GetField('timer',$flags).GetValue($desktop); $pollTimer.Start()
    $task=$desktop.GetType().GetMethod('Launch',$flags).Invoke($desktop,@())
    Await-Task $task
    $pollTimer.Stop()
    Assert ($script:observedNotices -eq 1) 'Native live polling did not enqueue exactly one new-trophy overlay'
    Assert (-not $desktop.Busy) 'Game operation remained busy after failed worker'
    Assert ($desktop.Window.FindName('TrophySummary').Text -like '*4 / 40*') 'Failed-exit trophy refresh missing'
    Assert ($desktop.Window.FindName('TrophySession').Text -like '*1*') 'Session delta missing'
    Assert ($desktop.Window.FindName('StatusText').Text -eq 'fixture recovery error') 'Trophy refresh hid recovery error'
    Assert (Test-Path (Join-Path $fixture 'session-proof.json')) 'Worker did not receive credential environment'
    $events=Get-Content -LiteralPath (Join-Path $fixture 'data\user\ui-trophy-events.json') -Raw | ConvertFrom-Json
    Assert ($events.events.Count -eq 1 -and $events.events[0].id -eq 36) 'Actual backend notice bridge reported imported history or missed the new unlock'
    $desktop.Validate((Join-Path $OutputDirectory 'launcher-trophies.png'),'Trophy')
    $desktop.Window.FindName('TrophyEarned').IsChecked=$true
    $desktop.Window.FindName('TrophyEarned').RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Primitives.ButtonBase]::ClickEvent)))
    Assert ($desktop.Window.FindName('TrophyItems').Children.Count -eq 6) 'Earned trophy filter/grouping incorrect'
    $desktop.Validate((Join-Path $OutputDirectory 'launcher-trophies-earned.png'),'Trophy')
    $desktop.Window.FindName('TrophyLocked').IsChecked=$true
    $desktop.Window.FindName('TrophyLocked').RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Primitives.ButtonBase]::ClickEvent)))
    Assert ($desktop.Window.FindName('TrophyItems').Children.Count -eq 38) 'Locked trophy filter/grouping incorrect'
    $desktop.Window.FindName('TrophyAll').IsChecked=$true
    foreach($page in @('Settings','Feature','Save','Server')) { $desktop.Validate((Join-Path $OutputDirectory ('launcher-'+$page.ToLower()+'.png')),$page) }
    Assert ($null -eq $desktop.Window.FindName('FontStylePicker')) 'Font picker remains in packaged UI'
    $icon=$desktop.GetType().GetField('images',$flags).GetValue($desktop)[7]
    $desktop.GetType().GetField('toast',$flags).GetValue($desktop).Validate((Join-Path $OutputDirectory 'trophy-toast.png'),$icon)
    [IO.File]::WriteAllText((Join-Path $fixture 'data\user\trophies.log'),'14 17',$utf8)
    Assert (-not (Await-Task ($desktop.RefreshTrophies($true)))) 'Partial append was accepted'
    Assert ($desktop.Window.FindName('TrophySummary').Text -like '*4 / 40*' -and $desktop.Window.FindName('TrophyNotice').Text -like '*上次成功*') 'Read error did not retain/label previous display'
    'PASS: actual packaged Python provider, native session launch/auth environment, nonzero worker exit, trophy delta, recovery-error preservation and stale display.'
} finally {
    $full=[IO.Path]::GetFullPath($fixture)
    if(-not $full.StartsWith($OutputDirectory+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase) -or (Split-Path -Leaf $full) -notlike 'fixture-*') { throw 'Unsafe session fixture cleanup path' }
    Remove-Item -LiteralPath $full -Recurse -Force
}
