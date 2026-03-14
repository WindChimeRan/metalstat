"""IOReport private API bindings via ctypes for sudoless GPU/CPU/power monitoring.

This module wraps libIOReport.dylib to access Apple Silicon performance counters
without requiring root privileges. The API is private and undocumented but has been
stable across macOS releases and is used by macmon, mactop, NeoAsitop, and
SocPowerBuddy.

Key channel groups:
- "GPU Stats"    -> GPU active residency (utilization), frequency states
- "Energy Model" -> CPU/GPU/ANE/Package power consumption (Watts)
"""

from __future__ import annotations

import ctypes
import ctypes.util
import time
from ctypes import c_int32, c_int64, c_uint32, c_uint64, c_void_p, c_char_p
from dataclasses import dataclass, field
from typing import Any


# --- CoreFoundation types and helpers ---

CFTypeRef = c_void_p
CFStringRef = c_void_p
CFDictionaryRef = c_void_p
CFNumberRef = c_void_p
CFArrayRef = c_void_p
CFIndex = c_int64
CFStringEncoding = c_uint32

kCFStringEncodingUTF8 = 0x08000100
kCFNumberSInt64Type = 4
kCFNumberFloat64Type = 6

# Load frameworks
_cf = ctypes.cdll.LoadLibrary(
    "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
)
_iokit = ctypes.cdll.LoadLibrary(
    "/System/Library/Frameworks/IOKit.framework/IOKit"
)

# Try to load IOReport
try:
    _ioreport = ctypes.cdll.LoadLibrary("/usr/lib/libIOReport.dylib")
    IOREPORT_AVAILABLE = True
except OSError:
    _ioreport = None
    IOREPORT_AVAILABLE = False


def _setup_cf_functions():
    """Set up CoreFoundation function signatures."""
    # CFStringCreateWithCString
    _cf.CFStringCreateWithCString.restype = CFStringRef
    _cf.CFStringCreateWithCString.argtypes = [c_void_p, c_char_p, CFStringEncoding]

    # CFStringGetCString
    _cf.CFStringGetCString.restype = ctypes.c_bool
    _cf.CFStringGetCString.argtypes = [CFStringRef, c_char_p, CFIndex, CFStringEncoding]

    # CFStringGetLength
    _cf.CFStringGetLength.restype = CFIndex
    _cf.CFStringGetLength.argtypes = [CFStringRef]

    # CFRelease
    _cf.CFRelease.restype = None
    _cf.CFRelease.argtypes = [CFTypeRef]

    # CFRetain
    _cf.CFRetain.restype = CFTypeRef
    _cf.CFRetain.argtypes = [CFTypeRef]

    # CFGetTypeID
    _cf.CFGetTypeID.restype = c_uint64
    _cf.CFGetTypeID.argtypes = [CFTypeRef]

    # CFDictionaryGetCount
    _cf.CFDictionaryGetCount.restype = CFIndex
    _cf.CFDictionaryGetCount.argtypes = [CFDictionaryRef]

    # CFArrayGetCount
    _cf.CFArrayGetCount.restype = CFIndex
    _cf.CFArrayGetCount.argtypes = [CFArrayRef]

    # CFArrayGetValueAtIndex
    _cf.CFArrayGetValueAtIndex.restype = c_void_p
    _cf.CFArrayGetValueAtIndex.argtypes = [CFArrayRef, CFIndex]

    # CFNumberGetValue
    _cf.CFNumberGetValue.restype = ctypes.c_bool
    _cf.CFNumberGetValue.argtypes = [CFNumberRef, c_int32, c_void_p]

    # Type IDs
    _cf.CFStringGetTypeID.restype = c_uint64
    _cf.CFStringGetTypeID.argtypes = []
    _cf.CFNumberGetTypeID.restype = c_uint64
    _cf.CFNumberGetTypeID.argtypes = []
    _cf.CFDictionaryGetTypeID.restype = c_uint64
    _cf.CFDictionaryGetTypeID.argtypes = []
    _cf.CFArrayGetTypeID.restype = c_uint64
    _cf.CFArrayGetTypeID.argtypes = []

    # CFDictionaryGetValue
    _cf.CFDictionaryGetValue.restype = c_void_p
    _cf.CFDictionaryGetValue.argtypes = [CFDictionaryRef, c_void_p]

    # CFDictionaryGetKeysAndValues
    _cf.CFDictionaryGetKeysAndValues.restype = None
    _cf.CFDictionaryGetKeysAndValues.argtypes = [
        CFDictionaryRef,
        ctypes.POINTER(c_void_p),
        ctypes.POINTER(c_void_p),
    ]


