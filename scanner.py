"""
WiFi and BLE scanner for macOS.
Reads all signals your MacBook's radio is already receiving.
No root required for basic scanning.
"""
import subprocess
import re
import json
import time
import threading
import asyncio
from collections import deque
from typing import Dict, List, Optional, Tuple
import statistics

# macOS airport utility path
AIRPORT = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"


class APReading:
    """A single RSSI reading from one access point."""
    def __init__(self, ssid: str, bssid: str, rssi: int, channel: int):
        self.ssid    = ssid
        self.bssid   = bssid
        self.rssi    = rssi
        self.channel = channel
        self.ts      = time.time()


class Device:
    """A device detected on or near the network."""
    def __init__(self, mac: str, name: str, rssi: int, source: str):
        self.mac       = mac
        self.name      = name
        self.rssi      = rssi
        self.source    = source   # 'wifi', 'ble', 'arp'
        self.first_seen = time.time()
        self.last_seen  = time.time()
        self.is_mobile  = False   # phones, watches, laptops


class WiFiScanner:
    """
    Scans for all WiFi access points using the airport utility.
    Identifies Orbi nodes by BSSID and tracks RSSI over time.
    """

    def __init__(self, orbi_ssid: str, node_bssids: Dict[str, str]):
        """
        orbi_ssid    — your network name e.g. "HomeNetwork"
        node_bssids  — {"node_1_up_left": "aa:bb:cc:...", ...}
        """
        self.orbi_ssid    = orbi_ssid
        self.node_bssids  = node_bssids   # name -> bssid prefix (first 8 chars enough)

        # Rolling windows of RSSI per node (last 30 readings)
        self.rssi_windows: Dict[str, deque] = {
            name: deque(maxlen=30) for name in node_bssids
        }

        # All APs seen (for device detection)
        self.all_aps: Dict[str, APReading] = {}

        self._running = False

    def scan_once(self) -> List[APReading]:
        """Run airport -s and parse output."""
        try:
            result = subprocess.run(
                [AIRPORT, "-s"],
                capture_output=True, text=True, timeout=10
            )
            return self._parse_airport(result.stdout)
        except Exception as e:
            print(f"[scanner] WiFi scan error: {e}")
            return []

    def _parse_airport(self, output: str) -> List[APReading]:
        readings = []
        for line in output.strip().split('\n')[1:]:  # skip header
            parts = line.split()
            if len(parts) < 3:
                continue
            try:
                # airport output: SSID BSSID RSSI CHANNEL ...
                # SSID may have spaces so we work backwards from BSSID
                bssid_match = re.search(r'([0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2})', line, re.I)
                rssi_match  = re.search(r'\s(-\d+)\s', line)
                if not bssid_match or not rssi_match:
                    continue

                bssid = bssid_match.group(1).lower()
                rssi  = int(rssi_match.group(1))

                # Extract SSID (everything before the BSSID)
                ssid = line[:bssid_match.start()].strip()

                # Channel (after RSSI)
                after_rssi = line[rssi_match.end():]
                chan_match  = re.search(r'(\d+)', after_rssi)
                channel     = int(chan_match.group(1)) if chan_match else 0

                readings.append(APReading(ssid, bssid, rssi, channel))
            except Exception:
                continue
        return readings

    def update(self) -> Dict[str, Optional[int]]:
        """
        Scan and update RSSI windows for each Orbi node.
        Returns current RSSI per node name.
        """
        readings = self.scan_once()

        # Update all_aps for device detection
        for r in readings:
            self.all_aps[r.bssid] = r

        # Match readings to known Orbi nodes
        node_rssi: Dict[str, Optional[int]] = {name: None for name in self.node_bssids}

        for r in readings:
            for name, bssid_prefix in self.node_bssids.items():
                # Match by BSSID prefix (same node, different bands share prefix)
                if r.bssid.startswith(bssid_prefix.lower()[:8]):
                    # Keep strongest signal (usually 5GHz)
                    if node_rssi[name] is None or r.rssi > node_rssi[name]:
                        node_rssi[name] = r.rssi

        # Push to windows
        for name, rssi in node_rssi.items():
            if rssi is not None:
                self.rssi_windows[name].append(rssi)

        return node_rssi

    def get_variance(self, node_name: str) -> float:
        """RSSI variance for a node — high variance = movement nearby."""
        w = list(self.rssi_windows.get(node_name, []))
        if len(w) < 4:
            return 0.0
        try:
            return statistics.variance(w)
        except Exception:
            return 0.0

    def get_all_device_bssids(self) -> List[APReading]:
        """Return all APs seen — used for device detection."""
        return list(self.all_aps.values())


