"""
Elite Dangerous Journal Reader
Extracts the player's current galactic position (StarPos) from the latest journal file.
"""

import glob
import json
import os


def get_latest_journal(journal_dir: str) -> str | None:
    """Return path to the most recently modified journal file."""
    pattern = os.path.join(journal_dir, "Journal.*.log")
    files = glob.glob(pattern)
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def get_current_position(journal_dir: str) -> dict | None:
    """
    Read the latest journal and return the most recent position event.

    Returns a dict with keys:
        event       - 'FSDJump', 'Location', or 'CarrierJump'
        timestamp   - ISO-8601 string (UTC)
        StarSystem  - system name string
        StarPos     - [x, y, z]  (galactic coordinates in ly)
        x, y, z     - individual floats for convenience

    Returns None if no position event is found.
    """
    journal_path = get_latest_journal(journal_dir)
    if journal_path is None:
        return None

    position_events = ('FSDJump', 'Location', 'CarrierJump')
    last_position = None

    with open(journal_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            if event.get('event') in position_events and 'StarPos' in event:
                x, y, z = event['StarPos']
                last_position = {
                    'event':      event['event'],
                    'timestamp':  event.get('timestamp', ''),
                    'StarSystem': event.get('StarSystem', ''),
                    'StarPos':    [x, y, z],
                    'x': x,
                    'y': y,   # galactic height — the key VSDS axis
                    'z': z,
                }

    return last_position


if __name__ == '__main__':
    import config
    pos = get_current_position(config.JOURNAL_DIR)
    if pos:
        print(f"Current system : {pos['StarSystem']}")
        print(f"Galactic coords: x={pos['x']:.3f}  y={pos['y']:.3f}  z={pos['z']:.3f} ly")
        print(f"Galactic height: {pos['y']:.3f} ly  (key VSDS axis)")
        print(f"Last event     : {pos['event']} at {pos['timestamp']}")
    else:
        print("No position found in latest journal.")
