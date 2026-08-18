"""Set Windows default *recording* devices via IPolicyConfig (userspace COM).

When a virtual cable is present, Discord / Zoom / Teams can stay on
Default microphone if that default is the cable's capture endpoint
(typically "CABLE Output"). This helper does that — and only that.

Never changes default playback. Fail-open on any error or non-Windows
host. No driver, no TESTSIGNING, no admin rights.

Method: IPolicyConfig.SetDefaultEndpoint is the same userspace COM call
the Sound control panel uses (CLSID CPolicyConfigClient). We pass a
*capture* device ID and the recording roles only.
"""

from __future__ import annotations

import sys
import uuid
from typing import Any, Optional

_CABLE_MARKERS = (
    "vb-audio",
    "vb audio",
    "vb-cable",
    "vb cable",
    "cable input",
    "cable output",
    "voicemeeter",
    "virtual cable",
    "virtual audio cable",
)

# eConsole + eCommunications are "default recording" and "communications
# recording". eMultimedia is set too so Settings' single Default mic updates.
# All three are applied to a capture endpoint ID — never a render ID.
_ROLES = (0, 1, 2)  # eConsole, eMultimedia, eCommunications

_E_CAPTURE = 1
_DEVICE_STATE_ACTIVE = 0x1
_STGM_READ = 0
_CLSCTX_ALL = 23
_VT_LPWSTR = 31
_S_OK = 0
_S_FALSE = 1
_RPC_E_CHANGED_MODE = 0x80010106

_CLSID_MMDEVICE_ENUMERATOR = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
_IID_IMMDEVICE_ENUMERATOR = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
_CLSID_POLICY_CONFIG_CLIENT = "{870AF99C-171D-4F9E-AF0D-E63DF40C2BC9}"
_IID_IPOLICY_CONFIG = "{F8679F50-850A-41CF-9C72-430F290290C8}"
_CLSID_POLICY_CONFIG_VISTA = "{294935CE-F637-4E7C-A41B-AB255460B862}"
_IID_IPOLICY_CONFIG_VISTA = "{568B9108-44BF-40B4-9006-86AFE5B5A620}"
# IPolicyConfig vtable: IUnknown + 10 methods, then SetDefaultEndpoint.
_SET_DEFAULT_ENDPOINT = 13
# IPolicyConfigVista: IUnknown + 9 methods, then SetDefaultEndpoint.
_SET_DEFAULT_ENDPOINT_VISTA = 12

# PKEY_Device_FriendlyName
_PKEY_FRIENDLY_FMTID = "{A45C254E-DF1C-4EFD-8020-67D146A850E0}"
_PKEY_FRIENDLY_PID = 14


def device_looks_like_cable(name: str) -> bool:
    n = (name or "").casefold()
    return any(marker in n for marker in _CABLE_MARKERS)


def pick_cable_capture(names: list[str]) -> str:
    """Prefer VB-CABLE's capture side (CABLE Output), else any cable-like name."""
    for name in names:
        if "cable output" in (name or "").casefold():
            return name
    for name in names:
        if device_looks_like_cable(name):
            return name
    return ""


def set_default_recording_to_cable() -> dict[str, Any]:
    """Point Windows default + comms recording at the cable capture device.

    Fail-open. Returns {"ok": bool, "device": str, "error": str|None}.
    """
    if sys.platform != "win32":
        return {"ok": False, "device": "", "error": "not_windows"}
    try:
        return _set_default_recording_win()
    except Exception as exc:
        return {"ok": False, "device": "", "error": str(exc)[:200]}


