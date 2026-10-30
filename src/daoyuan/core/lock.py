# -*- coding: utf-8 -*-
"""跨进程采集互斥锁（Windows 命名 Mutex）。

GUI 与计划任务同时启动时只能有一个实际执行采集；锁随进程退出自动释放。
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

from .errors import DaoyuanError

MUTEX_NAME = "Local\\DaoyuanKnowledgeBaseCollector"
REQUEST_MUTEX_NAME = "Local\\DaoyuanKnowledgeBaseRequest"
ERROR_ALREADY_EXISTS = 183
WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080


class LockBusyError(DaoyuanError):
    """已有其他采集任务在运行。"""


def is_lock_held(name: str = MUTEX_NAME) -> bool:
    """只读探测命名互斥锁是否已被其他进程创建。

    不创建、不抢占锁，因此不会让正在等待的持续采集进程误以为“已有任务”
    而退出；用于单篇链接采集前判断自动采集是否正在运行。
    """
    if os.name != "nt":
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenMutexW.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        ctypes.c_wchar_p,
    ]
    kernel32.OpenMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    synchronize = 0x00100000
    handle = kernel32.OpenMutexW(synchronize, False, name)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED 也按“已被占用”处理


class ProcessLock:
    def __init__(self, name: str = MUTEX_NAME):
        self.name = name
        self.handle = None
        self.acquired = False

    def acquire(self) -> bool:
        if os.name != "nt":
            # 本项目只发布 Windows 版；非 Windows 测试环境不引入平台依赖。
            self.acquired = True
            return True
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p,
            wintypes.BOOL,
            ctypes.c_wchar_p,
        ]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            raise LockBusyError("无法创建采集互斥锁。")
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self.handle = handle
        self.acquired = True
        return True

    def release(self) -> None:
        if os.name == "nt" and self.handle is not None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle(self.handle)
        self.handle = None
        self.acquired = False

    def __enter__(self):
        if not self.acquire():
            raise LockBusyError("已有其他采集任务在运行。")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


class RequestGate:
    """跨进程串行化每一次微信 HTTP 请求。

    自动采集和单篇链接采集各自可能有独立的 HttpClient，但它们都通过
    同一个命名互斥锁发请求；同一时刻只允许一个请求真正发出，避免手动
    补录和自动采集叠加触发验证页。
    """

    def __init__(self, name: str = REQUEST_MUTEX_NAME):
        self.name = name
        self.handle = None
        self.acquired = False

    def acquire(self, timeout_ms: int = 30000) -> bool:
        if os.name != "nt":
            self.acquired = True
            return True
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p,
            wintypes.BOOL,
            ctypes.c_wchar_p,
        ]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            raise LockBusyError("无法创建请求互斥锁。")
        result = kernel32.WaitForSingleObject(handle, max(0, int(timeout_ms)))
        if result in (WAIT_OBJECT_0, WAIT_ABANDONED):
            self.handle = handle
            self.acquired = True
            return True
        kernel32.CloseHandle(handle)
        return False

    def release(self) -> None:
        if os.name == "nt" and self.handle is not None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
            kernel32.ReleaseMutex.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.ReleaseMutex(self.handle)
            kernel32.CloseHandle(self.handle)
        self.handle = None
        self.acquired = False

    def __enter__(self):
        if not self.acquire():
            raise LockBusyError("另一个采集请求正在进行。")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False
