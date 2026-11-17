param([Parameter(Mandatory=$true)][string]$Repo,[Parameter(Mandatory=$true)][string]$OutputDirectory,[Parameter(Mandatory=$true)][string]$RuntimeRoot)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName PresentationFramework,PresentationCore,WindowsBase,System.Xaml,System.Web.Extensions
[void][Reflection.Assembly]::LoadFrom((Join-Path $Repo '.build\HuntUpdates.dll'))
[void][Reflection.Assembly]::LoadFrom((Join-Path $Repo '.build\TheHuntLauncher.exe'))
$utf8=New-Object Text.UTF8Encoding $false
$flags=[Reflection.BindingFlags]'Instance,NonPublic'
$parser=New-Object Web.Script.Serialization.JavaScriptSerializer
$root=Join-Path $OutputDirectory ('fixture-'+[Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($root)
foreach($n in @('Launcher.xaml','feature-catalog.json','trophy-catalog.json')) {Copy-Item (Join-Path $Repo ('launcher\'+$n)) $root}
Copy-Item (Join-Path $Repo 'assets') (Join-Path $OutputDirectory 'assets') -Recurse
Copy-Item $RuntimeRoot (Join-Path $root 'runtime') -Recurse
foreach($f in Get-ChildItem (Join-Path $Repo 'launcher') -Filter '*.py'){Copy-Item $f.FullName $root}
$oldApp=$env:APPDATA;$oldHome=$env:PYTHONHOME;$oldUtf8=$env:PYTHONUTF8
$env:APPDATA=Join-Path $root 'isolated-appdata';[void][IO.Directory]::CreateDirectory($env:APPDATA)
$env:PYTHONHOME=Join-Path $root 'runtime';$env:PYTHONUTF8='1'
function Assert($v,$m){if(-not $v){throw $m}}
function Call($n,[object[]]$a=@()){$b=New-Object object[] $a.Length;for($i=0;$i -lt $a.Length;$i++){$b[$i]=$a[$i].PSObject.BaseObject};return $desktop.GetType().GetMethod($n,$flags).Invoke($desktop,$b)}
function Await($t){$end=[DateTime]::UtcNow.AddSeconds(90);while(-not $t.IsCompleted){if([DateTime]::UtcNow -gt $end){throw 'UI timeout'};$f=New-Object Windows.Threading.DispatcherFrame;$tick=New-Object Windows.Threading.DispatcherTimer;$tick.Interval=[TimeSpan]::FromMilliseconds(20);$tick.Tag=$f;$tick.Add_Tick({$this.Stop();$this.Tag.Continue=$false});$tick.Start();[Windows.Threading.Dispatcher]::PushFrame($f)};return $t.GetAwaiter().GetResult()}
function Py($code,[string[]]$a=@()){
 $i=New-Object Diagnostics.ProcessStartInfo;$i.FileName=Join-Path $root 'runtime\bin\python.exe';$i.Arguments='-B -c '+[DesktopWindow]::Quote($code);foreach($v in $a){$i.Arguments+=' '+[DesktopWindow]::Quote($v)}
 $i.WorkingDirectory=$root;$i.UseShellExecute=$false;$i.CreateNoWindow=$true;$i.RedirectStandardOutput=$true;$i.RedirectStandardError=$true
 $p=[Diagnostics.Process]::Start($i);try{$o=$p.StandardOutput.ReadToEndAsync();$e=$p.StandardError.ReadToEndAsync();Assert ($p.WaitForExit(90000)) 'Python timeout';$err=$e.GetAwaiter().GetResult();Assert ($p.ExitCode -eq 0) $err;$o.GetAwaiter().GetResult()}finally{if(-not $p.HasExited){$p.Kill();$p.WaitForExit()};$p.Dispose()}
}
Add-Type @'
using System;using System.Runtime.InteropServices;using System.Text;using System.Diagnostics;
public static class SourceDialog{
delegate bool Visit(IntPtr w,IntPtr p);
[DllImport("user32.dll")]static extern bool EnumWindows(Visit v,IntPtr p);
[DllImport("user32.dll",CharSet=CharSet.Unicode)]static extern int GetWindowText(IntPtr w,StringBuilder b,int n);
[DllImport("user32.dll")]static extern uint GetWindowThreadProcessId(IntPtr w,out uint p);
[DllImport("user32.dll")]static extern IntPtr GetDlgItem(IntPtr w,int id);
[DllImport("user32.dll")]static extern IntPtr SendMessage(IntPtr w,int m,IntPtr a,IntPtr b);
public static bool No(string title){bool found=false;EnumWindows(delegate(IntPtr w,IntPtr ignored){uint p;GetWindowThreadProcessId(w,out p);var b=new StringBuilder(200);GetWindowText(w,b,200);if(p==(uint)Process.GetCurrentProcess().Id&&b.ToString()==title){SendMessage(GetDlgItem(w,7),0xf5,IntPtr.Zero,IntPtr.Zero);found=true;return false;}return true;},IntPtr.Zero);return found;}}
'@
[Threading.SynchronizationContext]::SetSynchronizationContext((New-Object Windows.Threading.DispatcherSynchronizationContext))
try{
 $ordinary=Join-Path $OutputDirectory ('ordinary-'+[Guid]::NewGuid().ToString('N'))
 $setup=@'
import sys,json,hashlib
from pathlib import Path
root,repo,ordinary=map(Path,sys.argv[1:]);sys.path[:0]=[str(root),str(repo/'tests')]
from synthetic_world_fixture import fixture_pair
raw,_=fixture_pair();raw=bytearray(raw);raw[0xC0F7:0xC0F7+12]='Hunter'.encode('utf-16-be');raw=bytes(raw);p=ordinary/'user/savedata/1/CUSA00207/SPRJ0005';p.mkdir(parents=True)
for stem in ('userdata','backup'):(p/(stem+'0000')).write_bytes(raw)
(p/'userdata0010').write_bytes(b'profile'+bytes(100))
for n in ('Bloodborne.exe','bbport.ini','eboot.bin'):(ordinary/n).write_bytes(b'synthetic never executed')
(root/'source-before.json').write_text(json.dumps({str(f.relative_to(ordinary)):hashlib.sha256(f.read_bytes()).hexdigest() for f in ordinary.rglob('*') if f.is_file()}))
'@
 Py $setup @($root,$Repo,$ordinary)
 $profile=Join-Path $root 'profile.json';[IO.File]::WriteAllText($profile,'{"unknown_setting":{"keep":true}}',$utf8)
 $desktop=New-Object DesktopWindow($root);$desktop.Page('Save')
 $desktop.Window.FindName('InstallPath').Text=Join-Path $ordinary 'Bloodborne.exe'
 $task=Call 'SetOrdinarySource' @((Join-Path $ordinary 'user'))
 Assert $desktop.Busy 'Selection not single-flight';Assert (-not $desktop.Window.FindName('PickSaveFolder').IsEnabled) 'Busy button enabled';Await $task
 Assert (-not(Test-Path (Join-Path $root 'data'))) 'Preview wrote data'
 Assert ($desktop.Window.FindName('SaveSummary').Text -like '*Hunter*首次启动*尚未初始化*') 'Preview lacks role or readonly notice'
 Assert (-not $desktop.Window.FindName('ImportSaves').IsEnabled) 'Preview enabled import'
 Assert ($parser.DeserializeObject([IO.File]::ReadAllText($profile))['unknown_setting']['keep']) 'Profile field lost'
 Assert (-not(Test-Path (Join-Path $root '.private\account.clixml'))) 'Path save wrote credentials'
 $script:noSeen=$false;$timer=New-Object Windows.Threading.DispatcherTimer;$timer.Interval=[TimeSpan]::FromMilliseconds(30);$timer.Add_Tick({if([SourceDialog]::No('确认首次导入来源')){$script:noSeen=$true;$this.Stop()}});$timer.Start()
 try{Await (Call 'Launch')}finally{$timer.Stop()}
 Assert $script:noSeen 'First import confirmation absent';Assert (-not(Test-Path (Join-Path $root 'data'))) 'No initialized data'
 Await (Call 'ClearOrdinarySource')
 Assert (-not $parser.DeserializeObject([IO.File]::ReadAllText($profile)).ContainsKey('ordinary_savedata')) 'Clear retained override'
 $savedata=Join-Path $ordinary 'user\savedata';$held=Join-Path $ordinary 'user\savedata-test-held'
 [IO.Directory]::Move($savedata,$held)
 $script:noSeen=$false;$timer=New-Object Windows.Threading.DispatcherTimer;$timer.Interval=[TimeSpan]::FromMilliseconds(30)
 $timer.Add_Tick({if([SourceDialog]::No('没有找到普通版存档')){$script:noSeen=$true;$this.Stop()}});$timer.Start()
 try{Await (Call 'Launch')}finally{$timer.Stop();[IO.Directory]::Move($held,$savedata)}
 Assert $script:noSeen 'Missing-source empty-save confirmation absent'
 Assert (-not(Test-Path (Join-Path $root 'data'))) 'Missing-source No initialized data'
 $init=@'
import shutil,sys
from pathlib import Path
root,ordinary=map(Path,sys.argv[1:]);shutil.copytree(ordinary/'user/savedata',root/'data/user/savedata')
(root/'data/user/savedata/before-import-sentinel').write_bytes(b'preserve')
'@
 Py $init @($root,$ordinary)
 Await (Call 'SetOrdinarySource' @((Join-Path $ordinary 'user/savedata/1/CUSA00207/SPRJ0005')))
 Assert ($desktop.Window.FindName('SaveSummary').Text -like '*Hunter*') 'Inspect omitted role'
 Assert $desktop.Window.FindName('ImportSaves').IsEnabled 'Inspect disabled import'
 Await (Call 'Transfer' @($true))
 $verify=@'
import sys,json,hashlib
from pathlib import Path
root,ordinary=map(Path,sys.argv[1:])
def files(p):return {str(f.relative_to(p)):hashlib.sha256(f.read_bytes()).hexdigest() for f in p.rglob('*') if f.is_file()}
assert files(root/'data/user/savedata')==files(ordinary/'user/savedata')
assert files(ordinary)==json.loads((root/'source-before.json').read_text())
assert list((root/'save-backups').rglob('before-import-sentinel'))
assert not (root/'.save-import.lock').exists()
'@
 Py $verify @($root,$ordinary)
 [IO.File]::WriteAllText($profile,'{ broken json',$utf8);$rejected=$false;try{Call 'SaveOrdinarySource' @('new')}catch{$rejected=$true};Assert $rejected 'Path-only edit overwrote invalid profile'
 $desktop.Window.FindName('GamePath').Text=Join-Path $ordinary 'eboot.bin';$desktop.Window.FindName('GraphicsPath').Text=Join-Path $ordinary 'bbport.ini'
 foreach($n in @('Shadnet','Webapi','Community','Relay')){$desktop.Window.FindName($n).Text='synthetic.invalid'}
 $desktop.Window.FindName('Account').Text='synthetic';$desktop.Window.FindName('Password').Password='synthetic';$desktop.Window.FindName('PublicOnline').IsChecked=$false
 Call 'SaveProfile'
 Assert ($parser.DeserializeObject([IO.File]::ReadAllText($profile))['install'] -eq $ordinary) 'Explicit SaveProfile did not repair invalid JSON'
 [IO.File]::WriteAllText((Join-Path $OutputDirectory 'result.txt'),'PASS: actual WPF/CLI uninitialized preview, native first-import No, initialized inspect/import, preservation and source unchanged, manual clear, Busy, invalid-profile explicit recovery. Folder picker bypassed by reflection; no real game.',$utf8)
}catch{[IO.File]::WriteAllText((Join-Path $OutputDirectory 'failure.txt'),($_|Out-String),$utf8);throw}
finally{if($desktop){$desktop.Window.Close()};$env:APPDATA=$oldApp;$env:PYTHONHOME=$oldHome;$env:PYTHONUTF8=$oldUtf8}
