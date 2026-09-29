using System.Diagnostics;
using System.Net;
using System.Net.Http;
using System.Net.Sockets;
using Renci.SshNet;
namespace H3Launcher;
sealed partial class Launcher {
 Process? browserProcess;
 ForwardedPortRemote? browserForward;
 CancellationTokenSource? browserCancel;
 Task? browserStarting;
 string BrowserExecutable(){var channel=browserChoice.SelectedIndex==1?"chrome":"msedge";var relative=channel=="chrome"?@"Google\Chrome\Application\chrome.exe":@"Microsoft\Edge\Application\msedge.exe";foreach(var dir in new[]{Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86),Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData)}){var f=Path.Combine(dir,relative);if(File.Exists(f))return f;}throw new IOException("未找到所选浏览器，请切换 Edge / Chrome。");}
 async Task StartBrowser(){
  var client=ssh;if(client==null||!client.IsConnected||session==null)return;
  browserButton.Enabled=false;browserChoice.Enabled=false;
  browserCancel=CancellationTokenSource.CreateLinkedTokenSource(session.Token);var ct=browserCancel.Token;
  try{
   var executable=BrowserExecutable();var runtime=Path.Combine(AppContext.BaseDirectory,"browser-runtime");var node=Path.Combine(runtime,"node.exe");var entry=Path.Combine(runtime,"owned-mcp.cjs");
   if(!File.Exists(node)||!File.Exists(entry))throw new IOException("缺少浏览器组件，请完整解压带浏览器功能的启动包。");
   var probe=new TcpListener(IPAddress.Loopback,8931);probe.Server.ExclusiveAddressUse=true;try{probe.Start();}catch{throw new IOException("本机 8931 已被占用，不接管已有进程。");}finally{probe.Stop();}
   var start=new ProcessStartInfo(node){UseShellExecute=false,CreateNoWindow=true,WorkingDirectory=runtime,RedirectStandardOutput=true,RedirectStandardError=true};
   foreach(var arg in new[]{entry,"--extension","--executable-path",executable,"--browser",browserChoice.SelectedIndex==1?"chrome":"msedge","--host","127.0.0.1","--port","8931","--allowed-hosts","127.0.0.1:8931,localhost:8931,127.0.0.1:41831,localhost:41831","--output-dir",Path.Combine(configDir,"browser-output")})start.ArgumentList.Add(arg);
   start.Environment["H3_LAUNCHER_PID"]=Environment.ProcessId.ToString();
   browserProcess=new Process(){StartInfo=start};browserProcess.OutputDataReceived+=(_,_)=>{};browserProcess.ErrorDataReceived+=(_,_)=>{};
   if(!browserProcess.Start())throw new IOException("Playwright MCP 启动失败。");browserProcess.BeginOutputReadLine();browserProcess.BeginErrorReadLine();
   browserStatus.Text="正在启动 Playwright MCP…";
   using var http=new HttpClient(new HttpClientHandler(){UseProxy=false}){Timeout=TimeSpan.FromSeconds(1)};
   bool ready=false;for(int i=0;i<40;i++){ct.ThrowIfCancellationRequested();if(browserProcess.HasExited)throw new IOException("Playwright MCP 已退出，请确认启动包完整及系统支持 Node 24。");try{using var res=await http.GetAsync("http://127.0.0.1:8931/mcp",ct);if(res.StatusCode==HttpStatusCode.Forbidden)throw new IOException("MCP Host 配置不匹配。");ready=true;break;}catch(HttpRequestException){}catch(TaskCanceledException)when(!ct.IsCancellationRequested){}await Task.Delay(250,ct);}
   if(!ready)throw new IOException("Playwright MCP 启动超时。");
   ct.ThrowIfCancellationRequested();var forward=new ForwardedPortRemote("127.0.0.1",41831,"127.0.0.1",8931);browserForward=forward;client.AddForwardedPort(forward);await Task.Run(()=>forward.Start(),ct);ct.ThrowIfCancellationRequested();
   browserButton.Text="断开浏览器";browserButton.Enabled=true;
   browserStatus.Text="浏览器通道已开启。请先安装扩展并登录 AutoDL；Codex 首次操作时，在浏览器弹窗中选择允许连接的标签页。";
  }catch(OperationCanceledException){StopBrowser();}catch(Exception e){StopBrowser();browserStatus.Text="浏览器未连接："+e.Message;}
 }
 void StopBrowser(){
  browserCancel?.Cancel();
  try{browserForward?.Dispose();}catch{}browserForward=null;
  var p=browserProcess;browserProcess=null;
  if(p!=null){try{if(!p.HasExited)p.Kill();}catch{}p.Dispose();}
  browserCancel?.Dispose();browserCancel=null;
  browserButton.Text="连接浏览器";browserButton.Enabled=!closing&&ssh?.IsConnected==true;browserChoice.Enabled=true;
  browserStatus.Text="浏览器控制未开启。浏览器和服务器视频任务不会被关闭。";
 }
}
