#!/usr/bin/env python3
"""
Halo Connect — First-time setup.
Discovers your Orbi nodes, maps them to positions, saves config.
Run once before starting the agent.
"""
import os
import sys
import json
import uuid
import subprocess
import re
import time
import getpass

CONFIG_PATH   = os.path.expanduser("~/.halo-connect/config.json")
AIRPORT       = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"
DEFAULT_SERVER = "halo-dashboard-production-0191.up.railway.app"


def clear(): print("\033[2J\033[H", end="")

def header():
    print("\033[32m")
    print("  ██╗  ██╗ █████╗ ██╗      ██████╗ ")
    print("  ██║  ██║██╔══██╗██║     ██╔═══██╗")
    print("  ███████║███████║██║     ██║   ██║")
    print("  ██╔══██║██╔══██║██║     ██║   ██║")
    print("  ██║  ██║██║  ██║███████╗╚██████╔╝")
    print("  ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝ ╚═════╝ CONNECT")
    print("\033[0m")
    print("  Mac Agent Setup\n")


def scan_wifi() -> list:
    """Scan for all nearby WiFi networks."""
    print("  Scanning for WiFi networks...")
    try:
        result = subprocess.run([AIRPORT, "-s"],
                                capture_output=True, text=True, timeout=15)
        networks = []
        for line in result.stdout.strip().split('\n')[1:]:
            bssid_match = re.search(r'([0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2})', line, re.I)
            rssi_match  = re.search(r'\s(-\d+)\s', line)
            if bssid_match and rssi_match:
                bssid = bssid_match.group(1).lower()
                rssi  = int(rssi_match.group(1))
                ssid  = line[:bssid_match.start()].strip()
                networks.append({'ssid': ssid, 'bssid': bssid, 'rssi': rssi})
        return networks
    except Exception as e:
        print(f"  Scan failed: {e}")
        return []


def find_orbi_nodes(networks: list, ssid: str) -> list:
    """Find all BSSIDs belonging to the Orbi network (same SSID, different BSSIDs = different nodes)."""
    orbi_nets = [n for n in networks if n['ssid'].lower() == ssid.lower()]
    # Deduplicate by first 8 chars of BSSID (same physical node, different bands)
    seen_prefixes = set()
    unique_nodes  = []
    for n in sorted(orbi_nets, key=lambda x: x['rssi'], reverse=True):
        prefix = n['bssid'][:8]
        if prefix not in seen_prefixes:
            seen_prefixes.add(prefix)
            unique_nodes.append(n)
    return unique_nodes


def get_mac_id() -> str:
    """Get a stable unique ID for this Mac."""
    try:
        result = subprocess.run(
            ['system_profiler', 'SPHardwareDataType'],
            capture_output=True, text=True
        )
        match = re.search(r'Serial Number.*?:\s*(\S+)', result.stdout)
        if match:
            return match.group(1).replace('-', '')[:12]
    except Exception:
        pass
    return uuid.uuid4().hex[:12]


