"""One collection at a time: a lock file holding the running process id.

Two collections in parallel double the requests (the job site starts answering
"429 Too Many Requests") and write to the same database. A lock whose process is gone
(crash, killed) is taken over automatically.
"""
import os
import sys
from pathlib import Path


def _alive(pid):
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def holder(lock_path):
    """pid of the running holder, or None."""
    try:
        pid = int(Path(lock_path).read_text().strip() or 0)
    except Exception:
        return None
    return pid if _alive(pid) and pid != os.getpid() else None


class RunLock:
    def __init__(self, lock_path):
        self.path = Path(lock_path)

    def __enter__(self):
        pid = holder(self.path)
        if pid:
            raise SystemExit(f"Another collection is already running (process {pid}). "
                             f"Wait for it to finish, or stop it first.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(str(os.getpid()))
        return self

    def __exit__(self, *exc):
        try:
            if self.path.read_text().strip() == str(os.getpid()):
                self.path.unlink()
        except Exception:
            pass
