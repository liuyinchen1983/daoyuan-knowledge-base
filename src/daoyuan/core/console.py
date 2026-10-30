# -*- coding: utf-8 -*-
"""为 windowed EXE 的无界面命令行恢复标准输出。

维护端同时承担 GUI 与计划任务 CLI。若用控制台子系统打包，GUI 启动时会弹出
黑窗；若用窗口子系统打包，CLI 的 print/argparse 输出又无处可写。这里在 CLI
分支按顺序尝试：

1. 已继承且可写的 stdout/stderr（例如被重定向到文件或管道）；
2. 当前进程已有的控制台句柄；
3. AttachConsole 附加到调用方控制台。

只影响当前进程，不修改系统环境或用户终端设置。
"""
from __future__ import annotations

import io
import locale
import os
import sys

STD_INPUT_HANDLE = -10
STD_OUTPUT_HANDLE = -11
STD_ERROR_HANDLE = -12

FILE_TYPE_UNKNOWN = 0
FILE_TYPE_CHAR = 2
FILE_TYPE_PIPE = 3
INVALID_HANDLE_VALUE = -1
ATTACH_PARENT_PROCESS = 0xFFFFFFFF


class ConsoleStream(io.TextIOBase):
    """用 WriteConsoleW 写 Unicode，避免中文在代码页 936 下乱码。"""

    encoding = "utf-8"
    errors = "replace"

    def __init__(self, handle: int):
        self._handle = handle

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        raise io.UnsupportedOperation("console stream has no Python fd")

    def write(self, text: str) -> int:
        if not text:
            return 0
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.WriteConsoleW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        kernel32.WriteConsoleW.restype = wintypes.BOOL
        written_total = 0
        # 控制台单次写入有长度限制，分段写入并处理部分成功。
        while text:
            chunk, text = text[:4096], text[4096:]
            written = wintypes.DWORD(0)
            ok = kernel32.WriteConsoleW(
                wintypes.HANDLE(self._handle),
                chunk,
                len(chunk),
                ctypes.byref(written),
                None,
            )
            if not ok:
                break
            written_total += int(written.value)
            if written.value < len(chunk):
                break
        return written_total

    def flush(self) -> None:
        return None


def _stream_ok(stream) -> bool:
    if stream is None:
        return False
    try:
        return bool(stream.writable())
    except Exception:
        return False


def _stream_from_handle(handle: int):
    import ctypes
    import msvcrt
    from ctypes import wintypes

    if handle in (0, INVALID_HANDLE_VALUE, None):
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetFileType.argtypes = [wintypes.HANDLE]
    kernel32.GetFileType.restype = wintypes.DWORD
    file_type = int(kernel32.GetFileType(wintypes.HANDLE(handle)))
    if file_type == FILE_TYPE_UNKNOWN:
        return None
    if file_type == FILE_TYPE_CHAR:
        mode = wintypes.DWORD(0)
        kernel32.GetConsoleMode.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        if kernel32.GetConsoleMode(wintypes.HANDLE(handle), ctypes.byref(mode)):
            return ConsoleStream(handle)
    try:
        fd = msvcrt.open_osfhandle(int(handle), os.O_WRONLY | os.O_BINARY)
        raw = os.fdopen(fd, "wb", buffering=0)
        encoding = locale.getpreferredencoding(False) or "utf-8"
        return io.TextIOWrapper(
            raw,
            encoding=encoding,
            errors="replace",
            line_buffering=True,
            write_through=True,
        )
    except OSError:
        return None


def _console_handle():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel32.GetStdHandle.restype = wintypes.HANDLE
    handle = int(kernel32.GetStdHandle(wintypes.DWORD(STD_OUTPUT_HANDLE)))
    if handle not in (0, INVALID_HANDLE_VALUE):
        stream = _stream_from_handle(handle)
        if stream is not None:
            return stream
    # AttachConsole 之后个别宿主不会刷新 GetStdHandle，直接打开 CONOUT$。
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    opened = int(
        kernel32.CreateFileW(
            "CONOUT$",
            0x40000000,  # GENERIC_WRITE
            0x00000001 | 0x00000002,  # FILE_SHARE_READ | FILE_SHARE_WRITE
            None,
            3,  # OPEN_EXISTING
            0,
            None,
        )
    )
    if opened in (0, INVALID_HANDLE_VALUE):
        return None
    return ConsoleStream(opened)


def _attach_parent_console() -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.AttachConsole.argtypes = [wintypes.DWORD]
    kernel32.AttachConsole.restype = wintypes.BOOL
    return bool(kernel32.AttachConsole(wintypes.DWORD(ATTACH_PARENT_PROCESS)))


def enable_cli_streams() -> bool:
    """确保 windowed EXE 的 CLI 分支有可写 stdout/stderr。"""
    if os.name != "nt":
        return _stream_ok(sys.stdout)
    stdout_ok = _stream_ok(sys.stdout)
    stderr_ok = _stream_ok(sys.stderr)
    if not stdout_ok:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        stream = _stream_from_handle(
            int(kernel32.GetStdHandle(wintypes.DWORD(STD_OUTPUT_HANDLE)))
        )
        if stream is None and _attach_parent_console():
            stream = _console_handle()
        if stream is not None:
            sys.stdout = stream
            stdout_ok = True
    if not stderr_ok:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        stream = _stream_from_handle(
            int(kernel32.GetStdHandle(wintypes.DWORD(STD_ERROR_HANDLE)))
        )
        if stream is None:
            stream = sys.stdout if _stream_ok(sys.stdout) else None
        if stream is not None:
            sys.stderr = stream
            stderr_ok = True
    # 计划任务/服务等无控制台宿主：丢弃输出但保证 print 与 argparse 不崩溃。
    if not _stream_ok(sys.stdout):
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
        stdout_ok = True
    if not _stream_ok(sys.stderr):
        sys.stderr = sys.stdout
        stderr_ok = True
    return stdout_ok and stderr_ok
