// SPDX-License-Identifier: GPL-2.0-or-later
using System;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Markup;
using System.Xml.Linq;

public static class HuntTheme
{
    [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr window, int attribute, ref int value, int size);
    public static Window Load(string xaml, string root = null) {
        if(root!=null) xaml=xaml.Replace("@assets/",new Uri(Path.GetFullPath(HuntTypography.Assets(root))+Path.DirectorySeparatorChar).AbsoluteUri);
        XNamespace ns = "http://schemas.microsoft.com/winfx/2006/xaml/presentation";
        XNamespace x = "http://schemas.microsoft.com/winfx/2006/xaml";
        var document = XDocument.Parse(xaml);
        foreach(var brush in document.Descendants(ns+"ImageBrush").Where(e=>e.Attribute("ImageSource")!=null).ToArray()) {
            string uri=(string)brush.Attribute("ImageSource"); brush.Attribute("ImageSource").Remove();
            brush.Add(new XElement(ns+"ImageBrush.ImageSource",new XElement(ns+"BitmapImage",new XAttribute("UriSource",uri),new XAttribute("CacheOption","OnLoad"))));
        }
        var resources = document.Root.Element(ns + "Window.Resources");
        if (resources == null) { resources = new XElement(ns + "Window.Resources"); document.Root.AddFirst(resources); }
        using (var stream = typeof(HuntTheme).Assembly.GetManifestResourceStream("TheHunt.Theme.xaml")) {
            var theme = XDocument.Load(stream);
            foreach (var item in theme.Root.Elements().ToArray()) {
                string key = (string)item.Attribute(x + "Key");
                string target = (string)item.Attribute("TargetType");
                foreach (var old in resources.Elements().Where(e => key != null ? (string)e.Attribute(x + "Key") == key :
                         e.Name == item.Name && (string)e.Attribute("TargetType") == target && e.Attribute(x + "Key") == null).ToArray()) old.Remove();
                resources.Add(new XElement(item));
            }
        }
        var window = (Window)XamlReader.Parse(document.ToString());
        window.SourceInitialized += delegate {
            int dark = 1, caption = 0x00141211, text = 0x00DAE5EA;
            var handle = new System.Windows.Interop.WindowInteropHelper(window).Handle;
            DwmSetWindowAttribute(handle, 20, ref dark, 4);
            DwmSetWindowAttribute(handle, 35, ref caption, 4);
            DwmSetWindowAttribute(handle, 36, ref text, 4);
        };
        return window;
    }
}
