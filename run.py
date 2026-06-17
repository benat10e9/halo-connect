#!/usr/bin/env python3
"""
Halo Connect — start the agent.
Run this after setup.py. Can run headless (no menubar) or as menubar app.
"""
import os
import sys
import json
import threading
import argparse

CONFIG_PATH = os.path.expanduser("~/.halo-connect/config.json")


def load_config():
    if not os.path.exists(CONFIG_PATH):
        print("No config found. Run: python3 setup.py")
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description='Halo Connect Agent')
    parser.add_argument('--headless', action='store_true',
                        help='Run without menubar (terminal only)')
    args = parser.parse_args()

    cfg = load_config()

    from agent import HaloConnectAgent
    agent = HaloConnectAgent(cfg)

    if args.headless or not sys.platform == 'darwin':
        # Run headless — just the sensing loop
        print("Starting Halo Connect in headless mode...")
        print("Press Ctrl+C to stop.")
        try:
            agent.start()
            # Keep main thread alive
            import signal
            signal.signal(signal.SIGINT, lambda s, f: (agent.stop(), sys.exit(0)))
            signal.pause()
        except KeyboardInterrupt:
            agent.stop()
    else:
        # Run with menubar
        t = threading.Thread(target=agent.start, daemon=True)
        t.start()
        from menubar import run_menubar
        run_menubar(agent)


if __name__ == '__main__':
    main()