def _setup_ioreport_functions():
    """Set up IOReport function signatures."""
    if not IOREPORT_AVAILABLE:
        return

    # IOReportCopyChannelsInGroup
    _ioreport.IOReportCopyChannelsInGroup.restype = CFDictionaryRef
    _ioreport.IOReportCopyChannelsInGroup.argtypes = [CFStringRef, CFStringRef, c_uint64, c_uint64, c_uint64]

    # IOReportMergeChannels
    _ioreport.IOReportMergeChannels.restype = None
    _ioreport.IOReportMergeChannels.argtypes = [CFDictionaryRef, CFDictionaryRef, c_void_p]

    # IOReportCreateSubscription
    _ioreport.IOReportCreateSubscription.restype = c_void_p
    _ioreport.IOReportCreateSubscription.argtypes = [
        c_void_p, CFDictionaryRef, ctypes.POINTER(CFDictionaryRef),
        c_uint64, c_void_p,
    ]

    # IOReportCreateSamples
    _ioreport.IOReportCreateSamples.restype = CFDictionaryRef
    _ioreport.IOReportCreateSamples.argtypes = [c_void_p, CFDictionaryRef, c_void_p]

    # IOReportCreateSamplesDelta
    _ioreport.IOReportCreateSamplesDelta.restype = CFDictionaryRef
    _ioreport.IOReportCreateSamplesDelta.argtypes = [CFDictionaryRef, CFDictionaryRef, c_void_p]

    # IOReportChannelGetGroup
    _ioreport.IOReportChannelGetGroup.restype = CFStringRef
    _ioreport.IOReportChannelGetGroup.argtypes = [CFDictionaryRef]

    # IOReportChannelGetSubGroup
    _ioreport.IOReportChannelGetSubGroup.restype = CFStringRef
    _ioreport.IOReportChannelGetSubGroup.argtypes = [CFDictionaryRef]

    # IOReportChannelGetChannelName
    _ioreport.IOReportChannelGetChannelName.restype = CFStringRef
    _ioreport.IOReportChannelGetChannelName.argtypes = [CFDictionaryRef]

    # IOReportSimpleGetIntegerValue
    # Second arg is i32 (field index), not a pointer — confirmed by macmon, mactop
    _ioreport.IOReportSimpleGetIntegerValue.restype = c_int64
    _ioreport.IOReportSimpleGetIntegerValue.argtypes = [CFDictionaryRef, c_int32]

    # IOReportStateGetCount
    _ioreport.IOReportStateGetCount.restype = c_int32
    _ioreport.IOReportStateGetCount.argtypes = [CFDictionaryRef]

    # IOReportStateGetResidency
    _ioreport.IOReportStateGetResidency.restype = c_int64
    _ioreport.IOReportStateGetResidency.argtypes = [CFDictionaryRef, c_int32]

    # IOReportStateGetNameForIndex
    _ioreport.IOReportStateGetNameForIndex.restype = CFStringRef
    _ioreport.IOReportStateGetNameForIndex.argtypes = [CFDictionaryRef, c_int32]

    # IOReportChannelGetFormat
    _ioreport.IOReportChannelGetFormat.restype = c_uint32
    _ioreport.IOReportChannelGetFormat.argtypes = [CFDictionaryRef]

    # IOReportChannelGetUnitLabel
    _ioreport.IOReportChannelGetUnitLabel.restype = CFStringRef
    _ioreport.IOReportChannelGetUnitLabel.argtypes = [CFDictionaryRef]


# Initialize function signatures
_setup_cf_functions()
_setup_ioreport_functions()


# --- Helper functions ---

def cfstr(s: str) -> CFStringRef:
    """Create a CFString from a Python string."""
    return _cf.CFStringCreateWithCString(None, s.encode("utf-8"), kCFStringEncodingUTF8)


def cfstr_to_str(ref: CFStringRef) -> str | None:
    """Convert a CFString to a Python string."""
    if not ref:
        return None
    length = _cf.CFStringGetLength(ref)
    buf_size = length * 4 + 1  # UTF-8 can be up to 4 bytes per char
    buf = ctypes.create_string_buffer(buf_size)
    if _cf.CFStringGetCString(ref, buf, buf_size, kCFStringEncodingUTF8):
        return buf.value.decode("utf-8")
    return None


def cfnum_to_int(ref: CFNumberRef) -> int:
    """Convert a CFNumber to a Python int."""
    val = c_int64(0)
    _cf.CFNumberGetValue(ref, kCFNumberSInt64Type, ctypes.byref(val))
    return val.value


def cfnum_to_float(ref: CFNumberRef) -> float:
    """Convert a CFNumber to a Python float."""
    val = ctypes.c_double(0)
    _cf.CFNumberGetValue(ref, kCFNumberFloat64Type, ctypes.byref(val))
    return val.value


# IOReport format types (from reverse engineering)
kIOReportFormatSimple = 1
kIOReportFormatState = 2
kIOReportFormatHistogram = 3


# --- IOReport channel data structures ---

@dataclass
class IOReportChannel:
    group: str
    subgroup: str | None
    channel_name: str
    format_type: int
    unit: str | None = None  # e.g. "mJ", "nJ"
    # For simple format:
    int_value: int | None = None
    # For state format:
    state_residencies: dict[str, int] = field(default_factory=dict)


