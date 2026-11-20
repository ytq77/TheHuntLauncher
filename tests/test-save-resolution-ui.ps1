param(
    [Parameter(Mandatory=$true)][string]$AssemblyPath,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [string]$RuntimeRoot,
    [string]$BackupPreviewFixture,
    [string]$CandidateRoot,
    [switch]$ViewOnly
)
$ErrorActionPreference='Stop'
$repo=Split-Path $PSScriptRoot
$OutputDirectory=[IO.Path]::GetFullPath($OutputDirectory)
[void][IO.Directory]::CreateDirectory($OutputDirectory)
$fixture=Join-Path $OutputDirectory ('fixture-'+[Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($fixture)
$utf8=New-Object Text.UTF8Encoding $false
$flags=[Reflection.BindingFlags]'Instance,NonPublic'
Add-Type -AssemblyName PresentationFramework,PresentationCore,WindowsBase,System.Xaml,System.Web.Extensions
[void][Reflection.Assembly]::LoadFrom((Join-Path (Split-Path $AssemblyPath) 'HuntUpdates.dll'))
[void][Reflection.Assembly]::LoadFrom([IO.Path]::GetFullPath($AssemblyPath))
Add-Type @'
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
public static class ResolutionTestDialog {
    delegate bool Visitor(IntPtr window,IntPtr param);
    [DllImport("user32.dll")] static extern bool EnumWindows(Visitor callback,IntPtr param);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr window,StringBuilder text,int size);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window,out uint process);
    [DllImport("user32.dll")] static extern IntPtr GetDlgItem(IntPtr window,int id);
    [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr window,int message,IntPtr wparam,IntPtr lparam);
    public static bool Choose(string title,bool yes) {
        bool chosen=false;
        EnumWindows(delegate(IntPtr window,IntPtr ignored) {
            uint process; GetWindowThreadProcessId(window,out process);
            var text=new StringBuilder(256); GetWindowText(window,text,text.Capacity);
            if(process==(uint)Process.GetCurrentProcess().Id && text.ToString()==title) {
                var button=GetDlgItem(window,yes ? 6:7);
                if(button!=IntPtr.Zero) { SendMessage(button,0x00f5,IntPtr.Zero,IntPtr.Zero); chosen=true; return false; }
            }
            return true;
        },IntPtr.Zero);
        return chosen;
    }
}
'@
function Assert($value,[string]$message) { if(-not $value) { throw $message } }
function Copy-FixtureReaders([string]$destination) {
    foreach($file in Get-ChildItem -LiteralPath (Join-Path $repo 'launcher') -Filter '*.py') { Copy-Item -LiteralPath $file.FullName -Destination $destination }
    if($CandidateRoot) {
        foreach($file in Get-ChildItem -LiteralPath $CandidateRoot -Filter '*.py') { Copy-Item -LiteralPath $file.FullName -Destination $destination -Force }
        $reader='engine\launcher\bbport_character.py'
        [void][IO.Directory]::CreateDirectory((Join-Path $destination 'engine\launcher'))
        Copy-Item -LiteralPath (Join-Path $CandidateRoot $reader) -Destination (Join-Path $destination $reader)
    }
}
function Invoke-Private($object,[string]$name,[object[]]$arguments=@()) {
    $raw=New-Object object[] $arguments.Length
    for($i=0;$i -lt $arguments.Length;$i++) { $raw[$i]=$arguments[$i].PSObject.BaseObject }
    return $object.GetType().GetMethod($name,$flags).Invoke($object,$raw)
}
function Field($object,[string]$name) { return $object.GetType().GetField($name,$flags).GetValue($object) }
function Await-Task($task) {
    $deadline=[DateTime]::UtcNow.AddSeconds(60)
    while(-not $task.IsCompleted) {
        if([DateTime]::UtcNow -gt $deadline) { throw 'Resolution UI task timeout' }
        $frame=New-Object Windows.Threading.DispatcherFrame
        $tick=New-Object Windows.Threading.DispatcherTimer
        $tick.Interval=[TimeSpan]::FromMilliseconds(15); $tick.Tag=$frame
        $tick.Add_Tick({ $this.Stop(); $this.Tag.Continue=$false })
        $tick.Start(); [Windows.Threading.Dispatcher]::PushFrame($frame)
    }
    return $task.GetAwaiter().GetResult()
}
function Hash-File([string]$path) {
    $sha=[Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash([IO.File]::ReadAllBytes($path))).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose() }
}
function Save-ChildReceipt($row,[string]$label) {
    [IO.File]::WriteAllText((Join-Path $OutputDirectory ($label+'-process.json')),($row | ConvertTo-Json -Depth 6),$utf8)
}
function Invoke-FixturePython([string]$script,[string[]]$arguments,[string]$label) {
    $info=New-Object Diagnostics.ProcessStartInfo
    $info.FileName=Join-Path $fixture 'runtime\bin\python.exe'
    $info.Arguments='-B -c '+[DesktopWindow]::Quote($script)
    foreach($argument in $arguments) { $info.Arguments+=' '+[DesktopWindow]::Quote($argument) }
    $info.WorkingDirectory=$fixture; $info.UseShellExecute=$false; $info.CreateNoWindow=$true
    $info.RedirectStandardOutput=$true; $info.RedirectStandardError=$true
    $process=[Diagnostics.Process]::Start($info)
    $row=@{pid=$process.Id;executable=$info.FileName;arguments=$info.Arguments;fixture=$fixture;started_utc=[DateTime]::UtcNow.ToString('o');completed=$false}
    try {
        $held=$process.Handle
        $stdout=$process.StandardOutput.ReadToEndAsync(); $stderr=$process.StandardError.ReadToEndAsync()
        Save-ChildReceipt $row $label
        if(-not $process.WaitForExit(30000)) { throw ('Fixture child timeout: '+$label) }
        Assert ([Threading.Tasks.Task]::WaitAll([Threading.Tasks.Task[]]@($stdout,$stderr),10000)) 'Fixture output drain timeout'
        $code=$process.ExitCode
        [IO.File]::WriteAllText((Join-Path $OutputDirectory ($label+'.stdout')),$stdout.GetAwaiter().GetResult(),$utf8)
        [IO.File]::WriteAllText((Join-Path $OutputDirectory ($label+'.stderr')),$stderr.GetAwaiter().GetResult(),$utf8)
        $row.completed=$true; $row.exit_code=$code
        $row.stdout_sha256=Hash-File (Join-Path $OutputDirectory ($label+'.stdout'))
        $row.stderr_sha256=Hash-File (Join-Path $OutputDirectory ($label+'.stderr'))
        Assert ($code -is [int] -and $code -eq 0) ('Fixture child failed: '+$label)
    } finally {
        if(-not $process.HasExited) { $process.Kill(); Assert ($process.WaitForExit(10000)) 'Owned fixture child did not exit after termination' }
        $row.exit_code=$process.ExitCode; $row.ended_utc=[DateTime]::UtcNow.ToString('o')
        Save-ChildReceipt $row $label; $process.Dispose()
    }
}
function Map([string]$text) { return $script:parser.DeserializeObject($text) }
function Plan($desktop) { return Field $desktop 'resolutionPlan' }
function Choose-Apply($desktop,[string]$optionId,[bool]$yes) {
    $plan=Plan $desktop
    $option=@($plan['options'] | Where-Object { $_['id'] -eq $optionId })[0]
    Assert ($null -ne $option) ('Missing production option '+$optionId)
    $script:dialogYes=$yes; $script:dialogChosen=$false
    $dialog=New-Object Windows.Threading.DispatcherTimer
    $dialog.Interval=[TimeSpan]::FromMilliseconds(40)
    $dialog.Add_Tick({ if([ResolutionTestDialog]::Choose('确认存档选择',$script:dialogYes)) { $script:dialogChosen=$true; $this.Stop() } })
    $dialog.Start()
    try {
        $task=Invoke-Private $desktop 'ApplySaveResolution' @($plan,$option,(Field $desktop 'resolutionGeneration'))
        Await-Task $task
        Assert $script:dialogChosen 'The actual native confirmation was not exercised'
    } finally { $dialog.Stop() }
}
function Choose-Abandon($desktop,[bool]$yes) {
    $script:dialogYes=$yes; $script:dialogChosen=$false
    $dialog=New-Object Windows.Threading.DispatcherTimer
    $dialog.Interval=[TimeSpan]::FromMilliseconds(40)
    $dialog.Add_Tick({ if([ResolutionTestDialog]::Choose('完整另存当前现场并放弃上次选择',$script:dialogYes)) { $script:dialogChosen=$true; $this.Stop() } })
    $dialog.Start()
    try { Await-Task (Invoke-Private $desktop 'AbandonSaveResolution'); Assert $script:dialogChosen 'Actual abandonment confirmation not exercised' }
    finally { $dialog.Stop() }
}
function Choose-Restart($desktop,[bool]$first,[bool]$second) {
    $plan=Plan $desktop; $option=@($plan['options'] | Where-Object { $_['id'] -eq 'restart' })[0]
    Assert ($null -ne $option) 'Missing real restart fallback'
    $script:restartFirst=$first; $script:restartSecond=$second; $script:restartDialogs=0
    $dialog=New-Object Windows.Threading.DispatcherTimer; $dialog.Interval=[TimeSpan]::FromMilliseconds(40)
    $dialog.Add_Tick({
        if($script:restartDialogs -eq 0 -and [ResolutionTestDialog]::Choose('确认存档选择',$script:restartFirst)) { $script:restartDialogs=1; if(-not $script:restartFirst) { $this.Stop() } }
        elseif($script:restartDialogs -eq 1 -and [ResolutionTestDialog]::Choose('再次确认从头开始',$script:restartSecond)) { $script:restartDialogs=2; $this.Stop() }
    })
    $dialog.Start()
    try { Await-Task (Invoke-Private $desktop 'ApplySaveResolution' @($plan,$option,(Field $desktop 'resolutionGeneration'))); Assert ($script:restartDialogs -eq $(if($first){2}else{1})) 'Restart did not exercise the expected native confirmations' }
    finally { $dialog.Stop() }
}
function Choose-Resume($desktop) {
    $script:dialogChosen=$false
    $dialog=New-Object Windows.Threading.DispatcherTimer
    $dialog.Interval=[TimeSpan]::FromMilliseconds(40)
    $dialog.Add_Tick({ if([ResolutionTestDialog]::Choose('继续上次的选择',$true)) { $script:dialogChosen=$true; $this.Stop() } })
    $dialog.Start()
    try { Await-Task (Invoke-Private $desktop 'ResumeSaveResolution'); Assert $script:dialogChosen 'Actual native resume confirmation was not exercised' }
    finally { $dialog.Stop() }
}
$parser=New-Object Web.Script.Serialization.JavaScriptSerializer
[Threading.SynchronizationContext]::SetSynchronizationContext((New-Object Windows.Threading.DispatcherSynchronizationContext))
$passed=New-Object 'Collections.Generic.List[string]'
$previousAppData=$env:APPDATA
$env:APPDATA=Join-Path $fixture 'isolated-appdata'
[void][IO.Directory]::CreateDirectory($env:APPDATA)
try {
    foreach($name in @('Launcher.xaml','feature-catalog.json','trophy-catalog.json')) { Copy-Item -LiteralPath (Join-Path $repo ('launcher\'+$name)) -Destination $fixture }
    # Assets are immutable source assets, never a real installation.
    Copy-Item -LiteralPath (Join-Path $repo 'assets') -Destination (Join-Path $OutputDirectory 'assets') -Recurse
    $desktop=New-Object DesktopWindow($fixture); $window=$desktop.Window; $desktop.Page('Save')
    $view=Map '{"ok":true,"needed":true,"show_choices":true,"summary":"synthetic view state","digest":"view-only","options":[{"id":"preserve","label":"先保全原件，暂不改变","description":"会先保全","recommended":false},{"id":"accept-all","label":"保留所有角色现在的存档（推荐）","description":"所有待补联机进度作废；先保全，不合并","recommended":true},{"id":"future-action","label":"未知动作","description":"不支持","recommended":false}],"characters":[{"slot":0,"name":"Synthetic","primary":"unreadable","backup":"readable"}],"warnings":[],"pending":null,"selection":null}'
    $view['readability_note']='部分代次当前版本无法读取，不代表永久损坏。'
    $view['characters'][0]['primary_detail']='当前版本无法读取：合成格式不支持'
    $generation=Field $desktop 'resolutionGeneration'
    Invoke-Private $desktop 'ShowSaveResolution' @($view,$generation)
    $buttons=@($window.FindName('SaveResolutionOptions').Children | Where-Object { $_ -is [Windows.Controls.Button] })
    Assert ($buttons[0].Content -like '*推荐*') 'Recommended action was not first'
    Assert (-not $buttons[0].IsDefault -and -not $buttons[2].IsEnabled) 'Default destructive action or unknown action enabled'
    Assert ($window.FindName('SaveResolutionCharacters').Text -like '*主档读不出*备档可读*') 'Primary and backup readability collapsed'
    Assert ($window.FindName('SaveResolutionCharacters').Text -like '*主档说明*合成格式不支持*不代表永久损坏*') 'Generation detail or backend readability note was hidden'
    $slice2=Map '{"ok":true,"needed":true,"show_choices":true,"summary":"slice2 DTO only","digest":"slice2-view","options":[],"characters":[],"warnings":[],"pending":null,"selection":null}'
    $slice2['options']=@('reimport-ordinary','restart','restore-generation-0','restore-generation-9','choose-ordinary','restore-generation-10','restore-generation-x','future-action') | ForEach-Object { Map ('{"id":"'+$_+'","label":"'+$_+'","description":"backend bound text","recommended":false}') }
    Invoke-Private $desktop 'ShowSaveResolution' @($slice2,$generation)
    $sliceButtons=@($window.FindName('SaveResolutionOptions').Children | Where-Object { $_ -is [Windows.Controls.Button] })
    for($i=0;$i -lt 8;$i++) { Assert ($sliceButtons[$i].IsEnabled -eq ($i -lt 5) -and -not $sliceButtons[$i].IsDefault) ('Slice2 allowlist/default mismatch '+$i) }
    $ordinaryDto=Join-Path $fixture 'ordinary-dto'
    [void][IO.Directory]::CreateDirectory((Join-Path $ordinaryDto 'user\savedata'))
    $window.FindName('InstallPath').Text=Join-Path $ordinaryDto 'Bloodborne.exe'
    $req=Invoke-Private $desktop 'ResolutionRequest' @($null)
    Assert ($req['ordinary_install'] -eq $ordinaryDto) 'Resolution request did not carry explicit selected ordinary directory'
    Assert ($null -eq (Plan $desktop)) 'Changing original path retained slice2 plan'
    $generation=Field $desktop 'resolutionGeneration'
    Invoke-Private $desktop 'ShowSaveResolution' @($view,$generation)
    $passed.Add('Slice2 DTO only: explicit original directory, path invalidation, exact single-slot IDs, original-import/restart/choose allowlist, unknown disabled and no default. Not transaction coverage.')
    $desktop.Validate((Join-Path $OutputDirectory 'resolution-page.png'),'Save')
    $window.FindName('SaveResolutionPanel').BringIntoView(); $window.UpdateLayout()
    $desktop.Validate((Join-Path $OutputDirectory 'resolution-view.png'),'Save')
    $window.FindName('CancelSaveResolution').RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Primitives.ButtonBase]::ClickEvent)))
    Assert ($null -eq (Plan $desktop)) 'Cancel retained the plan'
    Invoke-Private $desktop 'ShowSaveResolution' @($view,$generation)
    Assert ($null -eq (Plan $desktop)) 'Late response resurrected a cancelled plan'
    $generation=Field $desktop 'resolutionGeneration'; Invoke-Private $desktop 'ShowSaveResolution' @($view,$generation)
    $window.FindName('InstallPath').Text='synthetic-source-changed'
    Assert ($null -eq (Plan $desktop)) 'Source change retained the plan'
    $generation=Field $desktop 'resolutionGeneration'; Invoke-Private $desktop 'ShowSaveResolution' @($view,$generation)
    $desktop.Page('Home'); $desktop.Page('Save'); Invoke-Private $desktop 'ShowSaveResolution' @($view,$generation)
    Assert ($null -eq (Plan $desktop)) 'Leaving and returning accepted the old response'
    $generation=Field $desktop 'resolutionGeneration'; Invoke-Private $desktop 'ShowSaveResolution' @($view,$generation)
    $desktop.GetType().GetField('operation',$flags).SetValue($desktop,$true); Invoke-Private $desktop 'SetBusy'
    Assert (-not $window.FindName('SaveResolutionOptions').IsEnabled -and -not $window.FindName('RefreshSaveResolution').IsEnabled) 'Single-flight controls remained enabled'
    $window.Close()
    Assert ($null -eq (Plan $desktop)) 'Close attempt retained the plan'
    $desktop.GetType().GetField('operation',$flags).SetValue($desktop,$false); Invoke-Private $desktop 'SetBusy'
    $unknown=Map '{"backups":[{"kind":"future-backup","id":"x","time":"2026-11-16T01:00:00Z","file_count":1,"total_bytes":8,"fingerprint":"x"}],"warnings":[],"pending":null}'
    Invoke-Private $desktop 'ShowSaveBackups' @($unknown); Invoke-Private $desktop 'SetBusy'
    Assert (-not $window.FindName('RestoreSaveBackup').IsEnabled) 'Unknown backup kind enabled restore'
    $specialViews=Map '{"backups":[{"kind":"resolution-before","id":"partial","time":"2026-11-16T01:00:00Z","label":"处理前的存档（含读不出的角色）","file_count":3,"total_bytes":4096,"fingerprint":"partial","characters":[{"slot":0,"name":"Partial","primary":"unreadable","backup":"readable","primary_detail":"当前版本无法读取：合成格式不支持"}],"readability_note":"部分代次无法读取，不代表永久损坏。"},{"kind":"resolution-before","id":"profile","time":"2026-11-16T02:00:00Z","label":"处理前的存档（角色列表或身份待核对）","file_count":3,"total_bytes":4096,"fingerprint":"profile","characters":[{"slot":1,"name":"Profile","primary":"readable","backup":"readable"}],"readability_note":"角色列表无法核对；可读角色不等于整套可启动。"},{"kind":"resolution-before","id":"identity","time":"2026-11-16T03:00:00Z","label":"换回前的存档（角色列表或身份待核对）","file_count":3,"total_bytes":4096,"fingerprint":"identity","characters":[{"slot":2,"name":"Identity","primary":"readable","backup":"readable","identity_conflict":true}],"readability_note":"主备两代身份不一致，不能称整套一致。"}],"warnings":[],"pending":null}'
    Invoke-Private $desktop 'ShowSaveBackups' @($specialViews); Invoke-Private $desktop 'SetBusy'
    $specialItems=@($window.FindName('SaveBackupList').Items)
    Assert ($specialItems[0].Content -like '*2026-*角色槽位 1*主档读不出*备档可读*合成格式不支持*不代表永久损坏*') 'Partial special row omitted its timestamp, per-generation detail or note'
    Assert ($specialItems[1].Content -like '*2026-*角色槽位 2*主档可读*备档可读*角色列表无法核对*' -and $specialItems[1].Content -notlike '*主档读不出*') 'Profile issue was hidden or misrepresented as an unreadable role'
    Assert ($specialItems[2].Content -like '*2026-*角色槽位 3*主档可读*备档可读*两代角色身份不一致*不能称整套一致*') 'Identity issue was hidden behind individually readable generations'
    $window.FindName('SaveBackupList').SelectedItem=$specialItems[1]
    Assert ($window.FindName('SelectedSaveBackup').Text -like '*角色列表无法核对*') 'Selected special details omitted the backend note'
    $desktop.Validate((Join-Path $OutputDirectory 'special-row-page.png'),'Save')
    $window.FindName('SelectedSaveBackup').BringIntoView(); $window.UpdateLayout()
    $desktop.Validate((Join-Path $OutputDirectory 'special-row-profile.png'),'Save')
    if($BackupPreviewFixture) {
        $fromSaves=Map ([IO.File]::ReadAllText([IO.Path]::GetFullPath($BackupPreviewFixture)))
        Invoke-Private $desktop 'ShowSaveBackups' @($fromSaves)
        $list=$window.FindName('SaveBackupList')
        $details=$window.FindName('SelectedSaveBackup')
        Assert ($details.Text -eq '角色槽位 1 · 主档与备档 · 猎人甲 · 等级 33 · 回响 106') 'Launch preview changed the release name or duplicated equal progress'
        $list.SelectedIndex=1
        Assert ($details.Text -eq '角色槽位 1 · 主档与备档 · 旧月 · 等级 16 · 回响 0') 'Pre-restore preview retained the previous character or lost zero echoes'
        $list.SelectedIndex=2
        Assert ($window.FindName('RestoreSaveBackup').Content -eq '换回处理前状态') 'Sealed backup lost its dedicated restore action'
        Assert ($details.Text -like '*主档与备档 · 封存 · 等级 41 · 回响 777*' -and $details.Text -notlike '*角色槽位 3 ·*' -and $details.Text -notlike '*当前 ·*') 'Sealed detail showed an empty slot or the active character'
        Assert ($details.Text -like '*角色槽位 2 · 主档 · 猎人甲*角色槽位 2 · 备档 · 晓*角色槽位 4 · 备档 · 晓*') 'Different generations or the backup-only slot disappeared'
        Assert ($details.Text -like '*角色槽位 7 · 主档 · 角色数据无法识别*角色槽位 7 · 备档 · 晓*' -and $details.Text -like '*角色槽位 9 · 主档 · 猎人甲 · 等级、回响未识别*') 'Occupied unreadable slots were hidden'
        $list.SelectedIndex=4
        Assert ($details.Text -eq '没有已使用的角色槽位。') 'Empty backup retained stale character lines'
        $list.SelectedIndex=0
        Assert ($details.Text -eq '角色槽位 1 · 主档与备档 · 猎人甲 · 等级 33 · 回响 106' -and $window.FindName('RestoreSaveBackup').Content -eq '恢复到这个备份') 'Switching back failed to restore the ordinary view'
        $proofPlan=$fromSaves['explicit_plan']
        Invoke-Private $desktop 'ShowSaveResolution' @($proofPlan,(Field $desktop 'resolutionGeneration'))
        $panes=$window.FindName('SaveResolutionCharacters').Text -split '当前存档中的角色'
        Assert ($panes.Count -eq 2 -and $panes[0] -like '*备份中的角色*封存 · 等级 41 · 回响 777*' -and $panes[1] -like '*当前 · 等级 24 · 回响 999*') 'Processing panel confused the backup with the current save'
        Assert ($panes[1] -like '*主档 · 猎人甲*备档 · 猎人甲*两代角色身份不一致*' -and $panes[1] -like '*主档读不出，备档可读*主档说明*') 'Processing panel lost a conflict or the native readability diagnostics'
        Assert (([regex]::Matches($panes[1],'角色槽位 8 ·')).Count -eq 1 -and $panes[1] -like '*角色槽位 8 · 主档与备档 · 猎人甲 · 等级 33 · 回响 106*') 'Playtime-only differences duplicated a visible role'
        $restore=@($proofPlan['options'] | Where-Object { $_['id'] -eq 'restore-case' })[0]
        $confirm=Invoke-Private $desktop 'ResolutionConfirmation' @($proofPlan,$restore)
        Assert ($confirm.Contains($restore['description']) -and $confirm -like '*将恢复的备份角色*封存 · 等级 41 · 回响 777*当前版本检查*当前 · 等级 24 · 回响 999*主档说明*') 'Restore confirmation lost the actual source or current diagnostics'
        $passed.Add('Selected-backup WPF text matched actual synthetic launch, pre-restore, sealed and all-empty lists in both directions.')
    }
    $noReadable=Map '{"summary":"全部角色代次当前版本无法读取，不代表永久损坏。","characters":[{"slot":0,"primary":"unreadable","backup":"unreadable"}],"readability_note":"原件只封存，不列为可换回项。"}'
    $restoreDescription=Map '{"id":"restore-all","label":"恢复所选整套备份","description":"当前版本会先完整保全，但只封存，不列为可换回项；不能保证日后恢复，不合并进度。"}'
    $confirmation=Invoke-Private $desktop 'ResolutionConfirmation' @($noReadable,$restoreDescription)
    Assert ($confirmation.Contains($restoreDescription['description']) -and $confirmation -like '*主档读不出*备档读不出*只封存*' -and $confirmation -notlike '*可在列表换回*') 'UI changed the bound backend description or added an invalid undo promise'
    $normal=Map '{"ok":true,"needed":false,"show_choices":false,"summary":"no anomaly","digest":"clean","options":[],"characters":[],"warnings":[],"pending":null,"selection":null}'
    Invoke-Private $desktop 'ShowSaveResolution' @($normal,(Field $desktop 'resolutionGeneration'))
    Assert ($window.FindName('SaveResolutionPanel').Visibility -eq [Windows.Visibility]::Collapsed) 'Normal state displayed anomaly choices'
    $pending=Map '{"ok":true,"needed":true,"show_choices":true,"summary":"interrupted","digest":null,"options":[{"id":"accept-all","label":"must not show","description":"must not show","recommended":true}],"characters":[],"warnings":[],"pending":{"can_resume":true,"can_abandon":true,"abandon_digest":"synthetic-abandon","abandon_message":"preserve current before abandon","fingerprint":"synthetic-pending","message":"continue original choice"},"selection":null}'
    Invoke-Private $desktop 'ShowSaveResolution' @($pending,(Field $desktop 'resolutionGeneration'))
    Assert ($window.FindName('SaveResolutionOptions').Children.Count -eq 0 -and $window.FindName('ResumeSaveResolution').IsEnabled) 'Pending transaction allowed a new choice or hid resume'
    Assert ($window.FindName('AbandonSaveResolution').IsEnabled) 'Pending view omitted the distinct abandonment exit'
    $passed.Add('Native WPF view-only: recommendation order, per-row timestamps, explicit primary/backup readability, generation detail, profile/identity notes, bound confirmation text, no default, unknown types, Cancel, source, navigation, Close and stale response invalidation.')
    if(-not $ViewOnly) {
        Assert ($RuntimeRoot -and (Test-Path -LiteralPath (Join-Path $RuntimeRoot 'bin\python.exe'))) 'A known packaged Python runtime is required for real chain checks'
        Copy-Item -LiteralPath $RuntimeRoot -Destination (Join-Path $fixture 'runtime') -Recurse
        Copy-FixtureReaders $fixture
        $setup=@'
import json, sys
from pathlib import Path
root, repo = map(Path, sys.argv[1:])
sys.path[:0] = [str(root), str(repo/'tests')]
from synthetic_world_fixture import fixture_pair
import save_transfer as t
raw, _ = fixture_pair()
if (root/'engine/launcher/bbport_character.py').is_file():
    from check_backup_characters import character
    raw = character('猎人甲', 33, 106)
role = root/'data/user/savedata/1/CUSA00207/SPRJ0005'
role.mkdir(parents=True)
for stem in ('userdata', 'backup'): (role/f'{stem}0000').write_bytes(raw)
(role/'userdata0010').write_bytes(b'profile'+bytes(100))
t.validate_savedata(role.parents[2])
marker = root/'data/user/world-recovery/review-required.json'
marker.parent.mkdir(parents=True)
marker.write_text(json.dumps({'reason':'synthetic opaque anomaly'}), encoding='utf-8')
print('fixture ready, production validation passed; no game or process-check stub')
'@
        $env:PYTHONHOME=Join-Path $fixture 'runtime'; $env:PYTHONUTF8='1'
        Invoke-FixturePython $setup @($fixture,$repo) 'fixture-setup'
        $desktop.Page('Save')
        $primary=Join-Path $fixture 'data\user\savedata\1\CUSA00207\SPRJ0005\userdata0000'
        $beforePrimary=Hash-File $primary
        $shared=[IO.File]::Open($primary,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None)
        try {
            $refused=$false
            try { Await-Task (Invoke-Private $desktop 'RefreshSaveResolution') } catch { $refused=$true }
            Assert $refused 'Real Windows sharing denial did not fail the backend request'
            Assert ($null -eq (Plan $desktop) -and $window.FindName('SaveResolutionOptions').Children.Count -eq 0) 'Unreadable fixture exposed confirmable save choices'
            $message=$window.FindName('SaveResolutionStatus').Text
            Assert ($message -like '*正常退出*' -and $message -like '*权限*' -and $message -like '*不据此判定存档损坏*') 'Actual backend permission/sharing JSON lost local steps in the UI'
            [IO.File]::WriteAllText((Join-Path $OutputDirectory 'sharing-error-text.txt'),$message,$utf8)
            $desktop.Validate((Join-Path $OutputDirectory 'sharing-error-page.png'),'Save')
        } finally { $shared.Dispose() }
        Assert ((Hash-File $primary) -eq $beforePrimary) 'Read-only sharing test changed a primary'
        $unknownLock=Join-Path $fixture '.save-import.lock'
        [IO.File]::WriteAllText($unknownLock,'{unsupported synthetic control',$utf8)
        Await-Task (Invoke-Private $desktop 'RefreshSaveResolution')
        Assert (-not (Field $desktop 'pendingResolution')['can_resume'] -and -not (Field $desktop 'pendingResolution')['can_abandon']) 'Unknown lock offered a mutating exit'
        Assert ($window.FindName('SaveResolutionPending').Text -like '*当前流程不能安全继续*' -and $window.FindName('SaveResolutionPending').Text -notlike '*权限*') 'Unknown record was mislabeled as transient IO'
        [IO.File]::Move($unknownLock,(Join-Path $fixture 'unsupported-control.kept'))
        $passed.Add('Real Windows FileShare.None on one generated primary -> actual Request/save_resolution.main JSON -> error panel with specific local steps/no choices; release restores access. Actual unknown synthetic control remains unsupported and is not mislabeled as retryable IO. No ACL/global changes.')
        $late=Invoke-Private $desktop 'RefreshSaveResolution'
        Assert $desktop.Busy 'Production plan request was not single-flight'
        $rejected=$false
        try { Await-Task (Invoke-Private $desktop 'RefreshSaveResolution') } catch { $rejected=$true }
        Assert $rejected 'Repeated production refresh did not reject the second operation'
        $desktop.Page('Home'); $desktop.Page('Save'); Await-Task $late
        Assert ($null -eq (Plan $desktop)) 'Actual late Python response repopulated a plan after navigation'
        $late=Invoke-Private $desktop 'RefreshSaveResolution'
        $window.FindName('CancelSaveResolution').RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Primitives.ButtonBase]::ClickEvent)))
        Await-Task $late
        Assert ($null -eq (Plan $desktop)) 'Actual late Python response repopulated a cancelled plan'
        Await-Task (Invoke-Private $desktop 'RefreshSaveBackups')
        Assert ((Plan $desktop)['needed']) 'Production JSON chain did not expose the opaque anomaly'
        Choose-Apply $desktop 'accept-all' $false
        Assert (Test-Path (Join-Path $fixture 'data\user\world-recovery\review-required.json')) 'Declining confirmation changed the anomaly'
        Assert (-not (Test-Path (Join-Path $fixture 'save-backups'))) 'Declining confirmation created a transaction'
        Await-Task (Invoke-Private $desktop 'RefreshSaveResolution')
        Choose-Apply $desktop 'accept-all' $true
        Assert (-not (Test-Path (Join-Path $fixture '.save-import.lock'))) 'Completed actual backend transaction retained its lock'
        Assert (-not (Test-Path (Join-Path $fixture 'data\user\world-recovery\review-required.json'))) 'Actual accept-all did not retire the marker'
        $special=@($window.FindName('SaveBackupList').Items | Where-Object { $_.Tag['kind'] -eq 'resolution-before' })
        Assert ($special.Count -ge 1) 'Completed production original was not listed separately'
        foreach($item in $special) {
            $expected=if($CandidateRoot) { '*角色槽位 1 · 主档与备档 · 猎人甲 · 等级 33 · 回响 106*' } else { '*角色槽位 1 · 主档 · Hunter · 等级、回响未识别*角色槽位 1 · 备档 · Hunter · 等级、回响未识别*' }
            Assert ($item.Content -match '\d{4}-\d{2}-\d{2}' -and $item.Content -like $expected) 'Production special row omitted its timestamp or character details'
            if($item.Tag['readability_note']) { Assert ($item.Content.Contains($item.Tag['readability_note'])) 'Production special row hid its backend readability note' }
        }
        $window.FindName('SaveBackupList').SelectedItem=$special[0]
        Assert ($window.FindName('RestoreSaveBackup').Content -eq '换回处理前状态') 'Special row used ordinary restore wording'
        if($CandidateRoot) {
            $setCurrent=@'
import sys
from pathlib import Path
root,repo=map(Path,sys.argv[1:]);sys.path[:0]=[str(root),str(repo/'tests')]
from check_backup_characters import character
raw=character('当前',24,999)
for stem in ('userdata','backup'):
    (root/'data/user/savedata/1/CUSA00207/SPRJ0005'/(stem+'0000')).write_bytes(raw)
'@
            Invoke-FixturePython $setCurrent @($fixture,$repo) 'distinct-current-character'
        }
        Await-Task (Invoke-Private $desktop 'RestoreSaveBackup')
        if($CandidateRoot) {
            $realText=$window.FindName('SaveResolutionCharacters').Text
            Assert ($realText -like '*备份中的角色*猎人甲 · 等级 33 · 回响 106*当前存档中的角色*当前 · 等级 24 · 回响 999*主档可读，备档可读*') 'Actual restore button / JSON / processing panel did not separate the two save generations'
            [IO.File]::WriteAllText((Join-Path $OutputDirectory 'selected-and-current.txt'),$realText,$utf8)
        }
        Assert ((Plan $desktop)['selection']['kind'] -eq 'resolution-before') 'Actual special dispatch lost selection kind'
        Assert (-not (Plan $desktop)['needed'] -and (Plan $desktop)['show_choices']) 'Healthy explicit history was mislabeled or hidden'
        Assert ($window.FindName('SaveResolutionPanel').Visibility -eq [Windows.Visibility]::Visible -and $window.FindName('SaveResolutionOptions').Children.Count -gt 0) 'Healthy history has no real visible options'
        Choose-Apply $desktop 'restore-case' $true
        Assert (Test-Path (Join-Path $fixture 'data\user\save-resolution-review.json')) 'Actual restore-case did not persist the review gate'
        Assert ((Plan $desktop)['needed']) 'Actual restore-case did not return to the anomaly page'
        $desktop.Validate((Join-Path $OutputDirectory 'resolution-real-page.png'),'Save')
        $window.FindName('SaveResolutionPanel').BringIntoView(); $window.UpdateLayout()
        $desktop.Validate((Join-Path $OutputDirectory 'resolution-real-chain.png'),'Save')
        $passed.Add('Real DesktopWindow.Request -> packaged Python -> production save_resolution CLI: filesystem-validated synthetic saves, asynchronous response invalidation, duplicate refresh refusal, native No/Yes, accept-all, special list/selection, restore-case and persistent gate. No prepare/review/process-check stubs.')
        # The new restore-case's original is the clean branch from before the switch.
        $clean=@($window.FindName('SaveBackupList').Items | Where-Object { $_.Tag['kind'] -eq 'resolution-before' -and $_.Tag['label'] -like '换回前的存档*' })[0]
        Assert ($null -ne $clean) 'The branch from before the switch has no distinct selectable name'
        $window.FindName('SaveBackupList').SelectedItem=$clean
        Await-Task (Invoke-Private $desktop 'RestoreSaveBackup'); Choose-Apply $desktop 'restore-case' $true
        Assert (@((Plan $desktop)['options'] | Where-Object { $_['id'] -eq 'use-current' -and $_['recommended'] }).Count -eq 1) 'Clean restored branch has no recommended normal gate exit'
        Assert (@((Plan $desktop)['options'] | Where-Object { $_['id'] -in @('restart','reimport-ordinary','choose-ordinary') }).Count -eq 0) 'Launchable gate exposed slice2 destructive choices'
        # Keep the synthetic ordinary installation outside ROOT, exactly as the
        # real prepare guard requires. No game executable in it is ever run.
        $ordinary=Join-Path $OutputDirectory 'synthetic-ordinary'; $game=Join-Path $OutputDirectory 'synthetic-game'
        [void][IO.Directory]::CreateDirectory((Join-Path $ordinary 'user\savedata')); [void][IO.Directory]::CreateDirectory($game)
        foreach($name in @('Bloodborne.exe','bbport.ini')) { [IO.File]::WriteAllText((Join-Path $ordinary $name),'synthetic file only',$utf8) }
        [IO.File]::WriteAllText((Join-Path $game 'eboot.bin'),'synthetic file only',$utf8)
        $mod=Join-Path $fixture 'mod'; [void][IO.Directory]::CreateDirectory($mod)
        [IO.File]::WriteAllText((Join-Path $mod 'LICENSE.txt'),'GNU GENERAL PUBLIC LICENSE - synthetic notice',$utf8)
        [IO.File]::WriteAllText((Join-Path $mod 'NOTICE.txt'),'github.com/Classmg22/BBSC - synthetic notice',$utf8)
        [IO.File]::WriteAllText((Join-Path $mod 'SUPPORTERS.txt'),'BBSC SUPPORTERS - synthetic notice',$utf8)
        $window.FindName('InstallPath').Text=Join-Path $ordinary 'Bloodborne.exe'
        $window.FindName('GraphicsPath').Text=Join-Path $ordinary 'bbport.ini'
        $window.FindName('GamePath').Text=Join-Path $game 'eboot.bin'
        $window.FindName('Account').Text='synthetic-hunter'; $window.FindName('Password').Password='synthetic-password'
        $window.FindName('PublicOnline').IsChecked=$false
        $window.FindName('Shadnet').Text='127.0.0.1:31413'; $window.FindName('Relay').Text='127.0.0.1:20444'
        $window.FindName('Webapi').Text='http://127.0.0.1:31415'; $window.FindName('Community').Text='http://127.0.0.1:20443'
        Await-Task (Invoke-Private $desktop 'RefreshSaveResolution')
        Choose-Apply $desktop 'use-current' $true
        Assert (-not (Test-Path (Join-Path $fixture 'data\user\save-resolution-review.json'))) 'Explicit normal exit retained its gate'
        $state=Get-Content -LiteralPath (Join-Path $fixture 'launcher-state.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        Assert ($state.phase -eq 'error' -and $state.message -like '*bbport.ini*output_res*upscaler*preset*') 'Normal launch did not pass prepare and stop at the intended synthetic settings barrier'
        Assert (@(Get-ChildItem -LiteralPath (Join-Path $fixture 'save-backups') -Directory -Filter 'launch-*').Count -ge 1) 'Real launch backup after prepare was not created'
        Assert (@(Get-ChildItem -LiteralPath (Join-Path $fixture 'data\user\world-recovery\runs') -File -Filter '*.json').Count -ge 1) 'Real world.begin after prepare was not reached'
        Assert (-not $desktop.Busy) 'Normal launch barrier retained Busy'
        [IO.File]::WriteAllText((Join-Path $OutputDirectory 'prepare-chain-state.json'),($state | ConvertTo-Json -Depth 6),$utf8)
        $passed.Add('Actual clean restore -> persistent gate -> use-current -> native Yes -> DesktopWindow.Launch/client.main -> notice/profile/network checks -> real prepare -> launch backup -> world.begin. Intentionally stops at invalid synthetic bbport.ini in sync_settings, before any game/network child launch. No prepare/process-check success stub; this is not real-game validation.')
        # Stop an independent real backend only after its durable lock is published.
        $marker=Join-Path $fixture 'data\user\world-recovery\review-required.json'
        [void][IO.Directory]::CreateDirectory((Split-Path $marker)); [IO.File]::WriteAllText($marker,'{"reason":"synthetic resume anomaly"}',$utf8)
        $handshake=Join-Path $OutputDirectory 'after-lock.handshake'
        $driver=@'
import sys,time
from pathlib import Path
root,signal=map(Path,sys.argv[1:]);sys.path.insert(0,str(root))
import save_resolution as s
def observe(point):
    if point=='after:lock':
        signal.write_text(point,encoding='utf-8')
        while True: time.sleep(.05)
s._checkpoint=observe
p=s.plan(root)
s.apply(root,p['digest'],'accept-all',confirmed=True)
raise RuntimeError('Controlled strong-kill boundary was not reached')
'@
        $info=New-Object Diagnostics.ProcessStartInfo
        $info.FileName=Join-Path $fixture 'runtime\bin\python.exe'
        $info.Arguments='-B -c '+[DesktopWindow]::Quote($driver)+' '+[DesktopWindow]::Quote($fixture)+' '+[DesktopWindow]::Quote($handshake)
        $info.WorkingDirectory=$fixture; $info.UseShellExecute=$false; $info.CreateNoWindow=$true
        $info.RedirectStandardOutput=$true; $info.RedirectStandardError=$true
        $process=[Diagnostics.Process]::Start($info)
        $killRow=@{pid=$process.Id;executable=$info.FileName;arguments=$info.Arguments;fixture=$fixture;started_utc=[DateTime]::UtcNow.ToString('o');completed=$false;method='OS Process.Kill after real lock; not power loss'}
        try {
            $held=$process.Handle; Save-ChildReceipt $killRow 'strong-kill'
            $stdout=$process.StandardOutput.ReadToEndAsync(); $stderr=$process.StandardError.ReadToEndAsync()
            $deadline=[DateTime]::UtcNow.AddSeconds(30)
            while(-not (Test-Path -LiteralPath $handshake)) { if($process.HasExited -or [DateTime]::UtcNow -gt $deadline) { throw 'Real backend did not reach after:lock handshake' }; Start-Sleep -Milliseconds 20 }
            Assert (Test-Path (Join-Path $fixture '.save-import.lock')) 'Handshake preceded the actual lock publication'
            $process.Kill(); Assert ($process.WaitForExit(10000)) 'Kill child exit timeout'
            Assert ($process.ExitCode -is [int] -and $process.ExitCode -ne 0) 'OS kill did not produce a real nonzero exit status'
            Assert ([Threading.Tasks.Task]::WaitAll([Threading.Tasks.Task[]]@($stdout,$stderr),10000)) 'Kill child output drain timeout'
            [IO.File]::WriteAllText((Join-Path $OutputDirectory 'strong-kill.stdout'),$stdout.GetAwaiter().GetResult(),$utf8)
            [IO.File]::WriteAllText((Join-Path $OutputDirectory 'strong-kill.stderr'),$stderr.GetAwaiter().GetResult(),$utf8)
            $killRow.completed=$true
            $killRow.stdout_sha256=Hash-File (Join-Path $OutputDirectory 'strong-kill.stdout')
            $killRow.stderr_sha256=Hash-File (Join-Path $OutputDirectory 'strong-kill.stderr')
        } finally {
            if(-not $process.HasExited) { $process.Kill(); Assert ($process.WaitForExit(10000)) 'Owned kill child did not exit' }
            $killRow.exit_code=$process.ExitCode; $killRow.ended_utc=[DateTime]::UtcNow.ToString('o')
            Save-ChildReceipt $killRow 'strong-kill'; $process.Dispose()
        }
        $desktop.Page('Save'); Await-Task (Invoke-Private $desktop 'RefreshSaveBackups')
        Assert ((Field $desktop 'pendingResolution')['can_resume']) 'Real interrupted A lock did not expose a resumable UI'
        Assert ($window.FindName('SaveResolutionOptions').Children.Count -eq 0 -and $window.FindName('ResumeSaveResolution').IsEnabled) 'Actual pending UI allowed replacement choices or disabled resume'
        Choose-Resume $desktop
        Assert (-not (Test-Path (Join-Path $fixture '.save-import.lock'))) 'Actual native resume did not complete its transaction'
        $passed.Add('Independent production apply with only a checkpoint observer -> external Process.Kill after durable lock -> actual backup refresh/A pending -> native resume Yes -> production resume. Other strong-kill boundaries remain the backend suite responsibility.')
        # Second production pending uses an observer exception, not OS kill.
        [void][IO.Directory]::CreateDirectory((Split-Path $marker))
        [IO.File]::WriteAllText($marker,'{"reason":"second synthetic pending"}',$utf8)
        $abandonSetup=@'
import sys,json,os
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root))
import save_resolution as s
class Pause(Exception):pass
def point(name):
    if name=='after:lock':raise Pause()
