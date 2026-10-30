// SPDX-License-Identifier: GPL-2.0-or-later
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

// A display-only overlay. No input capture, injection, save access or synthetic unlocks.
public sealed class TrophyToast : IDisposable
{
    [StructLayout(LayoutKind.Sequential)] struct Rect { public int Left,Top,Right,Bottom; }
    [StructLayout(LayoutKind.Sequential)] struct Point { public int X,Y; }
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern bool GetClientRect(IntPtr window,out Rect rect);
    [DllImport("user32.dll")] static extern bool ClientToScreen(IntPtr window,ref Point point);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr window);
    [DllImport("user32.dll",EntryPoint="GetWindowLongW")] static extern int GetWindowLong(IntPtr window,int index);
    [DllImport("user32.dll",EntryPoint="SetWindowLongW")] static extern int SetWindowLong(IntPtr window,int index,int value);
    const int GwlExStyle=-20, NoActivate=0x08000000, Transparent=0x20, ToolWindow=0x80;
    sealed class Notice { public string Name,Grade; public BitmapSource Icon; public bool Preview,GameAnchor; }
    readonly Queue<Notice> queue=new Queue<Notice>();
    readonly Func<IntPtr> gameWindow;
    readonly Window launcher;
    readonly DispatcherTimer timer=new DispatcherTimer {Interval=TimeSpan.FromMilliseconds(250)};
    Notice current;
    DateTime shown;
    public Window Window { get; private set; }
    public int PendingCount { get { return queue.Count+(current==null?0:1); } }
    static SolidColorBrush Brush(string value) { return new SolidColorBrush((Color)ColorConverter.ConvertFromString(value)); }
    public TrophyToast(Window owner,Func<IntPtr> game) {
        launcher=owner; gameWindow=game;
        Window=new Window {Width=540,Height=180,WindowStyle=WindowStyle.None,ResizeMode=ResizeMode.NoResize,
            AllowsTransparency=true,Background=Brushes.Transparent,ShowInTaskbar=false,ShowActivated=false,
            Topmost=true,IsHitTestVisible=false,Focusable=false,FontFamily=owner.FontFamily,
            SizeToContent=SizeToContent.Manual,WindowStartupLocation=WindowStartupLocation.Manual};
        Window.SourceInitialized+=delegate {
            var handle=new WindowInteropHelper(Window).Handle;
            SetWindowLong(handle,GwlExStyle,GetWindowLong(handle,GwlExStyle)|NoActivate|Transparent|ToolWindow);
        };
        timer.Tick+=delegate { Tick(); };
    }
    public void Enqueue(string name,string grade,BitmapSource icon,bool preview,bool gameAnchor=false) {
        Window.FontFamily=launcher.FontFamily;
        queue.Enqueue(new Notice {Name=name,Grade=grade,Icon=icon,Preview=preview,GameAnchor=gameAnchor || !preview}); timer.Start();
    }
    static FrameworkElement Card(Notice notice) {
        var grid=new Grid(); grid.ColumnDefinitions.Add(new ColumnDefinition {Width=new GridLength(112)}); grid.ColumnDefinitions.Add(new ColumnDefinition());
        FrameworkElement icon=notice.Icon!=null ? (FrameworkElement)new Image {Source=notice.Icon,Width=88,Height=88} :
            new TextBlock {Text="♜",FontSize=56,Foreground=Brush("#DAC18A"),HorizontalAlignment=HorizontalAlignment.Center,VerticalAlignment=VerticalAlignment.Center};
        grid.Children.Add(icon);
        var body=new StackPanel {VerticalAlignment=VerticalAlignment.Center}; Grid.SetColumn(body,1); grid.Children.Add(body);
        body.Children.Add(new TextBlock {Text=notice.Preview ? "奖杯提示预览":"奖杯已解锁",Foreground=Brush("#DAC18A"),FontSize=18,Margin=new Thickness(0,0,0,8)});
        body.Children.Add(new TextBlock {Text=notice.Name,Foreground=Brushes.White,FontSize=26,TextWrapping=TextWrapping.Wrap,LineHeight=34,MaxHeight=68});
        body.Children.Add(new TextBlock {Text=notice.Grade,Foreground=Brush("#D3D0C7"),FontSize=17,Margin=new Thickness(0,8,0,0)});
        return new Border {Child=grid,Background=Brush("#FA18191B"),BorderBrush=Brush("#AC9160"),BorderThickness=new Thickness(1),CornerRadius=new CornerRadius(8),Padding=new Thickness(20,18,20,18)};
    }
    static FrameworkElement ScaledCard(Notice notice) {
        var card=Card(notice); card.Width=540; card.Height=180;
        return new Viewbox {Child=card,Stretch=Stretch.Uniform};
    }
    void FitClient(double width,double height) {
        // Reference the accepted 2560x1440 game view, in the overlay's DPI space.
        // Scale the complete card, so fonts, icons and padding keep their proportions.
        double scale=Math.Min(width/2560,height/1440);
        Window.Width=540*scale; Window.Height=180*scale;
    }
    void Tick() {
        if(current==null) {
            if(queue.Count==0) { Window.Hide(); timer.Stop(); return; }
            current=queue.Dequeue(); shown=DateTime.MinValue; Window.Content=ScaledCard(current);
        }
        IntPtr anchor=current.GameAnchor ? gameWindow():new WindowInteropHelper(launcher).Handle;
        if(anchor==IntPtr.Zero || IsIconic(anchor) || GetForegroundWindow()!=anchor) { Window.Hide(); return; }
        if(shown!=DateTime.MinValue && DateTime.UtcNow-shown>TimeSpan.FromSeconds(5)) { Window.Hide(); current=null; return; }
        Rect rect; var origin=new Point();
        if(!GetClientRect(anchor,out rect) || rect.Right<=0 || rect.Bottom<=0 || !ClientToScreen(anchor,ref origin)) return;
        var source=HwndSource.FromHwnd(new WindowInteropHelper(Window).EnsureHandle());
        var transform=source!=null && source.CompositionTarget!=null ? source.CompositionTarget.TransformFromDevice:Matrix.Identity;
        var corner=transform.Transform(new System.Windows.Point(origin.X+rect.Right,origin.Y));
        var size=transform.Transform(new Vector(rect.Right,rect.Bottom));
        FitClient(size.X,size.Y);
        Window.Left=corner.X-Window.Width-24; Window.Top=corner.Y+64;
        if(!Window.IsVisible) Window.Show();
        if(shown==DateTime.MinValue) shown=DateTime.UtcNow;
    }
    public void Stop() { timer.Stop(); queue.Clear(); current=null; Window.Hide(); }
    public void Dispose() { Stop(); Window.Close(); }
    public void Validate(string capture,BitmapSource icon=null,double clientWidth=2560,double clientHeight=1440) {
        FitClient(clientWidth,clientHeight);
        Window.Content=ScaledCard(new Notice {Name="武器大师",Grade="银奖杯 · 测试提示，不改变记录",Preview=true,Icon=icon});
        var content=(FrameworkElement)Window.Content;
        content.Measure(new Size(Window.Width,Window.Height)); content.Arrange(new System.Windows.Rect(0,0,Window.Width,Window.Height)); content.UpdateLayout();
        var bitmap=new RenderTargetBitmap((int)Window.Width,(int)Window.Height,96,96,PixelFormats.Pbgra32); bitmap.Render(content);
        var encoder=new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap)); using(var stream=System.IO.File.Create(capture)) encoder.Save(stream);
    }
}
