#!/usr/bin/env python3
"""
Halo Connect menubar app.
Shows status in menu bar, runs agent in background.
"""
import rumps
import json
import os
import sys
import threading
from agent import HaloConnectAgent

CONFIG_PATH = os.path.expanduser("~/.halo-connect/config.json")


class HaloMenuBar(rumps.App):

    def __init__(self, agent: HaloConnectAgent):
        super().__init__(
            name  = "Halo",
            title = "◉ Halo",
            quit_button = "Quit Halo Connect"
        )
        self.agent = agent

        self.menu = [
            rumps.MenuItem("Status: Starting...", callback=None),
            rumps.separator,
            rumps.MenuItem("Zone: —",            callback=None),
            rumps.MenuItem("Floor: —",           callback=None),
            rumps.MenuItem("Devices nearby: —",  callback=None),
            rumps.separator,
            rumps.MenuItem("Anomaly: None",      callback=None),
            rumps.separator,
            rumps.MenuItem("Dashboard →",        callback=self.open_dashboard),
            rumps.MenuItem("Reset / Reprovision", callback=self.reset),
        ]

        # Update timer
        self._update_timer = rumps.Timer(self._update_status, 5)
        self._update_timer.start()

    def _update_status(self, _):
        status = self.agent.get_status()

        # Menubar icon
        if not status['connected']:
            self.title = "○ Halo"
        elif status['anomaly']:
            self.title = "⚠ Halo"
        elif status['presence']:
            self.title = "● Halo"
        else:
            self.title = "◌ Halo"

        # Menu items
        conn   = "Connected" if status['connected'] else "Connecting..."
        pres   = "PRESENT" if status['presence'] else "Clear"
        uptime = f"{status['uptime_min']}m"

        self.menu["Status: Starting..."].title = f"Status: {conn} | {pres} | {uptime}"
        self.menu["Zone: —"].title   = f"Zone: {status['zone'].replace('_',' ').title()}"
        self.menu["Floor: —"].title  = f"Floor: {status['floor'].title()}"
        self.menu["Devices nearby: —"].title = f"Devices nearby: {status['devices']}"

        if status['anomaly']:
            self.menu["Anomaly: None"].title = "⚠ Anomaly detected"
        else:
            self.menu["Anomaly: None"].title = "Anomaly: None"

    def open_dashboard(self, _):
        import subprocess
        cfg = load_config()
        url = f"https://{cfg.get('dashboard_host', 'halo-dashboard-production-0191.up.railway.app')}"
        subprocess.run(['open', url])

    def reset(self, _):
        if rumps.alert("Reset Halo Connect?",
                       "This will clear your config. You'll need to set up again.",
                       ok="Reset", cancel="Cancel"):
            if os.path.exists(CONFIG_PATH):
                os.remove(CONFIG_PATH)
            rumps.quit_application()


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def run_menubar(agent: HaloConnectAgent):
    app = HaloMenuBar(agent)
    app.run()


if __name__ == '__main__':
    if not os.path.exists(CONFIG_PATH):
        print("No config found. Run: python3 setup.py")
        sys.exit(1)

    cfg   = load_config()
    agent = HaloConnectAgent(cfg)

    # Start agent in background thread
    t = threading.Thread(target=agent.start, daemon=True)
    t.start()

    # Run menubar (blocks main thread)
    run_menubar(agent)
