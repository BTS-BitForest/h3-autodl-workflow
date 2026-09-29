$ErrorActionPreference = 'Stop'
try {
    if (-not (Get-Command ssh.exe -ErrorAction SilentlyContinue)) { throw '请先在 Windows 可选功能安装 OpenSSH 客户端。' }
    $login = Read-Host '请输入 SSH 命令，例如 ssh -p 12345 root@your-host'
    if ($login -notmatch '^ssh\s+-p\s+(\d{1,5})\s+(root)@([a-zA-Z0-9.-]+)\s*$') { throw '格式应为 ssh -p 端口 root@主机' }
    $port = [int]$Matches[1]; $target = 'root@' + $Matches[3]
    if ($port -lt 1 -or $port -gt 65535) { throw '端口不合法' }
    foreach ($p in @(8190,8188)) {
        $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback,$p)
        try { $listener.Start() } finally { $listener.Stop() }
    }
    $bytes = [IO.File]::ReadAllBytes((Join-Path $PSScriptRoot 'deploy-linux.sh'))
    $encoded = [Convert]::ToBase64String($bytes)
    $remote = "bash -c '" + '$(printf %s ' + $encoded + " | base64 -d)' -- --hold"
    Write-Host '密码由 SSH 直接读取，不会保存。首次连接请核对服务器指纹。'
    & ssh.exe -tt -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -p $port -L '127.0.0.1:8190:127.0.0.1:8190' -L '127.0.0.1:8188:127.0.0.1:8188' $target $remote | ForEach-Object {
        $line = [string]$_
        if ($line -match 'H3_BROWSER_TICKET=([A-Za-z0-9_-]+)') {
            Start-Process ('http://127.0.0.1:8190/#login=' + $Matches[1])
            Write-Host 'H3 面板已打开。'
        } else { Write-Host $line }
    }
    if ($LASTEXITCODE -ne 0) { throw "SSH/部署退出码：$LASTEXITCODE" }
} catch { Write-Host $_ -ForegroundColor Red; exit 1 }