def main():
    clear()
    header()

    # Check if already configured
    if os.path.exists(CONFIG_PATH):
        print("  Existing config found.")
        choice = input("  Reconfigure? (y/N): ").strip().lower()
        if choice != 'y':
            print("  Setup cancelled.")
            sys.exit(0)

    print("  Let's connect Halo to your home WiFi mesh.\n")

    # ── Step 1: WiFi network name ──────────────────────────────────────────
    networks = scan_wifi()

    if networks:
        # Show unique SSIDs
        ssids = list(dict.fromkeys(n['ssid'] for n in networks if n['ssid']))
        print(f"  Found {len(ssids)} networks nearby:\n")
        for i, s in enumerate(ssids[:10], 1):
            print(f"    {i}. {s}")
        print()

        choice = input("  Enter network name (or number): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(ssids):
            orbi_ssid = ssids[int(choice) - 1]
        else:
            orbi_ssid = choice
    else:
        orbi_ssid = input("  Enter your WiFi network name (SSID): ").strip()

    print(f"\n  Network: {orbi_ssid}")

    # ── Step 2: Find Orbi nodes ────────────────────────────────────────────
    print("\n  Looking for your Orbi nodes...")
    orbi_nodes = find_orbi_nodes(networks, orbi_ssid)

    if len(orbi_nodes) < 1:
        print(f"  No nodes found for '{orbi_ssid}'. Make sure you're connected to it.")
        print("  You can enter BSSIDs manually.")
        orbi_nodes = []

    # Map nodes to positions
    node_bssids = {}
    node_names  = ['node_1_up_left', 'node_2_up_center', 'node_3_down_right']
    node_labels = [
        'Node 1 — Upstairs Left Wing',
        'Node 2 — Upstairs Center',
        'Node 3 — Downstairs Right',
    ]

    print(f"\n  Found {len(orbi_nodes)} Orbi node(s). Mapping to your home layout...\n")
    print("  Your layout (from your floor plan):")
    print("    Node 1: Upstairs, left wing")
    print("    Node 2: Upstairs, center")
    print("    Node 3: Downstairs, right\n")

    for i, (name, label) in enumerate(zip(node_names, node_labels)):
        if i < len(orbi_nodes):
            node = orbi_nodes[i]
            print(f"  {label}")
            print(f"    Auto-detected BSSID: {node['bssid']} (RSSI: {node['rssi']}dBm)")
            confirm = input(f"    Use this? (Y/n): ").strip().lower()
            if confirm == 'n':
                bssid = input(f"    Enter BSSID manually (xx:xx:xx:xx:xx:xx): ").strip()
            else:
                bssid = node['bssid']
        else:
            print(f"  {label}")
            bssid = input(f"    Enter BSSID (xx:xx:xx:xx:xx:xx) or skip (Enter): ").strip()
            if not bssid:
                continue

        # Store just the prefix (first 8 chars) to match across bands
        node_bssids[name] = bssid[:8]
        print(f"    ✓ Mapped\n")

    # ── Step 3: Server config ──────────────────────────────────────────────
    print(f"\n  Halo server configuration:")
    server = input(f"  Server host [{DEFAULT_SERVER}]: ").strip()
    if not server:
        server = DEFAULT_SERVER

    mqtt_user = input(f"  MQTT username [halo]: ").strip() or 'halo'
    mqtt_pass = getpass.getpass(f"  MQTT password: ")

    # ── Step 4: Claim code ─────────────────────────────────────────────────
    print(f"\n  Claim code:")
    print(f"  Go to your Halo dashboard → Sensors → Add Sensor")
    print(f"  Copy the claim code shown (format: HC-XXXXXXXX)\n")
    claim_code = input("  Claim code: ").strip().upper()

    # ── Step 5: Save config ────────────────────────────────────────────────
    config = {
        'mac_id':         get_mac_id(),
        'orbi_ssid':      orbi_ssid,
        'node_bssids':    node_bssids,
        'mqtt_host':      server,
        'mqtt_port':      1883,
        'mqtt_user':      mqtt_user,
        'mqtt_pass':      mqtt_pass,
        'claim_code':     claim_code,
        'dashboard_host': server,
    }

    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)

    # ── Done ───────────────────────────────────────────────────────────────
    print("\n" + "─" * 50)
    print("  ✓ Setup complete!\n")
    print(f"  Network:    {orbi_ssid}")
    print(f"  Nodes:      {len(node_bssids)} mapped")
    print(f"  Server:     {server}")
    print(f"  Claim code: {claim_code}")
    print()
    print("  To start the agent:")
    print("    python3 run.py")
    print()
    print("  Your Mac will appear in the Halo dashboard")
    print("  within 60 seconds of starting.\n")
    print("─" * 50)


if __name__ == '__main__':
    main()
