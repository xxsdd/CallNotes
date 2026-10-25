using System;
using System.IO;
using System.IO.Compression;
using System.Diagnostics;
using System.Drawing;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Win32;
using System.Collections.Generic;
[assembly: System.Reflection.AssemblyVersion("0.1.3.0")]
[assembly: System.Reflection.AssemblyFileVersion("0.1.3.0")]
[assembly: System.Reflection.AssemblyTitle("CallNotes Setup")]

static class Program {
    public const string Version="0.1.3", KeyPath=@"Software\Microsoft\Windows\CurrentVersion\Uninstall\CallNotes";
    public static string Menu { get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs),"通话笺 CallNotes"); } }
    public static string Desktop { get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),"通话笺.lnk"); } }
    [STAThread] static int Main(string[] args) {
        Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
        try {
#if UNINSTALL
            Uninstall(args.Length>0 && args[0]=="--silent");
#else
            if(args.Length==2 && args[0]=="--test-install") Install(args[1],false,false,null);
            else Application.Run(new SetupForm());
#endif
            return 0;
        } catch(Exception e) {
            if(args.Length>0) { Console.Error.WriteLine(e); return 1; }
            MessageBox.Show(e.Message,"通话笺",MessageBoxButtons.OK,MessageBoxIcon.Error); return 1;
        }
    }
    public static string CheckedRoot(string folder) {
        string target=Path.GetFullPath(folder).TrimEnd(Path.DirectorySeparatorChar);
        if(target.Length<4 || target.Equals(Path.GetPathRoot(target).TrimEnd('\\'),StringComparison.OrdinalIgnoreCase)) throw new IOException("请选择通话笺专用子文件夹。");
        foreach(string reserved in new[]{Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),Environment.GetFolderPath(Environment.SpecialFolder.Windows),Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles)})
            if(target.Equals(reserved.TrimEnd('\\'),StringComparison.OrdinalIgnoreCase)) throw new IOException("请选择通话笺专用子文件夹。");
        return target;
    }
    public static void Idle(string root) {
        string prefix=Path.GetFullPath(root).TrimEnd('\\')+"\\";
        foreach(string name in new[]{"CallNotes","CallNotesWorker"}) foreach(var process in Process.GetProcessesByName(name)) using(process) {
            string file;
            try { file=process.MainModule.FileName; } catch { throw new IOException("请先停止录音、关闭通话笺，再安装或卸载。"); }
            if(Path.GetFullPath(file).StartsWith(prefix,StringComparison.OrdinalIgnoreCase)) throw new IOException("请先停止录音、关闭通话笺，再安装或卸载。不会强制结束录音。");
        }
    }
    public static void Shortcut(string path,string exe,string root) {
        dynamic shell=Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell"));
        dynamic link=shell.CreateShortcut(path);link.TargetPath=exe;link.WorkingDirectory=root;link.Description="通话笺 · 本地录音、字幕与分析";link.IconLocation=Path.Combine(root,"CallNotes.exe")+",0";link.Save();
    }
    public static void Install(string folder,bool desktop,bool register,Action<int> progress) {
        string root=CheckedRoot(folder), prefix=root+"\\";
        if(Directory.Exists(root) && Directory.GetFileSystemEntries(root).Length>0 && !File.Exists(Path.Combine(root,"install-manifest.txt"))) throw new IOException("目标文件夹已有其他文件，请选择新的通话笺专用文件夹。");
        Idle(root);
        Directory.CreateDirectory(root);
        var files=new List<string>();
        using(var stream=System.Reflection.Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip"))
        using(var zip=new ZipArchive(stream,ZipArchiveMode.Read)) {
            long total=0,done=0;foreach(var entry in zip.Entries)total+=entry.Length;
            foreach(var entry in zip.Entries) {
                string relative=entry.FullName.Replace('/',Path.DirectorySeparatorChar);
                string target=Path.GetFullPath(Path.Combine(root,relative));
                if(!target.StartsWith(prefix,StringComparison.OrdinalIgnoreCase))throw new IOException("安装包路径无效。");
                if(entry.FullName.EndsWith("/")){Directory.CreateDirectory(target);continue;}
                Directory.CreateDirectory(Path.GetDirectoryName(target));
                using(var input=entry.Open())using(var output=File.Create(target)) {
                    byte[] buffer=new byte[1024*1024];int n;
                    while((n=input.Read(buffer,0,buffer.Length))>0) { output.Write(buffer,0,n);done+=n;if(progress!=null)progress((int)Math.Min(99,done*100/Math.Max(1,total))); }
                }
                files.Add(relative);
            }
        }
        File.WriteAllLines(Path.Combine(root,"install-manifest.txt"),files,Encoding.UTF8);
        if(register) {
            Directory.CreateDirectory(Menu);
            Shortcut(Path.Combine(Menu,"通话笺.lnk"),Path.Combine(root,"CallNotes.exe"),root);
            Shortcut(Path.Combine(Menu,"卸载通话笺.lnk"),Path.Combine(root,"UninstallCallNotes.exe"),root);
            if(desktop)Shortcut(Desktop,Path.Combine(root,"CallNotes.exe"),root);
            using(var key=Registry.CurrentUser.CreateSubKey(KeyPath)) {
                key.SetValue("DisplayName","通话笺 CallNotes");key.SetValue("DisplayVersion",Version);key.SetValue("Publisher","CallNotes");
                key.SetValue("InstallLocation",root);key.SetValue("DisplayIcon",Path.Combine(root,"CallNotes.exe"));
                key.SetValue("UninstallString","\""+Path.Combine(root,"UninstallCallNotes.exe")+"\"");key.SetValue("NoModify",1);key.SetValue("NoRepair",1);
            }
        }
        if(progress!=null)progress(100);
    }
    public static void Uninstall(bool silent) {
        string root=CheckedRoot(Path.GetDirectoryName(Application.ExecutablePath));
        string manifest=Path.Combine(root,"install-manifest.txt"),prefix=root+"\\";
        if(!File.Exists(manifest))throw new IOException("安装清单缺失，未删除文件。");
        if(!silent && MessageBox.Show("卸载通话笺？个人录音、文字稿和账号登录将保留。","通话笺",MessageBoxButtons.YesNo,MessageBoxIcon.Question)!=DialogResult.Yes)return;
        Idle(root);
        var directories=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach(string relative in File.ReadAllLines(manifest,Encoding.UTF8)) {
            if(string.IsNullOrWhiteSpace(relative))continue;
            string file=Path.GetFullPath(Path.Combine(root,relative));
            if(!file.StartsWith(prefix,StringComparison.OrdinalIgnoreCase))throw new IOException("安装清单路径无效。");
            if(file.Equals(Application.ExecutablePath,StringComparison.OrdinalIgnoreCase))continue;
            if(File.Exists(file))File.Delete(file);
            string parent=Path.GetDirectoryName(file);
            while(parent.StartsWith(prefix,StringComparison.OrdinalIgnoreCase)){directories.Add(parent);parent=Path.GetDirectoryName(parent);}
        }
        var ordered=new List<string>(directories);ordered.Sort((a,b)=>b.Length.CompareTo(a.Length));
        foreach(string directory in ordered)if(Directory.Exists(directory)&&Directory.GetFileSystemEntries(directory).Length==0)Directory.Delete(directory);
        File.Delete(manifest);
        using(var key=Registry.CurrentUser.OpenSubKey(KeyPath)) {
            if(key!=null && root.Equals(key.GetValue("InstallLocation") as string,StringComparison.OrdinalIgnoreCase)) {
                key.Close();Registry.CurrentUser.DeleteSubKeyTree(KeyPath,false);
                if(File.Exists(Desktop))File.Delete(Desktop);
                foreach(string name in new[]{"通话笺.lnk","卸载通话笺.lnk"}) {string link=Path.Combine(Menu,name);if(File.Exists(link))File.Delete(link);}
                if(Directory.Exists(Menu)&&Directory.GetFileSystemEntries(Menu).Length==0)Directory.Delete(Menu);
            }
        }
        if(!silent)MessageBox.Show("卸载完成，个人录音与文字稿已保留。","通话笺");
        string script="Wait-Process -Id "+Process.GetCurrentProcess().Id+" -ErrorAction SilentlyContinue; $taskRoot=[IO.Path]::GetFullPath('"+root.Replace("'","''")+"'); $taskFile=[IO.Path]::GetFullPath('"+Application.ExecutablePath.Replace("'","''")+"'); if($taskFile.StartsWith($taskRoot.TrimEnd('\\')+'\\',[StringComparison]::OrdinalIgnoreCase)){Remove-Item -LiteralPath $taskFile -ErrorAction SilentlyContinue; if((Test-Path -LiteralPath $taskRoot) -and @(Get-ChildItem -LiteralPath $taskRoot -Force).Count -eq 0){Remove-Item -LiteralPath $taskRoot -ErrorAction SilentlyContinue}}";
        Process.Start(new ProcessStartInfo("powershell.exe","-NoProfile -NonInteractive -EncodedCommand "+Convert.ToBase64String(Encoding.Unicode.GetBytes(script))){UseShellExecute=false,CreateNoWindow=true,WindowStyle=ProcessWindowStyle.Hidden});
    }
}

