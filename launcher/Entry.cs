// SPDX-License-Identifier: GPL-2.0-or-later
using System;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Windows;

[assembly: System.Reflection.AssemblyTitle("The Hunt")]
[assembly: System.Reflection.AssemblyProduct("The Hunt Launcher")]
[assembly: System.Reflection.AssemblyCompany("iytq")]
internal static class Entry {
    [STAThread] static int Main(string[] args) {
        try {
            if(args.Length>1 && args[0]=="--validate") {
                new DesktopWindow(args[1]).Validate(args.Length>2?args[2]:null,args.Length>3?args[3]:"Home"); return 0;
            }
            string root=AppDomain.CurrentDomain.BaseDirectory;
            HuntUpdates.EnsureNotUpdating(root);
            string key;
            using(var hash=SHA256.Create()) key=BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(Path.GetFullPath(root).TrimEnd('\\'))));
            using(var mutex=new Mutex(false,"Local\\BBSCLauncher-"+key)) {
                bool held;
                try { held=mutex.WaitOne(0); } catch(AbandonedMutexException) { held=true; }
                if(!held) { MessageBox.Show("这个目录的 The Hunt 已经打开，请从任务栏切换。","The Hunt"); return 0; }
                try { var app=new Application(); var desktop=new DesktopWindow(root); desktop.Start(); return app.Run(desktop.Window); }
                finally { mutex.ReleaseMutex(); }
            }
        } catch(Exception error) { MessageBox.Show(error.Message,"The Hunt",MessageBoxButton.OK,MessageBoxImage.Error); return 1; }
    }
}
