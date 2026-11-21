// SPDX-License-Identifier: GPL-2.0-or-later
using System;
using System.IO;
using System.Windows;
using System.Windows.Media;

public static class HuntTypography
{
    public static string Assets(string root) {
        string local=Path.Combine(root,"assets"); if(Directory.Exists(local)) return local;
        string parent=Path.GetDirectoryName(Path.GetFullPath(root).TrimEnd('\\'));
        string shared=parent==null ? local:Path.Combine(parent,"assets");
        return Directory.Exists(shared) ? shared:local;
    }
    static FontFamily Family(string root,string name,string fallback) {
        string fonts=Path.Combine(Assets(root),"fonts")+Path.DirectorySeparatorChar;
        return Directory.Exists(fonts) ? new FontFamily(new Uri(Path.GetFullPath(fonts)),"./#"+name):new FontFamily(fallback);
    }
    // Body text, forms and navigation: Noto Sans CJK. Titles and figures: Noto Serif CJK (Regular only, no synthetic bold).
    public static FontFamily Body(string root) { return Family(root,"Noto Sans CJK SC","Microsoft YaHei UI"); }
    public static FontFamily Display(string root) { return Family(root,"Noto Serif CJK SC","SimSun"); }
    public static void Bind(Window window,string root) {
        window.FontFamily=Body(root); window.FontSize=14;
        window.Resources["DisplayFont"]=Display(root);
    }
}
