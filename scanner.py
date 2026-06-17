"""
WiFi and BLE scanner for macOS.
Uses airport -I (current AP RSSI) which works without Location permission.
Tracks RSSI variance over time for motion detection.
"""
import subprocess
import re
import json
import time
import threading
import asyncio
from collections import deque
from typing import Dict, List, Optional
import statistics

AIRPORT = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"

# Known Orbi node BSSID prefixes
ORBI_PREFIXES = ['94:18:65:f6', '94:18:65:f7:3a', '94:18:65:f7:ec']


class WiFiScanner:
    """
    Reads current WiFi connection RSSI via airport -I.
    No location permission required on macOS Sequoia.
    Tracks rolling variance for motion detection.
    """

    def __init__(self, orbi_ssid: str, node_bssids: Dict[str, str]):
        self.orbi_ssid   = orbi_ssid
        self.node_bssids = node_bssids

        # Rolling RSSI window — track current AP signal over time
        self.rssi_window = deque(maxlen=40)  # ~2 minutes at 3s intervals
        self.current_rssi = None
        self.current_bssid = None
        self.current_node = None

    def _get_current_ap_rssi(self) -> Optional[dict]:
        """
        Read WiFi RSSI via wdutil (Sequoia) or system_profiler fallback.
        wdutil requires sudo -n (passwordless sudo already granted this session).
        """
        # Method 1: wdutil (works on Sequoia, needs sudo)
        try:
            result = subprocess.run(
                ['sudo', '-n', 'wdutil', 'info'],
                capture_output=True, text=True, timeout=8
            )
            if result.returncode == 0 and 'RSSI' in result.stdout:
                rssi_m = re.search(r'RSSI\s*:\s*(-\d+)', result.stdout)
                if rssi_m:
                    # Also try to get BSSID (may be redacted)
                    bssid_m = re.search(r'BSSID\s*:\s*(\S+)', result.stdout)
                    bssid = bssid_m.group(1) if bssid_m else 'orbi'
                    return {
                        'rssi':  int(rssi_m.group(1)),
                        'bssid': bssid.lower(),
                        'ssid':  self.orbi_ssid
                    }
        except Exception:
            pass

        # Method 2: system_profiler (no sudo, slower, less reliable)
        try:
            result = subprocess.run(
                ['system_profiler', 'SPAirPortDataType'],
                capture_output=True, text=True, timeout=15
            )
            rssi_m = re.search(r'Signal\s*/\s*Noise.*?(-\d+)\s*dBm', result.stdout, re.I)
            if not rssi_m:
                rssi_m = re.search(r'RSSI\s*:\s*(-\d+)', result.stdout)
            if rssi_m:
                return {'rssi': int(rssi_m.group(1)), 'bssid': 'orbi', 'ssid': self.orbi_ssid}
        except Exception:
            pass

        return None

    def _identify_node(self, bssid: str) -> Optional[str]:
        """Map BSSID to node name."""
        bssid_lower = bssid.lower()
        for name, prefix in self.node_bssids.items():
            if bssid_lower.startswith(prefix.lower()):
                return name
        return None

    def update(self) -> Dict[str, Optional[int]]:
        """
        Update RSSI reading. Returns dict of node_name -> rssi.
        Only the currently connected node gets a reading.
        """
        ap = self._get_current_ap_rssi()

        node_rssi = {name: None for name in self.node_bssids}

        if ap:
            self.current_rssi  = ap['rssi']
            self.current_bssid = ap['bssid']
            self.rssi_window.append(ap['rssi'])

            # Identify which Orbi node we're connected to
            node = self._identify_node(ap['bssid'])
            self.current_node = node

            if node:
                node_rssi[node] = ap['rssi']
            else:
                # Connected to an Orbi node but BSSID not in our list
                # Assign to closest match by prefix
                for name in self.node_bssids:
                    node_rssi[name] = ap['rssi']
                    break

        return node_rssi

    def get_variance(self, node_name: str = None) -> float:
        """
        RSSI variance over the rolling window.
        High variance = someone moving through the space.
        """
        w = list(self.rssi_window)
        if len(w) < 5:
            return 0.0
        try:
            return statistics.variance(w)
        except Exception:
            return 0.0

    def get_all_device_bssids(self):
        return []


