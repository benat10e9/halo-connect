#!/usr/bin/env python3
"""
Halo Connect — Mac Agent
========================
Turns your MacBook into a Halo sensor node.
Reads ambient WiFi signals from your Orbi mesh, detects presence,
tracks devices, and streams data to your Halo dashboard.

Runs as a menubar app. No window, no fuss.
"""
import os
import sys
import json
import time
import threading
import socket
import uuid
import statistics
from datetime import datetime
from typing import Dict, Optional

import paho.mqtt.client as mqtt

from scanner       import WiFiScanner, DeviceTracker, BLEScanner
from triangulation import detect_zone, detect_floor, compute_total_variance, NODE_POSITIONS
from baseline      import BaselineDetector

CONFIG_PATH = os.path.expanduser("~/.halo-connect/config.json")

# ── Presence detection thresholds ─────────────────────────────────────────
PRESENCE_VARIANCE_THRESHOLD = 0.5   # single AP variance threshold
MOTION_VARIANCE_THRESHOLD   = 1.2   # above this = active movement
SCAN_INTERVAL_SEC           = 3     # how often to scan (seconds)
PUBLISH_INTERVAL_SEC        = 5     # how often to publish to server


class HaloConnectAgent:

    def __init__(self, config: dict):
        self.config   = config
        self.node_id  = f"mac-{config['mac_id']}"
        self.running  = False

        # Components
        self.wifi_scanner  = WiFiScanner(
            orbi_ssid    = config['orbi_ssid'],
            node_bssids  = config['node_bssids']
        )
        self.device_tracker = DeviceTracker()
        self.ble_scanner    = BLEScanner()
        self.baseline       = BaselineDetector()

        # MQTT
        self.mqtt_client = None
        self.mqtt_connected = False

        # State
        self.last_event     = {}
        self.scan_count     = 0
        self.start_time     = time.time()

    def start(self):
        self.running = True
        print(f"[halo] Starting Halo Connect — node {self.node_id}")
        print(f"[halo] Server: {self.config['mqtt_host']}:{self.config.get('mqtt_port', 1883)}")
        print(f"[halo] Claim code: {self.config['claim_code']}")

        # Start BLE scanner in background
        self.ble_scanner.start()

        # Connect MQTT
        self._connect_mqtt()

        # Main sensing loop
        sensing_thread = threading.Thread(target=self._sensing_loop, daemon=True)
        sensing_thread.start()

        # Device tracking loop
        device_thread = threading.Thread(target=self._device_loop, daemon=True)
        device_thread.start()

    def stop(self):
        self.running = False
        self.ble_scanner.stop()
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()

    def _connect_mqtt(self):
        self.mqtt_client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"halo-connect-{self.node_id}"
        )

        if self.config.get('mqtt_user'):
            self.mqtt_client.username_pw_set(
                self.config['mqtt_user'],
                self.config.get('mqtt_pass', '')
            )

        def on_connect(client, userdata, flags, reason_code, properties):
            if reason_code == 0:
                self.mqtt_connected = True
                print("[mqtt] Connected to Halo server")
                # Publish claim code so server registers this agent
                self._publish_claim()
                # Subscribe to OTA/commands
                client.subscribe(f"halo/sensor/{self.node_id}/ota")
            else:
                print(f"[mqtt] Connection failed: {reason_code}")

        def on_disconnect(client, userdata, flags, reason_code, properties):
            self.mqtt_connected = False
            print(f"[mqtt] Disconnected (will retry): {reason_code}")

        def on_message(client, userdata, msg):
            topic   = msg.topic
            payload = msg.payload.decode()
            if '/ota' in topic:
                print(f"[mqtt] OTA command received (not applicable to Mac agent): {payload}")

        self.mqtt_client.on_connect    = on_connect
        self.mqtt_client.on_disconnect = on_disconnect
        self.mqtt_client.on_message    = on_message

        try:
            self.mqtt_client.connect(
                self.config['mqtt_host'],
                self.config.get('mqtt_port', 1883),
                keepalive=60
            )
            self.mqtt_client.loop_start()
        except Exception as e:
            print(f"[mqtt] Connect error: {e}")

    def _publish_claim(self):
        """Register this agent with the Halo server."""
        payload = json.dumps({
            'node_id':        self.node_id,
            'claimCode':      self.config['claim_code'],
            'firmware':       'connect-1.0.0',
            'sku':            'home',
            'platform':       'mac',
            'mac_model':      'MacBook Pro 2019'
        })
        topic = f"halo/sensor/{self.node_id}/claim"
        self.mqtt_client.publish(topic, payload, qos=1)
        print(f"[halo] Claim published for {self.config['claim_code']}")

    def _sensing_loop(self):
        last_publish = 0

        while self.running:
            try:
                # Scan WiFi — returns RSSI per Orbi node
                node_rssi = self.wifi_scanner.update()

                # Compute variance per node
                variances = {
                    name: self.wifi_scanner.get_variance(name)
                    for name in self.config['node_bssids']
                }

                total_variance = compute_total_variance(variances)

                # Detect presence and motion from variance
                presence     = total_variance > PRESENCE_VARIANCE_THRESHOLD
                motion_active = total_variance > MOTION_VARIANCE_THRESHOLD

                # BLE device count as presence boost
                ble_count    = self.ble_scanner.count()
                mobile_count = self.device_tracker.get_mobile_count()

                # If we see mobile devices, presence is confirmed even if variance is low
                device_presence = (ble_count + mobile_count) > 0

                # Combined presence
                final_presence = presence or device_presence
                person_count   = max(
                    1 if presence else 0,
                    ble_count,
                    mobile_count
                )

                # Zone and floor detection
                # Use fixed zone from config if set (BSSID redacted by wdutil)
                if self.config.get('fixed_zone'):
                    zone       = self.config['fixed_zone']
                    zone_conf  = 1.0
                    floor      = self.config.get('fixed_floor', 'ground')
                    floor_conf = 1.0
                else:
                    zone, zone_conf = detect_zone(node_rssi)
                    floor, floor_conf = detect_floor(node_rssi)

                # Motion state
                if not final_presence:
                    motion_state = 'absent'
                elif motion_active:
                    motion_state = 'active'
                else:
                    motion_state = 'resting'

                # Anomaly detection
                self.baseline.update(total_variance)
                is_anomaly, anomaly_desc, anomaly_severity = self.baseline.is_anomaly(total_variance)

                # Speed estimation from variance rate of change
                motion_speed = 'still'
                if total_variance > 15:
                    motion_speed = 'fast'
                elif total_variance > MOTION_VARIANCE_THRESHOLD:
                    motion_speed = 'normal'
                elif total_variance > PRESENCE_VARIANCE_THRESHOLD:
                    motion_speed = 'slow'

                # Build event
                event = {
                    'node_id':       self.node_id,
                    'ts':            int(time.time() * 1000),
                    'presence':      final_presence,
                    'person_count':  person_count,
                    'motion_state':  motion_state,
                    'motion_speed':  motion_speed,
                    'fall_detected': False,
                    'distress':      False,
                    'signal_strength': max((v for v in node_rssi.values() if v is not None), default=-100),
                    'firmware':      'connect-1.0.0',
                    'mode':          'connect',

                    # Extended data (Mac agent extras)
                    'zone':          zone,
                    'zone_confidence': round(zone_conf, 2),
                    'floor':         floor,
                    'floor_confidence': round(floor_conf, 2),
                    'variance':      round(total_variance, 2),
                    'node_rssi':     {k: v for k, v in node_rssi.items() if v is not None},
                    'ble_devices':   ble_count,
                    'mobile_devices': mobile_count,
                    'total_devices': self.device_tracker.get_all_count(),
                    'anomaly':       is_anomaly,
                    'anomaly_desc':  anomaly_desc if is_anomaly else None,
                    'anomaly_severity': round(anomaly_severity, 2) if is_anomaly else 0,

                    # Named device list
                    'devices_list': [
                        {
                            'name': d['name'],
                            'mac':  d['mac'],
                            'type': 'mobile' if d['is_mobile'] else 'fixed',
                            'ip':   d.get('ip', '')
                        }
                        for d in self.device_tracker.get_present_devices()
                        if d['name'] not in ('?', 'mdns.mcast.net')
                    ],
                    'ble_list': [
                        {'name': d['name'], 'address': d['address']}
                        for d in self.ble_scanner.get_present()
                        if d['name'] != 'Unknown BLE'
                    ],
                }

                self.last_event = event
                self.scan_count += 1

                # Print status every 10 scans
                if self.scan_count % 5 == 0:
                    status = "PRESENT" if final_presence else "CLEAR"
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] {status} | "
                          f"Zone: {zone} ({floor}) | "
                          f"Variance: {total_variance:.1f} | "
                          f"Devices: {ble_count}BLE + {mobile_count}WiFi | "
                          f"{'⚠ ANOMALY' if is_anomaly else 'Normal'}")

                # Publish to MQTT at publish interval
                now = time.time()
                if now - last_publish >= PUBLISH_INTERVAL_SEC and self.mqtt_connected:
                    topic = f"halo/sensor/{self.node_id}/event"
                    self.mqtt_client.publish(topic, json.dumps(event), qos=0)
                    last_publish = now

                    # Also send heartbeat
                    hb_topic = f"halo/sensor/{self.node_id}/heartbeat"
                    self.mqtt_client.publish(hb_topic, json.dumps({
                        'node_id': self.node_id,
                        'ts':      int(time.time() * 1000)
                    }), qos=0)

            except Exception as e:
                print(f"[sensing] Error: {e}")

            time.sleep(SCAN_INTERVAL_SEC)

    def _device_loop(self):
        """Background ARP scan for connected devices."""
        while self.running:
            try:
                self.device_tracker.update_from_arp()
                aps = self.wifi_scanner.get_all_device_bssids()
                self.device_tracker.update_from_wifi_scan(aps)
            except Exception as e:
                print(f"[devices] Error: {e}")
            time.sleep(30)  # ARP scan every 30s

    def get_status(self) -> dict:
        """Current status for menubar display."""
        evt = self.last_event
        return {
            'connected':  self.mqtt_connected,
            'presence':   evt.get('presence', False),
            'zone':       evt.get('zone', 'unknown'),
            'floor':      evt.get('floor', 'unknown'),
            'devices':    evt.get('total_devices', 0),
            'anomaly':    evt.get('anomaly', False),
            'uptime_min': int((time.time() - self.start_time) / 60),
            'scans':      self.scan_count,
        }
