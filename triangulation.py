"""
Triangulation and zone detection from three Orbi node RSSI values.

Ben's home layout:
  Node 1 — upstairs, top-left wing
  Node 2 — upstairs, center
  Node 3 — downstairs, right

Normalized coordinates (0-1 on each axis):
  x: left=0, right=1
  y: front=0, back=1
  floor: ground=0, upper=1
"""
import math
from typing import Dict, Optional, Tuple

# Node positions in normalized 3D space
# Based on Ben's floor plan drawing
NODE_POSITIONS = {
    'node_1_up_left':   (0.15, 0.80, 1.0),   # upstairs left wing
    'node_2_up_center': (0.45, 0.55, 1.0),   # upstairs center
    'node_3_down_right':(0.80, 0.30, 0.0),   # downstairs right
}

# Zone definitions — which node dominates in each zone
ZONES = [
    # (name, floor, dominant_node, secondary_node, description)
    ('upstairs_left',    'upper',  'node_1_up_left',    'node_2_up_center', 'Upstairs left wing'),
    ('upstairs_center',  'upper',  'node_2_up_center',  'node_1_up_left',   'Upstairs center'),
    ('upstairs_right',   'upper',  'node_2_up_center',  'node_3_down_right','Upstairs right area'),
    ('downstairs_right', 'ground', 'node_3_down_right', 'node_2_up_center', 'Downstairs right'),
    ('downstairs_left',  'ground', None,                None,               'Downstairs left (weak coverage)'),
]

# Path loss exponent for indoor WiFi (2=free space, 3=typical home, 4=many walls)
PATH_LOSS_N = 2.8
# Reference RSSI at 1 meter (typical for home router)
RSSI_REF    = -40


def rssi_to_distance(rssi: int) -> float:
    """Estimate distance in meters from RSSI value."""
    if rssi >= RSSI_REF:
        return 0.5
    d = 10 ** ((RSSI_REF - rssi) / (10 * PATH_LOSS_N))
    return min(d, 50.0)  # cap at 50m


def detect_floor(node_rssi: Dict[str, Optional[int]]) -> Tuple[str, float]:
    """
    Determine which floor the activity is on.
    Returns (floor_name, confidence).
    """
    up_signals   = []
    down_signals = []

    for name, rssi in node_rssi.items():
        if rssi is None:
            continue
        pos = NODE_POSITIONS.get(name)
        if not pos:
            continue
        if pos[2] > 0.5:   # upstairs
            up_signals.append(rssi)
        else:               # downstairs
            down_signals.append(rssi)

    if not up_signals and not down_signals:
        return ('unknown', 0.0)

    avg_up   = sum(up_signals)   / len(up_signals)   if up_signals   else -100
    avg_down = sum(down_signals) / len(down_signals) if down_signals else -100

    # Floor with stronger average signal is more likely
    diff = abs(avg_up - avg_down)
    confidence = min(diff / 20.0, 1.0)  # 20dB difference = high confidence

    if avg_up > avg_down:
        return ('upper', confidence)
    else:
        return ('ground', confidence)


def detect_zone(node_rssi: Dict[str, Optional[int]]) -> Tuple[str, float]:
    """
    Estimate which zone the activity is in based on relative RSSI.
    Returns (zone_name, confidence).
    """
    # Filter to known nodes with readings
    valid = {k: v for k, v in node_rssi.items() if v is not None and k in NODE_POSITIONS}

    if not valid:
        return ('unknown', 0.0)

    # Find dominant node (strongest signal = closest)
    dominant = max(valid, key=lambda k: valid[k])
    dominant_rssi = valid[dominant]

    # Check floor
    floor, floor_conf = detect_floor(node_rssi)

    # Match zone
    for zone_name, zone_floor, zone_dom, zone_sec, desc in ZONES:
        if zone_dom is None:
            # Dead zone — all signals weak
            all_weak = all(v < -75 for v in valid.values())
            if all_weak and floor == 'ground':
                return (zone_name, 0.4)
            continue

        if dominant == zone_dom:
            # Verify floor matches
            node_floor = 'upper' if NODE_POSITIONS[zone_dom][2] > 0.5 else 'ground'
            if node_floor == floor or floor == 'unknown':
                # Confidence based on signal strength margin over second node
                if zone_sec and zone_sec in valid:
                    margin    = dominant_rssi - valid[zone_sec]
                    confidence = min(margin / 15.0, 1.0)
                else:
                    confidence = 0.6
                return (zone_name, max(0.3, confidence))

    return ('unknown', 0.3)


def estimate_position(node_rssi: Dict[str, Optional[int]]) -> Optional[Tuple[float, float, float]]:
    """
    Trilaterate approximate (x, y, floor) position from 3 node RSSI values.
    Returns None if insufficient data.
    """
    valid = {k: v for k, v in node_rssi.items() if v is not None and k in NODE_POSITIONS}
    if len(valid) < 2:
        return None

    # Convert RSSI to distances
    distances = {k: rssi_to_distance(v) for k, v in valid.items()}

    # Weighted centroid (inverse distance weighting)
    total_weight = 0.0
    wx, wy, wz   = 0.0, 0.0, 0.0

    for node_name, dist in distances.items():
        pos    = NODE_POSITIONS[node_name]
        weight = 1.0 / (dist + 0.1)  # avoid division by zero
        wx    += pos[0] * weight
        wy    += pos[1] * weight
        wz    += pos[2] * weight
        total_weight += weight

    if total_weight == 0:
        return None

    return (wx / total_weight, wy / total_weight, wz / total_weight)


def compute_total_variance(variances: Dict[str, float]) -> float:
    """Aggregate variance across all nodes — high = movement detected."""
    vals = [v for v in variances.values() if v > 0]
    return sum(vals) / len(vals) if vals else 0.0
