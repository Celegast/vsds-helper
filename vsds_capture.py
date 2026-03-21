"""
VSDS Capture — main entry point
================================
Press F9 (configurable) while the nav panel is open in Elite Dangerous
with the FIRST entry selected (list scrolled to the top).

The tool will:
  1. OCR the initial visible window
  2. Auto-scroll through the entire list using keypresses
  3. Count every entry (exact total, not an estimate)
  4. Read the distance to the furthest system from the last entry
  5. Read your galactic position from the journal
  6. Prompt you to confirm / correct the parsed values
  7. Append one complete row to output/vsds_scans.csv
  8. Append one paste-ready row to output/vsds_paste.tsv

Press ESC to quit and see the session summary.
"""

import csv
import math
import os
import threading
import time
import winsound

import keyboard

import config
from journal_reader import get_current_position
from nav_panel_ocr import NavPanelOCR

# ─── Output file paths ────────────────────────────────────────────────────────

# Single-table TSV — one row per scan, columns match the VSDS spreadsheet exactly.
# Spreadsheet layout:
#   System | Z Sample | System Count | Corrected n | Max Distance | Rho | X | Z | Y
# Coordinate mapping:  spreadsheet X = ED x
#                      spreadsheet Z = ED y  (galactic height)
#                      spreadsheet Y = ED z
# Formula columns (Z Sample, Corrected n, Rho) are pre-calculated here so the
# whole table can be pasted in one go starting at column A.
PASTE_FILE = 'vsds_paste.tsv'

# ─── Paste-table helpers ───────────────────────────────────────────────────────

# Defined scan-step sequences.
_LOG_STEPS    = [-250, -200, -150, -100, -80, -70, -60, -50, -40, -30, -20, -10,
                    0,   10,   20,   30,  40,  50,  60,  70,  80, 100, 150, 200, 250]
_LINEAR_STEPS = list(range(0, 1001, 50))


def _z_sample(y: float) -> int:
    """Snap galactic height y to the nearest defined scan step.

    Auto-detects scan type:
      Y <= 250  → logarithmic scan  (snap to log steps)
      Y >  250  → linear 0–1000 scan (snap to linear steps; higher step wins ties
                  because 250 appears in both lists and we want 300 for y=275)
    """
    if y > _LOG_STEPS[-1]:   # > 250: beyond log range, must be linear
        return min(_LINEAR_STEPS, key=lambda s: (abs(s - y), -s))
    return min(_LOG_STEPS, key=lambda s: abs(s - y))


def _rho(corrected_n: int, max_dist) -> str:
    """
    Stellar density:  count / sphere_volume
      corrected_n == 50  →  sphere bounded by max_dist  (list at capacity)
      corrected_n  < 50  →  fixed 20 ly sphere
      corrected_n  > 50  →  sphere bounded by max_dist
    Returns a formatted string, or '' if inputs are invalid.
    """
    if max_dist == '' or max_dist is None:
        return ''
    try:
        md = float(max_dist)
        n  = int(corrected_n)
    except (TypeError, ValueError):
        return ''
    vol_20 = (4 * math.pi / 3) * 20.0 ** 3
    if n < 50:
        return f"{n / vol_20:.6g}"
    else:   # n == 50 or n > 50
        vol = (4 * math.pi / 3) * md ** 3
        return f"{n / vol:.6g}"


PASTE_HEADER = ['System', 'Z Sample', 'System Count', 'Corrected n',
                'Max Distance', 'Rho', 'X', 'Z', 'Y']

# ─── CSV helpers ──────────────────────────────────────────────────────────────

SCAN_HEADER = [
    'capture_time', 'journal_timestamp',
    'star_system', 'x', 'y', 'z',
    'visible_count', 'total_count', 'system_names',
    'max_distance_ly',
    'sb_bright_sum', 'sb_peak_val', 'sb_peak_row',
]

SAMPLE_HEADER = [
    'screenshot', 'true_total', 'visible_count',
    'bright_sum', 'peak_val', 'peak_row',
    'track_start', 'track_end',
]


