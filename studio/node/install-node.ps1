<#
  One-time, ADMIN setup of the always-on capture node (W2). Every step was approved by the user on 2026-11-19.
  Run from an elevated PowerShell:   powershell -ExecutionPolicy Bypass -File C:\GTA6Studio\studio\node\install-node.ps1
  Safe to run again. No password is asked or stored: the tasks run as SYSTEM.

  What it does
   1. Fast Startup off (clean reboots after a power cut / update)
   2. Wi-Fi adapter: "allow the computer to turn off this device to save power" off
   3. Windows Update: active hours 05:00-23:00 (the 18 h maximum), no auto-restart while a user is signed in
   4. Defender: exclude C:\GTA6Studio\data (many small writes)
   5. Clock: w32time automatic, polls hourly
   6. Disable background tasks of apps the user chose to trim (Opera GX updaters, Acer Jumpstart, Adobe CC background)
   7. Scheduled task GTA6Node   : at boot + every 5 min (a second copy exits at once), as SYSTEM, restart on failure
   8. Scheduled task GTA6NodeHealth: hourly health check (restarts a stuck node, frees disk)
#>
$ErrorActionPreference = 'Continue'
$repo = 'C:\GTA6Studio'
$py = Join-Path $repo '.venv\Scripts\python.exe'
function Step($name, [scriptblock]$do) { try { & $do; "[ok]   $name" } catch { "[FAIL] $name : $($_.Exception.Message)" } }

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  Write-Host 'Run this from an ADMIN PowerShell.' -ForegroundColor Red; exit 1 }
if (-not (Test-Path $py)) { Write-Host "Missing $py : run 'uv sync' in $repo first." -ForegroundColor Red; exit 1 }

Step 'Fast Startup off' { Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' HiberbootEnabled 0 -Type DWord }

Step 'Wi-Fi power saving off' {
  # The "allow the computer to turn off this device" box is bit 0x10 of PnPCapabilities in the adapter's class key
  # (no PowerShell cmdlet sets it). 24 = 0x18 = box unticked, wake-on-magic-packet box left as it was.
  $wifi = Get-NetAdapter -Physical | Where-Object { $_.PhysicalMediaType -match '802.11|Native' -or $_.Name -match 'Wi-?Fi|WLAN' }
  foreach ($n in $wifi) {
    $key = Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}' -ErrorAction SilentlyContinue |
           Where-Object { (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).NetCfgInstanceId -eq $n.InterfaceGuid }
    if (-not $key) { throw "class key for $($n.Name) not found" }
    Set-ItemProperty $key.PSPath PnPCapabilities 24 -Type DWord
    Restart-NetAdapter -Name $n.Name      # a few seconds offline; chat readers reconnect by themselves
  } }

Step 'Windows Update active hours and no auto-restart' {
  $ux = 'HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings'
  Set-ItemProperty $ux ActiveHoursStart 5 -Type DWord; Set-ItemProperty $ux ActiveHoursEnd 23 -Type DWord
  $au = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU'
  if (-not (Test-Path $au)) { New-Item $au -Force | Out-Null }
  Set-ItemProperty $au NoAutoRebootWithLoggedOnUsers 1 -Type DWord }

Step 'Defender exclusion for data' { Add-MpPreference -ExclusionPath (Join-Path $repo 'data') }

Step 'Clock sync hourly' {
  Set-Service w32time -StartupType Automatic; Start-Service w32time -ErrorAction SilentlyContinue
  Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\TimeProviders\NtpClient' SpecialPollInterval 3600 -Type DWord
  w32tm /config /update | Out-Null; w32tm /resync | Out-Null }

Step 'Trim background tasks' {
  Get-ScheduledTask | Where-Object { $_.TaskName -match '^(Opera GX scheduled|AcerJumpstartTask|JumpstartRuntimeTask|ACCBackgroundApplication)' } |
    ForEach-Object { Disable-ScheduledTask -TaskName $_.TaskName -TaskPath $_.TaskPath | Out-Null } }

function Register($name, $argline, $triggers, $restart) {
  $a = New-ScheduledTaskAction -Execute $py -Argument $argline -WorkingDirectory $repo
  $p = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
  $s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount $restart -RestartInterval (New-TimeSpan -Minutes 1)
  Register-ScheduledTask -TaskName $name -Action $a -Principal $p -Settings $s -Trigger $triggers -Force | Out-Null
}
Step 'Task GTA6Node (boot + every 5 min)' {
  $t1 = New-ScheduledTaskTrigger -AtStartup
  $t2 = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 5)
  Register 'GTA6Node' '-m studio.node' @($t1, $t2) 999 }
Step 'Task GTA6NodeHealth (hourly)' {
  $t = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Hours 1)
  Register 'GTA6NodeHealth' '-m studio.node health' @($t) 3 }

''
Get-ScheduledTask -TaskName 'GTA6Node*' | Select-Object TaskName, State | Format-Table -AutoSize
'Done. Reboot test later; the node starts by itself, nobody has to log in.'
