$ErrorActionPreference = 'Stop'
# Migrate only this package's old startup helper. Never kill unrelated SSH/VPN processes.
$owned = Join-Path $env:LOCALAPPDATA 'H3Network'
$watcher = Join-Path $owned 'Keep-Network.ps1'
$helper = Join-Path $owned 'H3Network.exe'
$startup = Join-Path ([Environment]::GetFolderPath('Startup')) 'H3 Network Helper.lnk'
if (Test-Path -LiteralPath $startup) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($startup)
    if ($link.Arguments -like "*$watcher*") {
        Copy-Item -LiteralPath $startup -Destination (Join-Path $owned 'H3 Network Helper.lnk.disabled') -Force
        Remove-Item -LiteralPath $startup
    } else { throw '发现同名开机快捷方式，但不是本工具创建的，未修改。' }
}
# Disable only the reverse forwarding injected by our previous installer; retain aliases/keys.
$config = Join-Path $env:USERPROFILE '.ssh\h3-auto.conf'
if (Test-Path -LiteralPath $config) {
    $lines = Get-Content -LiteralPath $config
    $filtered = @($lines | Where-Object { $_ -notmatch '^\s*RemoteForward\s+127\.0\.0\.1:41084\s+(127\.0\.0\.1|localhost):41084\s*$' })
    if ($filtered.Count -ne $lines.Count) {
        Copy-Item -LiteralPath $config -Destination ($config + '.before-exe-lifecycle.bak') -Force
        [IO.File]::WriteAllLines($config, $filtered, (New-Object Text.UTF8Encoding($false)))
    }
}
$processes = @(Get-CimInstance Win32_Process)
foreach ($p in $processes) {
    $isWatcher = ($p.Name -in @('powershell.exe','pwsh.exe')) -and ($p.CommandLine -like "*$watcher*")
    $isHelper = $p.ExecutablePath -and ($p.ExecutablePath -ieq $helper)
    if ($isWatcher) {
        # Stop watchdog first so it cannot recreate its own child SSH.
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        foreach ($child in $processes) {
            if ($child.ParentProcessId -eq $p.ProcessId -and $child.Name -ieq 'ssh.exe' -and $child.CommandLine -match 'BatchMode[= ]yes') {
                Stop-Process -Id $child.ProcessId -Force -ErrorAction SilentlyContinue
            }
        }
    } elseif ($isHelper) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
}
