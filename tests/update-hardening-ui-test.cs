// Offline regressions against real WPF dispatchers and disposable child processes.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Runtime.Serialization;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;

public static class HardeningTest {
    static readonly BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    static void Require(bool ok, string message) { if (!ok) throw new Exception(message); }
    static void Set(object target, string name, object value) { target.GetType().GetField(name, Private).SetValue(target, value); }
    static object Call(object target, string name, params object[] args) { return target.GetType().GetMethod(name, Private).Invoke(target, args); }
    static void Pump(Func<bool> done) {
        var frame = new DispatcherFrame(); var clock = Stopwatch.StartNew();
        var timer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(10) };
        timer.Tick += delegate { if (done() || clock.ElapsedMilliseconds > 7000) frame.Continue = false; };
        timer.Start(); Dispatcher.PushFrame(frame); timer.Stop(); Require(done(), "Dispatcher timed out");
    }
    static Process Worker(string mode) {
        return Process.Start(new ProcessStartInfo(Assembly.GetExecutingAssembly().Location, mode) {
            UseShellExecute = false, CreateNoWindow = true, RedirectStandardInput = true,
            RedirectStandardOutput = true, RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8 });
    }
    static object Manager(Assembly server, Process process) {
        var manager = FormatterServices.GetUninitializedObject(server.GetType("DesktopManager"));
        Set(manager, "pipeLock", new SemaphoreSlim(1, 1)); Set(manager, "json", new JavaScriptSerializer());
        Set(manager, "requestTimeoutMilliseconds", 350); Set(manager, "worker", process);
        var job = Activator.CreateInstance(server.GetType("OwnedJob"));
        job.GetType().GetMethod("Assign").Invoke(job, new object[] { process }); Set(manager, "job", job);
        return manager;
    }
    static void Workers(Assembly server) {
        using (var process = Worker("--pipe-echo")) {
            var manager = Manager(server, process);
            try {
                var task = (Task<object>)Call(manager, "Send", new Dictionary<string, object> { {"value", "雪 Δ 😀"} });
                Require(task.Wait(2000), "Pipe echo timed out");
                Require((string)((Dictionary<string, object>)task.Result)["value"] == "雪 Δ 😀", "Server stdin Unicode was damaged");
            } finally { ((IDisposable)manager.GetType().GetField("job", Private).GetValue(manager)).Dispose(); process.WaitForExit(2000); }
        }
        using (var process = Worker("--pipe-hang")) {
            var manager = Manager(server, process); var clock = Stopwatch.StartNew();
            var first = (Task<object>)Call(manager, "Send", new Dictionary<string, object> { {"action", "status"} });
            var queued = (Task<object>)Call(manager, "Send", new Dictionary<string, object> { {"action", "status"} });
            Pump(() => first.IsCompleted && queued.IsCompleted);
            Require(first.IsFaulted && first.Exception.GetBaseException() is TimeoutException, "Silent worker did not time out");
            Require(queued.IsFaulted && queued.Exception != null && clock.ElapsedMilliseconds < 2000, "Queued request remained stuck");
            Require(process.WaitForExit(2000), "Timed out worker survived");
            var retry = (Task<object>)Call(manager, "Send", new Dictionary<string, object>());
            Pump(() => retry.IsCompleted); Require(retry.IsFaulted && retry.Exception != null, "Poisoned worker accepted another request");
        }
        foreach (bool blockedWriter in new[] { false, true }) using (var process = Worker("--pipe-hang")) {
            var manager = Manager(server, process);
            Task writer = null;
            if (blockedWriter) {
                var pipe = (SemaphoreSlim)manager.GetType().GetField("pipeLock", Private).GetValue(manager);
                pipe.Wait();
                writer = Task.Run(delegate {
                    try { process.StandardInput.Write(new string('x', 4 * 1024 * 1024)); process.StandardInput.Flush(); }
                    catch (IOException) { } finally { pipe.Release(); }
                });
                Thread.Sleep(50);
            }
            var window = new Window { Width = 200, Height = 80, Left = -20000, Top = -20000, ShowInTaskbar = false, ShowActivated = false };
            Set(manager, "window", window);
            window.Closing += (CancelEventHandler)Delegate.CreateDelegate(typeof(CancelEventHandler), manager, "Closing");
            window.Show(); var clock = Stopwatch.StartNew(); window.Close();
            Require(clock.ElapsedMilliseconds < 200, "Closing blocked the UI thread");
            bool responsive = false; window.Dispatcher.BeginInvoke(new Action(() => responsive = true));
            Pump(() => responsive); Require(clock.ElapsedMilliseconds < 1000, "UI stopped dispatching during shutdown");
            Pump(() => !window.IsVisible); Require(process.WaitForExit(2000), "Worker survived window close");
            if (writer != null) Require(writer.Wait(2000), "Blocked stdin writer survived worker close");
        }
        Console.WriteLine("PASS: Unicode pipe, silent worker timeout, bounded queued requests, asynchronous UI close and child cleanup");
    }
    static void Desktop(string source, string area) {
        string root = Path.Combine(area, "desktop"); Directory.CreateDirectory(root);
        foreach (string name in new[] { "trophy-catalog.json", "feature-catalog.json" })
            File.Copy(Path.Combine(source, "launcher", name), Path.Combine(root, name));
        File.WriteAllText(Path.Combine(root, "Launcher.xaml"), File.ReadAllText(Path.Combine(source, "launcher", "Launcher.xaml"))
            .Replace("@assets/", new Uri(Path.Combine(source, "assets") + Path.DirectorySeparatorChar).AbsoluteUri));
        File.WriteAllText(Path.Combine(root, "update-settings.json"), "{\"http_proxy\":\"invalid-proxy\"}");
        var desktop = new DesktopWindow(root); var window = desktop.Window;
        var gate = new TaskCompletionSource<bool>(); Set(desktop, "trophyRead", gate.Task);
        desktop.Start(); window.Left = -20000; window.Top = -20000; window.ShowInTaskbar = false; window.ShowActivated = false;
        window.Show(); Pump(() => ((Button)window.FindName("CheckUpdates")).ToolTip != null);
        Require(!gate.Task.IsCompleted, "Startup test did not hold the trophy refresh");
        window.Close(); gate.SetResult(false);
        string bin = Path.Combine(root, "runtime", "bin"); Directory.CreateDirectory(bin);
        File.Copy(Assembly.GetExecutingAssembly().Location, Path.Combine(bin, "python.exe"));
        var request = (Task<Dictionary<string, object>>)Call(desktop, "Request", "ignored.py", new Dictionary<string, object> { {"value", "雪 Δ 😀"} }, "");
        Pump(() => request.IsCompleted);
        Require((string)request.GetAwaiter().GetResult()["value"] == "雪 Δ 😀", "Desktop stdin Unicode was damaged under CP936");
        Console.WriteLine("PASS: startup check runs while trophy refresh is pending; desktop stdin survives CP936");
    }
    static string Hash(byte[] data) { using (var hash = SHA256.Create()) return BitConverter.ToString(hash.ComputeHash(data)).Replace("-", "").ToLowerInvariant(); }
    static void Downloads(string area) {
        string root = Path.Combine(area, "download"); Directory.CreateDirectory(root); File.WriteAllText(Path.Combine(root, "Apply-Update.ps1"), "synthetic-helper");
        string temp = Path.Combine(area, "temp"); Directory.CreateDirectory(temp);
        Environment.SetEnvironmentVariable("TEMP", temp); Environment.SetEnvironmentVariable("TMP", temp);
        var prepare = typeof(HuntUpdates).GetMethod("PrepareUpdate", BindingFlags.Static | BindingFlags.NonPublic);
        var cleanup = typeof(HuntUpdates).GetMethod("CleanupSession", BindingFlags.Static | BindingFlags.NonPublic);
        foreach (string mode in new[] { "short", "large", "hash", "cancel", "success" }) {
            var listener = new TcpListener(IPAddress.Loopback, 0); listener.Start();
            int port = ((IPEndPoint)listener.LocalEndpoint).Port;
            var payload = Encoding.ASCII.GetBytes(mode == "short" ? "small" : mode == "large" ? "01234567890123456789" : "0123456789");
            using (var cancellation = new CancellationTokenSource()) {
                var serving = Task.Run(delegate {
                    using (var peer = listener.AcceptTcpClient()) using (var stream = peer.GetStream()) {
                        // Read the complete request before responding.
                        int state = 0; while (state < 4) { int ch = stream.ReadByte(); if (ch < 0) break; state = (state == 0 || state == 2) ? (ch == 13 ? state + 1 : 0) : (ch == 10 ? state + 1 : 0); }
                        if (mode == "cancel") { cancellation.Cancel(); Thread.Sleep(100); return; }
                        var header = Encoding.ASCII.GetBytes("HTTP/1.1 200 OK\r\nContent-Length: " + payload.Length + "\r\nConnection: close\r\n\r\n");
                        stream.Write(header, 0, header.Length); stream.Write(payload, 0, payload.Length);
                    }
                });
                string session = null; bool rejected = false;
                try {
                    var release = new HuntRelease { VerifiedOrigin = true, Size = 10, Sha256 = mode == "hash" ? new string('0', 64) : Hash(payload), Version = "1.0.0", Download = "http://127.0.0.1:" + port + "/package" };
                    session = (string)prepare.Invoke(null, new object[] { release, root, "TheHuntLauncher", "", "", new Progress<int>(), cancellation.Token });
                } catch (TargetInvocationException) { rejected = true; }
                finally { listener.Stop(); Require(serving.Wait(2000), "Loopback response stalled"); }
                Require(rejected == (mode != "success"), "Wrong download result for " + mode);
                if (session != null) { Require(File.Exists(Path.Combine(session, "plan.json")), "Prepared plan missing"); cleanup.Invoke(null, new object[] { session }); }
                Require(Directory.GetDirectories(Path.Combine(temp, "TheHuntUpdates")).Length == 0, "Download leaked a session: " + mode);
            }
        }
        Require(Directory.GetFiles(Path.Combine(temp, "TheHuntUpdates"), "*.failure.json").Length == 4, "Failed downloads did not retain diagnostics");
        Console.WriteLine("PASS: loopback short/oversize/hash/cancel/success download session cleanup");
    }
    static void Guards(string area) {
        string parent = Path.Combine(area, "parent"), child = Path.Combine(parent, "server"); Directory.CreateDirectory(child);
        File.WriteAllText(Path.Combine(parent, "TheHuntLauncher.exe"), "synthetic-entry");
        Require(HuntUpdates.IsEmbeddedServer(child + Path.DirectorySeparatorChar), "Embedded server was not detected");
        Require(!HuntUpdates.IsEmbeddedServer(Path.Combine(area, "standalone")), "Standalone server was disabled");
        foreach (string root in new[] { parent, child }) {
            string marker = Path.Combine(root, ".update-transaction.json"); File.WriteAllText(marker, "interrupted/malformed");
            foreach (string entry in new[] { parent, child }) {
                bool blocked = false; try { HuntUpdates.EnsureNotUpdating(entry); } catch (IOException error) { blocked = error.Message.Contains(Path.Combine(root, "update-backups")); }
                Require(blocked, "Unfinished transaction did not block both entry points");
            }
            File.Delete(marker);
        }
        string path = Path.Combine(parent, ".update.lock");
        using (File.Open(path, FileMode.Create, FileAccess.ReadWrite, FileShare.None)) {
            bool blocked = false; try { HuntUpdates.EnsureNotUpdating(child); } catch (IOException) { blocked = true; }
            Require(blocked, "Embedded server ignored parent update lock");
        }
        Console.WriteLine("PASS: incomplete transactions and parent locks block client/embedded entry points with backup paths");
    }
    [STAThread] public static int Main(string[] args) {
        if (args.Length == 0 || args[0].StartsWith("--") || (args.Length > 0 && args[0] == "-B")) {
            if (args.Length > 0 && args[0] == "--pipe-hang") { Thread.Sleep(15000); return 0; }
            using (var reader = new StreamReader(Console.OpenStandardInput(), Encoding.GetEncoding(936))) {
                string data = args.Length > 0 && args[0] == "--pipe-echo" ? reader.ReadLine() : reader.ReadToEnd();
                var value = Json.Deserialize<Dictionary<string, object>>(data);
                Console.OutputEncoding = new UTF8Encoding(false);
                Console.WriteLine(Json.Serialize(new { ok = true, data = value, value = value["value"] })); return 0;
            }
        }
        try {
            SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext());
            string source = Path.GetFullPath(args[0]), area = Path.GetFullPath(args[1]); Directory.CreateDirectory(area);
            var server = Assembly.LoadFrom(args[2]);
            Guards(area); Downloads(area); Desktop(source, area); Workers(server);
            Console.WriteLine("PASS: ALL hardening regressions completed"); return 0;
        } catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}
