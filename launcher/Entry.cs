// SPDX-License-Identifier: GPL-2.0-or-later
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyTitle("The Hunt")]
[assembly: System.Reflection.AssemblyProduct("The Hunt - BBSC Launcher")]
[assembly: System.Reflection.AssemblyCompany("iytq")]
[assembly: System.Reflection.AssemblyCopyright("The Hunt launcher contributions: iytq (2026)")]

internal static class Entry
{
    [STAThread]
    private static void Main()
    {
        var root = AppDomain.CurrentDomain.BaseDirectory;
        var updateLock = Path.Combine(root, ".update.lock");
        if (File.Exists(updateLock))
        {
            try { using (File.Open(updateLock, FileMode.Open, FileAccess.Read, FileShare.None)) { } }
            catch (IOException) { MessageBox.Show("此目录正在更新，请等待更新窗口完成后再启动。", "The Hunt"); return; }
        }
        var script = Path.Combine(root, "Launcher.ps1");
        if (!File.Exists(script))
        {
            MessageBox.Show("请完整解压 The Hunt 启动器后再启动。", "The Hunt");
            return;
        }
        try
        {
            Process.Start(new ProcessStartInfo {
                FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),
                                        @"WindowsPowerShell\v1.0\powershell.exe"),
                Arguments = "-NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File \"" + script + "\"",
                WorkingDirectory = root,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden
            });
        }
        catch (Exception e) { MessageBox.Show(e.Message, "The Hunt"); }
    }
}
