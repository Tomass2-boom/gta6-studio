# Posting phones

Old phones (no SIM, Wi-Fi only) used only for TikTok, YouTube, Instagram and Gmail.

## Speed-up recipe (per phone, over ADB)

1. Developer options → USB debugging on; accept the "Allow" prompt.
2. Check the phone: `adb shell top -b -n 1 -m 10 -s 9`, `/proc/meminfo`, `dumpsys package dexopt`.
   - On MediaTek/Xiaomi the load average is always ~30 because of `wdtk-*` watchdog
     threads. Ignore it; look at the `%idle` line in `top` instead.
3. Build `<model>/remove.txt` from the UAD-ng list
   (`resources/assets/uad_lists.json` in Universal-Debloater-Alliance/universal-android-debloater-next-generation).
   Only "Recommended" and a few obvious "Advanced" packages; never "Unsafe"; skip overlays.
   Always keep: TikTok, YouTube, Instagram, Gmail, Photos, Drive, Chrome, Play Store/Services,
   `com.android.remoteprovisioner`, `com.google.android.safetycore`, `com.google.android.verifier`.
4. `phones/debloat.sh phones/<model>/remove.txt` (undo: `phones/restore.sh`).
   Xiaomi refuses some with `Failure [-1000]`: use `pm disable-user --user 0 <pkg>` instead.
5. Restrict background: `cmd appops set <pkg> RUN_ANY_IN_BACKGROUND ignore`.
6. Compile: `cmd package compile -m speed-profile -f <pkg>` for the posting apps, then
   `cmd package bg-dexopt-job` for everything.
7. By hand on the phone (Xiaomi blocks ADB from changing settings):
   Developer options → Window / Transition / Animator scale → 0.5x.

## Done

- `redmi-note-11-pro-plus/` (21091116UG, HyperOS 1.0, Android 13), 2026-11-17:
  98 removed, 4 disabled (soundrecorder, weather2, scanner, Google app). RAM free 3.1 → 4.7 GB.
