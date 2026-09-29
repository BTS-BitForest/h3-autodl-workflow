using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.Win32;
using Renci.SshNet;

namespace H3Launcher;
static class Program {
 [STAThread] static void Main(){using var mutex=new Mutex(true,@"Local\H3LauncherOwnedTunnel",out bool first);if(!first){MessageBox.Show("H3 启动器已经打开，请使用现有窗口。");return;}ApplicationConfiguration.Initialize();Application.Run(new Launcher());}
}
sealed partial class Launcher : Form {
 readonly TextBox address=new(){PlaceholderText="粘贴 SSH 命令，或输入 user@host:port",Dock=DockStyle.Fill};
 readonly TextBox password=new(){UseSystemPasswordChar=true,Dock=DockStyle.Fill};
 readonly TextBox proxy=new(){PlaceholderText="自动检测；也可填 http://127.0.0.1:7890",Dock=DockStyle.Fill};
 readonly CheckBox remember=new(){Text="记住密码（仅当前 Windows 账户加密保存），下次打开自动连接",AutoSize=true};
 readonly Label status=new(){Text="固定面板端口 8190 · 首次连接后自动打开浏览器",AutoSize=true,MaximumSize=new Size(550,0)};
 readonly Button connect=new(){Text="连接并打开 H3",AutoSize=true};
 readonly Button disconnect=new(){Text="断开连接",AutoSize=true,Enabled=false};
 readonly Button browserButton=new(){Text="连接浏览器",AutoSize=true,Enabled=false};
 readonly ComboBox browserChoice=new(){DropDownStyle=ComboBoxStyle.DropDownList,Width=100};
 readonly Button extensionButton=new(){Text="安装浏览器扩展",AutoSize=true};
 readonly Label browserStatus=new(){Text="浏览器控制未开启（可选）",AutoSize=true,MaximumSize=new Size(610,0)};
 readonly Button reopen=new(){Text="重新打开面板",AutoSize=true,Enabled=false};
 SshClient? ssh; ProxyBridge? bridge; CancellationTokenSource? session; bool working=false,closing=false,allowClose=false; Task? running; Task? initializing;
 readonly string configDir=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"H3Launcher");
 public Launcher(){Icon=System.Drawing.Icon.ExtractAssociatedIcon(Application.ExecutablePath);Text="H3 面板启动器";Size=new(730,620);MinimumSize=new(700,600);StartPosition=FormStartPosition.CenterScreen;Font=new("Microsoft YaHei UI",10);var layout=new TableLayoutPanel(){Dock=DockStyle.Fill,Padding=new Padding(24),ColumnCount=1,RowCount=12,AutoScroll=true};
 layout.Controls.Add(new Label(){Text="连接你的 H3 工作站",Font=new(Font.FontFamily,19,FontStyle.Bold),AutoSize=true});
 layout.Controls.Add(new Label(){Text="SSH 登录命令 / 账户与服务器",AutoSize=true});layout.Controls.Add(address);
 layout.Controls.Add(new Label(){Text="SSH 密码（默认仅本次使用；勾选下方选项才保存）",AutoSize=true});layout.Controls.Add(password);layout.Controls.Add(remember);
 layout.Controls.Add(new Label(){Text="电脑代理（可留空，VPN 的 TUN 模式无需填写）",AutoSize=true});layout.Controls.Add(proxy);
 var buttons=new FlowLayoutPanel(){AutoSize=true,Dock=DockStyle.Fill};buttons.Controls.AddRange(new Control[]{connect,disconnect,reopen});layout.Controls.Add(buttons);layout.Controls.Add(status);var browserRow=new FlowLayoutPanel(){AutoSize=true,Dock=DockStyle.Fill};browserChoice.Items.AddRange(new object[]{"Edge","Chrome"});browserChoice.SelectedIndex=0;browserRow.Controls.AddRange(new Control[]{browserChoice,browserButton,extensionButton});layout.Controls.Add(browserRow);layout.Controls.Add(browserStatus);Controls.Add(layout);
 browserButton.Click+=async(_,_)=>{if(browserProcess!=null){StopBrowser();return;}browserStarting=StartBrowser();await browserStarting;};extensionButton.Click+=(_,_)=>{try{var bin=BrowserExecutable();Process.Start(new ProcessStartInfo(bin){ArgumentList={"https://chromewebstore.google.com/detail/playwright-extension/mmlmfjhmonkocbjadbfplnigmagldckm"},UseShellExecute=true});}catch(Exception e){browserStatus.Text=e.Message;}};
 Directory.CreateDirectory(configDir);var last=Path.Combine(configDir,"server.txt");if(File.Exists(last))address.Text=File.ReadAllText(last);
 connect.Click+=async(_,_)=>await Begin();disconnect.Click+=(_,_)=>Stop();reopen.Click+=async(_,_)=>{try{await OpenPanel();}catch(Exception e){Say(e.Message);}};
 LoadSettings();
 connect.Enabled=false;
 Shown+=async(_,_)=>{try{initializing=RetireStartup();await initializing;if(closing)return;connect.Enabled=true;if(password.Text.Length>0)await Begin();}catch(Exception e){Say("旧版助手清理失败："+e.Message+"。请处理后重新打开启动器。");connect.Enabled=false;}};
 FormClosing+=async(_,e)=>{if(allowClose)return;e.Cancel=true;if(closing)return;closing=true;Enabled=false;Stop();if(initializing!=null)await initializing.ContinueWith(_=>{});if(browserStarting!=null)await browserStarting;if(running!=null)await running;allowClose=true;Close();};
 }
 void Say(string s){if(IsDisposed||Disposing)return;if(InvokeRequired){try{BeginInvoke(()=>Say(s));}catch(InvalidOperationException){}return;}status.Text=s;}
 static (string host,string user,int port) Parse(string input){var m=Regex.Match(input.Trim(),@"^ssh\s+-p\s+(\d{1,5})\s+([a-zA-Z0-9_.-]+)@([a-zA-Z0-9.-]+)$");if(m.Success)return(m.Groups[3].Value,m.Groups[2].Value,ValidPort(m.Groups[1].Value));m=Regex.Match(input.Trim(),@"^([a-zA-Z0-9_.-]+)@([a-zA-Z0-9.-]+)(?::(\d{1,5}))?$");if(m.Success)return(m.Groups[2].Value,m.Groups[1].Value,m.Groups[3].Success?ValidPort(m.Groups[3].Value):22);throw new Exception("请填写 ssh -p 端口 用户@主机，例如 ssh -p 12345 root@example.com");}
 static int ValidPort(string p){int n=int.Parse(p);if(n<1||n>65535)throw new Exception("SSH 端口须为 1–65535");return n;}
 bool Trust(string host,int port,byte[] key){if(closing||session?.IsCancellationRequested==true)return false;var file=Path.Combine(configDir,"known-hosts.json");var entries=File.Exists(file)?JsonSerializer.Deserialize<Dictionary<string,string>>(File.ReadAllText(file))!:new();var id=host+":"+port;var fp="SHA256:"+Convert.ToBase64String(SHA256.HashData(key)).TrimEnd('=');if(entries.TryGetValue(id,out var previous)){if(previous==fp)return true;Say("服务器指纹已变化，连接已拒绝。请核对服务器后删除启动器 known-hosts.json 中对应记录。");return false;}
 var result=(DialogResult)Invoke(()=>MessageBox.Show(this,"首次连接服务器："+id+"\n指纹："+fp+"\n请与服务器管理员核对。是否信任此主机？","确认 SSH 主机",MessageBoxButtons.YesNo,MessageBoxIcon.Question));if(result!=DialogResult.Yes)return false;entries[id]=fp;File.WriteAllText(file,JsonSerializer.Serialize(entries));return true;}
 async Task Begin(){if(working||closing)return;running=Start();await running;}
 void LoadSettings(){try{if(string.IsNullOrWhiteSpace(address.Text)){var f=Path.Combine(AppContext.BaseDirectory,"connection.json");if(File.Exists(f)){using var j=JsonDocument.Parse(File.ReadAllText(f));address.Text=j.RootElement.GetProperty("target").GetString()+":"+j.RootElement.GetProperty("port").GetInt32();}}
 var saved=Path.Combine(configDir,"login.dpapi");if(File.Exists(saved)){var data=ProtectedData.Unprotect(File.ReadAllBytes(saved),null,DataProtectionScope.CurrentUser);var login=JsonSerializer.Deserialize<string[]>(data)!;if(login[0]==address.Text){password.Text=login[1];remember.Checked=true;proxy.Text=login[2];}Array.Clear(data);}}catch{Say("已保存的登录信息无法读取，请重新输入密码。");}}
 void SaveLogin(string server,string secret,string setting){var f=Path.Combine(configDir,"login.dpapi");if(remember.Checked){var bytes=JsonSerializer.SerializeToUtf8Bytes(new[]{server,secret,setting});try{File.WriteAllBytes(f,ProtectedData.Protect(bytes,null,DataProtectionScope.CurrentUser));}finally{Array.Clear(bytes);}}else if(File.Exists(f))File.Delete(f);}
 async Task RetireStartup(){using var resource=typeof(Launcher).Assembly.GetManifestResourceStream("H3Launcher.Retire-H3Startup.ps1")!;using var reader=new StreamReader(resource);var encoded=Convert.ToBase64String(Encoding.Unicode.GetBytes(await reader.ReadToEndAsync()));using var p=Process.Start(new ProcessStartInfo("powershell.exe"){Arguments="-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand "+encoded,UseShellExecute=false,CreateNoWindow=true,RedirectStandardError=true})!;var errors=p.StandardError.ReadToEndAsync();using var timeout=new CancellationTokenSource(TimeSpan.FromSeconds(20));try{await p.WaitForExitAsync(timeout.Token);}catch(OperationCanceledException){p.Kill(true);await p.WaitForExitAsync();throw new IOException("清理旧版助手超时，请重新打开启动器。");}if(p.ExitCode!=0)throw new IOException(await errors);}
 async Task Start(){if(working)return;working=true;connect.Enabled=false;string secret=password.Text;try{var(host,user,port)=Parse(address.Text);if(secret.Length==0)throw new Exception("请输入 SSH 密码");var probe=new TcpListener(IPAddress.Loopback,8190);try{probe.Start();}catch{throw new Exception("本机端口 8190 已被占用。请关闭旧 H3 启动器或占用此端口的程序，然后重试；不会自动切换端口。");}finally{probe.Stop();}
 session=new();var cancel=session.Token;bridge=new ProxyBridge(proxy.Text.Trim(),Say);await bridge.Start();
 File.WriteAllText(Path.Combine(configDir,"server.txt"),$"{user}@{host}:{port}");SaveLogin($"{user}@{host}:{port}",secret,proxy.Text.Trim());disconnect.Enabled=true;address.Enabled=false;proxy.Enabled=false;password.Enabled=false;remember.Enabled=false;
 while(!cancel.IsCancellationRequested){string warning="";try{Say("正在建立 SSH 连接…");await Task.Run(()=>{var auth=new PasswordAuthenticationMethod(user,secret);var keyboard=new KeyboardInteractiveAuthenticationMethod(user);keyboard.AuthenticationPrompt+=(_,e)=>{foreach(var p in e.Prompts)if(p.Request.Contains("assword",StringComparison.OrdinalIgnoreCase))p.Response=secret;};ssh=new SshClient(new ConnectionInfo(host,port,user,auth,keyboard){Timeout=TimeSpan.FromSeconds(20)});ssh.HostKeyReceived+=(_,e)=>e.CanTrust=Trust(host,port,e.HostKey);ssh.KeepAliveInterval=TimeSpan.FromSeconds(15);ssh.Connect();cancel.ThrowIfCancellationRequested();var forward=new ForwardedPortLocal("127.0.0.1",8190,"127.0.0.1",8190);ssh.AddForwardedPort(forward);forward.Start();try{var comfy=new ForwardedPortLocal("127.0.0.1",8188,"127.0.0.1",8188);ssh.AddForwardedPort(comfy);comfy.Start();}catch{warning+="\n本机 8188 被占用，ComfyUI 直达入口不可用。";}try{var reverse=new ForwardedPortRemote("127.0.0.1",41084,"127.0.0.1",(uint)bridge.Port);ssh.AddForwardedPort(reverse);reverse.Start();}catch{warning+="\n服务器 41084 被其他连接占用：本次网络转发未开启。请关闭其他控制端后重连。";}},cancel);
 cancel.ThrowIfCancellationRequested();await OpenPanel();cancel.ThrowIfCancellationRequested();reopen.Enabled=true;browserButton.Enabled=true;Say("已连接： http://127.0.0.1:8190/\n保持启动器打开。断开连接不会取消服务器上的视频任务。"+warning);while(ssh?.IsConnected==true&&!cancel.IsCancellationRequested)await Task.Delay(2000,cancel);
 if(!cancel.IsCancellationRequested)Say("连接中断，5 秒后自动重连…");
 }catch(OperationCanceledException){break;}catch(Renci.SshNet.Common.SshAuthenticationException){throw new Exception("SSH 密码或账户不正确，请重新输入。");}catch(Exception e){if(cancel.IsCancellationRequested)break;Say("连接未完成："+e.Message+"\n5 秒后重试；可点击断开停止。");}
 finally{StopBrowser();browserButton.Enabled=false;ssh?.Dispose();ssh=null;reopen.Enabled=false;}
 if(!cancel.IsCancellationRequested)await Task.Delay(5000,cancel);
 }
 }catch(OperationCanceledException){}catch(Exception e){Say(e.Message);}finally{if(session?.IsCancellationRequested==true)Say("已断开。服务器上的视频生成继续运行。");secret="";bridge?.Dispose();bridge=null;working=false;connect.Enabled=true;disconnect.Enabled=false;reopen.Enabled=false;address.Enabled=true;proxy.Enabled=true;password.Enabled=true;remember.Enabled=true;session?.Dispose();session=null;}}
 async Task OpenPanel(){var client=ssh;if(client==null||!client.IsConnected)throw new Exception("SSH 尚未连接");var token=session?.Token??CancellationToken.None;string output=await Task.Run(()=>{using var cmd=client.CreateCommand("if [ -f /opt/h3-suite/scripts/launch_session.py ]; then python3 /opt/h3-suite/scripts/launch_session.py; elif [ -f /root/autodl-tmp/h3/gateway/prepare_browser.py ]; then /root/autodl-tmp/h3/tools/runtime-venv/bin/python /root/autodl-tmp/h3/gateway/prepare_browser.py; else echo 'H3_NOT_INSTALLED'; exit 4; fi");cmd.CommandTimeout=TimeSpan.FromMinutes(3);using var registration=token.Register(()=>{try{cmd.CancelAsync();}catch{}});token.ThrowIfCancellationRequested();string result=cmd.Execute();token.ThrowIfCancellationRequested();if(cmd.ExitStatus!=0)throw new Exception("服务器启动失败："+cmd.Error);return result;});token.ThrowIfCancellationRequested();var m=Regex.Match(output,@"H3_BROWSER_TICKET=([A-Za-z0-9_-]+)");if(!m.Success)throw new Exception("服务器没有返回登录票据，请检查是否安装 H3 集成包");Process.Start(new ProcessStartInfo("http://127.0.0.1:8190/#login="+m.Groups[1].Value){UseShellExecute=true});}
 void Stop(){session?.Cancel();StopBrowser();bridge?.Dispose();Say("正在关闭本启动器的连接，服务器生成继续运行…");}
}