class DeviceTracker:
    """
    Tracks all devices in range using WiFi probe requests and ARP.
    Classifies as mobile (phone/laptop/watch) or fixed (TV/camera/speaker).
    """

    # OUI prefixes known to be mobile devices
    MOBILE_OUIS = {
        'Apple':   ['a4:c3:f0', 'f0:b4:29', '3c:22:fb', 'ac:de:48', 'f8:ff:c2',
                    'dc:2b:61', '98:01:a7', 'f4:f1:5a', 'a8:86:dd', '8c:85:90'],
        'Samsung': ['8c:77:12', 'f4:42:8f', 'c0:89:ab', 'a8:04:60', '50:85:69'],
        'Google':  ['f4:f5:d8', '54:60:09', 'dc:e5:5b', 'a4:77:58'],
        'OnePlus': ['ac:c1:ee', '8c:8d:28'],
    }

    # Known fixed device name patterns
    FIXED_PATTERNS = ['tv', 'camera', 'echo', 'alexa', 'nest', 'ring',
                      'printer', 'orbi', 'eero', 'router', 'hub', 'bridge']

    def __init__(self):
        self.devices: Dict[str, Device] = {}
        self._lock = threading.Lock()

    def update_from_arp(self):
        """Scan ARP table for connected devices."""
        try:
            result = subprocess.run(['arp', '-a'], capture_output=True, text=True, timeout=5)
            for line in result.stdout.split('\n'):
                match = re.search(r'\((\d+\.\d+\.\d+\.\d+)\) at ([0-9a-f:]{17})', line, re.I)
                if not match:
                    continue
                ip  = match.group(1)
                mac = match.group(2).lower()
                if mac == 'ff:ff:ff:ff:ff:ff':
                    continue

                # Try to get hostname
                try:
                    hostname = subprocess.run(['dns-sd', '-G', 'v4', ip],
                                              capture_output=True, text=True, timeout=1).stdout
                except Exception:
                    hostname = ip

                with self._lock:
                    if mac not in self.devices:
                        self.devices[mac] = Device(mac, ip, -70, 'arp')
                    self.devices[mac].last_seen = time.time()
                    self.devices[mac].is_mobile = self._is_mobile(mac, ip)

        except Exception as e:
            print(f"[tracker] ARP scan error: {e}")

    def update_from_wifi_scan(self, aps: List[APReading]):
        """Extract client devices from WiFi probe requests (best effort without monitor mode)."""
        # In non-monitor mode we can see APs but not client probes
        # We use the AP list to find non-Orbi devices which are other APs/routers
        with self._lock:
            for ap in aps:
                if ap.bssid not in self.devices:
                    self.devices[ap.bssid] = Device(ap.bssid, ap.ssid or ap.bssid, ap.rssi, 'wifi')
                self.devices[ap.bssid].last_seen = time.time()
                self.devices[ap.bssid].rssi      = ap.rssi

    def _is_mobile(self, mac: str, name: str) -> bool:
        mac_lower  = mac.lower()
        name_lower = name.lower()

        # Fixed device by name
        if any(p in name_lower for p in self.FIXED_PATTERNS):
            return False

        # Mobile by OUI
        for vendor_macs in self.MOBILE_OUIS.values():
            if any(mac_lower.startswith(prefix) for prefix in vendor_macs):
                return True

        return False

    def get_present_devices(self, timeout_sec: int = 120) -> List[Device]:
        """Devices seen in the last timeout_sec seconds."""
        cutoff = time.time() - timeout_sec
        with self._lock:
            return [d for d in self.devices.values() if d.last_seen > cutoff]

    def get_mobile_count(self) -> int:
        return len([d for d in self.get_present_devices() if d.is_mobile])

    def get_all_count(self) -> int:
        return len(self.get_present_devices())


class BLEScanner:
    """
    Scans for BLE devices — phones, watches, AirPods.
    Runs in a separate thread with asyncio.
    """

    def __init__(self):
        self.devices: Dict[str, dict] = {}
        self._lock    = threading.Lock()
        self._thread  = None
        self._running = False

    def start(self):
        self._running = True
        self._thread  = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _run(self):
        try:
            from bleak import BleakScanner
            asyncio.run(self._scan_loop())
        except ImportError:
            print("[ble] bleak not installed — BLE scanning disabled")
        except Exception as e:
            print(f"[ble] BLE scanner error: {e}")

    async def _scan_loop(self):
        from bleak import BleakScanner

        while self._running:
            try:
                found = await BleakScanner.discover(timeout=3.0)
                with self._lock:
                    for d in found:
                        # rssi attr varies by bleak version
                        rssi = getattr(d, 'rssi', None) or getattr(d, 'advertisement_data', None) and -70 or -70
                        self.devices[d.address] = {
                            'name':      d.name or 'Unknown',
                            'address':   d.address,
                            'rssi':      rssi,
                            'last_seen': time.time()
                        }
                await asyncio.sleep(2)
            except Exception as e:
                print(f"[ble] Scan error: {e}")
                await asyncio.sleep(5)

    def get_present(self, timeout_sec: int = 60) -> List[dict]:
        cutoff = time.time() - timeout_sec
        with self._lock:
            return [d for d in self.devices.values() if d['last_seen'] > cutoff]

    def count(self) -> int:
        return len(self.get_present())
