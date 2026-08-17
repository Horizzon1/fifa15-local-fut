# Continuously log TCP connection attempts made by fifa15.exe.
#
# Snapshots of Get-NetTCPConnection can miss a short-lived SYN_SENT to a dead
# host, so this polls fast and records anything the game opens. It is the
# independent check on the frida connect/ConnectEx hooks: if the hooks are
# silent but this shows connections, the hooks are the problem, not the game.

param(
    [int]$Seconds = 120,
    [string]$OutFile = "logs\connections.log"
)

$ErrorActionPreference = "Continue"
$deadline = (Get-Date).AddSeconds($Seconds)
$seen = @{}

$pids = @(Get-Process fifa15 -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
"watching pids: $($pids -join ',')" | Tee-Object -FilePath $OutFile

while ((Get-Date) -lt $deadline) {
    $conns = Get-NetTCPConnection -ErrorAction SilentlyContinue |
        Where-Object { $pids -contains $_.OwningProcess -or $_.RemotePort -eq 42127 -or $_.RemoteAddress -like "159.153.*" }

    foreach ($c in $conns) {
        # Skip our own listeners.
        if ($c.State -eq "Listen") { continue }
        $key = "$($c.LocalPort)->$($c.RemoteAddress):$($c.RemotePort):$($c.State)"
        if (-not $seen.ContainsKey($key)) {
            $seen[$key] = $true
            $line = "{0}  pid={1}  {2}:{3} -> {4}:{5}  {6}" -f (Get-Date -Format "HH:mm:ss.fff"),
                    $c.OwningProcess, $c.LocalAddress, $c.LocalPort, $c.RemoteAddress, $c.RemotePort, $c.State
            $line | Tee-Object -FilePath $OutFile -Append
        }
    }
    Start-Sleep -Milliseconds 250
}

"done. distinct connections seen: $($seen.Count)" | Tee-Object -FilePath $OutFile -Append