def _set_default_recording_win() -> dict[str, Any]:
    import ctypes
    from ctypes import POINTER, byref, c_int, c_uint, c_void_p, wintypes

    ole32 = ctypes.windll.ole32

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", wintypes.BYTE * 8),
        ]

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

    class PROPVARIANT(ctypes.Structure):
        # 8-byte header + 16-byte union = 24 bytes on Win64.
        class _Data(ctypes.Union):
            _fields_ = [
                ("pwszVal", wintypes.LPWSTR),
                ("_pad", ctypes.c_byte * 16),
            ]

        _fields_ = [
            ("vt", wintypes.USHORT),
            ("wReserved1", wintypes.USHORT),
            ("wReserved2", wintypes.USHORT),
            ("wReserved3", wintypes.USHORT),
            ("data", _Data),
        ]

    def guid(text: str) -> GUID:
        u = uuid.UUID(text)
        g = GUID()
        g.Data1 = u.time_low
        g.Data2 = u.time_mid
        g.Data3 = u.time_hi_version
        for i, b in enumerate(u.bytes[8:16]):
            g.Data4[i] = b
        return g

    def vtbl(obj: c_void_p, index: int, restype, *argtypes):
        p = ctypes.cast(obj, POINTER(c_void_p))
        table = ctypes.cast(p[0], POINTER(c_void_p))
        return ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)(table[index])

    def release(obj: Optional[c_void_p]) -> None:
        if not obj:
            return
        try:
            vtbl(obj, 2, ctypes.HRESULT)(obj)
        except Exception:
            pass

    def succeeded(hr: int) -> bool:
        return hr >= 0

    ole32.CoInitializeEx.argtypes = [c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.HRESULT
    ole32.CoCreateInstance.argtypes = [
        POINTER(GUID),
        c_void_p,
        wintypes.DWORD,
        POINTER(GUID),
        POINTER(c_void_p),
    ]
    ole32.CoCreateInstance.restype = ctypes.HRESULT
    ole32.CoTaskMemFree.argtypes = [c_void_p]
    ole32.PropVariantClear.argtypes = [POINTER(PROPVARIANT)]

    hr_init = ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
    owned = hr_init == _S_OK
    if not succeeded(hr_init) and (hr_init & 0xFFFFFFFF) != _RPC_E_CHANGED_MODE:
        return {"ok": False, "device": "", "error": "com_init"}

    enumerator = c_void_p()
    collection = c_void_p()
    policy = c_void_p()
    picked_name = ""
    picked_id = None
    try:
        clsid_enum = guid(_CLSID_MMDEVICE_ENUMERATOR)
        iid_enum = guid(_IID_IMMDEVICE_ENUMERATOR)
        hr = ole32.CoCreateInstance(
            byref(clsid_enum),
            None,
            _CLSCTX_ALL,
            byref(iid_enum),
            byref(enumerator),
        )
        if not succeeded(hr) or not enumerator:
            return {"ok": False, "device": "", "error": "enumerator"}

        # IMMDeviceEnumerator::EnumAudioEndpoints — capture only.
        hr = vtbl(enumerator, 3, ctypes.HRESULT, c_int, wintypes.DWORD, POINTER(c_void_p))(
            enumerator, _E_CAPTURE, _DEVICE_STATE_ACTIVE, byref(collection)
        )
        if not succeeded(hr) or not collection:
            return {"ok": False, "device": "", "error": "enum_capture"}

        count = c_uint(0)
        hr = vtbl(collection, 3, ctypes.HRESULT, POINTER(c_uint))(collection, byref(count))
        if not succeeded(hr) or count.value == 0:
            return {"ok": False, "device": "", "error": "no_capture"}

        pkey = PROPERTYKEY(guid(_PKEY_FRIENDLY_FMTID), _PKEY_FRIENDLY_PID)
        found: list[tuple[str, c_void_p]] = []
        for i in range(int(count.value)):
            device = c_void_p()
            hr = vtbl(collection, 4, ctypes.HRESULT, c_uint, POINTER(c_void_p))(
                collection, i, byref(device)
            )
            if not succeeded(hr) or not device:
                continue
            store = c_void_p()
            name = ""
            try:
                hr = vtbl(device, 4, ctypes.HRESULT, wintypes.DWORD, POINTER(c_void_p))(
                    device, _STGM_READ, byref(store)
                )
                if succeeded(hr) and store:
                    prop = PROPVARIANT()
                    hr = vtbl(
                        store, 5, ctypes.HRESULT, POINTER(PROPERTYKEY), POINTER(PROPVARIANT)
                    )(store, byref(pkey), byref(prop))
                    if succeeded(hr) and prop.vt == _VT_LPWSTR and prop.data.pwszVal:
                        name = prop.data.pwszVal
                    ole32.PropVariantClear(byref(prop))
                id_ptr = c_void_p()
                hr = vtbl(device, 5, ctypes.HRESULT, POINTER(c_void_p))(device, byref(id_ptr))
                if succeeded(hr) and id_ptr:
                    found.append((name, id_ptr))
            finally:
                release(store)
                release(device)

        chosen = pick_cable_capture([n for n, _ in found])
        if not chosen:
            for _, id_ptr in found:
                ole32.CoTaskMemFree(id_ptr)
            return {"ok": False, "device": "", "error": "no_cable_capture"}

        picked_name = chosen
        for name, id_ptr in found:
            if name == chosen and picked_id is None:
                picked_id = id_ptr
            else:
                ole32.CoTaskMemFree(id_ptr)
        if not picked_id:
            return {"ok": False, "device": picked_name, "error": "no_device_id"}

        device_id = ctypes.wstring_at(picked_id)

        def create_policy(clsid_text: str, iid_text: str) -> c_void_p:
            obj = c_void_p()
            hr_local = ole32.CoCreateInstance(
                byref(guid(clsid_text)),
                None,
                _CLSCTX_ALL,
                byref(guid(iid_text)),
                byref(obj),
            )
            if succeeded(hr_local) and obj:
                return obj
            return c_void_p()

        policy = create_policy(_CLSID_POLICY_CONFIG_CLIENT, _IID_IPOLICY_CONFIG)
        slot = _SET_DEFAULT_ENDPOINT
        if not policy:
            policy = create_policy(_CLSID_POLICY_CONFIG_VISTA, _IID_IPOLICY_CONFIG_VISTA)
            slot = _SET_DEFAULT_ENDPOINT_VISTA
        if not policy:
            return {"ok": False, "device": picked_name, "error": "policy_config"}

        any_ok = False
        last_hr = 0
        for role in _ROLES:
            last_hr = vtbl(policy, slot, ctypes.HRESULT, wintypes.LPCWSTR, c_int)(
                policy, device_id, role
            )
            if succeeded(last_hr):
                any_ok = True
        if not any_ok:
            return {
                "ok": False,
                "device": picked_name,
                "error": f"set_default:{last_hr & 0xFFFFFFFF:08x}",
            }
        return {"ok": True, "device": picked_name, "error": None}
    finally:
        if picked_id:
            ole32.CoTaskMemFree(picked_id)
        release(policy)
        release(collection)
        release(enumerator)
        if owned:
            try:
                ole32.CoUninitialize()
            except Exception:
                pass
