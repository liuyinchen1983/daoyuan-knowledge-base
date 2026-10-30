# -*- coding: utf-8 -*-
"""Windows 用户级 API Key 安全存储（DPAPI）。

密钥只在同一 Windows 用户下可解密，不写入配置、数据库或日志。
在没有 DPAPI 的平台上明确拒绝存储，而不是退化为明文。
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from ..core.errors import SecurityError

MAGIC = b"DYKEY1\x00"
ENTROPY = b"daoyuan/deepseek/api-key/v1"
CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _blob(data: bytes):
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(
        len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))
    ), buffer


def _ensure_windows() -> None:
    if os.name != "nt":
        raise SecurityError("当前系统不支持 Windows DPAPI，无法安全保存 API Key。")


def _dpapi(data: bytes, decrypt: bool) -> bytes:
    _ensure_windows()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.c_wchar_p,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    in_blob, in_buffer = _blob(data)
    entropy_blob, entropy_buffer = _blob(ENTROPY)
    out_blob = _DataBlob()
    # in_buffer / entropy_buffer 必须在本次调用结束前保持存活，不能提前释放。
    if decrypt:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(in_blob),
            None,
            ctypes.byref(entropy_blob),
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(out_blob),
        )
    else:
        ok = crypt32.CryptProtectData(
            ctypes.byref(in_blob),
            "道源知识库 API Key",
            ctypes.byref(entropy_blob),
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(out_blob),
        )
    if not ok:
        error = ctypes.WinError(ctypes.get_last_error())
        raise SecurityError(
            "Windows 安全存储操作失败：%s" % (error.strerror or "未知错误")
        )
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


class SecretStore:
    """保存单个密钥文件的 DPAPI 加密容器。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    @property
    def available(self) -> bool:
        return os.name == "nt"

    def exists(self) -> bool:
        try:
            return self.path.is_file() and self.path.stat().st_size > len(MAGIC)
        except OSError:
            return False

    def save(self, secret: str) -> None:
        secret = (secret or "").strip()
        if not secret:
            raise SecurityError("API Key 不能为空。")
        protected = _dpapi(secret.encode("utf-8"), decrypt=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + ".tmp")
        try:
            with open(temp, "wb") as handle:
                handle.write(MAGIC + protected)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        except OSError as exc:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise SecurityError("保存 API Key 失败：%s" % exc) from exc

    def load(self) -> str | None:
        if not self.exists():
            return None
        try:
            raw = self.path.read_bytes()
        except OSError as exc:
            raise SecurityError("读取 API Key 失败：%s" % exc) from exc
        if not raw.startswith(MAGIC):
            raise SecurityError("API Key 文件格式不正确，请清除后重新配置。")
        try:
            plain = _dpapi(raw[len(MAGIC) :], decrypt=True)
            return plain.decode("utf-8")
        except (SecurityError, UnicodeDecodeError) as exc:
            raise SecurityError(
                "无法解密 API Key。它可能由其他 Windows 用户或其他电脑保存，请清除后重新配置。"
            ) from exc

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise SecurityError("清除 API Key 失败：%s" % exc) from exc