class SetupForm:Form {
    TextBox folder;Button install,browse;CheckBox desktop;ProgressBar bar;Label status;bool complete=false;
    public SetupForm() {
        Text="通话笺 · 安装";ClientSize=new Size(640,430);FormBorderStyle=FormBorderStyle.FixedDialog;MaximizeBox=false;StartPosition=FormStartPosition.CenterScreen;Font=new Font("Microsoft YaHei UI",10);
        Controls.Add(new Label{Text="通话笺 CallNotes",Font=new Font("Microsoft YaHei UI",23,FontStyle.Bold),Location=new Point(30,24),AutoSize=true});
        Controls.Add(new Label{Text="Windows 64 位 · "+Program.Version+"\n自带运行环境与字幕模型，安装后即可本地录音与转写。",Location=new Point(32,87),Size=new Size(575,55)});
        Controls.Add(new Label{Text="安装位置",Location=new Point(32,158),AutoSize=true});
        string location=Directory.Exists(@"D:\")?@"D:\Apps\CallNotes-Desktop":Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Programs","CallNotes");
        folder=new TextBox{Text=location,Location=new Point(32,185),Size=new Size(460,29)};Controls.Add(folder);
        browse=new Button{Text="浏览…",Location=new Point(507,182),Size=new Size(95,33)};
        browse.Click+=(s,e)=>{using(var dialog=new FolderBrowserDialog()){dialog.Description="选择通话笺专用安装文件夹";dialog.SelectedPath=folder.Text;if(dialog.ShowDialog()==DialogResult.OK)folder.Text=dialog.SelectedPath;}};Controls.Add(browse);
        desktop=new CheckBox{Text="创建桌面快捷方式",Checked=true,Location=new Point(32,229),AutoSize=true};Controls.Add(desktop);
        bar=new ProgressBar{Location=new Point(32,274),Size=new Size(570,17)};Controls.Add(bar);
        status=new Label{Text="AI 分析需要使用者登录自己的 Codex 账号。\n卸载时保留个人录音和文字稿。",Location=new Point(32,308),Size=new Size(570,51)};Controls.Add(status);
        install=new Button{Text="安装",Location=new Point(463,375),Size=new Size(139,34)};install.Click+=InstallClicked;Controls.Add(install);
        FormClosing+=(s,e)=>{if(!install.Enabled)e.Cancel=true;};
    }
    async void InstallClicked(object sender,EventArgs e) {
        if(complete){Process.Start(new ProcessStartInfo(Path.Combine(folder.Text,"CallNotes.exe")){UseShellExecute=true});Close();return;}
        string destination=folder.Text;bool shortcut=desktop.Checked;int last=-1;
        folder.Enabled=browse.Enabled=desktop.Enabled=install.Enabled=false;status.Text="正在安装通话笺和本地字幕模型…";
        try {
            await Task.Run(()=>Program.Install(destination,shortcut,true,value=>{if(value!=last){last=value;BeginInvoke((Action)(()=>bar.Value=value));}}));
            complete=true;bar.Value=100;status.Text="安装完成。打开即可录音；AI 分析请到设置中登录 Codex。";install.Text="打开通话笺";
        } catch(Exception ex) {status.Text="安装未完成："+ex.Message;folder.Enabled=browse.Enabled=desktop.Enabled=true;}
        install.Enabled=true;
    }
}
