"""Device listing (FR-23): connected DirectInput game controllers and the
devices a controls.cfg / joyCalib.yaml reference.

Connected-device enumeration goes through IDirectInput8W via ctypes so the
instance/product GUIDs match what iRacing stores in controls.cfg. Failures
degrade to an empty list with a reason; the references listing still works.
"""
from __future__ import annotations

import ctypes
import struct
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Any

from irtracker.gfcc.codec import device_note, guid_from_str


@dataclass
class DeviceInfo:
    instance_guid: str
    product_guid: str
    name: str
    note: str | None = None


@dataclass
class DeviceReport:
    connected: list[DeviceInfo] = field(default_factory=list)
    enum_error: str | None = None
    referenced: list[DeviceInfo] = field(default_factory=list)
    calibrated: list[DeviceInfo] = field(default_factory=list)


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def to_str(self) -> str:
        d4 = bytes(self.Data4)
        return (f"{self.Data1:08X}-{self.Data2:04X}-{self.Data3:04X}-"
                f"{d4[:2].hex().upper()}-{d4[2:].hex().upper()}")

    def to_bytes(self) -> bytes:
        return struct.pack("<IHH", self.Data1, self.Data2, self.Data3) + bytes(self.Data4)


def _guid_from_canonical(s: str) -> _GUID:
    p = s.strip("{}").split("-")
    g = _GUID()
    g.Data1 = int(p[0], 16)
    g.Data2 = int(p[1], 16)
    g.Data3 = int(p[2], 16)
    g.Data4 = (ctypes.c_ubyte * 8)(*bytes.fromhex(p[3] + p[4]))
    return g


_MAX_PATH = 260


class _DIDEVICEINSTANCEW(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("guidInstance", _GUID),
        ("guidProduct", _GUID),
        ("dwDevType", wintypes.DWORD),
        ("tszInstanceName", ctypes.c_wchar * _MAX_PATH),
        ("tszProductName", ctypes.c_wchar * _MAX_PATH),
        ("guidFFDriver", _GUID),
        ("wUsagePage", wintypes.WORD),
        ("wUsage", wintypes.WORD),
    ]


_IID_IDirectInput8W = "BF798031-483A-4DA2-AA99-5D64ED369700"
_DIRECTINPUT_VERSION = 0x0800
_DI8DEVCLASS_GAMECTRL = 4
_DIEDFL_ATTACHEDONLY = 0x00000001
_DIENUM_CONTINUE = 1


def enumerate_connected() -> tuple[list[DeviceInfo], str | None]:
    """List attached DirectInput game controllers. Returns (devices, error)."""
    devices: list[DeviceInfo] = []
    try:
        kernel32 = ctypes.windll.kernel32
        # Explicit prototypes: default c_int restype truncates 64-bit handles.
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        dinput8 = ctypes.windll.dinput8
        dinput8.DirectInput8Create.restype = ctypes.c_long
        dinput8.DirectInput8Create.argtypes = [
            wintypes.HMODULE, wintypes.DWORD, ctypes.POINTER(_GUID),
            ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
        hinst = kernel32.GetModuleHandleW(None)
        iid = _guid_from_canonical(_IID_IDirectInput8W)
        obj = ctypes.c_void_p()
        hr = dinput8.DirectInput8Create(
            hinst, _DIRECTINPUT_VERSION, ctypes.byref(iid), ctypes.byref(obj), None)
        if hr != 0 or not obj:
            return [], f"DirectInput8Create failed (hr={hr:#010x})"

        vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents

        enum_cb_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, ctypes.POINTER(_DIDEVICEINSTANCEW), ctypes.c_void_p)

        def on_device(ddi_ptr, _ref):
            ddi = ddi_ptr.contents
            inst = ddi.guidInstance
            prod = ddi.guidProduct
            devices.append(DeviceInfo(
                instance_guid=inst.to_str(),
                product_guid=prod.to_str(),
                name=ddi.tszInstanceName,
                note=device_note(prod.to_bytes()),
            ))
            return _DIENUM_CONTINUE

        # vtable: 0=QueryInterface 1=AddRef 2=Release 3=CreateDevice 4=EnumDevices
        enum_devices = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, wintypes.DWORD, enum_cb_type,
            ctypes.c_void_p, wintypes.DWORD)(vtbl[4])
        release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])

        cb = enum_cb_type(on_device)
        hr = enum_devices(obj, _DI8DEVCLASS_GAMECTRL, cb, None, _DIEDFL_ATTACHEDONLY)
        release(obj)
        if hr != 0:
            return devices, f"EnumDevices failed (hr={hr:#010x})"
        return devices, None
    except Exception as exc:  # pragma: no cover - depends on host DirectInput
        return devices, f"DirectInput enumeration unavailable: {exc}"


def references_from_decoded(doc: dict[str, Any]) -> list[DeviceInfo]:
    """Devices a decoded controls.cfg references via entry GUID slots.

    Axis entries store instance/product in slot0/slot1; button entries in
    slot1/slot2. Product GUIDs carry the PIDVID marker, instance GUIDs do not,
    which is how the two are told apart here.
    """
    pairs: dict[str, str] = {}  # instance guid -> product guid
    for entry in doc["controls"]["entries"]:
        guids = [entry.get(f"slot{i}") for i in range(3)]
        inst = prod = None
        for g in guids:
            if not g:
                continue
            if guid_from_str(g)[10:] == b"PIDVID":
                prod = g
            elif g != "00000001-0000-0000-0000-000000000000":  # extra-slot marker
                inst = g
        if inst:
            pairs.setdefault(inst, prod or "")
    out = []
    for inst, prod in sorted(pairs.items()):
        note = device_note(guid_from_str(prod)) if prod else None
        out.append(DeviceInfo(instance_guid=inst, product_guid=prod, name="", note=note))
    return out


def devices_from_joycalib(text: str) -> list[DeviceInfo]:
    """Device names/GUIDs from joyCalib.yaml."""
    import yaml

    out: list[DeviceInfo] = []
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        return out
    info = data.get("CalibrationInfo") or {}
    for dev in info.get("DeviceList") or []:
        prod = str(dev.get("ProductGUID", "")).strip("{}")
        out.append(DeviceInfo(
            instance_guid=str(dev.get("InstanceGUID", "")).strip("{}"),
            product_guid=prod,
            name=str(dev.get("DeviceName", "")),
            note=device_note(guid_from_str(prod)) if prod else None,
        ))
    return out


def build_report(base_doc: dict[str, Any] | None, joycalib_text: str | None) -> DeviceReport:
    report = DeviceReport()
    report.connected, report.enum_error = enumerate_connected()
    if base_doc:
        report.referenced = references_from_decoded(base_doc)
    if joycalib_text:
        try:
            report.calibrated = devices_from_joycalib(joycalib_text)
        except Exception:
            pass

    # Fill in names for referenced devices from whatever source knows them.
    names = {d.instance_guid: d.name for d in report.connected + report.calibrated if d.name}
    for d in report.referenced:
        d.name = names.get(d.instance_guid, d.name)
    return report
