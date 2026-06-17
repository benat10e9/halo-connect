"""
Anomaly detection from rolling baseline.

Learns what normal looks like per time-of-day and flags deviations.
No ML required — pure rolling statistics.
"""
import time
import json
import os
import statistics
from collections import defaultdict
from typing import Optional


class BaselineDetector:
    """
    Maintains a rolling baseline of variance per 30-minute time bucket.
    Flags anomalies when current variance deviates significantly from baseline.
    """

    def __init__(self, data_path: str = "~/.halo-connect/baseline.json"):
        self.data_path = os.path.expanduser(data_path)
        # bucket -> list of variance values seen in that time slot
        # bucket = hour * 2 + (minute // 30)  →  48 buckets per day
        self.buckets: dict = defaultdict(list)
        self._load()

    def _bucket_key(self) -> int:
        """Current 30-minute bucket (0-47)."""
        t = time.localtime()
        return t.tm_hour * 2 + (t.tm_min // 30)

    def update(self, variance: float):
        """Add a variance reading to the current bucket."""
        key = self._bucket_key()
        self.buckets[key].append(variance)
        # Keep last 100 readings per bucket
        if len(self.buckets[key]) > 100:
            self.buckets[key] = self.buckets[key][-100:]
        # Persist periodically
        if len(self.buckets[key]) % 20 == 0:
            self._save()

    def is_anomaly(self, variance: float, threshold_sigma: float = 2.5) -> tuple:
        """
        Returns (is_anomaly, description, severity).
        Compares current variance to baseline for this time of day.
        """
        key = self._bucket_key()
        baseline = self.buckets.get(key, [])

        if len(baseline) < 10:
            return (False, "Learning baseline...", 0.0)

        try:
            mean   = statistics.mean(baseline)
            stdev  = statistics.stdev(baseline)
        except Exception:
            return (False, "Insufficient data", 0.0)

        if stdev < 0.1:
            stdev = 0.1  # avoid division by near-zero

        z_score = (variance - mean) / stdev

        if z_score > threshold_sigma:
            severity = min((z_score - threshold_sigma) / threshold_sigma, 1.0)
            t        = time.strftime("%I:%M %p")
            if variance < 1.0:
                desc = f"Unusual stillness at {t} — normally more active"
            else:
                desc = f"Unusual activity at {t} — normally quieter"
            return (True, desc, severity)

        return (False, "Normal", 0.0)

    def get_summary(self) -> dict:
        """Return baseline stats for current time bucket."""
        key      = self._bucket_key()
        baseline = self.buckets.get(key, [])
        if len(baseline) < 2:
            return {'status': 'learning', 'samples': len(baseline)}

        return {
            'status':  'active',
            'samples': len(baseline),
            'mean':    round(statistics.mean(baseline), 2),
            'stdev':   round(statistics.stdev(baseline), 2),
            'bucket':  key,
            'time_window': f"{(key // 2):02d}:{(key % 2) * 30:02d}"
        }

    def _save(self):
        os.makedirs(os.path.dirname(self.data_path), exist_ok=True)
        try:
            with open(self.data_path, 'w') as f:
                json.dump(dict(self.buckets), f)
        except Exception:
            pass

    def _load(self):
        try:
            if os.path.exists(self.data_path):
                with open(self.data_path) as f:
                    data = json.load(f)
                self.buckets = defaultdict(list, {int(k): v for k, v in data.items()})
        except Exception:
            self.buckets = defaultdict(list)
