// A disposable, non-activating offscreen window in the real bin/bb-probe.exe layout.
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;

internal static class GameWindowProbe {
    [DllImport("user32.dll",EntryPoint="GetWindowLongW")] static extern int GetWindowLong(IntPtr window,int index);
    [DllImport("user32.dll",EntryPoint="SetWindowLongW")] static extern int SetWindowLong(IntPtr window,int index,int value);
    [STAThread] static void Main(string[] args) {
        var app=new Application();
        var window=new Window {Title="The Hunt isolated window probe",Width=120,Height=80,
            WindowStartupLocation=WindowStartupLocation.Manual,Left=-20000,Top=-20000,
            ShowActivated=false,ShowInTaskbar=true};
        window.SourceInitialized+=delegate {
            var handle=new WindowInteropHelper(window).Handle;
            SetWindowLong(handle,-20,(GetWindowLong(handle,-20)|0x08000080)&~0x00040000);
        };
        window.Loaded+=delegate { File.WriteAllText(args[0],new WindowInteropHelper(window).Handle.ToInt64().ToString()); };
        app.Run(window);
    }
}