s._checkpoint=point
p=s.plan(root)
try:s.apply(root,p['digest'],'accept-all',confirmed=True)
except Pause:pass
else:raise AssertionError('real lock was not published')
assert (root/'.save-import.lock').is_file()
print(json.dumps({'pid':os.getpid(),'stopped':'checkpoint exception after actual lock; not OS kill'}))
'@
        Invoke-FixturePython $abandonSetup @($fixture) 'abandon-setup'
        $later=Join-Path $fixture 'data\user\savedata\later-synthetic-progress'
        [IO.File]::WriteAllText($later,'new current progress never merged',$utf8)
        Await-Task (Invoke-Private $desktop 'RefreshSaveBackups')
        Assert (-not (Field $desktop 'pendingResolution')['can_resume'] -and (Field $desktop 'pendingResolution')['can_abandon']) 'Changed production prefix has no forward abandonment exit'
        Assert (-not $window.FindName('ResumeSaveResolution').IsEnabled -and $window.FindName('AbandonSaveResolution').IsEnabled) 'Actual pending buttons do not reflect independent eligibility'
        $oldLock=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $fixture '.save-import.lock')))
        Choose-Abandon $desktop $false
        Assert ([Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $fixture '.save-import.lock'))) -eq $oldLock) 'Declined abandonment changed the active choice'
        Await-Task (Invoke-Private $desktop 'RefreshSaveBackups'); Choose-Abandon $desktop $true
        Assert (-not (Test-Path (Join-Path $fixture '.save-import.lock')) -and (Test-Path (Join-Path $fixture 'data\user\save-resolution-review.json'))) 'Real abandonment failed to retire its lock behind a review gate'
        Assert ([IO.File]::ReadAllText($later) -eq 'new current progress never merged') 'Abandonment overwrote later current progress'
        Assert (-not $desktop.Busy -and (Plan $desktop)['needed']) 'Abandonment did not return to the processing page'
        $desktop.Validate((Join-Path $OutputDirectory 'abandon-complete-page.png'),'Save')
        $passed.Add('Real second pending A via checkpoint exception (not OS kill), later file invalidates resume -> production dual eligibility -> native No leaves old lock -> native Yes -> JSON/CLI abandonment -> same progress, gate and no automatic launch. Healthy explicit history had needed=false/show_choices=true and visible options.')

        # Independent slice2 root: no old state and no host settings.
        $desktop.Window.Close(); $desktop=$null
        $fixture=Join-Path $OutputDirectory ('slice2-fixture-'+[Guid]::NewGuid().ToString('N'))
        [void][IO.Directory]::CreateDirectory($fixture)
        $env:APPDATA=Join-Path $fixture 'isolated-appdata'; [void][IO.Directory]::CreateDirectory($env:APPDATA)
        Copy-Item -LiteralPath $RuntimeRoot -Destination (Join-Path $fixture 'runtime') -Recurse
        $env:PYTHONHOME=Join-Path $fixture 'runtime'
        Copy-FixtureReaders $fixture
        foreach($name in @('Launcher.xaml','feature-catalog.json','trophy-catalog.json')) { Copy-Item -LiteralPath (Join-Path $repo ('launcher\'+$name)) -Destination $fixture }
        $sliceOrdinary=Join-Path $OutputDirectory ('slice2-ordinary-'+[Guid]::NewGuid().ToString('N'))
        $sliceExpected=Join-Path $OutputDirectory 'slice2-expected.json'
        $sliceSetup=@'
import hashlib,json,shutil,sys
from pathlib import Path
root,repo,ordinary,record=map(Path,sys.argv[1:]);sys.path[:0]=[str(root),str(repo/'tests')]
from synthetic_world_fixture import fixture_pair
import save_transfer as t
raw,_=fixture_pair();role=root/'data/user/savedata/1/CUSA00207/SPRJ0005';role.mkdir(parents=True)
for slot in (0,1):
 for stem in ('userdata','backup'):(role/f'{stem}{slot:04d}').write_bytes(raw)
(role/'userdata0010').write_bytes(b'profile'+bytes(100));t.validate_savedata(root/'data/user/savedata')
shutil.copytree(root/'data/user/savedata',ordinary/'user/savedata');(ordinary/'Bloodborne.exe').write_bytes(b'fake never executed')
for base in (root/'data/user/trophy',ordinary/'user/trophy'):
 base.mkdir();(base/'sentinel').write_bytes(b'unchanged trophy')
(role/'userdata0000').write_bytes(b'bad-primary'+bytes(127))
def row(p):
 st=p.stat();return [hashlib.sha256(p.read_bytes()).hexdigest(),st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns]
def tree(base):return {str(p.relative_to(base)):row(p) for p in base.rglob('*') if p.is_file()}
record.write_text(json.dumps({'target':tree(root/'data/user/savedata'),'ordinary':tree(ordinary),'trophy':tree(root/'data/user/trophy')}),encoding='utf-8')
'@
        Invoke-FixturePython $sliceSetup @($fixture,$repo,$sliceOrdinary,$sliceExpected) 'slice2-setup'
        $desktop=New-Object DesktopWindow($fixture); $window=$desktop.Window; $desktop.Page('Save')
        $slicePrimary=Join-Path $fixture 'data\user\savedata\1\CUSA00207\SPRJ0005\userdata0000'
        $before=Hash-File $slicePrimary
        Await-Task (Invoke-Private $desktop 'RefreshSaveResolution'); Choose-Apply $desktop 'restore-generation-0' $false
        Assert ((Hash-File $slicePrimary) -eq $before -and -not (Test-Path (Join-Path $fixture '.save-import.lock'))) 'Generation No mutated state'
        Await-Task (Invoke-Private $desktop 'RefreshSaveResolution'); Choose-Apply $desktop 'restore-generation-0' $true
        Assert (Test-Path (Join-Path $fixture 'data\user\save-resolution-review.json')) 'Generation restore omitted gate'
        $sliceVerify=@'
import hashlib,json,sys
from pathlib import Path
root,ordinary,record=map(Path,sys.argv[1:]);e=json.loads(record.read_text());base=root/'data/user/savedata'
def row(p):
 st=p.stat();return [hashlib.sha256(p.read_bytes()).hexdigest(),st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns]
def tree(base):return {str(p.relative_to(base)):row(p) for p in base.rglob('*') if p.is_file()}
a=tree(base);primary=str(Path('1/CUSA00207/SPRJ0005/userdata0000'));backup=str(Path('1/CUSA00207/SPRJ0005/backup0000'))
assert set(a)==set(e['target'])
for k,v in e['target'].items():
 if k!=primary:assert a[k]==v,(k,'content/inode/mtime changed')
assert a[primary][0]==e['target'][backup][0]
assert tree(ordinary)==e['ordinary'];assert tree(root/'data/user/trophy')==e['trophy']
for stem in ('userdata','backup'):(base/'1/CUSA00207/SPRJ0005'/f'{stem}0000').write_bytes(b'bad-'+stem.encode()+bytes(127))
e['bad_target']={k:v[0] for k,v in tree(base).items()};record.write_text(json.dumps(e),encoding='utf-8')
'@
        Assert (@($window.FindName('SaveBackupList').Items | Where-Object { $_.Tag['kind'] -eq 'resolution-before' }).Count -ge 1) 'Generation original absent from undo list'
        Invoke-FixturePython $sliceVerify @($fixture,$sliceOrdinary,$sliceExpected) 'slice2-generation-verify-import-setup'
        $window.FindName('InstallPath').Text=Join-Path $sliceOrdinary 'Bloodborne.exe'
        Await-Task (Invoke-Private $desktop 'RefreshSaveBackups')
        $sourcePlan=Plan $desktop; $sourceChoice=@($sourcePlan['options'] | Where-Object { $_['id'] -eq 'reimport-ordinary' })[0]
        Assert ($null -ne $sourceChoice -and $sourceChoice.ContainsKey('digest') -and $sourceChoice['digest'] -ne $sourcePlan['digest']) 'Real reimport lacks independent source binding'
        Assert ($window.FindName('SaveResolutionCharacters').Text -like '*原版导入来源中的角色*Hunter*当前存档中的角色*') 'Processing page omitted the offered import source'
        $importConfirm=Invoke-Private $desktop 'ResolutionConfirmation' @($sourcePlan,$sourceChoice)
        Assert ($importConfirm -like '*将导入的原版角色*Hunter*当前版本检查*' -and $importConfirm -notlike '*将恢复的备份角色*') 'Import confirmation displayed the wrong branch'
        $oldIds=@($window.FindName('SaveBackupList').Items | Where-Object { $_.Tag['kind'] -eq 'resolution-before' } | ForEach-Object { $_.Tag['id'] })
        $before=Hash-File $slicePrimary
        Choose-Apply $desktop 'reimport-ordinary' $false
        Assert ((Hash-File $slicePrimary) -eq $before -and -not (Test-Path (Join-Path $fixture '.save-import.lock'))) 'Import No mutated state'
        Await-Task (Invoke-Private $desktop 'RefreshSaveResolution'); Choose-Apply $desktop 'reimport-ordinary' $true
        $verifyImport=@'
import hashlib,json,sys
from pathlib import Path
root,ordinary,record=map(Path,sys.argv[1:]);e=json.loads(record.read_text())
def row(p):
 st=p.stat();return [hashlib.sha256(p.read_bytes()).hexdigest(),st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns]
def tree(base):return {str(p.relative_to(base)):row(p) for p in base.rglob('*') if p.is_file()}
assert tree(ordinary)==e['ordinary'];assert tree(root/'data/user/trophy')==e['trophy']
assert {k:v[0] for k,v in tree(root/'data/user/savedata').items()}=={k:v[0] for k,v in tree(ordinary/'user/savedata').items()}
assert not (root/'.save-import.lock').exists()
'@
        Invoke-FixturePython $verifyImport @($fixture,$sliceOrdinary,$sliceExpected) 'slice2-import-verify'
        $undo=@($window.FindName('SaveBackupList').Items | Where-Object { $_.Tag['kind'] -eq 'resolution-before' -and $oldIds -notcontains $_.Tag['id'] })
        Assert ($undo.Count -eq 1) 'Import original missing or ambiguous'
        $window.FindName('SaveBackupList').SelectedItem=$undo[0]
        Await-Task (Invoke-Private $desktop 'RestoreSaveBackup'); Choose-Apply $desktop 'restore-case' $true
        $verifyUndo=@'
import hashlib,json,sys
from pathlib import Path
root,record=map(Path,sys.argv[1:]);base=root/'data/user/savedata';e=json.loads(record.read_text())
assert {str(p.relative_to(base)):hashlib.sha256(p.read_bytes()).hexdigest() for p in base.rglob('*') if p.is_file()}==e['bad_target']
assert (root/'data/user/save-resolution-review.json').is_file()
assert not (root/'launcher-state.json').exists(),'undo started a game session'
'@
        Invoke-FixturePython $verifyUndo @($fixture,$sliceExpected) 'slice2-import-undo-verify'
        Assert ((Plan $desktop)['needed'] -and -not $desktop.Busy) 'Bad undo did not return to processing page'
        Assert (@((Plan $desktop)['options'] | Where-Object { $_['id'] -eq 'use-current' }).Count -eq 0) 'Bad-primary undo exposed normal launch'
        $passed.Add('Real slice2 CLI via WPF: generation No/Yes preserves other slot/profile/backup inode+mtime; reimport No/Yes preserves source/trophies; listed original undo returns exact bad state with gate and no launch. Synthetic only.')
        # Restart is an explicit fallback, not a diagnosis. Exercise the actual
        # production option only after the backend supplies it on this bad state.
        $partialRestart=@((Plan $desktop)['options'] | Where-Object { $_['id'] -eq 'restart' })[0]
        Assert ($null -ne $partialRestart) 'Blocked partial save lacks explicit restart fallback'
        Assert ($partialRestart['description'] -like '*Hunter*') 'Restart omitted the identified readable role'
        $beforeRestartCases=@($window.FindName('SaveBackupList').Items).Count
        Choose-Restart $desktop $false $false
        Invoke-FixturePython $verifyUndo @($fixture,$sliceExpected) 'restart-first-no-unchanged'
        Assert (@($window.FindName('SaveBackupList').Items).Count -eq $beforeRestartCases -and -not (Test-Path (Join-Path $fixture '.save-import.lock'))) 'First No published preservation or lock'
        Await-Task (Invoke-Private $desktop 'RefreshSaveResolution')
        Choose-Restart $desktop $true $false
        Invoke-FixturePython $verifyUndo @($fixture,$sliceExpected) 'restart-second-no-unchanged'
        Assert (@($window.FindName('SaveBackupList').Items).Count -eq $beforeRestartCases -and -not (Test-Path (Join-Path $fixture '.save-import.lock'))) 'Second No published preservation or lock'
        # Make every role unrecognized using only generated files. No valid
        # character names may survive as a guessed current character list.
        $unknownSetup=@'
import hashlib,json,sys
from pathlib import Path
root,record=map(Path,sys.argv[1:]);base=root/'data/user/savedata';role=base/'1/CUSA00207/SPRJ0005'
for slot in (0,1):
 for stem in ('userdata','backup'):(role/f'{stem}{slot:04d}').write_bytes(b'unknown-role-format'+bytes(97))
e=json.loads(record.read_text());e['bad_target']={str(p.relative_to(base)):hashlib.sha256(p.read_bytes()).hexdigest() for p in base.rglob('*') if p.is_file()};record.write_text(json.dumps(e),encoding='utf-8')
'@
        Invoke-FixturePython $unknownSetup @($fixture,$sliceExpected) 'restart-unknown-setup'
        Await-Task (Invoke-Private $desktop 'RefreshSaveBackups')
        $restartPlan=Plan $desktop
        $restart=@($restartPlan['options'] | Where-Object { $_['id'] -eq 'restart' })[0]
        Assert ($null -ne $restart -and -not $restart['recommended']) 'Unknown-format restart was absent or recommended'
        Assert ($restart['description'] -like '*无法确认*' -and $restart['description'] -notlike '*已损坏*' -and $restart['description'] -notlike '*Hunter*') 'Unknown restart diagnosed damage or invented a role name'
        Assert ($restartPlan['options'][-1]['id'] -eq 'restart') 'Nonrecommended restart was not last'
        $oldIds=@($window.FindName('SaveBackupList').Items | Where-Object { $_.Tag['kind'] -eq 'resolution-before' } | ForEach-Object { $_.Tag['id'] })
        Choose-Restart $desktop $true $true
        Assert (@(Get-ChildItem -LiteralPath (Join-Path $fixture 'data\user\savedata') -Recurse -File).Count -eq 0) 'Restart did not leave fresh empty savedata'
        Assert (-not (Test-Path (Join-Path $fixture '.save-import.lock')) -and -not (Test-Path (Join-Path $fixture 'launcher-state.json'))) 'Restart retained lock or automatically launched game'
        $restartOriginal=@($window.FindName('SaveBackupList').Items | Where-Object { $_.Tag['kind'] -eq 'resolution-before' -and $oldIds -notcontains $_.Tag['id'] })
        Assert ($restartOriginal.Count -eq 1 -and $restartOriginal[0].IsEnabled) 'Unrecognized restart original cannot be selected for exact undo'
        $window.FindName('SaveBackupList').SelectedItem=$restartOriginal[0]
        Await-Task (Invoke-Private $desktop 'RestoreSaveBackup'); Choose-Apply $desktop 'restore-case' $true
        Invoke-FixturePython $verifyUndo @($fixture,$sliceExpected) 'restart-unknown-undo'
        Assert ((Plan $desktop)['needed'] -and @((Plan $desktop)['options'] | Where-Object { $_['id'] -eq 'restart' }).Count -eq 1) 'Restart undo has no gate processing choices'
        $passed.Add('Real restart: first No and second No preserve state; both Yes empty synthetic savedata; wholly unidentified original remains selectable; exact undo returns gate/processing choices. Unknown is not diagnosed as damaged or recommended. No real game launched.')
    } else { $passed.Add('NOT RUN: real Python backend chain. ViewOnly does not establish transaction safety.') }
    [IO.File]::WriteAllText((Join-Path $OutputDirectory 'result.txt'),[string]::Join("`r`n",$passed),$utf8)
    $passed
} catch {
    [IO.File]::WriteAllText((Join-Path $OutputDirectory 'failure.txt'),($_ | Out-String),$utf8)
    throw
} finally {
    $env:APPDATA=$previousAppData
    if($desktop) { $desktop.GetType().GetField('operation',$flags).SetValue($desktop,$false); $desktop.Window.Close() }
    # Preserve the unique synthetic fixture and all failures for inspection. No cleanup/delete.
}
