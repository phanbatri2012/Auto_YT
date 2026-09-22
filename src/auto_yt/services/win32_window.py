"""Windows desktop process spawning and window visibility helpers.

Ensures persistent browser service processes run on the interactive
'WinSta0\\Default' desktop so that Chromium windows are physically visible to
the user when toggled, even when the parent server was started in a background
or virtual desktop session.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Collection


def spawn_service_on_interactive_desktop(
    command: list[str],
    cwd: Path | str,
    env: dict[str, str],
    stdout_path: Path,
    stderr_path: Path,
) -> None:
    """Launch a background service process attached to WinSta0\\Default on Windows."""
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
        try:
            import ctypes
            import msvcrt
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

            class STARTUPINFOW(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("lpReserved", wintypes.LPWSTR),
                    ("lpDesktop", wintypes.LPWSTR),
                    ("lpTitle", wintypes.LPWSTR),
                    ("dwX", wintypes.DWORD),
                    ("dwY", wintypes.DWORD),
                    ("dwXSize", wintypes.DWORD),
                    ("dwYSize", wintypes.DWORD),
                    ("dwXCountChars", wintypes.DWORD),
                    ("dwYCountChars", wintypes.DWORD),
                    ("dwFillAttribute", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD),
                    ("wShowWindow", wintypes.WORD),
                    ("cbReserved2", wintypes.WORD),
                    ("lpReserved2", ctypes.c_char_p),
                    ("hStdInput", wintypes.HANDLE),
                    ("hStdOutput", wintypes.HANDLE),
                    ("hStdError", wintypes.HANDLE),
                ]

            class PROCESS_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("hProcess", wintypes.HANDLE),
                    ("hThread", wintypes.HANDLE),
                    ("dwProcessId", wintypes.DWORD),
                    ("dwThreadId", wintypes.DWORD),
                ]

            kernel32.CreateProcessW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.LPWSTR,
                ctypes.c_void_p,
                ctypes.c_void_p,
                wintypes.BOOL,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.LPCWSTR,
                ctypes.POINTER(STARTUPINFOW),
                ctypes.POINTER(PROCESS_INFORMATION),
            ]
            kernel32.CreateProcessW.restype = wintypes.BOOL

            # Open log files in append mode and get inheritable OS handles
            stdout_fd = os.open(
                str(stdout_path),
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o666,
            )
            stderr_fd = os.open(
                str(stderr_path),
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o666,
            )
            h_stdout = msvcrt.get_osfhandle(stdout_fd)
            h_stderr = msvcrt.get_osfhandle(stderr_fd)

            HANDLE_FLAG_INHERIT = 0x00000001
            kernel32.SetHandleInformation(h_stdout, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT)
            kernel32.SetHandleInformation(h_stderr, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT)

            si = STARTUPINFOW()
            si.cb = ctypes.sizeof(STARTUPINFOW)
            si.lpDesktop = r"WinSta0\Default"
            si.dwFlags = 0x00000100  # STARTF_USESTDHANDLES
            si.hStdInput = None
            si.hStdOutput = h_stdout
            si.hStdError = h_stderr

            pi = PROCESS_INFORMATION()
            cmd_str = subprocess.list2cmdline(command)

            CREATE_NO_WINDOW = 0x08000000
            CREATE_UNICODE_ENVIRONMENT = 0x00000400

            env_block = "".join(f"{k}={v}\0" for k, v in sorted(env.items())) + "\0"
            env_buf = ctypes.create_unicode_buffer(env_block)

            success = kernel32.CreateProcessW(
                None,
                cmd_str,
                None,
                None,
                True,  # bInheritHandles
                CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
                ctypes.byref(env_buf),
                str(cwd),
                ctypes.byref(si),
                ctypes.byref(pi),
            )
            os.close(stdout_fd)
            os.close(stderr_fd)

            if success:
                kernel32.CloseHandle(pi.hProcess)
                kernel32.CloseHandle(pi.hThread)
                return
            else:
                last_error = ctypes.get_last_error()
                print(
                    f"CreateProcessW for interactive desktop failed (error {last_error}), falling back.",
                    file=sys.stderr,
                )
        except Exception as exc:
            print(
                f"Interactive desktop spawn failed ({exc}), falling back to subprocess.Popen.",
                file=sys.stderr,
            )

    # Standard fallback (and non-Windows platforms)
    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))

    with (
        stdout_path.open("a", encoding="utf-8") as stdout_handle,
        stderr_path.open("a", encoding="utf-8") as stderr_handle,
    ):
        subprocess.Popen(
            command,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            creationflags=creation_flags,
            close_fds=True,
        )


def set_desktop_window_visibility(
    browser_pids: Collection[int],
    visible: bool,
) -> bool:
    """Find top-level Chromium windows for the given process IDs and restore or minimize."""
    if sys.platform != "win32" or not browser_pids:
        return False

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumDesktopWindows.argtypes = [wintypes.HANDLE, WNDENUMPROC, wintypes.LPARAM]
        user32.EnumDesktopWindows.restype = wintypes.BOOL
        user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL

        target_pids = {int(p) for p in browser_pids if int(p) > 0}
        if not target_pids:
            return False

        matching_hwnds: list[int] = []

        def enum_callback(hwnd: int, _lparam: int) -> bool:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in target_pids:
                class_buf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, class_buf, 256)
                if class_buf.value == "Chrome_WidgetWin_1":
                    matching_hwnds.append(hwnd)
            return True

        h_desktop = user32.OpenDesktopW("Default", 0, False, 0x01FF)
        if h_desktop:
            try:
                user32.EnumDesktopWindows(h_desktop, WNDENUMPROC(enum_callback), 0)
            finally:
                user32.CloseDesktop(h_desktop)
        else:
            user32.EnumWindows(WNDENUMPROC(enum_callback), 0)

        for hwnd in matching_hwnds:
            if visible:
                SW_RESTORE = 9
                user32.ShowWindow(hwnd, SW_RESTORE)
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
            else:
                SW_MINIMIZE = 6
                user32.ShowWindow(hwnd, SW_MINIMIZE)

        return len(matching_hwnds) > 0
    except Exception as exc:
        print(f"Failed to adjust Win32 window visibility: {exc}", file=sys.stderr)
        return False
