"""Process helpers that work on Windows (no os.killpg there) and still on macOS/Linux."""
import os
import signal
import subprocess
import sys

WIN = sys.platform == "win32"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def kill_tree(pid: int):
    """Stop a process and everything it started (streamlink | ffmpeg pipes, yt-dlp helpers). Never raises."""
    try:
        if WIN:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
        else:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
    except Exception:
        pass


class KillOnCloseJob:
    """Windows Job Object: every process assigned to it dies when the supervisor dies, however it dies (crash, kill,
    end task). Without this a killed supervisor leaves orphaned chatlog/streamlink processes that the restarted
    supervisor would duplicate. A no-op on other systems."""

    def __init__(self):
        self.h = None
        if not WIN:
            return
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class Ext(ctypes.Structure):
            _fields_ = [("Basic", Basic), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self._k = k
        h = k.CreateJobObjectW(None, None)
        info = Ext()
        info.Basic.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if h and k.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):   # 9 = ExtendedLimitInformation
            self.h = h

    def add(self, p: subprocess.Popen) -> bool:
        return bool(self.h) and bool(self._k.AssignProcessToJobObject(self.h, int(p._handle)))