def _init_csv(path: str, header: list):
    if not os.path.exists(path):
        with open(path, 'w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(header)


def _append_csv(path: str, header: list, row: dict):
    with open(path, 'a', newline='', encoding='utf-8') as f:
        csv.DictWriter(f, fieldnames=header).writerow(row)


def _init_paste_tsv(path: str):
    if not os.path.exists(path):
        open(path, 'w').close()   # create empty file


def _rewrite_paste_tsv(path: str, scans: list):
    """Rewrite the paste file as a single table sorted by galactic height."""
    ordered = sorted(scans, key=lambda s: s['y'])
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\t'.join(PASTE_HEADER) + '\n')
        for s in ordered:
            corrected_n = s['total_count'] + 1
            md = s['max_distance_ly'] if s['max_distance_ly'] != '' else ''
            row = [
                s['star_system'],
                _z_sample(s['y']),
                s['total_count'],
                corrected_n,
                md,
                _rho(corrected_n, md),
                s['x'],
                s['y'],   # spreadsheet Z = ED y (galactic height)
                s['z'],   # spreadsheet Y = ED z
            ]
            f.write('\t'.join(str(v) for v in row) + '\n')


# ─── Audio ────────────────────────────────────────────────────────────────────

def _beep_ok():
    try:
        winsound.Beep(1000, 200)
    except Exception:
        print('\a')


def _beep_done():
    try:
        winsound.Beep(1000, 150)
        time.sleep(0.05)
        winsound.Beep(1400, 200)
    except Exception:
        print('\a')


def _beep_confirm():
    """Three short descending tones: scan done, action required."""
    try:
        winsound.Beep(1200, 120)
        time.sleep(0.04)
        winsound.Beep(1000, 120)
        time.sleep(0.04)
        winsound.Beep(800, 180)
    except Exception:
        print('\a')


def _beep_err():
    try:
        winsound.Beep(400, 400)
    except Exception:
        print('\a\a')


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("  ELITE DANGEROUS — VSDS HELPER")
    print("  Vertical Stellar Density Scanner")
    print("=" * 70)
    print()
    print(f"  {config.CAPTURE_HOTKEY.upper()} = full scan  (auto-scrolls the nav panel)")
    print(f"  {config.QUIT_HOTKEY.upper()} = quit")
    print()
    print(f"  Output  : {os.path.join(config.OUTPUT_DIR, config.OUTPUT_FILE)}")
    print(f"  Paste   : {os.path.join(config.OUTPUT_DIR, PASTE_FILE)}")
    print()
    print("  Before pressing F9:")
    print("  1. Open the nav panel (key 1), filter to star systems only")
    print("  2. Make sure the FIRST entry is selected (list at the top)")
    print("  3. Alt-Tab back to this window to confirm each scan result")
    print()
    print("  The tool will auto-scroll through the list, count every entry,")
    print("  and record the distance to the furthest system.")
    print()
    print("=" * 70)
    print(f"  Ready — press {config.CAPTURE_HOTKEY.upper()} ...")
    print("=" * 70)

    os.makedirs(config.OUTPUT_DIR,    exist_ok=True)
    os.makedirs(config.DEBUG_DIR,     exist_ok=True)
    os.makedirs(config.SCREENSHOTS_DIR, exist_ok=True)

    # Clear screenshots and debug images from previous sessions.
    cleared = 0
    for folder in (config.SCREENSHOTS_DIR, config.DEBUG_DIR):
        for fname in os.listdir(folder):
            fpath = os.path.join(folder, fname)
            if os.path.isfile(fpath):
                os.remove(fpath)
                cleared += 1
    if cleared:
        print(f"  Cleared {cleared} file(s) from screenshots/ and debug/.")
        print()

    scan_path   = os.path.join(config.OUTPUT_DIR, config.OUTPUT_FILE)
    paste_path  = os.path.join(config.OUTPUT_DIR, PASTE_FILE)
    sample_path = config.SCROLLBAR_SAMPLES_FILE
    _init_csv(scan_path,   SCAN_HEADER)
    _init_csv(sample_path, SAMPLE_HEADER)
    _init_paste_tsv(paste_path)

    ocr        = NavPanelOCR()
    print(f"  Screen  : {ocr.screen_w}×{ocr.screen_h}"
          f"  (scale {ocr.screen_h / config.EXPECTED_SCREEN_HEIGHT:.3f}×)")
    print()
    scans      = []
    idx        = 0
    _scan_lock = threading.Lock()   # prevents F9 re-entry during confirmation

    def on_capture():
        nonlocal idx

        if not _scan_lock.acquire(blocking=False):
            print("\n  [!] Still confirming previous scan — finish that first.")
            return

        try:
            idx   += 1
            prefix = f"scan_{idx:03d}"
            ts     = time.strftime('%H:%M:%S')
            print(f"\n[{ts}] Scan #{idx} — scrolling through list...")
            _beep_ok()

            # 1. Galactic position from journal
            pos = get_current_position(config.JOURNAL_DIR)
            if pos is None:
                print("  [!] Cannot read position from journal. Is ED running?")
                _beep_err()
                return

            # 2. Full auto-scroll scan
            result = ocr.scan_with_scroll(debug_prefix=prefix)
            sb     = result['scrollbar']

            # 3. Build scan row (not yet written — confirmed below)
            scan_row = {
                'capture_time':      time.strftime('%Y-%m-%dT%H:%M:%S'),
                'journal_timestamp': pos['timestamp'],
                'star_system':       pos['StarSystem'],
                'x':                 round(pos['x'], 5),
                'y':                 round(pos['y'], 5),
                'z':                 round(pos['z'], 5),
                'visible_count':     result['visible_count'],
                'total_count':       result['total_count'],
                'system_names':      '|'.join(result['system_names']),
                'max_distance_ly':   result['max_distance_ly'] if result['max_distance_ly'] is not None else '',
                'sb_bright_sum':     round(sb['bright_sum'], 2),
                'sb_peak_val':       round(sb['peak_val'], 2),
                'sb_peak_row':       sb['peak_row'],
            }

            # 4. Print parsed results
            print(f"  System  : {pos['StarSystem']}")
            print(f"  Coords  : x={pos['x']:.2f}  y={pos['y']:.2f}  z={pos['z']:.2f} ly")
            print(f"  Visible : {result['visible_count']}   Total: {result['total_count']}")
            md_display = f"{result['max_distance_ly']} ly" if result['max_distance_ly'] is not None else "(not found)"
            print(f"  Max dist: {md_display}")
            if result['system_names']:
                sample = result['system_names'][:3]
                more   = f" (+{len(result['system_names'])-3} more)" if len(result['system_names']) > 3 else ""
                print(f"  Names   : {', '.join(sample)}{more}")

            # 5. Confirm / correct key values before saving
            _beep_confirm()
            print()
            print("  [?] Confirm — press Enter to accept, or type a correction:")
            for key, label, cast in [
                ('total_count',     'System count', int),
                ('max_distance_ly', 'Max dist (ly)', float),
            ]:
                current = scan_row[key]
                disp    = str(current) if current != '' else '(empty)'
                try:
                    raw = input(f"      {label} [{disp}]: ").strip()
                except EOFError:
                    raw = ''
                if raw:
                    try:
                        scan_row[key] = cast(raw.replace(',', '.'))
                    except ValueError:
                        print(f"      (invalid — keeping {disp})")

            # 6. Write to disk after confirmation
            _append_csv(scan_path, SCAN_HEADER, scan_row)
            scans.append(scan_row)
            _rewrite_paste_tsv(paste_path, scans)

            # 7. Scrollbar sample (true_total now confirmed)
            sample_row = {
                'screenshot':    prefix,
                'true_total':    scan_row['total_count'],
                'visible_count': result['visible_count'],
                'bright_sum':    round(sb['bright_sum'], 2),
                'peak_val':      round(sb['peak_val'], 2),
                'peak_row':      sb['peak_row'],
                'track_start':   sb['track_start'],
                'track_end':     sb['track_end'],
            }
            _append_csv(sample_path, SAMPLE_HEADER, sample_row)

            print(f"  [OK] Scan #{idx} saved.")
            _beep_done()

        except Exception as e:
            print(f"  [X] Error: {e}")
            import traceback; traceback.print_exc()
            _beep_err()

        finally:
            _scan_lock.release()

    keyboard.add_hotkey(config.CAPTURE_HOTKEY, on_capture)
    try:
        keyboard.wait(config.QUIT_HOTKEY)
    except KeyboardInterrupt:
        pass

    # ── Session summary ───────────────────────────────────────────────────────

    print("\n" + "=" * 70)
    print(f"  SESSION COMPLETE — {len(scans)} scan(s) recorded")
    print("=" * 70)
    if scans:
        print()
        print(f"  {'Y (ly)':>10}  {'Vis':>4}  {'Total':>6}  {'MaxDist':>8}  {'System'}")
        print(f"  {'-'*10}  {'-'*4}  {'-'*6}  {'-'*8}  {'-'*30}")
        for s in scans:
            md = f"{s['max_distance_ly']} ly" if s['max_distance_ly'] else "  --"
            print(f"  {s['y']:>10.2f}  {s['visible_count']:>4}  "
                  f"{s['total_count']:>6}  {md:>8}  {s['star_system']}")
        print()
        print(f"  Full results : {scan_path}")
        print(f"  Excel paste  : {paste_path}")

    print("\n  o7  Fly safe, Commander.")


if __name__ == '__main__':
    main()
