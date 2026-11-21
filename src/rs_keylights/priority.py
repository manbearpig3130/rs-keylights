"""The notes on time while the Mac is busy: macOS's hints for the threads that play a song's notes. Each does nothing
where it can't (another system, or macOS saying no): the notes play as before.

- playing_thread(): the thread that steps through the notes is of the most urgent kind (QoS user-interactive, as a
  game's or an app's main thread): the performance cores first, ahead of the rest of the Mac's work.
- Activity: while a song plays, the process says it's latency-critical work the user started (NSProcessInfo's
  activity): no App Nap, which slows the timers of an app whose window isn't seen (the Dock app has none), and timers
  as precise as they go.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import sys
from typing import Any

QOS_CLASS_USER_INTERACTIVE = 0x21
# NSActivityUserInitiatedAllowingIdleSystemSleep | NSActivityLatencyCritical (the display and the Mac may still sleep)
ACTIVITY_OPTIONS = (0x00FFFFFF & ~(1 << 20)) | 0xFF00000000


def playing_thread() -> bool:
    """This thread made the most urgent kind (QoS user-interactive); whether it was."""
    if sys.platform != "darwin":
        return False
    try:
        set_qos = ctypes.CDLL(None).pthread_set_qos_class_self_np
    except (OSError, AttributeError):
        return False
    set_qos.argtypes = [ctypes.c_uint, ctypes.c_int]
    set_qos.restype = ctypes.c_int
    return set_qos(QOS_CLASS_USER_INTERACTIVE, 0) == 0


class Activity:
    """Latency-critical work the user started, for macOS, from begin() to end() (NSProcessInfo's
    beginActivityWithOptions:reason:); begun again, or ended again, nothing more."""

    def __init__(self, reason: str):
        self.reason = reason
        self._token: int | None = None

    @property
    def active(self) -> bool:
        return self._token is not None

    def begin(self) -> None:
        if self._token is not None:
            return
        runtime = _runtime()
        if runtime is None:
            return
        objc, send, sel, cls = runtime
        pool = objc.objc_autoreleasePoolPush()
        try:
            info = send(ctypes.c_void_p)(cls(b"NSProcessInfo"), sel(b"processInfo"))
            reason = send(ctypes.c_void_p, ctypes.c_char_p)(cls(b"NSString"), sel(b"stringWithUTF8String:"),
                                                             self.reason.encode())
            token = send(ctypes.c_void_p, ctypes.c_uint64, ctypes.c_void_p)(
                info, sel(b"beginActivityWithOptions:reason:"), ACTIVITY_OPTIONS, reason)
            if token:
                self._token = send(ctypes.c_void_p)(token, sel(b"retain"))
        finally:
            objc.objc_autoreleasePoolPop(pool)

    def end(self) -> None:
        token, self._token = self._token, None
        runtime = _runtime()
        if token is None or runtime is None:
            return
        objc, send, sel, cls = runtime
        pool = objc.objc_autoreleasePoolPush()
        try:
            info = send(ctypes.c_void_p)(cls(b"NSProcessInfo"), sel(b"processInfo"))
            send(None, ctypes.c_void_p)(info, sel(b"endActivity:"), token)
            send(None)(token, sel(b"release"))
        finally:
            objc.objc_autoreleasePoolPop(pool)


_RUNTIME: Any = None


def _runtime() -> Any:
    """The Objective-C runtime, Foundation loaded: (libobjc, a typed objc_msgSend maker, sel, class); None where it
    isn't."""
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = False
        if sys.platform == "darwin":
            try:
                objc = ctypes.CDLL(ctypes.util.find_library("objc") or "libobjc.dylib")
                ctypes.CDLL("/System/Library/Frameworks/Foundation.framework/Foundation")
            except OSError:
                return None
            objc.objc_getClass.restype = ctypes.c_void_p
            objc.objc_getClass.argtypes = [ctypes.c_char_p]
            objc.sel_registerName.restype = ctypes.c_void_p
            objc.sel_registerName.argtypes = [ctypes.c_char_p]
            objc.objc_autoreleasePoolPush.restype = ctypes.c_void_p
            objc.objc_autoreleasePoolPop.argtypes = [ctypes.c_void_p]
            address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
            if address is None:
                return None

            def send(restype: Any, *argtypes: Any) -> Any:  # (arm64: called with its real argument types)
                return ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(address)  # type: ignore[call-overload]

            _RUNTIME = (objc, send, objc.sel_registerName, objc.objc_getClass)
    return _RUNTIME or None
