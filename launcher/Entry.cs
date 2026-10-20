// SPDX-License-Identifier: GPL-2.0-or-later
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class Entry
{
    [STAThread]
    private static void Main()
    {
        var root = AppDomain.CurrentDomain.BaseDirectory;
        var script = Path.Combine(root, "Launcher.ps1");
        if (!File.Exists(script))
        {
            MessageBox.Show("请完整解压 BBSC Launcher 后再启动。", "BBSC Launcher");
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
        catch (Exception e) { MessageBox.Show(e.Message, "BBSC Launcher"); }
    }
}
