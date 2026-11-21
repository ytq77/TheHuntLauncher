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
    Dictionary<string,object> catalog, savedFeatures, inspection, conversionJob, pendingSaveRestore, resolutionPlan, pendingResolution;
    Process worker;
    bool operation, featuresDirty, closed;
    Task<bool> trophyRead;
    string iconGame, version="0.1.0-preview.17";
    DateTime startedAt;
    string noticeSession;
    string recoveryDigest;
    int noticeSequence;
    int resolutionGeneration;
    string currentPage;
    bool drawingBackups, resolutionApplying, resolutionNeeded, drawingTimeline, fillingHost;
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
        if(currentPage!=name) InvalidateResolution();
        currentPage=name;
        if(name=="Home") UpdateReadiness();
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
    // Desktop entry named "The Hunt Launcher" (Windows wraps it as "The Hunt / Launcher");
    // the exe keeps its name so automatic updates keep working.
    void CreateDesktopShortcut() {
        string exe=Path.GetFullPath(PathAt("TheHuntLauncher.exe"));
        if(!File.Exists(exe)) throw new InvalidOperationException("没有找到 TheHuntLauncher.exe，请从完整包目录运行启动器后再创建。");
        string link=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),"The Hunt Launcher.lnk");
        var shellType=Type.GetTypeFromProgID("WScript.Shell"); object shell=Activator.CreateInstance(shellType), shortcut=null;
        var invoke=System.Reflection.BindingFlags.InvokeMethod; var get=System.Reflection.BindingFlags.GetProperty; var set=System.Reflection.BindingFlags.SetProperty;
        try {
            shortcut=shellType.InvokeMember("CreateShortcut",invoke,null,shell,new object[] {link});
            var type=shortcut.GetType();
            if(File.Exists(link)) {
                string target=Convert.ToString(type.InvokeMember("TargetPath",get,null,shortcut,null));
                if(!String.Equals(target,exe,StringComparison.OrdinalIgnoreCase) && !Confirm("桌面上已有「The Hunt Launcher」快捷方式，指向：\n"+target+"\n\n改为指向当前这份启动器吗？","桌面快捷方式")) return;
            }
            type.InvokeMember("TargetPath",set,null,shortcut,new object[] {exe});
            type.InvokeMember("WorkingDirectory",set,null,shortcut,new object[] {Path.GetDirectoryName(exe)});
            type.InvokeMember("IconLocation",set,null,shortcut,new object[] {exe+",0"});
            type.InvokeMember("Description",set,null,shortcut,new object[] {"The Hunt 无缝联机启动器"});
            type.InvokeMember("Save",invoke,null,shortcut,null);
        } finally {
            if(shortcut!=null) System.Runtime.InteropServices.Marshal.FinalReleaseComObject(shortcut);
            System.Runtime.InteropServices.Marshal.FinalReleaseComObject(shell);
        }
        Say("已在桌面创建「The Hunt Launcher」快捷方式。");
    }
    Dictionary<string,object> ReadJson(string path) {
        using(var stream=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete))
        using(var reader=new StreamReader(stream,Encoding.UTF8))
            return json.Deserialize<Dictionary<string,object>>(reader.ReadToEnd());
    }
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
            Get<RadioButton>(n+"Nav").Click+=async delegate { Page(page); if(page=="Trophy") await RefreshTrophies(); if(page=="Save" && !Busy) { try { await RefreshSaveBackups(); } catch(Exception e) { Error(e.Message); } } };
        }
        Click("HomeTrophies",delegate { Page("Trophy"); RefreshInBackground(); });
        Click("HomeFeatureLink",delegate { Page("Feature"); });
        Click("HomeTrophiesAll",delegate { Page("Trophy"); RefreshInBackground(); });
        AsyncClick("HomeSavesLink",async delegate { Page("Save"); if(!Busy) await RefreshSaveBackups(); });
        AsyncClick("RefreshTrophies",async delegate { await RefreshTrophies(); });
        Click("TestTrophyToast",delegate {
            bool gameActive=worker!=null && !worker.HasExited;
            if(gameActive && GameHandle()==IntPtr.Zero) { Say("尚未找到当前游戏窗口，加载完成后再测试提示。"); return; }
            BitmapSource icon; images.TryGetValue(7,out icon);
            toast.Enqueue("武器大师", "银奖杯 · 测试提示，不改变记录",icon,true,gameActive);
            Say(gameActive ? "测试提示已排队，切回游戏查看。":"正在预览奖杯提示。");
        });
        foreach(string name in new [] {"TrophyAll","TrophyEarned","TrophyLocked"}) Get<RadioButton>(name).Click+=delegate { DrawTrophies(); };
        Click("PickInstall",delegate { PickOrdinaryInstall(); });
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
        AsyncClick("ReviewWorldRecovery",ReviewWorldRecovery);
        AsyncClick("CopyWorldRecoveryEvidence",CopyWorldRecoveryEvidence);
        Click("OpenGuide",delegate { Process.Start(new ProcessStartInfo("https://thehunt.iytq.top/") {UseShellExecute=true}); });
        Click("CreateShortcut",CreateDesktopShortcut);
        Click("OpenLogs",delegate { OpenFolder(Directory.Exists(PathAt(@"data\user")) ? PathAt(@"data\user"):root); });
        Click("OpenSaveBackups",delegate { if(!OpenFolder(PathAt("save-backups"))) Get<TextBlock>("SaveSummary").Text="还没有存档备份。启动前备份、恢复前备份和导入备份都保存在此目录。"; });
        AsyncClick("RefreshSaveBackups",RefreshSaveBackups);
        AsyncClick("RestoreSaveBackup",RestoreSaveBackup);
        AsyncClick("ResumeSaveRestore",ResumeSaveRestore);
        AsyncClick("RefreshSaveResolution",RefreshSaveResolution);
        AsyncClick("ResumeSaveResolution",ResumeSaveResolution);
        AsyncClick("AbandonSaveResolution",AbandonSaveResolution);
        Click("CancelSaveResolution",delegate { if(resolutionApplying) { Say("选择已开始处理，不能当作取消；请等待结果，再检查能否继续原事务。"); return; } InvalidateResolution(); Get<TextBlock>("SaveResolutionStatus").Text="这次选择已取消；存档没有因此改变。重新检查后才能选择。"; SetBusy(); });
        Get<ComboBox>("SaveBackupList").SelectionChanged+=delegate { if(!drawingBackups) InvalidateResolution(); ShowSelectedBackup(); SetBusy(); };
        Get<TextBox>("InstallPath").TextChanged+=delegate { inspection=null; InvalidateResolution(); SetBusy(); };
        AsyncClick("RefreshSaves",delegate { return Transfer(false); });
        AsyncClick("PickSaveFolder",PickSaveFolder);
        AsyncClick("ClearSaveFolder",ClearOrdinarySource);
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
        if(File.Exists(PathAt("BUILD-INFO.json"))) Get<TextBlock>("VersionLabel").Text=version;
        foreach(string n in new [] {"ReadyGameAction","ReadyServerAction","ReadyAccountAction","ReadyPublicAction"}) Click(n,delegate { Page("Settings"); });
        Get<TextBox>("ServerHost").TextChanged+=delegate { FillServerHost(); };
        foreach(string n in new [] {"Shadnet","Webapi","Community","Relay","Account"}) Get<TextBox>(n).TextChanged+=delegate { UpdateReadiness(); };
        Get<CheckBox>("PublicOnline").Checked+=delegate { UpdateReadiness(); };
        Get<CheckBox>("PublicOnline").Unchecked+=delegate { UpdateReadiness(); };
        Get<ListBox>("SaveBackupTimeline").SelectionChanged+=delegate {
            if(drawingTimeline) return;
            var list=Get<ComboBox>("SaveBackupList"); int index=Get<ListBox>("SaveBackupTimeline").SelectedIndex;
            if(index>=0 && index<list.Items.Count && list.SelectedIndex!=index) list.SelectedIndex=index;
        };
        LoadProfile(); BuildFeatures(); LoadAdmin(); LoadConversion(); UpdateReadiness();
        Get<TextBlock>("DestinationSavePath").Text=PathAt(@"data\user\savedata");
        timer.Tick+=delegate { Poll(); };
        Window.Closing+=delegate(object sender,System.ComponentModel.CancelEventArgs e) {
            InvalidateResolution();
            if(Busy) { e.Cancel=true; Say("请先正常退出游戏，并等待保存或管理操作完成后关闭。"); }
            else { closed=true; timer.Stop(); toast.Dispose(); }
        };
        Page("Home"); SetBusy();
    }
    public void Start() {
        try { HuntUpdates.WatchStartup(Window,Get<Button>("CheckUpdates"),root,"TheHuntLauncher",version); }
        catch { Get<Button>("CheckUpdates").ToolTip="自动检查暂不可用，可手动检查更新。"; }
        Window.Loaded+=async delegate {
            timer.Start(); await RefreshTrophies(); await RefreshHomeSaves();
        };
    }
    bool Pick(string field,string filter) { if(Busy) return false; var d=new OpenFileDialog {Filter=filter}; if(d.ShowDialog(Window)!=true) return false; Get<TextBox>(field).Text=d.FileName; return true; }
    bool PickOrdinaryInstall() {
        if(!Pick("InstallPath","Bloodborne.exe|Bloodborne.exe")) return false;
        string ini=Path.Combine(Path.GetDirectoryName(Text("InstallPath")),"bbport.ini");
        if(File.Exists(ini)) Get<TextBox>("GraphicsPath").Text=ini;
        return true;
    }
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
        var profile=ProfileForExplicitSave();
        foreach(var pair in Profile()) profile[pair.Key]=pair.Value;
        foreach(string n in new [] {"Shadnet","Webapi","Community","Relay","Account"}) if(Text(n).Length==0) throw new InvalidOperationException("请填写完整的联机地址与账号。");
        string path=PathAt(@".private\account.clixml"), password=Get<PasswordBox>("Password").Password;
        if(password.Length==0 && (!File.Exists(path) || DesktopCredentials.Read(path)[0]!=Text("Account"))) throw new InvalidOperationException("首次使用或更换账号时需要填写密码。");
        if(password.Length>0) { DesktopCredentials.Save(path,Text("Account"),password); Get<PasswordBox>("Password").Clear(); }
        profile["network"]=Map("shadnet",Text("Shadnet"),"webapi",Text("Webapi"),"community",Text("Community"),"relay",Text("Relay"));
        profile["public_online"]=Get<CheckBox>("PublicOnline").IsChecked==true;
        if((bool)profile["public_online"]) SavePublic();
        WriteJson(PathAt("profile.json"),profile); Summary();
    }
    Dictionary<string,object> ProfileForExplicitSave() {
        if(!File.Exists(PathAt("profile.json"))) return Map();
        // IO failures are deliberately outside the JSON-only recovery boundary.
        string text=File.ReadAllText(PathAt("profile.json"),Encoding.UTF8);
        try { return json.Deserialize<Dictionary<string,object>>(text) ?? Map(); }
        catch(ArgumentException) { return Map(); }
        catch(InvalidOperationException) { return Map(); }
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
            ShowServerHost();
            if(File.Exists(PathAt(@".private\account.clixml"))) Get<TextBox>("Account").Text=DesktopCredentials.Read(PathAt(@".private\account.clixml"))[0];
            Get<CheckBox>("PublicOnline").IsChecked=!p.ContainsKey("public_online") || Flag(p,"public_online");
            Summary(); Say("已加载本机配置，可以启动游戏。");
        } catch { Say("配置或本机凭据无法读取，请重新填写；存档仍在 data 目录。"); }
    }
    void Summary() {
        graphicsText="";
        if(File.Exists(Text("GraphicsPath"))) {
            var values=new Dictionary<string,string>();
            foreach(string line in File.ReadAllLines(Text("GraphicsPath"))) { var m=Regex.Match(line,@"^\s*(output_res|upscaler)\s*=(.*)$"); if(m.Success) values[m.Groups[1].Value]=m.Groups[2].Value.Trim(); }
            graphicsText=String.Join(" · ",values.Values.ToArray());
        }
        UpdateReadiness();
    }
    string graphicsText="";
    void Ready(string row,int state,string detail) {
        // state: 1 ready, 0 needs action, -1 optional / off.
        var icon=Get<TextBlock>("Ready"+row+"Icon");
        icon.Text=state>0 ? "":state==0 ? "":"";
        icon.Foreground=Brush(state>0 ? "#8FAE8A":state==0 ? "#E0B66E":"#7D7A73");
        Get<Button>("Ready"+row+"Action").Content=state==0 ? "去设置  →":"查看  →";
        Get<Button>("Ready"+row+"Action").Foreground=Brush(state==0 ? "#E0B66E":"#BBA47A");
        var target=Get<TextBlock>(row=="Game" ? "GraphicsSummary":row=="Server" ? "ServerSummary":row=="Account" ? "AccountSummary":"ReadyPublicDetail");
        target.Text=detail; target.Foreground=Brush(state==0 ? "#D9BE8A":"#A8A397");
    }
    void UpdateReadiness() {
        if(Window==null) return;
        var missing=new [] {new [] {"InstallPath","Bloodborne.exe"},new [] {"GamePath","eboot.bin"},new [] {"GraphicsPath","bbport.ini"}}.Where(p=>!File.Exists(Text(p[0]))).Select(p=>p[1]).ToArray();
        Ready("Game",missing.Length==0 ? 1:0,missing.Length==0 ? "CUSA03173 1.09 已选择"+(graphicsText.Length>0 ? " · 画质 "+graphicsText:""):"缺少 "+String.Join("、",missing));
        bool network=new [] {"Shadnet","Webapi","Community","Relay"}.All(n=>Text(n).Length>0);
        string host=Text("ServerHost").Length>0 ? Text("ServerHost"):Text("Shadnet");
        Ready("Server",network ? 1:0,network ? host:"尚未填写服务器地址");
        string account=Text("Account"), saved="";
        try { string path=PathAt(@".private\account.clixml"); if(File.Exists(path)) saved=DesktopCredentials.Read(path)[0]; } catch { saved=""; }
        Ready("Account",account.Length>0 && saved==account ? 1:0,account.Length==0 ? "尚未设置":saved==account ? account+" · 已加密保存":account+" · 保存设置后生效");
        bool publicOn=Get<CheckBox>("PublicOnline").IsChecked==true, publicSaved=File.Exists(PathAt(@".private\public-online.clixml"));
        Ready("Public",!publicOn ? -1:publicSaved ? 1:0,!publicOn ? "已关闭 · 使用私服的独立内容":publicSaved ? Text("PublicAccount")+" · 启动时自动登录":"已开启，但还没有保存公共账号");
    }
    static readonly Regex HostPattern=new Regex(@"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$");
    void FillServerHost() {
        if(fillingHost) return;
        string host=Text("ServerHost"); var note=Get<TextBlock>("AddressNote");
        if(host.Length==0) { note.Text="只填主机名或 IP，四项端口会自动补全：31413 / 31415 / 20443 / 20444。"; note.Foreground=Brush("#9A958A"); UpdateReadiness(); return; }
        if(!HostPattern.IsMatch(host)) { note.Text="只填主机名或 IP，不要带 http:// 或端口；需要特殊端口时请在「高级」里分别填写。"; note.Foreground=Brush("#E0B66E"); return; }
        Get<TextBox>("Shadnet").Text=host+":31413"; Get<TextBox>("Webapi").Text="http://"+host+":31415";
        Get<TextBox>("Community").Text="http://"+host+":20443"; Get<TextBox>("Relay").Text=host+":20444";
        note.Text="已按默认端口填好四项地址，可在「高级」里查看或修改。"; note.Foreground=Brush("#9A958A");
        UpdateReadiness();
    }
    void ShowServerHost() {
        // Show one host only when all four addresses are exactly that host's default endpoints.
        var m=Regex.Match(Text("Shadnet"),@"^(.+):31413$"); string host=m.Success ? m.Groups[1].Value:"";
        bool standard=host.Length>0 && HostPattern.IsMatch(host) && Text("Webapi")=="http://"+host+":31415" && Text("Community")=="http://"+host+":20443" && Text("Relay")==host+":20444";
        fillingHost=true;
        try { Get<TextBox>("ServerHost").Text=standard ? host:""; } finally { fillingHost=false; }
        bool any=new [] {"Shadnet","Webapi","Community","Relay"}.Any(n=>Text(n).Length>0);
        Get<Expander>("AdvancedAddresses").IsExpanded=any && !standard;
        var note=Get<TextBlock>("AddressNote");
        note.Text=any && !standard ? "当前四项地址不是同一主机的默认端口，已在「高级」中保留原值。":"只填主机名或 IP，四项端口会自动补全：31413 / 31415 / 20443 / 20444。";
        note.Foreground=Brush("#9A958A");
        UpdateReadiness();
    }
    void SetBusy() {
        foreach(string n in new [] {"UseLocalServer","Launch","ReviewWorldRecovery","SaveSettings","RefreshSaves","SaveFeatures","RecommendFeatures","FeatureItems","PublicFields","AdminFields","AdminRefresh","AdminCreate","AdminVerify","PickInstall","PickGame","PickGraphics"}) Get<FrameworkElement>(n).IsEnabled=!Busy;
        Get<Button>("ImportSaves").IsEnabled=!Busy && inspection!=null;
        Get<Button>("PickSaveFolder").IsEnabled=!Busy;
        Get<Button>("ClearSaveFolder").IsEnabled=!Busy;
        Get<Button>("RefreshSaveBackups").IsEnabled=!Busy;
        Get<ComboBox>("SaveBackupList").IsEnabled=!Busy;
        var selected=Get<ComboBox>("SaveBackupList").SelectedItem as ComboBoxItem;
        string kind=selected==null ? "" : Str(selected.Tag as Dictionary<string,object>,"kind");
        Get<Button>("RestoreSaveBackup").IsEnabled=!Busy && pendingSaveRestore==null && pendingResolution==null && (kind=="launch" || kind=="pre-restore" || kind=="resolution-before");
        Get<Button>("ResumeSaveRestore").IsEnabled=!Busy && Flag(pendingSaveRestore,"can_resume");
        Get<Button>("RefreshSaveResolution").IsEnabled=!Busy;
        Get<Button>("CancelSaveResolution").IsEnabled=!resolutionApplying;
        Get<Button>("ResumeSaveResolution").IsEnabled=!Busy && Flag(pendingResolution,"can_resume");
        Get<Button>("AbandonSaveResolution").IsEnabled=!Busy && Flag(pendingResolution,"can_abandon");
        Get<StackPanel>("SaveResolutionOptions").IsEnabled=!Busy && resolutionPlan!=null;
        Get<Button>("CopyWorldRecoveryEvidence").IsEnabled=!Busy && !String.IsNullOrEmpty(recoveryDigest);
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
            if(request!=null) {
                var text=new StringBuilder();
                foreach(char c in json.Serialize(request)) text.Append(c>127 ? "\\u"+((int)c).ToString("x4"):c.ToString());
                await p.StandardInput.WriteAsync(text.ToString()); p.StandardInput.Close();
            }
            await Task.Run(()=>p.WaitForExit()); string data=await output; await errors;
            var result=json.Deserialize<Dictionary<string,object>>(data);
            if(p.ExitCode!=0 || !Flag(result,"ok")) throw new InvalidOperationException(Str(result,"error").Length>0 ? Str(result,"error"):"操作未完成，请保留日志。");
            return result;
        }
    }
    async Task ReviewWorldRecovery() {
        RequireIdle(); Page("Save"); Get<RadioButton>("SaveTabAdvanced").IsChecked=true; InvalidateResolution(); recoveryDigest=null; operation=true; SetBusy();
        int generation=resolutionGeneration;
        var summary=Get<TextBlock>("WorldRecoverySummary"); summary.Text="正在只读核对世界恢复记录…";
        try {
            var review=await Request("world_recovery.py",null,"review");
            var lines=new List<string>();
            foreach(Dictionary<string,object> row in (System.Collections.IEnumerable)review["items"]) {
                string slot=Str(row,"slot"), mode=Str(row,"mode");
                string label=slot.Length>0 ? "槽位 "+slot : "世界恢复";
                if(mode.Length>0) label+=" · "+mode;
                var origin=row.ContainsKey("origin") ? row["origin"] as Dictionary<string,object>:null;
                string source=String.Join(" · ",new [] {"origin","source","reason"}.Select(key=>Str(origin,key)).Where(v=>v.Length>0));
                var generations=row.ContainsKey("generations") ? row["generations"] as Dictionary<string,object>:null;
                string details=generations==null ? "" : "\n"+String.Join(" · ",generations.Select(pair=>
                    (pair.Key.StartsWith("userdata",StringComparison.Ordinal) ? "主档：":"备档：")+
                    RecoveryGeneration(Str(pair.Value as Dictionary<string,object>,"state"))));
                lines.Add(label+"： "+Str(row,"reason")+(source.Length==0 ? "":"\n标记来源："+source)+details);
            }
            if(generation!=resolutionGeneration || currentPage!="Save" || closed) return;
            summary.Text=lines.Count==0 ? "当前没有待复核的世界记录。":String.Join("\n\n",lines);
            recoveryDigest=Str(review,"digest");
            await LoadSaveResolution(null,generation);
        } catch {
            summary.Text="无法完成本次复核，详情见提示。";
            throw;
        } finally { operation=false; SetBusy(); }
    }
    async Task CopyWorldRecoveryEvidence() {
        RequireIdle();
        if(String.IsNullOrEmpty(recoveryDigest) ||
           !Confirm("创建一份校验过的本地证据副本？副本含私人存档，请仅在本机核对。\n\n证据不足时请保留原记录。","复制恢复证据")) return;
        operation=true; SetBusy();
        try {
            var result=await Request("world_recovery.py",Map("digest",recoveryDigest),"copy-evidence");
            Get<TextBlock>("WorldRecoverySummary").Text+="\n\n已保留证据副本："+Str(result,"path");
            OpenFolder(Str(result,"path"));
        } finally { recoveryDigest=null; operation=false; SetBusy(); }
    }
    static string RecoveryGeneration(string state) {
        switch(state) {
            case "verified": return "已核验";
            case "missing": return "缺失";
            case "different-character": return "角色不符";
            case "invalid": case "unreadable": return "无法读取";
            default: return "未核验";
        }
    }
    async Task Launch() {
        RequireIdle();
        if(!Directory.Exists(PathAt("data"))) {
            operation=true; SetBusy();
            try {
                var source=await InspectOrdinarySource(null);
                if(Flag(source,"exists")) {
                    if(!Confirm("首次启动将复制以下普通版存档：\n"+Str(source,"source_label")+"\n"+Str(source,"source")+"\n\n普通版原件不会被修改。确认从此来源开始？","确认首次导入来源")) return;
                } else if(!Confirm(Str(source,"missing_message")+"\n\n继续会进入空存档；之后可以在存档页导入。是否继续？","没有找到普通版存档")) return;
            } finally { operation=false; SetBusy(); }
        }
        if(featuresDirty) { Page("Feature"); throw new InvalidOperationException("请先保存功能页中的变更，再启动游戏。"); }
        if(File.Exists(PathAt(".save-import.lock")) || File.Exists(PathAt(@"data\user\save-resolution-review.json"))) {
            Page("Save"); await RefreshSaveBackups();
            throw new InvalidOperationException(Str(pendingResolution,"message").Length>0 ? Str(pendingResolution,"message"):Str(pendingSaveRestore,"message").Length>0 ? Str(pendingSaveRestore,"message"):"这份存档需要先在本页明确选择；尚未启动游戏。");
        }
        foreach(string n in new [] {".save-conversion.lock",".save-conversion-pending.json"}) if(File.Exists(PathAt(n))) throw new InvalidOperationException("存档转换正在进行或上次未完整结束，请先检查转换页和备份。");
        SaveProfile(); operation=true; SetBusy(); Exception failure=null; bool resolutionRequired=false;
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
                resolutionRequired=phase=="save-resolution-needed";
                if(phase!="exited" && phase!="error" && !resolutionRequired) Say("启动进程已结束，请查看 startup.log。");
            }
        } catch(Exception error) { failure=error; }
        finally {
            if(worker!=null) { worker.Dispose(); worker=null; }
            toast.Stop(); noticeSession=null;
            LoadPublic();
        }
        // Refresh even when exit recovery reports an error. Trophy failure has its own notice.
        trophies.Exited();
        try { await RefreshTrophies(true); }
        finally {
            operation=false; SetBusy(); Get<Button>("Launch").Content="开始游戏  →";
        }
        // The production worker can discover an anomaly after the UI's lightweight check.
        if(resolutionRequired || File.Exists(PathAt(@"data\user\save-resolution-review.json")) || File.Exists(PathAt(".save-import.lock"))) {
            Page("Save"); await RefreshSaveBackups();
        }
        if(failure!=null) System.Runtime.ExceptionServices.ExceptionDispatchInfo.Capture(failure).Throw();
    }
    static async Task Pump(StreamReader reader,StreamWriter log) {
        string line; bool logFailed=false;
        while((line=await reader.ReadLineAsync())!=null) {
            if(logFailed) continue; // Keep draining; a full pipe can block the game supervisor.
            try { lock(log) { log.WriteLine(line); log.Flush(); } }
            catch(IOException) { logFailed=true; }
            catch(ObjectDisposedException) { logFailed=true; }
        }
    }
    static string PublicStatusText(Dictionary<string,object> state) {
        // The worker computes reconnect/failed notices; empty keeps the normal status text.
        string notice=Str(state,"notice"); if(notice.Length>0) return notice;
        // A drop shorter than the 20 s grace still reports "reconnecting" with no notice.
        string account=Str(state,"account_state");
        bool connected=account.Length>0 ? account=="connected" || account=="reconnecting" : Flag(state,"account_connected");
        object count; int failures=0;
        if(state.TryGetValue("consecutive_failures",out count) || state.TryGetValue("failures",out count)) failures=Convert.ToInt32(count);
        return !connected ? "公共账号连接已断开。": failures>0 ? "公共内容请求失败，请检查网络或重新启动。":Flag(state,"game_authenticated") ? "公共在线已连接 · 私服无缝保持独立。":"公共账号已连接，等待游戏登录。";
    }
    void Poll() {
        if(worker==null) return;
        if(!worker.HasExited) PollTrophyNotices();
        try {
            string path=PathAt(@"data\user\public-online-state.json");
            if(!worker.HasExited && File.Exists(path)) {
                var state=ReadJson(path);
                if(Str(state,"ui_session")==noticeSession) Get<TextBlock>("PublicStatus").Text=PublicStatusText(state);
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
    static IEnumerable<Dictionary<string,object>> Rows(Dictionary<string,object> value,string key) {
        object rows;
        return value!=null && value.TryGetValue(key,out rows) && rows is System.Collections.IEnumerable ?
            ((System.Collections.IEnumerable)rows).Cast<object>().OfType<Dictionary<string,object>>() : Enumerable.Empty<Dictionary<string,object>>();
    }
    static string[] Warnings(Dictionary<string,object> value) {
        object rows;
        return value!=null && value.TryGetValue("warnings",out rows) && rows is System.Collections.IEnumerable ?
            ((System.Collections.IEnumerable)rows).Cast<object>().Select(Convert.ToString).ToArray() : new string[0];
    }
    static string Readability(string value) {
        switch(value) {
            case "readable": case "valid": case "verified": return "可读";
            case "missing": case "absent": return "缺失";
            case "empty": case "unused": return "未使用";
            case "invalid": case "unreadable": return "读不出";
            default: return "未确认可读";
        }
    }
    static bool EmptyGeneration(string value) {
        return value=="missing" || value=="absent" || value=="empty" || value=="unused";
    }
    static string GenerationStatus(Dictionary<string,object> row) {
        return "主档"+Readability(Str(row,"primary"))+"，备档"+Readability(Str(row,"backup"))+
            (Flag(row,"identity_conflict") ? "，两代角色身份不一致":"")+
            (Str(row,"primary_detail").Length>0 ? "\n主档说明："+Str(row,"primary_detail"):"")+
            (Str(row,"backup_detail").Length>0 ? "\n备档说明："+Str(row,"backup_detail"):"");
    }
    static string CharacterLine(Dictionary<string,object> row) {
        string source=Str(row,"source"), name=Str(row,"name"), detail;
        if(name.Length==0) name="姓名未识别";
        switch(Str(row,"issue")) {
            case "unrecognized": detail="角色数据无法识别"; break;
            case "read_failed": detail="读取失败，请重新检查"; break;
            case "stats_unavailable": detail=name+" · 等级、回响未识别"; break;
            default: detail=name+" · 等级 "+Str(row,"level")+" · 回响 "+Str(row,"echoes"); break;
        }
        string label=source=="both" ? "主档与备档":source=="primary" ? "主档":"备档";
        return "角色槽位 "+SlotLabel(Str(row,"slot"))+" · "+label+" · "+detail;
    }
    static string CharacterSummary(Dictionary<string,object> value) {
        var lines=new List<string>();
        var states=Rows(value,"characters").Where(row=>!EmptyGeneration(Str(row,"primary")) || !EmptyGeneration(Str(row,"backup"))).ToList();
        if(value!=null && value.ContainsKey("character_preview")) {
            foreach(var slot in Rows(value,"character_preview").GroupBy(row=>Str(row,"slot"))) {
                lines.AddRange(slot.Select(CharacterLine));
                var state=states.FirstOrDefault(row=>Str(row,"slot")==slot.Key);
                if(state!=null) lines[lines.Count-1]+=" · "+GenerationStatus(state);
                else if(slot.Any(row=>Flag(row,"identity_conflict"))) lines[lines.Count-1]+=" · 两代角色身份不一致";
            }
            if(lines.Count==0) lines.Add("没有已使用的角色槽位。");
        } else {
            foreach(var row in states)
                lines.Add("角色槽位 "+SlotLabel(Str(row,"slot"))+" · "+
                    (Str(row,"name").Length>0 ? Str(row,"name"):"名字未识别")+" · "+GenerationStatus(row));
        }
        if(Str(value,"readability_note").Length>0) lines.Add(Str(value,"readability_note"));
        return String.Join("\n",lines.ToArray());
    }
    static string SourceCharacters(Dictionary<string,object> plan,string key,string heading) {
        object item;
        if(!plan.TryGetValue(key,out item)) return "";
        return heading+"\n"+CharacterSummary((Dictionary<string,object>)item);
    }
    static string SlotLabel(string slot) { int number; return Int32.TryParse(slot,out number) && number>=0 && number<10 ? (number+1).ToString():"未识别"; }
    void InvalidateResolution() {
        ++resolutionGeneration; resolutionPlan=null; pendingResolution=null;
        Get<StackPanel>("SaveResolutionOptions").Children.Clear();
        Get<Button>("ResumeSaveResolution").IsEnabled=false;
        Get<Button>("AbandonSaveResolution").IsEnabled=false;
    }
    Dictionary<string,object> ResolutionRequest(Dictionary<string,object> selection) {
        var request=Map("action","plan");
        string install=Text("InstallPath");
        request["ordinary_install"]=String.IsNullOrWhiteSpace(install) ? "":Path.GetDirectoryName(install);
        if(selection!=null) {
            request["selection_kind"]=Str(selection,"kind"); request["selection_id"]=Str(selection,"id");
            request["selection_fingerprint"]=Str(selection,"fingerprint");
        }
        return request;
    }
    void ShowSaveResolution(Dictionary<string,object> plan,int generation) {
        if(generation!=resolutionGeneration || closed || currentPage!="Save") return;
        resolutionPlan=plan; pendingResolution=plan.ContainsKey("pending") ? plan["pending"] as Dictionary<string,object>:null;
        resolutionNeeded=Flag(plan,"needed");
        DrawCurrentSlots(plan);
        if(pendingResolution!=null) ShowSaveState(0,"上次的存档选择还没有完成，请在下方继续或另存后放弃；完成前不会启动游戏。");
        else if(Flag(plan,"show_choices")) ShowSaveState(0,"这份存档需要先确认处理方式，选择前不会启动游戏。处理方式见下方。");
        else if(Flag(plan,"startable")) ShowSaveState(1,"当前存档已通过只读检查，可以开始游戏。");
        else ShowSaveState(2,Str(plan,"summary").Length>0 ? Str(plan,"summary"):"当前存档已读取。");
        Get<Border>("SaveResolutionPanel").Visibility=Flag(plan,"show_choices") ? Visibility.Visible:Visibility.Collapsed;
        Get<TextBlock>("SaveResolutionSummary").Text=Str(plan,"summary");
        string current=CharacterSummary(plan);
        Get<TextBlock>("SaveResolutionCharacters").Text=String.Join("\n\n", new [] {
            SourceCharacters(plan,"backup_view","备份中的角色"), SourceCharacters(plan,"import_view","原版导入来源中的角色"),
            current.Length>0 ? "当前存档中的角色\n"+current:"" }.Where(s=>s.Length>0));
        Get<TextBlock>("SaveResolutionPending").Text=Str(pendingResolution,"message")+"\n"+Str(pendingResolution,"abandon_message");
        Get<Button>("ResumeSaveResolution").Visibility=pendingResolution!=null ? Visibility.Visible:Visibility.Collapsed;
        Get<Button>("AbandonSaveResolution").Visibility=pendingResolution!=null ? Visibility.Visible:Visibility.Collapsed;
        Get<TextBlock>("SaveResolutionStatus").Text=String.Join("\n",Warnings(plan));
        var panel=Get<StackPanel>("SaveResolutionOptions"); panel.Children.Clear();
        if(pendingResolution==null && Flag(plan,"show_choices")) foreach(var option in Rows(plan,"options").OrderByDescending(row=>Flag(row,"recommended"))) {
            var captured=option;
            string id=Str(option,"id");
            bool known=KnownResolutionOption(id);
            var button=new Button {Content=Str(option,"label"),HorizontalAlignment=HorizontalAlignment.Left,
                Margin=new Thickness(0,0,0,6),IsDefault=false,IsEnabled=known};
            button.Click+=async delegate { try { await ApplySaveResolution(plan,captured,generation); } catch(Exception e) { Error(e.Message); } };
            panel.Children.Add(button);
            panel.Children.Add(Block(Str(option,"description")+(known ? "":"（此启动器不支持这个选项，请先更新。）"),12));
        }
        SetBusy();
        if(Flag(plan,"show_choices")) { Window.UpdateLayout(); Get<Border>("SaveResolutionPanel").BringIntoView(); }
    }
    static bool KnownResolutionOption(string id) {
        if(new [] {"accept-bound","accept-all","restore-all","restore-case","use-current","preserve","reimport-ordinary","restart","choose-ordinary"}.Contains(id)) return true;
        return id.Length==20 && id.StartsWith("restore-generation-",StringComparison.Ordinal) && id[19]>='0' && id[19]<='9';
    }
    async Task LoadSaveResolution(Dictionary<string,object> selection,int generation) {
        try { ShowSaveResolution(await Request("save_resolution.py",ResolutionRequest(selection),""),generation); }
        catch(Exception e) {
            if(generation==resolutionGeneration && !closed && currentPage=="Save") {
                resolutionPlan=null; pendingResolution=null;
                Get<Border>("SaveResolutionPanel").Visibility=Visibility.Visible;
                Get<StackPanel>("SaveResolutionOptions").Children.Clear();
                Get<TextBlock>("SaveResolutionSummary").Text="本次检查未完成；没有据此判定存档损坏。";
                DrawCurrentSlots(null); ShowSaveState(-1,"本次检查未完成；没有据此判定存档损坏。原因见下方。");
                Get<TextBlock>("SaveResolutionCharacters").Text=""; Get<TextBlock>("SaveResolutionPending").Text="";
                Get<Button>("ResumeSaveResolution").Visibility=Visibility.Collapsed;
                Get<Button>("AbandonSaveResolution").Visibility=Visibility.Collapsed;
                Get<TextBlock>("SaveResolutionStatus").Text=e.Message+"\n请按上方原因处理后重新检查；不会自动更换存档。";
            }
            throw;
        }
    }
    async Task RefreshSaveResolution() {
        RequireIdle(); InvalidateResolution(); int generation=resolutionGeneration; operation=true; SetBusy();
        try { Get<TextBlock>("SaveResolutionStatus").Text="正在只读检查；尚未改变存档…"; await LoadSaveResolution(null,generation); }
        finally { operation=false; SetBusy(); }
    }
    async Task ApplySaveResolution(Dictionary<string,object> plan,Dictionary<string,object> option,int generation) {
        RequireIdle();
        if(generation!=resolutionGeneration || resolutionPlan!=plan || currentPage!="Save") throw new InvalidOperationException("这次选择已失效，请重新检查。");
        string optionId=Str(option,"id");
        if(!KnownResolutionOption(optionId)) throw new InvalidOperationException("当前启动器不支持这个选项。");
        if(optionId=="choose-ordinary") {
            InvalidateResolution();
            if(!PickOrdinaryInstall()) { Get<TextBlock>("SaveResolutionStatus").Text="未选择原版程序；没有改动存档。请重新检查。"; return; }
            Page("Save"); await RefreshSaveResolution(); return;
        }
        bool launch=false; Dictionary<string,object> completed=null; operation=true; resolutionApplying=true; SetBusy();
        try {
            if(!Confirm(ResolutionConfirmation(plan,option),"确认存档选择")) { InvalidateResolution(); Get<TextBlock>("SaveResolutionStatus").Text="已取消，未执行这次选择。请重新检查后再选择。"; return; }
            if(optionId=="restart") {
                if(generation!=resolutionGeneration || resolutionPlan!=plan || currentPage!="Save" || closed) throw new InvalidOperationException("确认期间来源或页面已变化，请重新检查。");
                if(!Confirm("请再次确认：这不是恢复原来的游戏进度。\n\n"+Str(option,"description")+"\n\n确认封存当前存档，并像新安装一样从头开始？","再次确认从头开始")) {
                    InvalidateResolution(); Get<TextBlock>("SaveResolutionStatus").Text="已取消从头开始；没有执行，也没有改动存档。请重新检查。"; return;
                }
            }
            if(generation!=resolutionGeneration || resolutionPlan!=plan || currentPage!="Save" || closed) throw new InvalidOperationException("确认期间来源或页面已变化，请重新检查。");
            var selection=plan.ContainsKey("selection") ? plan["selection"] as Dictionary<string,object>:null;
            var request=ResolutionRequest(selection); request["action"]="apply"; request["digest"]=option.ContainsKey("digest") ? Str(option,"digest"):Str(plan,"digest"); request["option_id"]=Str(option,"id"); request["confirmed"]=true;
            if(optionId=="restart") request["restart_confirmed"]=true;
            InvalidateResolution(); Get<TextBlock>("SaveResolutionStatus").Text="正在保全并核验原件，完成前不要关闭。尚未报告保全成功…";
            var result=await Request("save_resolution.py",request,""); completed=result;
            launch=Str(option,"id")=="use-current" && Flag(result,"can_launch");
            await LoadSaveBackups();
            await LoadSaveResolution(null,resolutionGeneration);
            Get<TextBlock>("SaveResolutionStatus").Text=Str(result,"message")+"\n"+Get<TextBlock>("SaveResolutionStatus").Text;
            Say(Str(result,"message"));
        } catch(Exception e) {
            InvalidateResolution(); Get<Border>("SaveResolutionPanel").Visibility=Visibility.Visible;
            Get<TextBlock>("SaveResolutionStatus").Text=completed!=null ? Str(completed,"message")+"\n处理已完成，但刷新状态失败："+e.Message+"\n请重新检查；不会重复执行已完成的选择。":"这次处理没有确认完成："+e.Message+"\n不自动回退；请重新检查，可继续原选择，或完整另存当前现场后明确放弃上次选择。";
            throw;
        } finally { resolutionApplying=false; operation=false; SetBusy(); }
        if(launch) await Launch();
    }
    string ResolutionConfirmation(Dictionary<string,object> plan,Dictionary<string,object> option) {
        string characters=CharacterSummary(plan), summary=Str(plan,"summary"), id=Str(option,"id"), source="";
        if(id=="restore-all" || id=="restore-case") source=SourceCharacters(plan,"backup_view","将恢复的备份角色");
        if(id=="reimport-ordinary") source=SourceCharacters(plan,"import_view","将导入的原版角色");
        return Str(option,"label")+"\n\n"+Str(option,"description")+
            (source.Length>0 ? "\n\n"+source:"")+
            (summary.Length>0 ? "\n\n当前版本检查："+summary:"")+
            (characters.Length>0 ? "\n"+characters:"")+
            "\n\n会先完整保全当前存档和相关记录，核验成功后才执行。不同版本不会合并。确认继续？";
    }
    async Task ResumeSaveResolution() {
        RequireIdle(); var pending=pendingResolution; int generation=resolutionGeneration;
        if(!Flag(pending,"can_resume")) throw new InvalidOperationException("无法证明原事务可继续，请重新检查能否完整另存当前现场后放弃上次选择。");
        operation=true; resolutionApplying=true; SetBusy(); bool launch=false; Dictionary<string,object> completed=null;
        try {
            if(!Confirm(Str(pending,"message")+"\n\n只继续上次已经确认的选择，不会自动回退或改选其它存档。确认继续？","继续上次的选择")) { InvalidateResolution(); return; }
            if(generation!=resolutionGeneration || pendingResolution!=pending || currentPage!="Save") throw new InvalidOperationException("这次续办已失效，请重新检查。");
            InvalidateResolution(); Get<TextBlock>("SaveResolutionStatus").Text="正在核验并继续上次的选择…";
            var result=await Request("save_resolution.py",Map("action","resume","fingerprint",Str(pending,"fingerprint"),"confirmed",true),""); completed=result;
            launch=Flag(result,"can_launch");
            await LoadSaveBackups(); await LoadSaveResolution(null,resolutionGeneration);
            Get<TextBlock>("SaveResolutionStatus").Text=Str(result,"message")+"\n"+Get<TextBlock>("SaveResolutionStatus").Text; Say(Str(result,"message"));
        } catch(Exception e) { InvalidateResolution(); Get<TextBlock>("SaveResolutionStatus").Text=completed!=null ? Str(completed,"message")+"\n续办已完成，但刷新状态失败："+e.Message+"\n请重新检查，不会重复执行。":"续办尚未确认完成："+e.Message+"\n不会自动回退。请重新检查。"; throw; }
        finally { resolutionApplying=false; operation=false; SetBusy(); }
        if(launch) await Launch();
    }
    async Task AbandonSaveResolution() {
        RequireIdle(); var pending=pendingResolution; int generation=resolutionGeneration;
        if(!Flag(pending,"can_abandon")) throw new InvalidOperationException(Str(pending,"abandon_message"));
        operation=true; resolutionApplying=true; SetBusy(); Dictionary<string,object> completed=null;
        try {
            string description=Str(pending,"abandon_message")+"\n\n"+CharacterSummary(pending)+
                "\n\n不会自动恢复上次目标。完整保全成功后才放弃原选择，然后重新检查当前版本。确认继续？";
            if(!Confirm(description,"完整另存当前现场并放弃上次选择")) { InvalidateResolution(); return; }
            if(generation!=resolutionGeneration || pendingResolution!=pending || currentPage!="Save" || closed)
                throw new InvalidOperationException("确认期间现场或页面已变化，请重新检查。");
            InvalidateResolution(); Get<TextBlock>("SaveResolutionStatus").Text="正在完整另存当前现场；尚未报告保全成功…";
            var result=await Request("save_resolution.py",Map("action","abandon","digest",Str(pending,"abandon_digest"),"confirmed",true),""); completed=result;
            await LoadSaveBackups(); await LoadSaveResolution(null,resolutionGeneration);
            Get<TextBlock>("SaveResolutionStatus").Text=Str(result,"message")+"\n"+Get<TextBlock>("SaveResolutionStatus").Text;
            Say(Str(result,"message"));
        } catch(Exception e) {
            InvalidateResolution(); Get<TextBlock>("SaveResolutionStatus").Text=completed!=null ?
                Str(completed,"message")+"\n刷新未完成，请重新检查："+e.Message :
                "尚未确认放弃完成："+e.Message+"\n请重新检查；不会自动回退、删除原件或启动游戏。";
            throw;
        } finally { resolutionApplying=false; operation=false; SetBusy(); }
    }
    void ShowSelectedBackup() {
        var item=Get<ComboBox>("SaveBackupList").SelectedItem as ComboBoxItem;
        var row=item==null ? null:item.Tag as Dictionary<string,object>;
        bool special=Str(row,"kind")=="resolution-before";
        Get<Button>("RestoreSaveBackup").Content=special ? "换回处理前状态":"恢复到这个备份";
        string characters=CharacterSummary(row);
        Get<TextBlock>("SelectedSaveBackup").Text=special ? Str(row,"label")+"\n"+characters+"\n会成套换回存档和联机记录；现在的版本会另存，不会合并，换回后须重新选择，不自动启动。":characters;
        int drawn=DrawBackupTable(row);
        Get<TextBlock>("SelectedSaveBackup").Visibility=special || drawn==0 || Str(row,"readability_note").Length>0 ? Visibility.Visible:Visibility.Collapsed;
        drawingTimeline=true;
        try { Get<ListBox>("SaveBackupTimeline").SelectedIndex=Get<ComboBox>("SaveBackupList").SelectedIndex; } finally { drawingTimeline=false; }
    }
    static string TimelineTitle(Dictionary<string,object> row) {
        string kind=Str(row,"kind");
        return kind=="launch" ? "游戏启动前":kind=="pre-restore" ? "恢复前":kind=="resolution-before" ? (Str(row,"label").Length>0 ? Str(row,"label"):"处理前"):"未知类型（不可恢复）";
    }
    static string BackupKind(Dictionary<string,object> row) {
        string kind=Str(row,"kind");
        return kind=="resolution-before" ? "处理前":kind=="pre-restore" ? "恢复前":kind=="launch" ? "启动前":"未知类型";
    }
    static string Number(Dictionary<string,object> row,string key) { long n; return Int64.TryParse(Str(row,key),out n) ? n.ToString("N0"):Str(row,key); }
    void DrawBackupTimeline() {
        var timeline=Get<ListBox>("SaveBackupTimeline"); drawingTimeline=true;
        try {
            timeline.Items.Clear();
            foreach(ComboBoxItem source in Get<ComboBox>("SaveBackupList").Items) {
                var row=source.Tag as Dictionary<string,object>;
                DateTimeOffset time; string stamp=Str(row,"time");
                if(DateTimeOffset.TryParse(stamp,out time)) stamp=time.ToLocalTime().ToString("yyyy-MM-dd HH:mm");
                // Row: gold kind title, then a muted "time · characters" line.
                var roles=Rows(row,"character_preview").Where(r=>Str(r,"source")!="backup" || !Rows(row,"character_preview").Any(o=>Str(o,"slot")==Str(r,"slot") && Str(o,"source")!="backup")).ToArray();
                string line=roles.Length==0 ? "没有可显示的角色":String.Join(" · ",roles.Take(2).Select(r=>(Str(r,"name").Length>0 ? Str(r,"name"):"未识别")+(Str(r,"level").Length>0 ? " Lv"+Str(r,"level"):"")))+(roles.Length>2 ? " 等 "+roles.Length+" 名":"");
                var body=new StackPanel();
                body.Children.Add(new TextBlock {Text=TimelineTitle(row),Style=(Style)Window.FindResource("RowTitle"),TextTrimming=TextTrimming.CharacterEllipsis});
                var meta=new TextBlock {FontSize=12,Foreground=Brush("#948F84"),Margin=new Thickness(0,4,0,0),TextTrimming=TextTrimming.CharacterEllipsis};
                meta.Inlines.Add(new System.Windows.Documents.Run(stamp) {FontFamily=(FontFamily)Window.FindResource("MonoFont"),Foreground=Brush("#C9C4B8")});
                meta.Inlines.Add(new System.Windows.Documents.Run("  ·  "+line));
                body.Children.Add(meta);
                timeline.Items.Add(new ListBoxItem {Content=body,IsEnabled=source.IsEnabled,ToolTip=Convert.ToString(source.Content)});
            }
            timeline.SelectedIndex=Get<ComboBox>("SaveBackupList").SelectedIndex;
        } finally { drawingTimeline=false; }
    }
    int DrawBackupTable(Dictionary<string,object> row) {
        var table=Get<StackPanel>("SelectedBackupTable"); table.Children.Clear();
        var rows=Rows(row,"character_preview").ToArray();
        if(rows.Length==0) return 0;
        Func<string,double,string,int,TextBlock> cell=(text,size,color,column)=>{ var t=new TextBlock {Text=text,FontSize=size,Foreground=Brush(color),VerticalAlignment=VerticalAlignment.Center,TextTrimming=TextTrimming.CharacterEllipsis}; Grid.SetColumn(t,column); return t; };
        Func<Grid> line=()=>{ var g=new Grid {Margin=new Thickness(0,0,0,0)}; foreach(var w in new [] {64.0,0,80,110,110}) g.ColumnDefinitions.Add(new ColumnDefinition {Width=w==0 ? new GridLength(1,GridUnitType.Star):new GridLength(w)}); return g; };
        var header=line(); int c=0;
        foreach(string h in new [] {"槽位","角色","等级","回响","来源"}) header.Children.Add(cell(h,12,"#7D7A73",c++));
        table.Children.Add(new Border {Child=header,BorderBrush=Brush("#2E2C31"),BorderThickness=new Thickness(0,0,0,1),Padding=new Thickness(0,0,0,7)});
        foreach(var r in rows) {
            var g=line(); string issue=Str(r,"issue"), source=Str(r,"source");
            g.Children.Add(cell(SlotLabel(Str(r,"slot")),13,"#A8A397",0));
            var name=cell(issue=="unrecognized" ? "无法识别":issue=="read_failed" ? "读取失败":Str(r,"name").Length>0 ? Str(r,"name"):"姓名未识别",14.5,issue.Length>0 && issue!="name_unavailable" ? "#D9BE8A":"#EDE6D6",1);
            name.FontFamily=(FontFamily)Window.FindResource("DisplayFont"); g.Children.Add(name);
            g.Children.Add(cell(Str(r,"level").Length>0 ? Str(r,"level"):"—",13,"#D2CCBF",2));
            g.Children.Add(cell(Str(r,"echoes").Length>0 ? Number(r,"echoes"):"—",13,"#D2CCBF",3));
            g.Children.Add(cell((source=="both" ? "主档+备档":source=="primary" ? "仅主档":"仅备档")+(Flag(r,"identity_conflict") ? " · 身份不一致":""),12,Flag(r,"identity_conflict") ? "#E0B66E":"#9A958A",4));
            table.Children.Add(new Border {Child=g,BorderBrush=Brush("#242226"),BorderThickness=new Thickness(0,0,0,1),Padding=new Thickness(0,7,0,7)});
        }
        return rows.Length;
    }
    void DrawCurrentSlots(Dictionary<string,object> plan) {
        var panel=Get<WrapPanel>("CurrentSlots"); panel.Children.Clear();
        if(plan==null) return;
        var preview=Rows(plan,"character_preview").ToArray();
        foreach(var row in Rows(plan,"characters").Where(r=>!EmptyGeneration(Str(r,"primary")) || !EmptyGeneration(Str(r,"backup")))) {
            string slot=Str(row,"slot"), primary=Str(row,"primary"), backup=Str(row,"backup");
            bool both=Readability(primary)=="可读" && Readability(backup)=="可读", bad=Flag(row,"identity_conflict") || primary=="unreadable" || backup=="unreadable" || primary=="invalid" || backup=="invalid";
            string tag=bad ? "需核对":both ? "主档+备档":Readability(primary)=="可读" ? "仅主档":Readability(backup)=="可读" ? "仅备档":"未确认";
            var detail=preview.FirstOrDefault(p=>Str(p,"slot")==slot && Str(p,"level").Length>0);
            var head=new DockPanel();
            var badge=new Border {Style=(Style)Window.FindResource("Tag"),BorderBrush=Brush(bad ? "#8A5A50":"#3A383D"),Child=new TextBlock {Text=tag,FontSize=11,Foreground=Brush(bad ? "#D58A80":"#A8A397")}};
            DockPanel.SetDock(badge,Dock.Right); head.Children.Add(badge);
            head.Children.Add(new TextBlock {Text=Str(row,"name").Length>0 ? Str(row,"name"):"姓名未识别",FontFamily=(FontFamily)Window.FindResource("DisplayFont"),FontSize=17,Foreground=Brush(bad ? "#D9BE8A":"#EDE6D6"),VerticalAlignment=VerticalAlignment.Center,TextTrimming=TextTrimming.CharacterEllipsis});
            var body=new StackPanel(); body.Children.Add(head);
            body.Children.Add(new TextBlock {Text="槽位 "+SlotLabel(slot)+"  ·  "+(detail!=null ? "等级 "+Str(detail,"level")+" · 回响 "+Number(detail,"echoes"):"主档"+Readability(primary)+" · 备档"+Readability(backup)),FontSize=12,Foreground=Brush("#948F84"),Margin=new Thickness(0,5,0,0),TextTrimming=TextTrimming.CharacterEllipsis});
            panel.Children.Add(new Border {Style=(Style)Window.FindResource("Card"),Width=276,Margin=new Thickness(0,0,10,10),Padding=new Thickness(16,11,14,11),BorderBrush=Brush(bad ? "#6E4A44":"#2E2C31"),Child=body});
        }
    }
    void ShowSaveState(int state,string message) {
        // state: 1 ok, 0 needs a decision, -1 check failed, 2 neutral.
        var strip=Get<Border>("SaveStateStrip"); var icon=Get<TextBlock>("SaveStateIcon");
        string color=state==1 ? "#8FAE8A":state==0 ? "#E0B66E":state==-1 ? "#D58A80":"#A8A397";
        icon.Text=state==1 ? "":state==0 ? "":state==-1 ? "":""; icon.Foreground=Brush(color);
        strip.BorderBrush=Brush(state==1 ? "#3C4A3A":state==0 ? "#6E5A38":state==-1 ? "#6E4A44":"#3A383D");
        strip.Background=Brush(state==1 ? "#161A16":state==0 ? "#1D1A16":state==-1 ? "#1E1716":"#18171A");
        Get<TextBlock>("SaveStateText").Text=message;
    }
    async Task LoadSaveBackups() {
        var ordinary=await Request("save_backups.py",Map("action","list"),"");
        var special=await Request("save_resolution.py",Map("action","list"),"");
        ordinary["backups"]=Rows(ordinary,"backups").Concat(Rows(special,"backups")).ToArray();
        ordinary["warnings"]=Warnings(ordinary).Concat(Warnings(special)).ToArray();
        ShowSaveBackups(ordinary);
    }
    // Home "角色" card: list of verified backups; shows the newest launch/pre-restore snapshot.
    // Startup must stay read-only. With an import/restore lock present, save_backups list may
    // finish the interrupted restore, so leave that to the Save page and skip the read here.
    async Task RefreshHomeSaves() {
        if(File.Exists(PathAt(".save-import.lock"))) {
            Get<StackPanel>("HomeCharacters").Children.Clear(); Get<TextBlock>("HomeSaveSource").Text="";
            Get<TextBlock>("HomeSaveNote").Text="有未完成的存档导入或恢复，请打开「存档」页处理。"; return;
        }
        try { DrawHomeSaves(await Request("save_backups.py",Map("action","list"),"")); }
        catch { if(!closed) Get<TextBlock>("HomeSaveNote").Text="暂时无法读取备份；打开「存档」页可重新检查。"; }
    }
    static string Friendly(DateTimeOffset time) {
        var local=time.ToLocalTime().DateTime; int days=(DateTime.Today-local.Date).Days;
        return (days==0 ? "今天 ":days==1 ? "昨天 ":local.ToString("MM-dd "))+local.ToString("HH:mm");
    }
    void DrawHomeSaves(Dictionary<string,object> result) {
        if(closed) return;
        var panel=Get<StackPanel>("HomeCharacters"); panel.Children.Clear();
        Dictionary<string,object> latest=null; DateTimeOffset latestTime=DateTimeOffset.MinValue;
        foreach(var row in Rows(result,"backups")) {
            string kind=Str(row,"kind"); DateTimeOffset time;
            if((kind=="launch" || kind=="pre-restore") && DateTimeOffset.TryParse(Str(row,"time"),out time) && time>latestTime) { latest=row; latestTime=time; }
        }
        var note=Get<TextBlock>("HomeSaveNote"); var source=Get<TextBlock>("HomeSaveSource");
        if(latest==null) { source.Text=""; note.Text="还没有备份。存档非空时，启动游戏前会自动备份。"; return; }
        source.Text="截至 "+Friendly(latestTime)+" · "+(Str(latest,"kind")=="launch" ? "游戏启动前备份":"恢复前备份");
        var all=Rows(latest,"character_preview").ToArray();
        var roles=all.Where(r=>Str(r,"source")!="backup" || !all.Any(o=>Str(o,"slot")==Str(r,"slot") && Str(o,"source")!="backup")).ToArray();
        Func<string,double,string,int,TextBlock> cell=(text,size,color,column)=>{ var t=new TextBlock {Text=text,FontSize=size,Foreground=Brush(color),VerticalAlignment=VerticalAlignment.Center,TextTrimming=TextTrimming.CharacterEllipsis}; Grid.SetColumn(t,column); return t; };
        foreach(var r in roles.Take(4)) {
            var g=new Grid(); foreach(var w in new [] {64.0,0,90,120}) g.ColumnDefinitions.Add(new ColumnDefinition {Width=w==0 ? new GridLength(1,GridUnitType.Star):new GridLength(w)});
            string issue=Str(r,"issue");
            g.Children.Add(cell("槽位 "+SlotLabel(Str(r,"slot")),12,"#948F84",0));
            var name=cell(issue=="unrecognized" ? "无法识别":issue=="read_failed" ? "读取失败":Str(r,"name").Length>0 ? Str(r,"name"):"姓名未识别",15,issue.Length>0 && issue!="name_unavailable" ? "#D9BE8A":"#EDE6D6",1);
            name.FontFamily=(FontFamily)Window.FindResource("DisplayFont"); g.Children.Add(name);
            g.Children.Add(cell(Str(r,"level").Length>0 ? "Lv "+Str(r,"level"):"—",12.5,"#C9C3B6",2));
            g.Children.Add(cell(Str(r,"echoes").Length>0 ? "回响 "+Number(r,"echoes"):"",12.5,"#C9C3B6",3));
            panel.Children.Add(new Border {Child=g,BorderBrush=Brush("#29272C"),BorderThickness=new Thickness(0,0,0,1),Padding=new Thickness(0,8,0,8)});
        }
        note.Text=roles.Length==0 ? "这份备份中没有可显示的角色。":(roles.Length>4 ? "另有 "+(roles.Length-4)+" 名角色。":"")+"来自最近一次自动备份，下次启动游戏前会更新。";
    }
    void DrawHomeTrophies(Dictionary<string,object>[] rows,int count) {
        Get<ProgressBar>("HomeTrophyProgress").Value=count;
        var panel=Get<StackPanel>("HomeRecentTrophies"); panel.Children.Clear();
        var recent=rows.Where(r=>r["unlocked"]!=null).OrderByDescending(r=>Convert.ToDouble(r["unlocked"])).Take(3).ToArray();
        if(recent.Length==0) { panel.Children.Add(new TextBlock {Text="还没有解锁的奖杯。",FontSize=12.5,Foreground=Brush("#948F84"),Margin=new Thickness(0,8,0,0)}); return; }
        foreach(var row in recent) {
            var grid=new Grid {Margin=new Thickness(0,9,0,0)};
            grid.ColumnDefinitions.Add(new ColumnDefinition {Width=new GridLength(42)}); grid.ColumnDefinitions.Add(new ColumnDefinition());
            BitmapSource source; FrameworkElement icon;
            if(images.TryGetValue(Convert.ToInt32(row["id"]),out source)) icon=new Image {Source=source,Width=32,Height=32,HorizontalAlignment=HorizontalAlignment.Left};
            else icon=new TextBlock {Text="♜",FontSize=20,Foreground=Brush("#BBA47A"),VerticalAlignment=VerticalAlignment.Center,Margin=new Thickness(6,0,0,0)};
            grid.Children.Add(icon);
            string when=""; try { when=new DateTime(1970,1,1,0,0,0,DateTimeKind.Utc).AddSeconds(Convert.ToDouble(row["unlocked"])).ToLocalTime().ToString("yyyy-MM-dd HH:mm"); } catch(ArgumentOutOfRangeException) {}
            var body=new StackPanel {VerticalAlignment=VerticalAlignment.Center}; Grid.SetColumn(body,1);
            body.Children.Add(new TextBlock {Text=Str(row,"name"),FontSize=13,Foreground=Brush("#E4DFD4"),TextTrimming=TextTrimming.CharacterEllipsis});
            if(when.Length>0) body.Children.Add(new TextBlock {Text=when,FontSize=11.5,Foreground=Brush("#948F84"),FontFamily=(FontFamily)Window.FindResource("MonoFont"),Margin=new Thickness(0,2,0,0)});
            grid.Children.Add(body); panel.Children.Add(grid);
        }
    }
    void ShowSaveBackups(Dictionary<string,object> result) {
        DrawHomeSaves(result);
        var list=Get<ComboBox>("SaveBackupList"); string selected="";
        var previous=list.SelectedItem as ComboBoxItem;
        if(previous!=null) selected=Str(previous.Tag as Dictionary<string,object>,"id");
        drawingBackups=true;
        try {
        list.Items.Clear();
        foreach(var row in Rows(result,"backups")) {
            DateTimeOffset time; string stamp=Str(row,"time");
            if(DateTimeOffset.TryParse(stamp,out time)) stamp=time.ToLocalTime().ToString("yyyy-MM-dd HH:mm:ss");
            string kind=Str(row,"kind"), label=kind=="resolution-before" ? Str(row,"label"):kind=="pre-restore" ? "恢复前":kind=="launch" ? "启动前":"未知类型（不可恢复）";
            var item=new ComboBoxItem { Content=stamp+" · "+label+" · "+Str(row,"file_count")+" 个文件 · "+Convert.ToInt64(row["total_bytes"]).ToString("N0")+" 字节"+
                (kind=="resolution-before" ? "\n"+CharacterSummary(row):""), Tag=row, IsEnabled=kind=="launch" || kind=="pre-restore" || kind=="resolution-before" };
            list.Items.Add(item); if(Str(row,"id")==selected) list.SelectedItem=item;
        }
        if(list.SelectedIndex<0 && list.Items.Count>0) list.SelectedIndex=0;
        } finally { drawingBackups=false; }
        pendingSaveRestore=result["pending"] as Dictionary<string,object>;
        var warnings=((System.Collections.IEnumerable)result["warnings"]).Cast<object>().Select(x=>Convert.ToString(x)).ToArray();
        Get<TextBlock>("SaveBackupStatus").Text=(list.Items.Count==0?"还没有可选备份。存档非空时，启动游戏前会自动创建普通备份。":"已读取 "+list.Items.Count+" 份备份。正常恢复普通备份只替换 savedata；异常处理页的整套恢复还会停止旧联机待补记录。处理前存档按专用事务换回联机记录，不代表可直接启动。")+(warnings.Length>0?"\n"+String.Join("\n",warnings):"");
        Get<TextBlock>("PendingSaveRestore").Text=Str(pendingSaveRestore,"message");
        DrawBackupTimeline();
        ShowSelectedBackup();
    }
    async Task RefreshSaveBackups() {
        RequireIdle(); InvalidateResolution(); int generation=resolutionGeneration; operation=true; SetBusy(); Get<TextBlock>("SaveBackupStatus").Text="正在读取并校验备份…";
        try {
            Exception listFailure=null;
            try { await LoadSaveBackups(); }
            catch(Exception e) {
                listFailure=e; drawingBackups=true;
                try { Get<ComboBox>("SaveBackupList").Items.Clear(); pendingSaveRestore=null; DrawBackupTimeline(); ShowSelectedBackup(); }
                finally { drawingBackups=false; }
                Get<TextBlock>("SaveBackupStatus").Text="备份列表读取失败："+e.Message+"\n仍会单独检查能否继续上次的选择。";
            }
            await LoadSaveResolution(null,generation);
            if(listFailure!=null) throw new InvalidOperationException("异常处理状态已单独检查；备份列表暂不可用："+listFailure.Message,listFailure);
        }
        finally { operation=false; SetBusy(); }
    }
    async Task RestoreSaveBackup() {
        RequireIdle(); var selected=Get<ComboBox>("SaveBackupList").SelectedItem as ComboBoxItem;
        if(selected==null) throw new InvalidOperationException("请先选择已校验的备份。");
        var row=selected.Tag as Dictionary<string,object>;
        string kind=Str(row,"kind");
        if(kind=="resolution-before" || (resolutionNeeded && (kind=="launch" || kind=="pre-restore"))) {
            InvalidateResolution(); int generation=resolutionGeneration; operation=true; SetBusy();
            try { await LoadSaveResolution(row,generation); }
            finally { operation=false; SetBusy(); }
            return;
        }
        if(kind!="launch" && kind!="pre-restore") throw new InvalidOperationException("未知备份类型，不能执行恢复。");
        InvalidateResolution();
        if(!Confirm("恢复到："+Convert.ToString(selected.Content)+"\n"+CharacterSummary(row)+"\n\n当前存档会先自动备份。只恢复无缝版 savedata；奖杯和联机辅助记录不变。\n\n确认恢复？","恢复存档")) return;
        await ApplySaveBackup(Map("action","restore","id",Str(row,"id"),"fingerprint",Str(row,"fingerprint"),"confirmed",true));
    }
    async Task ResumeSaveRestore() {
        RequireIdle(); if(!Flag(pendingSaveRestore,"can_resume")) throw new InvalidOperationException("这不是可安全处理的恢复事务，请保留现场。");
        if(!Confirm(Str(pendingSaveRestore,"message")+"\n\n只处理这次已验证的恢复事务；未知状态不会覆盖进度。确认继续？","处理未完成恢复")) return;
        await ApplySaveBackup(Map("action","resume","fingerprint",Str(pendingSaveRestore,"fingerprint"),"confirmed",true));
    }
    async Task ApplySaveBackup(Dictionary<string,object> request) {
        operation=true; SetBusy(); Get<TextBlock>("SaveBackupStatus").Text="正在校验并处理存档恢复，请等待完成…";
        Dictionary<string,object> completed=null; Exception failure=null;
        try {
            try {
                completed=await Request("save_backups.py",request,"");
                ShowSaveBackups(await Request("save_backups.py",Map("action","list"),""));
                Get<TextBlock>("SaveBackupStatus").Text=Str(completed,"message")+"\n"+Get<TextBlock>("SaveBackupStatus").Text;
                return;
            } catch(Exception e) { failure=e; }
            string message=failure.Message;
            if(completed!=null) message=Str(completed,"message")+"\n备份列表刷新失败："+message;
            else { try { ShowSaveBackups(await Request("save_backups.py",Map("action","list"),"")); } catch(Exception refresh) { message+="\n备份状态读取失败："+refresh.Message; } }
            Get<TextBlock>("SaveBackupStatus").Text=message; throw new InvalidOperationException(message,failure);
        }
        finally { operation=false; SetBusy(); }
    }
    void SaveOrdinarySource(string source) {
        var profile=File.Exists(PathAt("profile.json")) ? ReadJson(PathAt("profile.json")):Map();
        if(source.Length==0) profile.Remove("ordinary_savedata"); else profile["ordinary_savedata"]=source;
        WriteJson(PathAt("profile.json"),profile);
        inspection=null; InvalidateResolution(); SetBusy();
        Get<TextBlock>("SourceSavePath").Text=source.Length==0 ? "已清除手动来源，使用自动查找。":source;
    }
    async Task<Dictionary<string,object>> InspectOrdinarySource(string manual,bool preview=false) {
        string install=Text("InstallPath");
        string result=PathAt("save-transfer-result.json"); if(File.Exists(result)) File.Delete(result);
        string arguments="--source-only --install "+Quote(String.IsNullOrWhiteSpace(install) ? "":Path.GetDirectoryName(install))+" --result "+Quote(result);
        if(manual!=null) arguments+=" --manual-source "+Quote(manual);
        if(preview) arguments+=" --preview-source";
        using(var process=Process.Start(Python("save_transfer.py",arguments))) {
            var output=process.StandardOutput.ReadToEndAsync(); var errors=process.StandardError.ReadToEndAsync();
            await Task.Run(()=>process.WaitForExit()); await Task.WhenAll(output,errors);
            var value=ReadJson(result);
            if(process.ExitCode!=0 || !Flag(value,"ok")) throw new InvalidOperationException(Str(value,"error"));
            return value;
        }
    }
    async Task PickSaveFolder() {
        RequireIdle();
        if(!File.Exists(Text("InstallPath"))) throw new InvalidOperationException("请先在‘游戏与联机’选择存在的普通版 Bloodborne.exe，再手动选择存档文件夹。");
        var assembly=System.Reflection.Assembly.Load("System.Windows.Forms, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089");
        var type=assembly.GetType("System.Windows.Forms.FolderBrowserDialog",true);
        string selected;
        using(var dialog=(IDisposable)Activator.CreateInstance(type)) {
            type.GetProperty("Description").SetValue(dialog,"选择 user、savedata 或 SPRJ0005 存档文件夹",null);
            type.GetProperty("ShowNewFolderButton").SetValue(dialog,false,null);
            if(Convert.ToInt32(type.GetMethod("ShowDialog",Type.EmptyTypes).Invoke(dialog,null))!=1) return;
            selected=(string)type.GetProperty("SelectedPath").GetValue(dialog,null);
        }
        await SetOrdinarySource(selected);
    }
    async Task SetOrdinarySource(string selected) {
        RequireIdle(); operation=true; SetBusy();
        try {
            var value=await InspectOrdinarySource(selected,true);
            if(!Flag(value,"exists")) throw new InvalidOperationException(Str(value,"missing_message"));
            SaveOrdinarySource(Str(value,"source"));
            if(!Directory.Exists(PathAt("data"))) { ShowOrdinaryPreview(value); return; }
        } finally { operation=false; SetBusy(); }
        await Transfer(false);
    }
    void ShowOrdinaryPreview(Dictionary<string,object> value) {
        inspection=null; InvalidateResolution();
        Get<TextBlock>("SourceSavePath").Text=Str(value,"source");
        Get<TextBlock>("SaveSummary").Text=Str(value,"source_label")+"\n"+OrdinaryCharacters(value)+"\n首次启动会复制此来源；尚未初始化，当前没有改动存档。";
    }
    async Task ClearOrdinarySource() {
        RequireIdle(); SaveOrdinarySource("");
        if(Directory.Exists(PathAt("data"))) { await Transfer(false); return; }
        operation=true; SetBusy();
        try {
            var value=await InspectOrdinarySource(null,true);
            if(Flag(value,"exists")) ShowOrdinaryPreview(value);
            else { Get<TextBlock>("SourceSavePath").Text=Str(value,"source"); Get<TextBlock>("SaveSummary").Text=Str(value,"missing_message"); }
        } finally { operation=false; SetBusy(); }
    }
    static string OrdinaryCharacters(Dictionary<string,object> value) {
        return String.Join("\n",Rows(value,"characters").Select(row=>"角色："+Str(row,"name")+" · 槽位 "+Str(row,"slot")).ToArray());
    }
    async Task Transfer(bool apply) {
        RequireIdle(); if(!File.Exists(Text("InstallPath"))) throw new InvalidOperationException("请先选择普通版 Bloodborne.exe。");
        InvalidateResolution();
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
                else { inspection=value; Get<TextBlock>("SaveSummary").Text=Str(value,"source_label")+"\n"+Str(value,"file_count")+" 个文件 · 最近保存："+Str(value,"latest_write")+"\n"+OrdinaryCharacters(value); }
            }
        } catch(Exception e) { Get<TextBlock>("SaveSummary").Text=e.Message; throw; }
        finally { operation=false; SetBusy(); }
    }
    void UseLocalServer() {
        RequireIdle(); string address="127.0.0.1";
        if(File.Exists(PathAt(@"server\server.json"))) address=Str(ReadJson(PathAt(@"server\server.json")),"address");
        IPAddress ip; if(!IPAddress.TryParse(address,out ip) || ip.AddressFamily!=AddressFamily.InterNetwork || address=="0.0.0.0") throw new InvalidOperationException("请先在开服窗口配置有效的本机 IPv4 地址。");
        Get<TextBox>("Shadnet").Text=address+":31413"; Get<TextBox>("Webapi").Text="http://"+address+":31415";
        Get<TextBox>("Community").Text="http://"+address+":20443"; Get<TextBox>("Relay").Text=address+":20444"; ShowServerHost(); Page("Settings");
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
            var panel=new StackPanel {Margin=new Thickness(14,0,14,4)}; var expander=new Expander {Header=group,Content=panel,Margin=new Thickness(0,0,0,10),Foreground=Brush("#E6DFCF"),FontSize=14.5}; expanders.Add(expander);
            foreach(Dictionary<string,object> item in ((System.Collections.IEnumerable)catalog["settings"]).Cast<Dictionary<string,object>>().Where(i=>Str(i,"category")==group)) {
                string id=Str(item,"id"), type=Str(item,"type"); var body=new StackPanel();
                if(type=="bool") { var c=new CheckBox {Content=Str(item,"title"),IsEnabled=Flag(item,"editable"),IsChecked=Convert.ToBoolean(savedFeatures[id]),FontSize=14}; featureControls[id]=c; body.Children.Add(c); c.Checked+=FeatureChanged; c.Unchecked+=FeatureChanged; }
                else { body.Children.Add(Block(Str(item,"title"),14)); if(type=="int") { var c=new TextBox {Text=Convert.ToString(savedFeatures[id]),Width=90,HorizontalAlignment=HorizontalAlignment.Left,Margin=new Thickness(0,8,0,0)}; featureControls[id]=c; body.Children.Add(c); c.TextChanged+=FeatureChanged; } }
                body.Children.Add(Block(Str(item,"description"),12)); body.Children.Add(Block(Str(item,"scope"),11));
                panel.Children.Add(new Border {Child=body,BorderBrush=Brush("#29272C"),BorderThickness=new Thickness(0,0,0,1),Padding=new Thickness(4,12,4,12)});
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
    void FeatureSummary() { if(featureControls.ContainsKey("shared_progress")) Get<TextBlock>("FeatureSummary").Text="共享进度："+(((CheckBox)featureControls["shared_progress"]).IsChecked==true?"开":"关")+"\n敌人强化："+(((CheckBox)featureControls["scaling"]).IsChecked==true?"开":"关")+"\n队友互伤由纷争之铃控制"; }
    void SaveFeatures() {
        RequireIdle(); var values=new Dictionary<string,object>();
        foreach(var p in featureControls) { if(p.Value is CheckBox) values[p.Key]=((CheckBox)p.Value).IsChecked==true; else { int n; if(!Regex.IsMatch(((TextBox)p.Value).Text,@"^\d+$") || !Int32.TryParse(((TextBox)p.Value).Text,out n)) throw new InvalidDataException("需要填写整数百分比。"); values[p.Key]=n; } }
        values=ValidateFeatures(values);
        if((bool)values["shared_progress"] && !(bool)savedFeatures["shared_progress"] && !Confirm("客机归返后会把房主已有的 Boss、捷径、门和提灯进度追加到客机无缝存档。再次关闭不会撤销。建议使用合作角色，并备份双方存档。\n\n确认开启永久共享进度？","永久共享世界进度")) return;
        WriteJson(PathAt("features.json"),Map("schema",1,"values",values)); savedFeatures=values; featuresDirty=false; Get<TextBlock>("FeatureStatus").Text="功能配置已保存，下次启动生效。"; FeatureSummary();
    }
    static SolidColorBrush Brush(string color) { return new SolidColorBrush((Color)ColorConverter.ConvertFromString(color)); }
    static TextBlock Block(string text,int size) { return new TextBlock {Text=text,FontSize=size,Foreground=Brush("#9A958A"),TextWrapping=TextWrapping.Wrap,Margin=new Thickness(0,5,0,0),LineHeight=size+8}; }
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
        if(data==null) { Get<TextBlock>("TrophySummary").Text="记录暂不可用"; Get<TextBlock>("TrophyNotice").Text=trophies.Warning; Get<Button>("HomeTrophies").Content="奖杯 · 记录暂不可用"; var recentPanel=Get<StackPanel>("HomeRecentTrophies"); recentPanel.Children.Clear(); recentPanel.Children.Add(new TextBlock {Text="奖杯记录暂不可用。",FontSize=12.5,Foreground=Brush("#948F84"),Margin=new Thickness(0,8,0,0)}); return; }
        var rows=((System.Collections.IEnumerable)data["trophies"]).Cast<Dictionary<string,object>>().ToArray();
        int count=rows.Count(r=>r["unlocked"]!=null), baseCount=rows.Count(r=>Str(r,"group")=="base" && r["unlocked"]!=null);
        Get<TextBlock>("TrophySummary").Text="已解锁 "+count+" / 40    本体 "+baseCount+" / 34    老猎人 "+(count-baseCount)+" / 6";
        Get<Button>("HomeTrophies").Content="奖杯 · "+count+" / 40"; Get<ProgressBar>("TrophyProgress").Value=count; DrawHomeTrophies(rows,count);
        Get<TextBlock>("TrophyNotice").Text=trophies.Warning!=null ? "读取失败，当前显示上次成功结果。"+trophies.Warning :
            (Flag(data,"missing") ? "尚无奖杯记录。":"最近读取："+DateTime.Now.ToString("HH:mm:ss"))+" · "+Str(data,"path")+
            (Convert.ToInt32(data["ignored"])>0 ? " · 已忽略 "+data["ignored"]+" 行无效记录":"")+(Flag(data,"platinum_due") ? "\n白金条件已满足，尚无解锁记录。":"");
        Get<TextBlock>("TrophySession").Text=trophies.Added.Length==0 ? "":"本次新增 "+trophies.Added.Length+" 项："+String.Join("、",rows.Where(r=>trophies.Added.Contains(Convert.ToInt32(r["id"]))).Select(r=>Str(r,"name")).ToArray());
        string prior="";
        foreach(var row in rows) {
            bool earned=row["unlocked"]!=null;
            if(Get<RadioButton>("TrophyEarned").IsChecked==true && !earned || Get<RadioButton>("TrophyLocked").IsChecked==true && earned) continue;
            string group=Str(row,"group"); if(prior!=group) { panel.Children.Add(Block(group=="base"?"BLOODBORNE · 本体":"THE OLD HUNTERS · 老猎人",14)); prior=group; }
            var grid=new Grid(); grid.ColumnDefinitions.Add(new ColumnDefinition {Width=new GridLength(64)}); grid.ColumnDefinitions.Add(new ColumnDefinition());
            BitmapSource source; FrameworkElement icon;
            if(images.TryGetValue(Convert.ToInt32(row["id"]),out source)) icon=new Image {Source=source,Width=48,Height=48,Opacity=earned?1:.30,HorizontalAlignment=HorizontalAlignment.Left};
            else icon=new TextBlock {Text="♜",FontSize=28,Foreground=Brush("#BBA47A"),VerticalAlignment=VerticalAlignment.Center,HorizontalAlignment=HorizontalAlignment.Left,Margin=new Thickness(8,0,0,0),Opacity=earned?1:.35};
            grid.Children.Add(icon); var body=new StackPanel(); Grid.SetColumn(body,1); grid.Children.Add(body);
            string grade=Str(row,"grade"); var grades=new Dictionary<string,string>{{"platinum","白金"},{"gold","金"},{"silver","银"},{"bronze","铜"}};
            body.Children.Add(new TextBlock {Text=Str(row,"name")+"  ·  "+grades[grade],FontSize=14,Foreground=Brush(earned?"#EDE6D6":"#8F8A7F"),TextWrapping=TextWrapping.Wrap});
            body.Children.Add(Block(Str(row,"description").Replace("\n",""),12));
            string when="未解锁"; if(earned) { try { when="已解锁 · "+new DateTime(1970,1,1,0,0,0,DateTimeKind.Utc).AddSeconds(Convert.ToDouble(row["unlocked"])).ToLocalTime().ToString("yyyy-MM-dd HH:mm"); } catch(ArgumentOutOfRangeException) { when="已解锁 · 时间超出显示范围"; } }
            body.Children.Add(Block(when,11)); panel.Children.Add(new Border {Child=grid,Padding=new Thickness(8,12,8,12),BorderBrush=Brush("#29272C"),BorderThickness=new Thickness(0,0,0,1)});
        }
    }
    public void Validate(string capture,string page) {
        foreach(string n in conversions.Keys) Get<Button>(n);
        Get<Border>("SaveBackupsPanel"); Get<ComboBox>("SaveBackupList");
        foreach(string n in new [] {"RefreshSaveBackups","RestoreSaveBackup","ResumeSaveRestore"}) Get<Button>(n);
        foreach(string n in new [] {"Launch","SaveSettings","RefreshSaves","ImportSaves","OpenLocalServer","UseLocalServer","AdminRefresh","AdminCreate","AdminVerify","SaveFeatures","HomeTrophies","RefreshTrophies"}) Get<Button>(n);
        Page(page); if(page=="Feature") expanders[0].IsExpanded=true;
        if(String.IsNullOrEmpty(capture)) return;
        var view=(FrameworkElement)Window.Content; view.Width=1160; view.Height=800;
        view.Measure(new Size(1160,800)); view.Arrange(new Rect(0,0,1160,800)); view.UpdateLayout();
        var bitmap=new RenderTargetBitmap(1160,800,96,96,PixelFormats.Pbgra32); bitmap.Render(view);
        var encoder=new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap)); using(var file=File.Create(capture)) encoder.Save(file);
    }
}