# --- Main IOReport sampler class ---

class IOReportSampler:
    """Manages IOReport subscriptions and sampling for GPU/power metrics."""

    def __init__(self, groups: list[str] | None = None):
        if not IOREPORT_AVAILABLE:
            raise RuntimeError("IOReport (libIOReport.dylib) is not available")

        if groups is None:
            groups = ["GPU Stats", "Energy Model"]

        self._subscription = None
        self._sub_channels = None  # output channels from CreateSubscription
        self._prev_sample = None
        self._groups = groups
        self._init_subscription()

    def _init_subscription(self):
        """Create an IOReport subscription for the requested channel groups."""
        # Get channels for each group and merge
        merged = None
        for group_name in self._groups:
            group_cf = cfstr(group_name)
            channels = _ioreport.IOReportCopyChannelsInGroup(
                group_cf, None, 0, 0, 0,
            )
            _cf.CFRelease(group_cf)

            if not channels:
                continue

            if merged is None:
                merged = channels
            else:
                _ioreport.IOReportMergeChannels(merged, channels, None)
                # channels is consumed by merge, do not release

        if not merged:
            raise RuntimeError("Failed to get any IOReport channels")

        # Create subscription
        # IOReportCreateSubscription outputs a subscribed-channels dict that
        # MUST be passed to IOReportCreateSamples (passing None causes NULL
        # return). Store it as self._sub_channels.
        sub_channels = CFDictionaryRef()
        self._subscription = _ioreport.IOReportCreateSubscription(
            None, merged, ctypes.byref(sub_channels), 0, None,
        )

        if not self._subscription:
            raise RuntimeError("Failed to create IOReport subscription")

        self._sub_channels = sub_channels

    def sample(self) -> CFDictionaryRef | None:
        """Take a single IOReport sample."""
        if not self._subscription:
            return None
        return _ioreport.IOReportCreateSamples(
            self._subscription, self._sub_channels, None,
        )

    def sample_delta(self, interval: float = 0.2) -> list[IOReportChannel]:
        """Take two samples with a delay and return the delta channels.

        Args:
            interval: Seconds between samples (min 0.05, default 0.2)

        Returns:
            List of IOReportChannel with delta values.
        """
        interval = max(0.05, interval)

        sample1 = self.sample()
        if not sample1:
            return []

        time.sleep(interval)

        sample2 = self.sample()
        if not sample2:
            _cf.CFRelease(sample1)
            return []

        delta = _ioreport.IOReportCreateSamplesDelta(sample1, sample2, None)
        _cf.CFRelease(sample1)
        _cf.CFRelease(sample2)

        if not delta:
            return []

        channels = self._parse_delta(delta)
        _cf.CFRelease(delta)
        return channels

    def _parse_delta(self, delta: CFDictionaryRef) -> list[IOReportChannel]:
        """Parse a delta sample into IOReportChannel objects."""
        channels: list[IOReportChannel] = []

        # The delta is a CFDictionary with an "IOReportChannels" key
        # which contains a CFArray of channel dictionaries
        channels_key = cfstr("IOReportChannels")
        channels_array = _cf.CFDictionaryGetValue(delta, channels_key)
        _cf.CFRelease(channels_key)

        if not channels_array:
            return channels

        count = _cf.CFArrayGetCount(channels_array)

        for i in range(count):
            ch_dict = _cf.CFArrayGetValueAtIndex(channels_array, i)
            if not ch_dict:
                continue

            group_cf = _ioreport.IOReportChannelGetGroup(ch_dict)
            group = cfstr_to_str(group_cf) or ""

            subgroup_cf = _ioreport.IOReportChannelGetSubGroup(ch_dict)
            subgroup = cfstr_to_str(subgroup_cf)

            name_cf = _ioreport.IOReportChannelGetChannelName(ch_dict)
            name = cfstr_to_str(name_cf) or ""

            fmt = _ioreport.IOReportChannelGetFormat(ch_dict)

            unit_cf = _ioreport.IOReportChannelGetUnitLabel(ch_dict)
            unit = cfstr_to_str(unit_cf)

            ch = IOReportChannel(
                group=group,
                subgroup=subgroup,
                channel_name=name,
                format_type=fmt,
                unit=unit,
            )

            if fmt == kIOReportFormatSimple:
                ch.int_value = _ioreport.IOReportSimpleGetIntegerValue(ch_dict, 0)
            elif fmt == kIOReportFormatState:
                state_count = _ioreport.IOReportStateGetCount(ch_dict)
                for si in range(state_count):
                    state_name_cf = _ioreport.IOReportStateGetNameForIndex(ch_dict, si)
                    state_name = cfstr_to_str(state_name_cf) or f"state_{si}"
                    residency = _ioreport.IOReportStateGetResidency(ch_dict, si)
                    ch.state_residencies[state_name] = residency

            channels.append(ch)

        return channels