class DeviceTracker:
    """Tracks all devices on the network via ARP and mDNS."""

    MOBILE_PREFIXES = [
        'a4:c3', 'f0:b4', '3c:22', 'ac:de', 'f8:ff', 'dc:2b', '98:01',
        'f4:f1', 'a8:86', '8c:85', '86:24', 'be:e5', 'b4:18', 'de:c4',
        '8c:77', 'f4:42', 'c0:89', 'a8:04', '50:85', '3a:6e', '86:a6',
        'c6:7d', 'b0:99'
    ]

    FIXED_PATTERNS = [
        'tv', 'camera', 'echo', 'alexa', 'nest', 'ring', 'printer',
        'orbi', 'rbe', 'router', 'hub', 'bridge', 'switch', 'restore'
    ]

    def __init__(self):
        self.devices = {}
        self._lock   = threading.Lock()

    def update_from_arp(self):
        try:
            result = subprocess.run(['arp', '-a'],
                                    capture_output=True, text=True, timeout=5)
            now = time.time()
            for line in result.stdout.split('\n'):
                match = re.search(
                    r'(\S+)\s+\((\d+\.\d+\.\d+\.\d+)\)\s+at\s+([0-9a-f:]+)',
                    line, re.I
                )
                if not match:
                    continue
                hostname = match.group(1)
                ip       = match.group(2)
                mac      = match.group(3).lower()
                if mac in ('ff:ff:ff:ff:ff:ff', '(incomplete)'):
                    continue

                with self._lock:
                    if mac not in self.devices:
                        self.devices[mac] = {
                            'mac':        mac,
                            'name':       hostname,
                            'ip':         ip,
                            'first_seen': now,
                            'last_seen':  now,
                            'is_mobile':  self._is_mobile(mac, hostname)
                        }
                    else:
                        self.devices[mac]['last_seen'] = now
        except Exception as e:
            print(f"[tracker] ARP error: {e}")

    def _is_mobile(self, mac: str, name: str) -> bool:
        name_lower = name.lower()
        if any(p in name_lower for p in self.FIXED_PATTERNS):
            return False
        mac_lower = mac.lower()
        if any(mac_lower.startswith(p) for p in self.MOBILE_PREFIXES):
            return True
        # Randomized MACs (private addresses) are usually phones
        if mac_lower[1] in ('2', '6', 'a', 'e'):
            return True
        return False

    def get_present_devices(self, timeout_sec: int = 120) -> list:
        cutoff = time.time() - timeout_sec
        with self._lock:
            return [d for d in self.devices.values() if d['last_seen'] > cutoff]

    def get_mobile_count(self) -> int:
        return len([d for d in self.get_present_devices() if d['is_mobile']])

    def get_all_count(self) -> int:
        return len(self.get_present_devices())

    def update_from_wifi_scan(self, aps):
        pass  # Not available on Sequoia without location permission


class BLEScanner:
    """Scans for BLE devices — phones, watches, AirPods."""

    def __init__(self):
        self.devices  = {}
        self._lock    = threading.Lock()
        self._running = False

    def start(self):
        self._running = True
        t = threading.Thread(target=self._run, daemon=True)
        t.start()

    def stop(self):
        self._running = False

    def _run(self):
        try:
            asyncio.run(self._scan_loop())
        except Exception as e:
            print(f"[ble] Scanner error: {e}")

    async def _scan_loop(self):
        try:
            from bleak import BleakScanner
        except ImportError:
            print("[ble] bleak not installed — BLE disabled")
            return

        while self._running:
            try:
                found = await BleakScanner.discover(timeout=3.0)
                now   = time.time()
                with self._lock:
                    for d in found:
                        # Handle different bleak versions
                        try:
                            rssi = d.rssi
                        except AttributeError:
                            try:
                                rssi = d.advertisement_data.rssi
                            except Exception:
                                rssi = -70

                        self.devices[d.address] = {
                            'name':      d.name or 'Unknown BLE',
                            'address':   d.address,
                            'rssi':      rssi,
                            'last_seen': now
                        }
                await asyncio.sleep(5)
            except Exception as e:
                print(f"[ble] Scan error: {e}")
                await asyncio.sleep(10)

    def get_present(self, timeout_sec: int = 30) -> list:
        cutoff = time.time() - timeout_sec
        with self._lock:
            return [d for d in self.devices.values() if d['last_seen'] > cutoff]

    def count(self) -> int:
        return len(self.get_present())
