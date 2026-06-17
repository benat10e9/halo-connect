# Halo Connect

Turn your Mac into a Halo sensor node. Uses your MacBook's WiFi card to
read signals from your Orbi mesh and detect presence, motion, and device
activity across your home.

## Install

```bash
./install.sh
```

## Setup (run once)

```bash
python3 setup.py
```

Discovers your Orbi nodes, maps them to your floor plan, connects to your
Halo dashboard. Takes about 2 minutes.

## Run

```bash
python3 run.py           # menubar app
python3 run.py --headless  # terminal only
```

## What it detects

- **Presence** — someone in the home, from RSSI variance across all 3 Orbi nodes
- **Motion state** — active, resting, or absent
- **Motion speed** — still, slow, normal, fast
- **Zone** — which area of your home (upstairs left, downstairs right, etc.)
- **Floor** — ground floor vs upper floor
- **Device count** — phones, laptops, AirPods visible via WiFi and BLE
- **Anomaly** — unusual activity for this time of day vs learned baseline

## What it does NOT detect (needs hardware node)

- Breathing rate
- Heart rate
- Fall detection

These require raw CSI data which macOS doesn't expose. Add a Halo Home
node to any room for full vital sign monitoring.

## Your home layout

3 Orbi 770 nodes mapped to your floor plan:
- Node 1: Upstairs left wing
- Node 2: Upstairs center  
- Node 3: Downstairs right
