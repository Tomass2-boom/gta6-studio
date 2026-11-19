<#
  Self-healing test: simulate the internet going away for N seconds for the node's own programs only (Windows Firewall
  outbound block on python, yt-dlp, curl, streamlink, syncthing), then restore it. Admin needed.
  Run:  powershell -ExecutionPolicy Bypass -File C:\GTA6Studio\studio\node\selftest-netdrop.ps1 -Seconds 120
  Expected afterwards: coverage-<date>.jsonl has minutes with "chat_ok": [] (the hole), node.log says "internet LOST" then
  "internet back", no restart loop, and chat files grow again.
#>
param([int]$Seconds = 120)
$ErrorActionPreference = 'Stop'
$repo = 'C:\GTA6Studio'
$log = Join-Path $repo 'data\netdrop-test.log'
function Log($m) { "$(Get-Date -Format 'HH:mm:ss') $m" | Tee-Object -FilePath $log -Append }
$progs = @((Join-Path $repo '.venv\Scripts\python.exe'))
$pyhome = (Get-Content (Join-Path $repo '.venv\pyvenv.cfg') | Where-Object { $_ -match '^home\s*=' }) -replace '^home\s*=\s*', ''
if ($pyhome) { $progs += (Join-Path $pyhome 'python.exe') }
$pk = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages"
$progs += Get-ChildItem 'C:\Users\*\AppData\Local\Microsoft\WinGet\Packages' -Recurse -Include yt-dlp.exe, syncthing.exe -ErrorAction SilentlyContinue | ForEach-Object FullName
$progs += Get-ChildItem 'C:\Users\*\AppData\Local\Programs\Streamlink' -Recurse -Include python.exe, streamlink.exe -ErrorAction SilentlyContinue | ForEach-Object FullName
$progs += 'C:\Windows\System32\curl.exe'
$rules = @()
try {
  $i = 0
  foreach ($p in ($progs | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique)) {
    $i++; $n = "GTA6-netdrop-test-$i"
    New-NetFirewallRule -DisplayName $n -Direction Outbound -Action Block -Program $p | Out-Null; $rules += $n
  }
  Log "internet BLOCKED for $($rules.Count) programs for $Seconds s"
  Start-Sleep $Seconds
} finally {
  foreach ($n in $rules) { Remove-NetFirewallRule -DisplayName $n -ErrorAction SilentlyContinue }
  Log "internet RESTORED (rules removed: $($rules.Count))"
}
