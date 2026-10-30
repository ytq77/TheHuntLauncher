// SPDX-License-Identifier: GPL-2.0-or-later
using System;
using System.IO;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Xml.Linq;

// Retain the existing PowerShell PSCredential CLIXML format and Windows-user DPAPI scope.
public static class DesktopCredentials
{
    static string Decode(string value) { return Regex.Replace(value, "_x([0-9a-fA-F]{4})_", m => ((char)Convert.ToInt32(m.Groups[1].Value,16)).ToString()); }
    static string Encode(string value) {
        return Regex.Replace(value, @"_x(?=[0-9a-fA-F]{4}_)|[\x00-\x08\x0B\x0C\x0E-\x1F]", m => m.Value.Length > 1 ? "_x005F_x" : "_x" + ((int)m.Value[0]).ToString("X4") + "_");
    }
    public static string[] Read(string path) {
        XNamespace ns = "http://schemas.microsoft.com/powershell/2004/04";
        var document = XDocument.Load(path);
        string user = null, password = null;
        foreach (var item in document.Descendants(ns + "Props").Elements()) {
            if ((string)item.Attribute("N") == "UserName") user = Decode(item.Value);
            if ((string)item.Attribute("N") == "Password") password = item.Value;
        }
        if (user == null || password == null || password.Length % 2 != 0) throw new InvalidDataException("本机账号保存状态无效，请重新填写密码。");
        var blob = new byte[password.Length / 2];
        for (int i=0; i<blob.Length; ++i) blob[i] = Convert.ToByte(password.Substring(i*2,2),16);
        byte[] raw = ProtectedData.Unprotect(blob, null, DataProtectionScope.CurrentUser);
        try { return new [] {user, Encoding.Unicode.GetString(raw)}; }
        finally { Array.Clear(raw,0,raw.Length); }
    }
    public static void Save(string path, string user, string password) {
        string folder = Path.GetDirectoryName(path);
        Directory.CreateDirectory(folder);
        var acl = new DirectorySecurity();
        acl.SetAccessRuleProtection(true, false);
        foreach (var sid in new [] {WindowsIdentity.GetCurrent().User, new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null)})
            acl.AddAccessRule(new FileSystemAccessRule(sid, FileSystemRights.FullControl, InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
        Directory.SetAccessControl(folder, acl);
        byte[] raw = Encoding.Unicode.GetBytes(password), blob;
        try { blob = ProtectedData.Protect(raw,null,DataProtectionScope.CurrentUser); }
        finally { Array.Clear(raw,0,raw.Length); }
        XNamespace ns = "http://schemas.microsoft.com/powershell/2004/04";
        var obj = new XElement(ns+"Obj", new XAttribute("RefId","0"),
            new XElement(ns+"TN", new XAttribute("RefId","0"),new XElement(ns+"T","System.Management.Automation.PSCredential"),new XElement(ns+"T","System.Object")),
            new XElement(ns+"ToString","System.Management.Automation.PSCredential"),
            new XElement(ns+"Props",new XElement(ns+"S",new XAttribute("N","UserName"),Encode(user)),
                new XElement(ns+"SS",new XAttribute("N","Password"),BitConverter.ToString(blob).Replace("-","").ToLowerInvariant())));
        var doc = new XDocument(new XElement(ns+"Objs",new XAttribute("Version","1.1.0.1"),obj));
        string temp = path + ".tmp";
        doc.Save(temp);
        if (File.Exists(path)) File.Replace(temp,path,null); else File.Move(temp,path);
    }
}
