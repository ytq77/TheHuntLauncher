// SPDX-License-Identifier: GPL-2.0-or-later
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using Microsoft.Win32;

public sealed class DesktopWindow
{
    readonly string root;
    readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength=24*1024*1024 };
    readonly Dictionary<string,FrameworkElement> ui = new Dictionary<string,FrameworkElement>();
    readonly Dictionary<string,Control> featureControls = new Dictionary<string,Control>();
    readonly List<Expander> expanders = new List<Expander>();
    readonly TrophySession trophies = new TrophySession();
    readonly Dictionary<int,BitmapSource> images = new Dictionary<int,BitmapSource>();
    readonly TrophyToast toast;
    readonly Dictionary<int,Dictionary<string,object>> trophyCatalog = new Dictionary<int,Dictionary<string,object>>();
    readonly DispatcherTimer timer = new DispatcherTimer { Interval=TimeSpan.FromSeconds(1) };
    readonly Dictionary<string,string> conversions = new Dictionary<string,string> {
        {"ConversionNew","create"},{"ConversionBaseline","baseline"},{"ConversionInspect","inspect"},{"ConversionConvert","convert"},
        {"ConversionVerify","verify"},{"ConversionExport","export"},{"ConversionStartExport","start-export"},{"ConversionStartReplace","start-replace"}};
    Dictionary<string,object> catalog, savedFeatures, inspection, conversionJob;
    Process worker;
    bool operation, featuresDirty, closed;
    Task<bool> trophyRead;
    string iconGame, version="0.1.0-preview.17";
    DateTime startedAt;
    string noticeSession;
    int noticeSequence;
    public Window Window { get; private set; }
    public bool Busy { get { return operation || worker != null; } }
    T Get<T>(string name) where T:FrameworkElement {
        FrameworkElement value;
        if(!ui.TryGetValue(name,out value)) { value=(FrameworkElement)Window.FindName(name); if(value==null) throw new InvalidDataException("Missing UI control: "+name); ui[name]=value; }
        return (T)value;
    }
    static Dictionary<string,object> Map(params object[] pairs) { var d=new Dictionary<string,object>(); for(int i=0;i<pairs.Length;i+=2) d[(string)pairs[i]]=pairs[i+1]; return d; }
    static string Str(Dictionary<string,object> d,string key) { object v; return d!=null && d.TryGetValue(key,out v) ? Convert.ToString(v) : ""; }
    static bool Flag(Dictionary<string,object> d,string key) { object v; return d!=null && d.TryGetValue(key,out v) && v is bool && (bool)v; }
    string Text(string n) { return Get<TextBox>(n).Text.Trim(); }
    void Say(string value) { Get<TextBlock>("StatusText").Text=value; }
    public void Page(string name) {
        foreach(string n in new [] {"Home","Settings","Feature","Server","Save","Trophy"}) {
            Get<Grid>(n+"Page").Visibility=n==name ? Visibility.Visible:Visibility.Collapsed;
            Get<RadioButton>(n+"Nav").IsChecked=n==name;
        }
    }
    void Click(string name,Action action) { Get<Button>(name).Click+=delegate { try { action(); } catch(Exception e) { Error(e.Message); } }; }
    void AsyncClick(string name,Func<Task> action) { Get<Button>(name).Click+=async delegate { try { await action(); } catch(Exception e) { Error(e.Message); } }; }
    void Error(string message) { Say(message); MessageBox.Show(Window,message,"The Hunt",MessageBoxButton.OK,MessageBoxImage.Warning); }
    bool Confirm(string message,string title) { return MessageBox.Show(Window,message,title,MessageBoxButton.YesNo,MessageBoxImage.Question,MessageBoxResult.No)==MessageBoxResult.Yes; }
    string PathAt(string name) { return Path.Combine(root,name); }
    Dictionary<string,object> ReadJson(string path) { return json.Deserialize<Dictionary<string,object>>(File.ReadAllText(path,Encoding.UTF8)); }
    void WriteJson(string path,object value) {
        string temp=path+".tmp"; File.WriteAllText(temp,json.Serialize(value),new UTF8Encoding(false));
        if(File.Exists(path)) File.Replace(temp,path,null); else File.Move(temp,path);
    }
    public DesktopWindow(string directory) {
        root=Path.GetFullPath(directory);
        Window=HuntTheme.Load(File.ReadAllText(PathAt("Launcher.xaml"),Encoding.UTF8),root);
        HuntTypography.Bind(Window,root);
        ((Panel)Window.Content).Background=Window.Background;
        TextOptions.SetTextFormattingMode(Window,TextFormattingMode.Display);
        toast=new TrophyToast(Window,GameHandle);
        foreach(Dictionary<string,object> row in (System.Collections.IEnumerable)json.DeserializeObject(File.ReadAllText(PathAt("trophy-catalog.json"),Encoding.UTF8))) trophyCatalog[Convert.ToInt32(row["id"])]=row;
        if(File.Exists(PathAt("TheHunt.ico"))) Window.Icon=BitmapFrame.Create(new Uri(PathAt("TheHunt.ico")));
        if(File.Exists(PathAt("BUILD-INFO.json"))) version=Str(ReadJson(PathAt("BUILD-INFO.json")),"version");
        foreach(string n in new [] {"Home","Settings","Feature","Server","Save","Trophy"}) {
            string page=n;
            Get<RadioButton>(n+"Nav").Click+=async delegate { Page(page); if(page=="Trophy") await RefreshTrophies(); };
        }
        Click("HomeTrophies",delegate { Page("Trophy"); RefreshInBackground(); });
        AsyncClick("RefreshTrophies",async delegate { await RefreshTrophies(); });
        Click("TestTrophyToast",delegate {
            bool gameActive=worker!=null && !worker.HasExited;
            if(gameActive && GameHandle()==IntPtr.Zero) { Say("尚未找到当前游戏窗口，加载完成后再测试提示。"); return; }
            BitmapSource icon; images.TryGetValue(7,out icon);
            toast.Enqueue("武器大师", "银奖杯 · 测试提示，不改变记录",icon,true,gameActive);
            Say(gameActive ? "测试提示已排队，切回游戏查看。":"正在预览奖杯提示。");
        });
        foreach(string name in new [] {"TrophyAll","TrophyEarned","TrophyLocked"}) Get<RadioButton>(name).Click+=delegate { DrawTrophies(); };
        Click("PickInstall",delegate { if(!Pick("InstallPath","Bloodborne.exe|Bloodborne.exe")) return; string ini=Path.Combine(Path.GetDirectoryName(Text("InstallPath")),"bbport.ini"); if(File.Exists(ini)) Get<TextBox>("GraphicsPath").Text=ini; });
        Click("PickGame",delegate { Pick("GamePath","eboot.bin|eboot.bin"); });
        Click("PickGraphics",delegate { Pick("GraphicsPath","bbport.ini|bbport.ini"); });
        Click("PickAdminIdentity",delegate { Pick("AdminIdentity","所有文件|*.*"); });
        Click("PickAdminKnownHosts",delegate { Pick("AdminKnownHosts","所有文件|*.*"); });
        Click("SaveSettings",delegate { RequireIdle(); SaveProfile(); Say("设置已保存。"); Page("Home"); });
        AsyncClick("PublicVerify",VerifyPublic);
        Click("PublicForget",delegate {
            RequireIdle(); string path=PathAt(@".private\public-online.clixml"); if(File.Exists(path)) File.Delete(path);
            Get<TextBox>("PublicAccount").Clear(); Get<PasswordBox>("PublicPassword").Clear();
            Get<TextBlock>("PublicStatus").Text="公共账号已移除；如需公共内容，请重新填写账号。";
        });
        AsyncClick("Launch",Launch);
        Click("OpenLogs",delegate { OpenFolder(Directory.Exists(PathAt(@"data\user")) ? PathAt(@"data\user"):root); });
        Click("OpenSaveBackups",delegate { if(!OpenFolder(PathAt("save-backups"))) Get<TextBlock>("SaveSummary").Text="还没有导入备份。首次替换现有无缝存档时会自动创建。"; });
        AsyncClick("RefreshSaves",delegate { return Transfer(false); });
        AsyncClick("ImportSaves",async delegate {
            if(inspection==null) throw new InvalidOperationException("请先读取普通版存档信息。");
            if(Confirm("将普通版全部角色进度导入无缝版。\n\n来源："+Str(inspection,"source")+"\n目标："+Str(inspection,"destination")+"\n\n现有无缝存档及已完成的辅助记录会备份、隔离，再整体替换。未完成的世界恢复或共享进度会阻止导入。普通版保持原样。继续？","导入存档")) await Transfer(true);
        });
        Click("OpenLocalServer",delegate { string entry=PathAt(@"server\Server.exe"); if(!File.Exists(entry)) throw new FileNotFoundException("缺少内置服务端，请使用完整安装包。"); Process.Start(new ProcessStartInfo(entry) {WorkingDirectory=Path.GetDirectoryName(entry),UseShellExecute=true}); });
        Click("UseLocalServer",UseLocalServer);
        AsyncClick("AdminRefresh",delegate { return Admin("status"); });
        AsyncClick("AdminCreate",delegate { return Admin("create"); });
        AsyncClick("AdminVerify",delegate { return Admin("verify"); });
        foreach(var pair in conversions) { string action=pair.Value; AsyncClick(pair.Key,delegate { return ConvertSave(action); }); }
        Click("ConversionOpen",delegate { OpenFolder(Str(conversionJob,"directory")); });
        Click("ConversionOpenOrdinaryBackup",delegate { OpenFolder(ConversionBackup()); });
        Click("CheckUpdates",delegate { HuntUpdates.Show(Window,root,"TheHuntLauncher",version,CanUpdate); });
        LoadProfile(); BuildFeatures(); LoadAdmin(); LoadConversion();
        Get<TextBlock>("DestinationSavePath").Text=PathAt(@"data\user\savedata");
        timer.Tick+=delegate { Poll(); };
        Window.Closing+=delegate(object sender,System.ComponentModel.CancelEventArgs e) {
            if(Busy) { e.Cancel=true; Say("请先正常退出游戏，并等待保存或管理操作完成后关闭。"); }
            else { closed=true; timer.Stop(); toast.Dispose(); }
        };
        Page("Home"); SetBusy();
    }
    public void Start() {
        Window.Loaded+=async delegate {
            timer.Start(); await RefreshTrophies();
            try { HuntUpdates.WatchStartup(Window,Get<Button>("CheckUpdates"),root,"TheHuntLauncher",version); }
            catch { Get<Button>("CheckUpdates").ToolTip="自动检查暂不可用，可手动检查更新。"; }
        };
    }
    bool Pick(string field,string filter) { if(Busy) return false; var d=new OpenFileDialog {Filter=filter}; if(d.ShowDialog(Window)!=true) return false; Get<TextBox>(field).Text=d.FileName; return true; }
    static bool OpenFolder(string path) { if(!Directory.Exists(path)) return false; Process.Start("explorer.exe",Quote(path)); return true; }
    public static string Quote(string value) {
        var b=new StringBuilder("\""); int slashes=0;
        foreach(char c in value) {
            if(c=='\\') { ++slashes; continue; }
            b.Append('\\',c=='"' ? slashes*2+1 : slashes); b.Append(c); slashes=0;
        }
        b.Append('\\',slashes*2); b.Append('"'); return b.ToString();
    }
    bool GameRunning() { var processes=Process.GetProcessesByName("bb-probe"); bool running=processes.Length>0; foreach(var p in processes) p.Dispose(); return running; }
    void RequireIdle() { if(Busy || GameRunning()) throw new InvalidOperationException("请先正常退出游戏，并等待当前操作完成。"); }
    public bool CanUpdate() {
        if(Busy || GameRunning() || (trophyRead!=null && !trophyRead.IsCompleted)) return false;
        foreach(var p in Process.GetProcessesByName("Server")) using(p) { try { if(String.Equals(p.MainModule.FileName,PathAt(@"server\Server.exe"),StringComparison.OrdinalIgnoreCase)) return false; } catch(System.ComponentModel.Win32Exception) { return false; } }
        return true;
    }
    Dictionary<string,object> Profile() {
        foreach(string n in new [] {"InstallPath","GamePath","GraphicsPath"}) if(!File.Exists(Text(n))) throw new InvalidOperationException("请先选择存在的游戏与配置文件。");
        return Map("install",Path.GetDirectoryName(Text("InstallPath")),"game",Path.GetDirectoryName(Text("GamePath")),"graphics_source",Text("GraphicsPath"));
    }
    void SaveProfile() {
        var profile=Profile();
        foreach(string n in new [] {"Shadnet","Webapi","Community","Relay","Account"}) if(Text(n).Length==0) throw new InvalidOperationException("请填写完整的联机地址与账号。");
        string path=PathAt(@".private\account.clixml"), password=Get<PasswordBox>("Password").Password;
        if(password.Length==0 && (!File.Exists(path) || DesktopCredentials.Read(path)[0]!=Text("Account"))) throw new InvalidOperationException("首次使用或更换账号时需要填写密码。");
        if(password.Length>0) { DesktopCredentials.Save(path,Text("Account"),password); Get<PasswordBox>("Password").Clear(); }
        profile["network"]=Map("shadnet",Text("Shadnet"),"webapi",Text("Webapi"),"community",Text("Community"),"relay",Text("Relay"));
        profile["public_online"]=Get<CheckBox>("PublicOnline").IsChecked==true;
        if((bool)profile["public_online"]) SavePublic();
        WriteJson(PathAt("profile.json"),profile); Summary();
    }
    string[] PublicCredential() {
        string name=Text("PublicAccount"), password=Get<PasswordBox>("PublicPassword").Password, path=PathAt(@".private\public-online.clixml");
        if(name.Length==0) throw new InvalidOperationException("请填写 SuperMedo 公共账号，或关闭公共在线内容。");
        if(password.Length>0) return new [] {name,password};
        if(File.Exists(path)) { var saved=DesktopCredentials.Read(path); if(saved[0]==name) return saved; }
        throw new InvalidOperationException("首次使用或更换公共账号时需要填写公共密码。");
    }
    void SavePublic() {
        var account=PublicCredential();
        try { DesktopCredentials.Save(PathAt(@".private\public-online.clixml"),account[0],account[1]); }
        finally { account[1]=null; Get<PasswordBox>("PublicPassword").Clear(); }
        Get<TextBlock>("PublicStatus").Text="公共账号已保存 · 启动时自动连接。";
    }
    async Task VerifyPublic() {
        RequireIdle(); var credential=PublicCredential(); operation=true; SetBusy();
        try {
            Get<TextBlock>("PublicStatus").Text="正在验证公共账号…";
            var result=await Request("public_online.py",Map("name",credential[0],"password",credential[1]),"");
            DesktopCredentials.Save(PathAt(@".private\public-online.clixml"),credential[0],credential[1]);
            Get<PasswordBox>("PublicPassword").Clear(); Get<TextBlock>("PublicStatus").Text=Str(result,"message");
        } catch(Exception error) { Get<TextBlock>("PublicStatus").Text=error.Message; throw; }
        finally { credential[1]=null; operation=false; SetBusy(); }
    }
    void LoadPublic() {
        try {
            string path=PathAt(@".private\public-online.clixml");
            if(!File.Exists(path)) return;
            Get<TextBox>("PublicAccount").Text=DesktopCredentials.Read(path)[0];
            Get<TextBlock>("PublicStatus").Text="公共账号已保存 · 启动时自动连接。";
        } catch { Get<TextBlock>("PublicStatus").Text="本机公共凭据无法读取，请重新填写账号密码。"; }
    }
    void LoadProfile() {
        LoadPublic();
        try {
            if(!File.Exists(PathAt("profile.json"))) return;
            var p=ReadJson(PathAt("profile.json"));
            Get<TextBox>("InstallPath").Text=Path.Combine(Str(p,"install"),"Bloodborne.exe");
            Get<TextBox>("GamePath").Text=Path.Combine(Str(p,"game"),"eboot.bin"); Get<TextBox>("GraphicsPath").Text=Str(p,"graphics_source");
            if(p.ContainsKey("network")) { var net=(Dictionary<string,object>)p["network"]; foreach(string n in new [] {"Shadnet","Webapi","Community","Relay"}) Get<TextBox>(n).Text=Str(net,n.ToLowerInvariant()); }
            if(File.Exists(PathAt(@".private\account.clixml"))) Get<TextBox>("Account").Text=DesktopCredentials.Read(PathAt(@".private\account.clixml"))[0];
            Get<CheckBox>("PublicOnline").IsChecked=!p.ContainsKey("public_online") || Flag(p,"public_online");
            Summary(); Say("已加载本机配置，可以启动游戏。");
        } catch { Say("配置或本机凭据无法读取，请重新填写；存档仍在 data 目录。"); }
    }
    void Summary() {
        Get<TextBlock>("AccountSummary").Text=Text("Account").Length>0 ? Text("Account"):"尚未设置";
        Get<TextBlock>("ServerSummary").Text=Text("Shadnet");
        if(File.Exists(Text("GraphicsPath"))) {
            var values=new Dictionary<string,string>();
            foreach(string line in File.ReadAllLines(Text("GraphicsPath"))) { var m=Regex.Match(line,@"^\s*(output_res|upscaler)\s*=(.*)$"); if(m.Success) values[m.Groups[1].Value]=m.Groups[2].Value.Trim(); }
            Get<TextBlock>("GraphicsSummary").Text=String.Join(" · ",values.Values.ToArray());
        }
    }
    void SetBusy() {
        foreach(string n in new [] {"UseLocalServer","Launch","SaveSettings","RefreshSaves","SaveFeatures","RecommendFeatures","FeatureItems","PublicFields","AdminFields","AdminRefresh","AdminCreate","AdminVerify","PickInstall","PickGame","PickGraphics"}) Get<FrameworkElement>(n).IsEnabled=!Busy;
        Get<Button>("ImportSaves").IsEnabled=!Busy && inspection!=null;
        foreach(var p in conversions) Get<Button>(p.Key).IsEnabled=!Busy && (p.Value=="create" || p.Value.StartsWith("start-") || Str(conversionJob,"next")==p.Value);
        foreach(string n in new [] {"ConversionSameRole","ConversionClearSlots","ConversionConfirmed"}) Get<CheckBox>(n).IsEnabled=!Busy;
        Get<Button>("ConversionOpenOrdinaryBackup").IsEnabled=!Busy && Directory.Exists(ConversionBackup());
    }
    ProcessStartInfo Python(string script,string arguments) {
        string bin=PathAt(@"runtime\bin"), python=Path.Combine(bin,"python.exe");
        if(!File.Exists(python)) throw new FileNotFoundException("请下载完整安装包；源码目录不启动游戏或管理操作。");
        var info=new ProcessStartInfo(python,"-B "+Quote(PathAt(script))+(arguments.Length>0 ? " "+arguments:"")) {
            WorkingDirectory=root,UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true,
            StandardOutputEncoding=Encoding.UTF8,StandardErrorEncoding=Encoding.UTF8 };
        info.EnvironmentVariables["PYTHONHOME"]=PathAt("runtime"); info.EnvironmentVariables["PYTHONUTF8"]="1";
        info.EnvironmentVariables.Remove("PYTHONPATH"); info.EnvironmentVariables["PATH"]=bin+";"+Environment.GetEnvironmentVariable("PATH");
        return info;
    }
    async Task<Dictionary<string,object>> Request(string script,object request,string arguments) {
        var info=Python(script,arguments); info.RedirectStandardInput=request!=null;
        using(var p=Process.Start(info)) {
            var output=p.StandardOutput.ReadToEndAsync(); var errors=p.StandardError.ReadToEndAsync();
            if(request!=null) { await p.StandardInput.WriteAsync(json.Serialize(request)); p.StandardInput.Close(); }
            await Task.Run(()=>p.WaitForExit()); string data=await output; await errors;
            var result=json.Deserialize<Dictionary<string,object>>(data);
            if(p.ExitCode!=0 || !Flag(result,"ok")) throw new InvalidOperationException(Str(result,"error").Length>0 ? Str(result,"error"):"操作未完成，请保留日志。");
            return result;
        }
    }
    async Task Launch() {
        RequireIdle();
        if(featuresDirty) { Page("Feature"); throw new InvalidOperationException("请先保存功能页中的变更，再启动游戏。"); }
        foreach(string n in new [] {".save-conversion.lock",".save-import.lock"}) if(File.Exists(PathAt(n))) throw new InvalidOperationException("存档操作正在进行或上次未完整结束，请先检查存档页和备份。");
        SaveProfile(); operation=true; SetBusy(); Exception failure=null;
        try {
            bool fresh=await RefreshTrophies(true); trophies.Begin(fresh); DrawTrophies();
            var credential=DesktopCredentials.Read(PathAt(@".private\account.clixml"));
            var info=Python("client.py",""); info.EnvironmentVariables["BBSC_ACCOUNT"]=credential[0]; info.EnvironmentVariables["BBSC_PASSWORD"]=credential[1];
            if(Get<CheckBox>("PublicOnline").IsChecked==true) {
                var publicAccount=DesktopCredentials.Read(PathAt(@".private\public-online.clixml"));
                info.EnvironmentVariables["THEHUNT_PUBLIC_ACCOUNT"]=publicAccount[0]; info.EnvironmentVariables["THEHUNT_PUBLIC_PASSWORD"]=publicAccount[1]; publicAccount[1]=null;
            }
            noticeSession=Guid.NewGuid().ToString("N"); noticeSequence=0; toast.Stop();
            info.EnvironmentVariables["THEHUNT_UI_SESSION"]=noticeSession;
            WriteJson(PathAt("launcher-state.json"),Map("phase","preparing","message","正在准备独立环境…"));
            startedAt=DateTime.Now;
            using(var log=new StreamWriter(PathAt("startup.log"),false,new UTF8Encoding(false))) {
                worker=Process.Start(info); info.EnvironmentVariables.Remove("BBSC_PASSWORD"); credential[1]=null;
                info.EnvironmentVariables.Remove("THEHUNT_PUBLIC_PASSWORD");
                Get<Button>("Launch").Content="游戏运行中"; Get<TextBlock>("KitStatus").Text="正在等待模组加载…"; Page("Home");
                var stdout=Pump(worker.StandardOutput,log); var stderr=Pump(worker.StandardError,log);
                await Task.Run(()=>worker.WaitForExit()); await Task.WhenAll(stdout,stderr);
                Poll();
                string phase=File.Exists(PathAt("launcher-state.json")) ? Str(ReadJson(PathAt("launcher-state.json")),"phase"):"";
                if(phase!="exited" && phase!="error") Say("启动进程已结束，请查看 startup.log。");
            }
        } catch(Exception error) { failure=error; }
        finally {
            if(worker!=null) { worker.Dispose(); worker=null; }
            toast.Stop(); noticeSession=null;
        }
        // Refresh even when exit recovery reports an error. Trophy failure has its own notice.
        trophies.Exited();
        try { await RefreshTrophies(true); }
        finally {
            operation=false; SetBusy(); Get<Button>("Launch").Content="启动游戏  →";
        }
        if(failure!=null) System.Runtime.ExceptionServices.ExceptionDispatchInfo.Capture(failure).Throw();
    }
    static async Task Pump(StreamReader reader,StreamWriter log) {
        string line; while((line=await reader.ReadLineAsync())!=null) { lock(log) { log.WriteLine(line); log.Flush(); } }
    }
    void Poll() {
        if(worker==null) return;
        if(!worker.HasExited) PollTrophyNotices();
        try {
            string path=PathAt(@"data\user\public-online-state.json");
            if(File.Exists(path)) {
                var state=ReadJson(path);
                if(Str(state,"ui_session")==noticeSession) Get<TextBlock>("PublicStatus").Text=!Flag(state,"account_connected") ? "公共账号连接已断开。": Convert.ToInt32(state["failures"])>0 ? "公共内容请求失败，请检查网络或重新启动。":Flag(state,"game_authenticated") ? "公共在线已连接 · 私服无缝保持独立。":"公共账号已连接，等待游戏登录。";
            }
        } catch(IOException) {} catch(ArgumentException) {}
        try { if(File.Exists(PathAt("launcher-state.json"))) Say(Str(ReadJson(PathAt("launcher-state.json")),"message")); } catch(IOException) {} catch(ArgumentException) {}
        try {
            string logs=PathAt(@"mod\logs");
            if(!Directory.Exists(logs)) return;
            string file=Directory.GetFiles(logs,"bbsc-mod-*.log").Where(f=>File.GetLastWriteTime(f)>=startedAt).OrderByDescending(File.GetLastWriteTime).FirstOrDefault();
            if(file==null) return;
            var match=Regex.Match(File.ReadAllText(file),@"BBSC kit initialization: ready after (\d+) ms");
            if(match.Success) Get<TextBlock>("KitStatus").Text="五件猎具已准备完成 · "+Math.Round(Double.Parse(match.Groups[1].Value)/1000,1)+" 秒";
        } catch(IOException) {}
    }
    IntPtr GameHandle() {
        foreach(var p in Process.GetProcessesByName("bb-probe")) using(p) {
            try { if(String.Equals(p.MainModule.FileName,PathAt(@"bin\bb-probe.exe"),StringComparison.OrdinalIgnoreCase)) return p.MainWindowHandle; }
            catch(System.ComponentModel.Win32Exception) {} catch(InvalidOperationException) {}
        }
        return IntPtr.Zero;
    }
    void PollTrophyNotices() {
        if(String.IsNullOrEmpty(noticeSession)) return;
        try {
            string path=PathAt(@"data\user\ui-trophy-events.json"); if(!File.Exists(path)) return;
            var data=ReadJson(path); if(Str(data,"session")!=noticeSession) return;
            bool changed=false;
            foreach(Dictionary<string,object> row in (System.Collections.IEnumerable)data["events"]) {
                int sequence=Convert.ToInt32(row["seq"]),id=Convert.ToInt32(row["id"]);
                if(sequence<=noticeSequence) continue;
                noticeSequence=sequence;
                Dictionary<string,object> item; if(!trophyCatalog.TryGetValue(id,out item)) continue;
                BitmapSource icon; images.TryGetValue(id,out icon);
                var grades=new Dictionary<string,string>{{"platinum","白金奖杯"},{"gold","金奖杯"},{"silver","银奖杯"},{"bronze","铜奖杯"}};
                toast.Enqueue(Str(item,"name"),grades[Str(item,"grade")],icon,false); changed=true;
            }
            if(changed) RefreshInBackground();
        } catch(IOException) {} catch(ArgumentException) {} catch(KeyNotFoundException) {} catch(InvalidCastException) {} catch(FormatException) {}
    }
    async Task Transfer(bool apply) {
        RequireIdle(); if(!File.Exists(Text("InstallPath"))) throw new InvalidOperationException("请先选择普通版 Bloodborne.exe。");
        string expected=Str(inspection,"source"); if(apply && expected.Length==0) throw new InvalidOperationException("请先读取存档信息。");
        inspection=null; operation=true; SetBusy();
        try {
            string result=PathAt("save-transfer-result.json"); if(File.Exists(result)) File.Delete(result);
            string arguments="--install "+Quote(Path.GetDirectoryName(Text("InstallPath")))+" --result "+Quote(result)+(apply ? " --apply --expected-source "+Quote(expected):"");
            Get<TextBlock>("SaveSummary").Text=apply ? "正在校验并导入存档…":"正在读取存档信息…";
            using(var p=Process.Start(Python("save_transfer.py",arguments))) {
                var output=p.StandardOutput.ReadToEndAsync(); var errors=p.StandardError.ReadToEndAsync(); await Task.Run(()=>p.WaitForExit()); await Task.WhenAll(output,errors);
                var value=ReadJson(result); if(!Flag(value,"ok") || p.ExitCode!=0) throw new InvalidOperationException(Str(value,"error"));
                Get<TextBlock>("SourceSavePath").Text=Str(value,"source"); Get<TextBlock>("DestinationSavePath").Text=Str(value,"destination");
                if(apply) { Get<TextBlock>("SaveSummary").Text="导入完成。\n"+Str(value,"notice")+"\n备份："+Str(value,"backup"); await RefreshTrophies(); }
                else { inspection=value; Get<TextBlock>("SaveSummary").Text=Str(value,"file_count")+" 个文件 · 最近保存："+Str(value,"latest_write"); }
            }
        } catch(Exception e) { Get<TextBlock>("SaveSummary").Text=e.Message; throw; }
        finally { operation=false; SetBusy(); }
    }
    void UseLocalServer() {
        RequireIdle(); string address="127.0.0.1";
        if(File.Exists(PathAt(@"server\server.json"))) address=Str(ReadJson(PathAt(@"server\server.json")),"address");
        IPAddress ip; if(!IPAddress.TryParse(address,out ip) || ip.AddressFamily!=AddressFamily.InterNetwork || address=="0.0.0.0") throw new InvalidOperationException("请先在开服窗口配置有效的本机 IPv4 地址。");
        Get<TextBox>("Shadnet").Text=address+":31413"; Get<TextBox>("Webapi").Text="http://"+address+":31415";
        Get<TextBox>("Community").Text="http://"+address+":20443"; Get<TextBox>("Relay").Text=address+":20444"; Page("Settings");
        Say("已填入本地服务地址；请填写本服务创建的账号并保存。127.0.0.1 仅限同机，同伴使用局域网或 VPN 地址。");
    }
    void LoadAdmin() {
        try { if(!File.Exists(PathAt("server-admin.json"))) return; var d=ReadJson(PathAt("server-admin.json"));
            foreach(var p in new [] {new [] {"AdminTarget","target"},new [] {"AdminPort","port"},new [] {"AdminDirectory","directory"},new [] {"AdminIdentity","identity"},new [] {"AdminKnownHosts","known_hosts"}}) Get<TextBox>(p[0]).Text=Str(d,p[1]);
            Get<CheckBox>("AdminSudo").IsChecked=Flag(d,"sudo");
        } catch { Get<TextBlock>("AdminStatus").Text="管理员连接设置无法读取，请重新填写。"; }
    }
    async Task Admin(string action) {
        RequireIdle(); int port;
        if(!Int32.TryParse(Text("AdminPort"),out port) || port<1 || port>65535) throw new InvalidOperationException("SSH 端口必须在 1–65535。");
        if(!Regex.IsMatch(Text("AdminTarget"),@"^[A-Za-z0-9_][A-Za-z0-9_.@-]{0,200}$") || !Regex.IsMatch(Text("AdminDirectory"),@"^/opt/[A-Za-z0-9_./-]+$") || Text("AdminDirectory").Split('/').Contains("..")) throw new InvalidOperationException("请核对 SSH 目标和 /opt 下的部署目录。");
        var conn=Map("target",Text("AdminTarget"),"port",port,"directory",Text("AdminDirectory"),"identity",Text("AdminIdentity"),"known_hosts",Text("AdminKnownHosts"),"sudo",Get<CheckBox>("AdminSudo").IsChecked==true);
        var request=Map("action",action,"connection",conn);
        if(action!="status") {
            string name=Text("AdminName"), password=Get<PasswordBox>("AdminPassword").Password;
            if(!Regex.IsMatch(name,@"^[A-Za-z0-9_-]{3,16}$") || password.Length==0) throw new InvalidOperationException("请填写 3–16 位账号名及密码。");
            if(action=="create" && !Regex.IsMatch(Text("AdminEmail"),@"^[^\s@]+@[^\s@]+\.[^\s@]+$")) throw new InvalidOperationException("新建账号需要不重复的有效邮箱标识。");
            if(!Confirm(action=="create" ? "将在 "+Text("AdminTarget")+" 新建账号 "+name+"。先备份数据库，再注册并验证。继续？":"这会短暂登录指定账号验证密码。请先退出该账号在所有电脑上的游戏。继续？","联机账号")) return;
            request["fields"]=Map("name",name,"password",password,"email",Text("AdminEmail"),"confirmed",true);
        }
        operation=true; SetBusy(); Get<TextBlock>("AdminStatus").Text="正在通过 SSH 检查服务器…";
        try {
            WriteJson(PathAt("server-admin.json"),conn); var r=await Request("server_admin.py",request,"");
            if(r.ContainsKey("accounts")) Get<TextBlock>("AdminAccounts").Text=String.Join("\n",((System.Collections.IEnumerable)r["accounts"]).Cast<Dictionary<string,object>>().Select(a=>Str(a,"id")+" · "+Str(a,"name")+" · "+(Flag(a,"banned")?"已封禁":Flag(a,"admin")?"管理员":"普通账号")).ToArray());
            Get<TextBlock>("AdminStatus").Text=action=="status" ? "服务器："+Str(r,"server_hostname")+"\n"+String.Join("\n",((System.Collections.IEnumerable)r["services"]).Cast<Dictionary<string,object>>().Select(s=>Str(s,"Service")+"："+Str(s,"State")+" / "+Str(s,"Health")).ToArray())+"\n"+Str(r,"notice") : Str(r,"message")+"\n"+Str(r,"backup");
        } catch(Exception e) { Get<TextBlock>("AdminStatus").Text=e.Message; throw; }
        finally { Get<PasswordBox>("AdminPassword").Clear(); operation=false; SetBusy(); }
    }
    string ConversionBackup() { string p=Str(conversionJob,"ordinary_backup"); return p.Length>0 ? p : Str(conversionJob,"directory").Length>0 ? Path.Combine(Str(conversionJob,"directory"),@"backup\ordinary\savedata"):""; }
    void ShowConversion(Dictionary<string,object> job) {
        conversionJob=job; Get<TextBlock>("ConversionStatus").Text=Str(job,"message");
        Get<TextBlock>("ConversionPath").Text=Str(job,"export").Length>0 ? Str(job,"export"):Str(job,"directory");
    }
    void LoadConversion() {
        try {
            if(!File.Exists(PathAt("conversion-current.json"))) return;
            string id=Str(ReadJson(PathAt("conversion-current.json")),"job"); if(!Regex.IsMatch(id,"^[0-9a-f]{32}$")) return;
            string folder=PathAt(@"conversion-jobs\"+id); var d=ReadJson(Path.Combine(folder,"job.json")); d["directory"]=folder;
            var next=new Dictionary<string,string>{{"created","baseline"},{"baselined","inspect"},{"inspected","convert"},{"converted","verify"},{"awaiting-confirmation","export"}};
            d["next"]=next.ContainsKey(Str(d,"state")) ? next[Str(d,"state")] : null;
            if(Str(d,"state")=="running") d["message"]="检测到未结束的诊断记录。若游戏和旧操作均已退出，请新建任务；原始存档未替换。";
            ShowConversion(d);
        } catch { Get<TextBlock>("ConversionStatus").Text="转换记录暂不可用，请保留 conversion-jobs 目录。"; }
    }
    async Task ConvertSave(string action) {
        RequireIdle(); var request=Map("action",action);
        if(action=="create" || action.StartsWith("start-")) {
            if(Get<CheckBox>("ConversionSameRole").IsChecked!=true) throw new InvalidOperationException("请先确认两份存档对应同一角色。");
            request["profile"]=Profile(); request["same_role"]=true;
            if(action!="create") { request["action"]="begin"; request["flow"]="offline-v1"; request["output"]=action=="start-replace"?"replace":"export"; }
            Get<CheckBox>("ConversionClearSlots").IsChecked=false; Get<CheckBox>("ConversionConfirmed").IsChecked=false;
        } else {
            if(Str(conversionJob,"id").Length==0) throw new InvalidOperationException("请先新建诊断任务。"); request["job"]=conversionJob["id"];
            if(action=="convert") { if(Get<CheckBox>("ConversionClearSlots").IsChecked!=true) throw new InvalidOperationException("请先卸下无缝道具快捷栏和随身物品引用，再勾选确认。"); request["cleared"]=true; }
            if(action=="export") { if(Get<CheckBox>("ConversionConfirmed").IsChecked!=true) throw new InvalidOperationException("请先用普通版重载验证并勾选确认。"); request["confirmed"]=true; }
        }
        operation=true; SetBusy(); Get<TextBlock>("ConversionStatus").Text=action.StartsWith("start-") ? "正在备份并离线转换…":"正在准备独立诊断副本…";
        try { var r=await Request("conversion_jobs.py",request,""); ShowConversion((Dictionary<string,object>)r["job"]); await RefreshTrophies(); }
        catch(Exception e) { Get<TextBlock>("ConversionStatus").Text=e.Message; throw; }
        finally { operation=false; SetBusy(); }
    }
    void BuildFeatures() {
        catalog=ReadJson(PathAt("feature-catalog.json")); var values=new Dictionary<string,object>();
        foreach(Dictionary<string,object> item in (System.Collections.IEnumerable)catalog["settings"]) if(Str(item,"type")=="bool" || Str(item,"type")=="int") values[Str(item,"id")]=item["default"];
        savedFeatures=new Dictionary<string,object>(values);
        try { if(File.Exists(PathAt("features.json"))) { var d=ReadJson(PathAt("features.json")); if(Convert.ToInt32(d["schema"])!=1) throw new InvalidDataException(); savedFeatures=ValidateFeatures((Dictionary<string,object>)d["values"]); } }
        catch { Get<TextBlock>("FeatureStatus").Text="功能配置无法读取，请恢复推荐后保存。游戏启动仍会核对配置。"; }
        foreach(string group in (System.Collections.IEnumerable)catalog["groups"]) {
            var panel=new StackPanel(); var expander=new Expander {Header=group,Content=panel,Margin=new Thickness(0,0,0,14),Foreground=Brushes.Beige,FontSize=16}; expanders.Add(expander);
            foreach(Dictionary<string,object> item in ((System.Collections.IEnumerable)catalog["settings"]).Cast<Dictionary<string,object>>().Where(i=>Str(i,"category")==group)) {
                string id=Str(item,"id"), type=Str(item,"type"); var body=new StackPanel();
                if(type=="bool") { var c=new CheckBox {Content=Str(item,"title"),IsEnabled=Flag(item,"editable"),IsChecked=Convert.ToBoolean(savedFeatures[id]),FontSize=15}; featureControls[id]=c; body.Children.Add(c); c.Checked+=FeatureChanged; c.Unchecked+=FeatureChanged; }
                else { body.Children.Add(Block(Str(item,"title"),15)); if(type=="int") { var c=new TextBox {Text=Convert.ToString(savedFeatures[id]),Width=90,HorizontalAlignment=HorizontalAlignment.Left,Margin=new Thickness(0,8,0,0)}; featureControls[id]=c; body.Children.Add(c); c.TextChanged+=FeatureChanged; } }
                body.Children.Add(Block(Str(item,"description"),12)); body.Children.Add(Block(Str(item,"scope"),11));
                panel.Children.Add(new Border {Child=body,Background=Brush("#191A1C"),BorderBrush=Brush("#37383A"),BorderThickness=new Thickness(1),CornerRadius=new CornerRadius(6),Padding=new Thickness(16),Margin=new Thickness(0,10,0,0)});
            }
            Get<StackPanel>("FeatureItems").Children.Add(expander);
        }
        Click("SaveFeatures",SaveFeatures);
        Click("RecommendFeatures",delegate { foreach(var p in values) { var c=featureControls[p.Key]; if(c is CheckBox) ((CheckBox)c).IsChecked=(bool)p.Value; else ((TextBox)c).Text=Convert.ToString(p.Value); } featuresDirty=true; Get<TextBlock>("FeatureStatus").Text="已填入推荐方案，保存后生效。"; FeatureSummary(); });
        Click("ExpandFeatures",delegate { bool show=expanders.Any(e=>!e.IsExpanded); foreach(var e in expanders) e.IsExpanded=show; });
        featuresDirty=false; FeatureSummary();
    }
    Dictionary<string,object> ValidateFeatures(Dictionary<string,object> values) {
        var entries=((System.Collections.IEnumerable)catalog["settings"]).Cast<Dictionary<string,object>>().Where(i=>Str(i,"type")=="bool" || Str(i,"type")=="int").ToArray();
        if(values.Keys.Except(entries.Select(i=>Str(i,"id"))).Any()) throw new InvalidDataException("功能配置包含不支持的项目。");
        var resolved=new Dictionary<string,object>();
        foreach(var i in entries) {
            string id=Str(i,"id"); object v=values.ContainsKey(id)?values[id]:i["default"];
            if(Str(i,"type")=="bool" ? !(v is bool) : !(v is int) || (int)v<Convert.ToInt32(i["min"]) || (int)v>Convert.ToInt32(i["max"])) throw new InvalidDataException(Str(i,"title")+"的数值无效。");
            if(!Flag(i,"editable") && !Object.Equals(v,i["default"])) throw new InvalidDataException(Str(i,"title")+"是必要基础功能。"); resolved[id]=v;
        }
        return resolved;
    }
    void FeatureChanged(object sender,RoutedEventArgs e) { featuresDirty=true; Get<TextBlock>("FeatureStatus").Text="有未保存的功能变更。"; FeatureSummary(); }
    void FeatureSummary() { if(featureControls.ContainsKey("shared_progress")) Get<TextBlock>("FeatureSummary").Text="共享进度："+(((CheckBox)featureControls["shared_progress"]).IsChecked==true?"开":"关")+" · 敌人强化："+(((CheckBox)featureControls["scaling"]).IsChecked==true?"开":"关")+" · 队友互伤由纷争之铃控制"; }
    void SaveFeatures() {
        RequireIdle(); var values=new Dictionary<string,object>();
        foreach(var p in featureControls) { if(p.Value is CheckBox) values[p.Key]=((CheckBox)p.Value).IsChecked==true; else { int n; if(!Regex.IsMatch(((TextBox)p.Value).Text,@"^\d+$") || !Int32.TryParse(((TextBox)p.Value).Text,out n)) throw new InvalidDataException("需要填写整数百分比。"); values[p.Key]=n; } }
        values=ValidateFeatures(values);
        if((bool)values["shared_progress"] && !(bool)savedFeatures["shared_progress"] && !Confirm("客机归返后会把房主已有的 Boss、捷径、门和提灯进度追加到客机无缝存档。再次关闭不会撤销。建议使用合作角色，并备份双方存档。\n\n确认开启永久共享进度？","永久共享世界进度")) return;
        WriteJson(PathAt("features.json"),Map("schema",1,"values",values)); savedFeatures=values; featuresDirty=false; Get<TextBlock>("FeatureStatus").Text="功能配置已保存，下次启动生效。"; FeatureSummary();
    }
    static SolidColorBrush Brush(string color) { return new SolidColorBrush((Color)ColorConverter.ConvertFromString(color)); }
    static TextBlock Block(string text,int size) { return new TextBlock {Text=text,FontSize=size,Foreground=Brush("#A6AAA8"),TextWrapping=TextWrapping.Wrap,Margin=new Thickness(0,8,0,0),LineHeight=size+9}; }
    async void RefreshInBackground() { await RefreshTrophies(); }
    public async Task<bool> RefreshTrophies(bool fresh=false) {
        if(trophyRead!=null && !trophyRead.IsCompleted) { bool ok=await trophyRead; if(!fresh) return ok; }
        trophyRead=ReadTrophies(); return await trophyRead;
    }
    async Task<bool> ReadTrophies() {
        Get<Button>("RefreshTrophies").IsEnabled=false;
        try {
            string game=File.Exists(PathAt("profile.json")) ? Str(ReadJson(PathAt("profile.json")),"game") : ""; bool reload=images.Count==0 || game!=iconGame;
            var data=await Request("trophy_view.py",null,reload ? "--icons":"");
            if(reload) { images.Clear(); iconGame=game; foreach(var p in (Dictionary<string,object>)data["images"]) {
                try { byte[] raw=Convert.FromBase64String((string)p.Value); using(var stream=new MemoryStream(raw)) { var image=new BitmapImage(); image.BeginInit(); image.CacheOption=BitmapCacheOption.OnLoad; image.StreamSource=stream; image.DecodePixelWidth=96; image.EndInit(); image.Freeze(); images[Int32.Parse(p.Key)]=image; } } catch(NotSupportedException) {} catch(System.IO.FileFormatException) {}
            } }
            trophies.Accept(data); if(!closed) DrawTrophies(); return true;
        } catch(Exception e) { trophies.Failed(e.Message); if(!closed) DrawTrophies(); return false; }
        finally { if(!closed) Get<Button>("RefreshTrophies").IsEnabled=true; }
    }
    void DrawTrophies() {
        var panel=Get<StackPanel>("TrophyItems"); panel.Children.Clear();
        var data=trophies.Snapshot;
        if(data==null) { Get<TextBlock>("TrophySummary").Text="记录暂不可用"; Get<TextBlock>("TrophyNotice").Text=trophies.Warning; Get<Button>("HomeTrophies").Content="奖杯 · 记录暂不可用"; return; }
        var rows=((System.Collections.IEnumerable)data["trophies"]).Cast<Dictionary<string,object>>().ToArray();
        int count=rows.Count(r=>r["unlocked"]!=null), baseCount=rows.Count(r=>Str(r,"group")=="base" && r["unlocked"]!=null);
        Get<TextBlock>("TrophySummary").Text="已解锁 "+count+" / 40    本体 "+baseCount+" / 34    老猎人 "+(count-baseCount)+" / 6";
        Get<Button>("HomeTrophies").Content="奖杯 · "+count+" / 40"; Get<ProgressBar>("TrophyProgress").Value=count;
        Get<TextBlock>("TrophyNotice").Text=trophies.Warning!=null ? "读取失败，当前显示上次成功结果。"+trophies.Warning :
            (Flag(data,"missing") ? "尚无奖杯记录。":"最近读取："+DateTime.Now.ToString("HH:mm:ss"))+" · "+Str(data,"path")+
            (Convert.ToInt32(data["ignored"])>0 ? " · 已忽略 "+data["ignored"]+" 行无效记录":"")+(Flag(data,"platinum_due") ? "\n白金条件已满足，尚无解锁记录。":"");
        Get<TextBlock>("TrophySession").Text=trophies.Added.Length==0 ? "":"本次新增 "+trophies.Added.Length+" 项："+String.Join("、",rows.Where(r=>trophies.Added.Contains(Convert.ToInt32(r["id"]))).Select(r=>Str(r,"name")).ToArray());
        string prior="";
        foreach(var row in rows) {
            bool earned=row["unlocked"]!=null;
            if(Get<RadioButton>("TrophyEarned").IsChecked==true && !earned || Get<RadioButton>("TrophyLocked").IsChecked==true && earned) continue;
            string group=Str(row,"group"); if(prior!=group) { panel.Children.Add(Block(group=="base"?"BLOODBORNE · 本体":"THE OLD HUNTERS · 老猎人",14)); prior=group; }
            var grid=new Grid(); grid.ColumnDefinitions.Add(new ColumnDefinition {Width=new GridLength(76)}); grid.ColumnDefinitions.Add(new ColumnDefinition());
            BitmapSource source; FrameworkElement icon;
            if(images.TryGetValue(Convert.ToInt32(row["id"]),out source)) icon=new Image {Source=source,Width=60,Height=60,Opacity=earned?1:.30};
            else icon=new TextBlock {Text="♜",FontSize=34,Foreground=Brush("#BBA47A"),VerticalAlignment=VerticalAlignment.Center,HorizontalAlignment=HorizontalAlignment.Center,Opacity=earned?1:.35};
            grid.Children.Add(icon); var body=new StackPanel(); Grid.SetColumn(body,1); grid.Children.Add(body);
            string grade=Str(row,"grade"); var grades=new Dictionary<string,string>{{"platinum","白金"},{"gold","金"},{"silver","银"},{"bronze","铜"}};
            body.Children.Add(new TextBlock {Text=Str(row,"name")+"  ·  "+grades[grade],FontSize=16,Foreground=Brush(earned?"#EAE5DA":"#A09D96"),TextWrapping=TextWrapping.Wrap});
            body.Children.Add(Block(Str(row,"description").Replace("\n",""),12));
            string when="未解锁"; if(earned) { try { when="已解锁 · "+new DateTime(1970,1,1,0,0,0,DateTimeKind.Utc).AddSeconds(Convert.ToDouble(row["unlocked"])).ToLocalTime().ToString("yyyy-MM-dd HH:mm"); } catch(ArgumentOutOfRangeException) { when="已解锁 · 时间超出显示范围"; } }
            body.Children.Add(Block(when,11)); panel.Children.Add(new Border {Child=grid,Padding=new Thickness(16),Margin=new Thickness(0,10,0,0),Background=Brush("#191A1C"),BorderBrush=Brush(earned?"#514534":"#303133"),BorderThickness=new Thickness(1),CornerRadius=new CornerRadius(6)});
        }
    }
    public void Validate(string capture,string page) {
        foreach(string n in conversions.Keys) Get<Button>(n);
        foreach(string n in new [] {"Launch","SaveSettings","RefreshSaves","ImportSaves","OpenLocalServer","UseLocalServer","AdminRefresh","AdminCreate","AdminVerify","SaveFeatures","HomeTrophies","RefreshTrophies"}) Get<Button>(n);
        Page(page); if(page=="Feature") expanders[0].IsExpanded=true;
        if(String.IsNullOrEmpty(capture)) return;
        var view=(FrameworkElement)Window.Content; view.Width=1160; view.Height=800;
        view.Measure(new Size(1160,800)); view.Arrange(new Rect(0,0,1160,800)); view.UpdateLayout();
        var bitmap=new RenderTargetBitmap(1160,800,96,96,PixelFormats.Pbgra32); bitmap.Render(view);
        var encoder=new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap)); using(var file=File.Create(capture)) encoder.Save(file);
    }
}
