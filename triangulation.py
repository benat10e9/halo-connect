"""
Triangulation and zone detection from three Orbi node RSSI values.

Ben's Orbi 770 layout (8,500 sqft, two floors):
  Node 1 — 94:18:65:F6:FF:4F — upstairs, far end (left wing)
  Node 2 — 94:18:65:F7:3A:5F — upstairs, center
  Node 3 — 94:18:65:F7:EC:26 — downstairs, right (first floor)

Normalized coordinates:
  x: left=0, right=1
  y: front=0, back=1
  floor: ground=0, upper=1
"""
import math
import statistics
from typing import Dict, Optional, Tuple

NODE_POSITIONS = {
    'node_1_up_left':    (0.10, 0.85, 1.0),   # upstairs far end/left wing
    'node_2_up_center':  (0.45, 0.55, 1.0),   # upstairs center
    'node_3_down_right': (0.80, 0.30, 0.0),   # downstairs right/first floor
}

# Human-readable zone names keyed to floor plan
ZONES = [
    ('upstairs_far_end',  'upper',  'node_1_up_left',    'node_2_up_center',  'Upstairs far end'),
    ('upstairs_center',   'upper',  'node_2_up_center',  'node_1_up_left',    'Upstairs center'),
    ('upstairs_overlap',  'upper',  'node_2_up_center',  'node_3_down_right', 'Upstairs right side'),
    ('downstairs',        'ground', 'node_3_down_right', 'node_2_up_center',  'Downstairs'),
    ('dead_zone',         'ground', None,                None,                'Downstairs far left (weak)'),
]

PATH_LOSS_N = 2.8
RSSI_REF    = -40


def rssi_to_distance(rssi: int) -> float:
    if rssi >= RSSI_REF:
        return 0.5
    d = 10 ** ((RSSI_REF - rssi) / (10 * PATH_LOSS_N))
    return min(d, 50.0)


def detect_floor(node_rssi: Dict[str, Optional[int]]) -> Tuple[str, float]:
    up_signals, down_signals = [], []

    for name, rssi in node_rssi.items():
        if rssi is None:
            continue
        pos = NODE_POSITIONS.get(name)
        if not pos:
            continue
        (up_signals if pos[2] > 0.5 else down_signals).append(rssi)

    if not up_signals and not down_signals:
        return ('unknown', 0.0)

    avg_up   = sum(up_signals)   / len(up_signals)   if up_signals   else -100
    avg_down = sum(down_signals) / len(down_signals) if down_signals else -100

    diff       = abs(avg_up - avg_down)
    confidence = min(diff / 20.0, 1.0)

    return ('upper', confidence) if avg_up > avg_down else ('ground', confidence)


def detect_zone(node_rssi: Dict[str, Optional[int]]) -> Tuple[str, float]:
    valid = {k: v for k, v in node_rssi.items() if v is not None and k in NODE_POSITIONS}
    if not valid:
        return ('unknown', 0.0)

    dominant      = max(valid, key=lambda k: valid[k])
    dominant_rssi = valid[dominant]
    floor, _      = detect_floor(node_rssi)

    for zone_name, zone_floor, zone_dom, zone_sec, desc in ZONES:
        if zone_dom is None:
            if all(v < -75 for v in valid.values()) and floor == 'ground':
                return (zone_name, 0.4)
            continue
        if dominant == zone_dom:
            node_floor = 'upper' if NODE_POSITIONS[zone_dom][2] > 0.5 else 'ground'
            if node_floor == floor or floor == 'unknown':
                if zone_sec and zone_sec in valid:
                    margin     = dominant_rssi - valid[zone_sec]
                    confidence = min(margin / 15.0, 1.0)
                else:
                    confidence = 0.6
                return (zone_name, max(0.3, confidence))

    return ('unknown', 0.3)


def estimate_position(node_rssi: Dict[str, Optional[int]]) -> Optional[Tuple[float, float, float]]:
    valid = {k: v for k, v in node_rssi.items() if v is not None and k in NODE_POSITIONS}
    if len(valid) < 2:
        return None

    distances    = {k: rssi_to_distance(v) for k, v in valid.items()}
    total_weight = 0.0
    wx = wy = wz = 0.0

    for node_name, dist in distances.items():
        pos    = NODE_POSITIONS[node_name]
        weight = 1.0 / (dist + 0.1)
        wx += pos[0] * weight
        wy += pos[1] * weight
        wz += pos[2] * weight
        total_weight += weight

    if not total_weight:
        return None
    return (wx / total_weight, wy / total_weight, wz / total_weight)


def compute_total_variance(variances: Dict[str, float]) -> float:
    vals = [v for v in variances.values() if v > 0]
    return sum(vals) / len(vals) if vals else 0.0
