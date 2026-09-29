using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.RegularExpressions;
using System.Text.Json;
using Microsoft.Win32;
using System.Collections.Concurrent;
namespace H3Launcher;
sealed class ProxyBridge : IDisposable {
 readonly TcpListener listener=new(IPAddress.Loopback,0);
 readonly CancellationTokenSource stop=new(); readonly string manual;
 readonly ConcurrentDictionary<TcpClient,byte> active=new(); int disposed;
 public int Port=>((IPEndPoint)listener.LocalEndpoint).Port;
 public ProxyBridge(string setting,Action<string> message){manual=setting;}
 public Task Start(){listener.Start();_ = Accept();return Task.CompletedTask;}
 async Task Accept(){try{while(!stop.IsCancellationRequested){var c=await listener.AcceptTcpClientAsync(stop.Token);active.TryAdd(c,0);if(stop.IsCancellationRequested){c.Dispose();active.TryRemove(c,out _);break;}_ = Handle(c);}}catch(OperationCanceledException){}catch(ObjectDisposedException){}catch(SocketException)when(stop.IsCancellationRequested){}}
 public void Dispose(){if(Interlocked.Exchange(ref disposed,1)!=0)return;stop.Cancel();listener.Stop();foreach(var c in active.Keys)c.Dispose();}
IEnumerable<Uri> Candidates(string target){
 var values=new List<string>();if(manual.Length>0)values.Add(manual);
 foreach(var k in new[]{"HTTPS_PROXY","HTTP_PROXY","ALL_PROXY"}){var v=Environment.GetEnvironmentVariable(k);if(!string.IsNullOrEmpty(v))values.Add(v);}
 if(OperatingSystem.IsWindows()){
  try{using var key=Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Internet Settings");if(Convert.ToInt32(key?.GetValue("ProxyEnable")??0)!=0){foreach(var s in (key?.GetValue("ProxyServer")?.ToString()??"").Split(';')){var v=s.Contains('=')?s.Split('=',2)[1]:s;values.Add(v.Contains("://")?v:"http://"+v);}}}catch{}

 }
 values.AddRange(new[]{"http://127.0.0.1:7890","http://127.0.0.1:7897","http://127.0.0.1:10809","socks5://127.0.0.1:1080","socks5://127.0.0.1:10808"});
 return values.Select(x=>Uri.TryCreate(x,UriKind.Absolute,out var u)?u:null).Where(u=>u!=null&&(u.Scheme=="http"||u.Scheme=="socks5")&&u.UserInfo==""&&!(u.IsLoopback&&u.Port==Port)).Cast<Uri>().DistinctBy(u=>u.ToString());
}
async Task<string> Header(Stream s,CancellationToken ct){var b=new byte[1];var h=new List<byte>();while(h.Count<16384){if(await s.ReadAsync(b,ct)==0)throw new IOException();h.Add(b[0]);if(h.Count>=4&&h[^4]==13&&h[^3]==10&&h[^2]==13&&h[^1]==10)return Encoding.ASCII.GetString(h.ToArray());}throw new IOException();}
async Task<TcpClient> Dial(string h,int p,CancellationToken ct){var c=new TcpClient();try{await c.ConnectAsync(h,p,ct);return c;}catch{c.Dispose();throw;}}
async Task<byte[]> Read(Stream s,int n,CancellationToken ct){var b=new byte[n];await s.ReadExactlyAsync(b,ct);return b;}
async Task Tunnel(TcpClient c,Uri proxy,string host,int dest,CancellationToken ct){var s=c.GetStream();if(proxy.Scheme=="http"){await s.WriteAsync(Encoding.ASCII.GetBytes($"CONNECT {host}:{dest} HTTP/1.1\r\nHost: {host}:{dest}\r\n\r\n"),ct);if(!Regex.IsMatch(await Header(s,ct),@"^HTTP/1\.[01] 200\b"))throw new IOException();}else{
 await s.WriteAsync(new byte[]{5,1,0},ct);var hello=await Read(s,2,ct);if(hello[0]!=5||hello[1]!=0)throw new IOException("SOCKS authentication unsupported");var domain=Encoding.ASCII.GetBytes(host);if(domain.Length>255)throw new IOException();var req=new List<byte>{5,1,0,3,(byte)domain.Length};req.AddRange(domain);req.Add((byte)(dest>>8));req.Add((byte)dest);await s.WriteAsync(req.ToArray(),ct);var reply=await Read(s,4,ct);if(reply[0]!=5||reply[1]!=0)throw new IOException();int n=reply[3] switch{1=>4,4=>16,3=>(await Read(s,1,ct))[0],_=>throw new IOException()};await Read(s,n+2,ct);
 }}
async Task Handle(TcpClient incoming){try{using(incoming)using(var timeout=CancellationTokenSource.CreateLinkedTokenSource(stop.Token)){timeout.CancelAfter(TimeSpan.FromSeconds(15));TcpClient? outgoing=null;bool established=false;try{var stream=incoming.GetStream();var head=await Header(stream,timeout.Token);
 if(head.StartsWith("GET /health HTTP/")){var body=JsonSerializer.Serialize(new{service="h3-windows-network",port=Port,mode="auto-http-socks-tun"});await stream.WriteAsync(Encoding.UTF8.GetBytes("HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: "+Encoding.UTF8.GetByteCount(body)+"\r\n\r\n"+body),timeout.Token);return;}
 var m=Regex.Match(head,@"^CONNECT ([A-Za-z0-9.-]+):(443|80) HTTP/1\.[01]\r\n");if(!m.Success)throw new IOException("CONNECT to HTTPS/HTTP only");var host=m.Groups[1].Value;int dest=int.Parse(m.Groups[2].Value);
 foreach(var proxy in Candidates(host)){using var attempt=CancellationTokenSource.CreateLinkedTokenSource(timeout.Token);attempt.CancelAfter(TimeSpan.FromSeconds(1.5));try{outgoing=await Dial(proxy.Host,proxy.Port,attempt.Token);await Tunnel(outgoing,proxy,host,dest,attempt.Token);break;}catch{outgoing?.Dispose();outgoing=null;}}
 outgoing??=await Dial(host,dest,timeout.Token);await stream.WriteAsync(Encoding.ASCII.GetBytes("HTTP/1.1 200 Connection Established\r\n\r\n"),timeout.Token);established=true;timeout.CancelAfter(Timeout.InfiniteTimeSpan);
 using(outgoing){var remote=outgoing.GetStream();var a=stream.CopyToAsync(remote,stop.Token);var b=remote.CopyToAsync(stream,stop.Token);await Task.WhenAny(a,b);incoming.Close();outgoing.Close();try{await Task.WhenAll(a,b);}catch{}}
 }catch{outgoing?.Dispose();if(!established)try{await incoming.GetStream().WriteAsync(Encoding.ASCII.GetBytes("HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"));}catch{}}}}finally{active.TryRemove(incoming,out _);}}
}
