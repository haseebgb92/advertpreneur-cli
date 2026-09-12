from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
from ctypes import wintypes
from pathlib import Path


class CredentialError(RuntimeError):
    pass


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_DATA_BLOB, ctypes.Array]:
    buf = ctypes.create_string_buffer(data)
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))), buf


def _dpapi_protect(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise CredentialError("DPAPI is only available on Windows")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB)]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    in_blob, _buf = _blob(data)
    out_blob = _DATA_BLOB()
    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob), "Advertpreneur CLI", None, None, None, 0, ctypes.byref(out_blob)
    ):
        raise CredentialError("Windows DPAPI could not protect the API key")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise CredentialError("DPAPI is only available on Windows")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    in_blob, _buf = _blob(data)
    out_blob = _DATA_BLOB()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    ):
        raise CredentialError("Windows DPAPI could not decrypt the saved API key")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _broadcast_environment_change() -> None:
    if sys.platform != "win32":
        return
    try:
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHUNG = 0x0002
        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment",
            SMTO_ABORTIFHUNG, 2000, ctypes.byref(result)
        )
    except Exception:
        pass


def _delete_windows_user_env(name: str) -> bool:
    """Delete one HKCU\\Environment value. Never touches machine-level variables."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
            try:
                winreg.QueryValueEx(key, name)
            except FileNotFoundError:
                return False
            winreg.DeleteValue(key, name)
        _broadcast_environment_change()
        return True
    except Exception:
        return False


class CredentialStore:
    """Small local credential store.

    On Windows, the key is encrypted with DPAPI and can only be decrypted by the
    same Windows user account. Environment-variable credentials are still accepted
    for compatibility, but an Advertpreneur-saved login takes precedence.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def source(self) -> str:
        if self.path.exists():
            return "saved"
        if os.environ.get("OLLAMA_API_KEY"):
            return "environment"
        return "none"

    def load(self) -> str | None:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                encoded = base64.b64decode(str(raw.get("data", "")))
                scheme = raw.get("scheme")
                if scheme == "dpapi":
                    return _dpapi_unprotect(encoded).decode("utf-8")
                if scheme == "base64":
                    return encoded.decode("utf-8")
            except Exception as exc:
                raise CredentialError(f"Could not read saved Ollama credential: {exc}") from exc
        env = os.environ.get("OLLAMA_API_KEY")
        return env.strip() if env and env.strip() else None

    def save(self, key: str) -> None:
        key = key.strip()
        if not key:
            raise CredentialError("API key cannot be empty")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            protected = _dpapi_protect(key.encode("utf-8"))
            payload = {"scheme": "dpapi", "data": base64.b64encode(protected).decode("ascii")}
        else:
            # Development fallback; production target is Windows. Keep permissions tight.
            payload = {"scheme": "base64", "data": base64.b64encode(key.encode("utf-8")).decode("ascii")}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def remove_legacy_environment_key(self) -> bool:
        """Remove an old per-user OLLAMA_API_KEY created with setx.

        This is intentionally limited to the current user's Windows Environment
        registry key; machine-level environment variables are never changed.
        """
        removed = _delete_windows_user_env("OLLAMA_API_KEY")
        os.environ.pop("OLLAMA_API_KEY", None)
        return removed

    def delete(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
