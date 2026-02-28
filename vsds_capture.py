"""
VSDS Capture — main entry point
================================
Press F9 (configurable) while the nav panel is open in Elite Dangerous.
The tool reads:
  - Visible system count from the nav panel (OCR)
  - Current galactic position from the latest journal file

Press ESC to quit and see the session summary.

Results are appended to output/vsds_scans.csv.
Scrollbar measurements are also saved (output/scrollbar_samples.csv) so that
the scrollbar calibration can be refined later with scrollbar_calibrate.py.
"""

import csv
import json
import os
import time
import winsound

import keyboard

import config
from journal_reader import get_current_position
from nav_panel_ocr import NavPanelOCR

# ─── CSV helpers ──────────────────────────────────────────────────────────────

SCAN_HEADER = [
    'capture_time', 'journal_timestamp',
    'star_system', 'x', 'y', 'z',
    'visible_count', 'system_names',
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


# ─── Audio ────────────────────────────────────────────────────────────────────

def _beep_ok():
    try:
        winsound.Beep(1000, 200)
    except Exception:
        print('\a')


def _beep_err():
    try:
        winsound.Beep(400, 400)
    except Exception:
        print('\a\a')


# ─── Load scrollbar model (if calibrated) ────────────────────────────────────

def _load_scrollbar_model() -> dict | None:
    path = "output/scrollbar_model.json"
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    model = _load_scrollbar_model()

    print("=" * 70)
    print("  ELITE DANGEROUS — VSDS HELPER")
    print("  Vertical Stellar Density Scanner")
    print("=" * 70)
    print()
    print(f"  Hotkey : {config.CAPTURE_HOTKEY.upper()} = capture   "
          f"{config.QUIT_HOTKEY.upper()} = quit")
    print(f"  Output : {os.path.join(config.OUTPUT_DIR, config.OUTPUT_FILE)}")
    if model:
        print(f"  Scrollbar model loaded (RMSE ≈ {model['rmse']:.1f}, "
              f"n={model['n_samples']} samples)")
    else:
        print("  Scrollbar model: NOT calibrated yet — visible count only.")
        print("  Use scrollbar_calibrate.py to build the model.")
    print()
    print("  Instructions:")
    print("  1. Fly to your scan location")
    print("  2. Open the nav panel (key 1), filter to star systems only")
    print(f"  3. Press {config.CAPTURE_HOTKEY.upper()}")
    print()
    print("=" * 70)
    print(f"  Ready — press {config.CAPTURE_HOTKEY.upper()} ...")
    print("=" * 70)

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    os.makedirs(config.DEBUG_DIR, exist_ok=True)
    scan_path   = os.path.join(config.OUTPUT_DIR, config.OUTPUT_FILE)
    sample_path = config.SCROLLBAR_SAMPLES_FILE
    _init_csv(scan_path,   SCAN_HEADER)
    _init_csv(sample_path, SAMPLE_HEADER)

    ocr = NavPanelOCR()
    scans = []
    idx = 0

    def on_capture():
        nonlocal idx
        idx += 1
        prefix = f"scan_{idx:03d}"
        ts = time.strftime('%H:%M:%S')
        print(f"\n[{ts}] Capture #{idx} — processing...")

        try:
            # 1. Journal position
            pos = get_current_position(config.JOURNAL_DIR)
            if pos is None:
                print("  [!] Cannot read position from journal. Is ED running?")
                _beep_err()
                return

            # 2. Screenshot + full scan
            screenshot_path = ocr.take_screenshot()
            result = ocr.scan(screenshot_path, debug_prefix=prefix)
            sb = result['scrollbar']

            # 3. Estimate total using model (if available)
            if model:
                total_est = round(model['A'] * sb['bright_sum'] + model['B'])
                total_str = f"~{total_est} (model)"
            else:
                total_str = f"{result['visible_count']} visible (no model yet)"

            # 4. Build and save scan row
            scan_row = {
                'capture_time':      time.strftime('%Y-%m-%dT%H:%M:%S'),
                'journal_timestamp': pos['timestamp'],
                'star_system':       pos['StarSystem'],
                'x':                 round(pos['x'], 5),
                'y':                 round(pos['y'], 5),
                'z':                 round(pos['z'], 5),
                'visible_count':     result['visible_count'],
                'system_names':      '|'.join(result['system_names']),
                'sb_bright_sum':     round(sb['bright_sum'], 2),
                'sb_peak_val':       round(sb['peak_val'], 2),
                'sb_peak_row':       sb['peak_row'],
            }
            _append_csv(scan_path, SCAN_HEADER, scan_row)

            # 5. Also save scrollbar measurements for calibration
            sample_row = {
                'screenshot':    screenshot_path,
                'true_total':    '',                  # filled in by user manually
                'visible_count': result['visible_count'],
                'bright_sum':    round(sb['bright_sum'], 2),
                'peak_val':      round(sb['peak_val'], 2),
                'peak_row':      sb['peak_row'],
                'track_start':   sb['track_start'],
                'track_end':     sb['track_end'],
            }
            _append_csv(sample_path, SAMPLE_HEADER, sample_row)

            # 6. Print results
            print(f"  System  : {pos['StarSystem']}")
            print(f"  Coords  : x={pos['x']:.2f}  y={pos['y']:.2f}  z={pos['z']:.2f} ly")
            print(f"  Y (galactic height): {pos['y']:.2f} ly")
            print(f"  Visible : {result['visible_count']} systems")
            print(f"  Total   : {total_str}")
            if result['system_names']:
                sample = result['system_names'][:4]
                more = f" (+{len(result['system_names'])-4} more)" if len(result['system_names']) > 4 else ""
                print(f"  Names   : {', '.join(sample)}{more}")
            print(f"  [OK] Scan #{idx} saved.")
            scans.append(scan_row)
            _beep_ok()

        except Exception as e:
            print(f"  [X] Error: {e}")
            import traceback; traceback.print_exc()
            _beep_err()

    keyboard.add_hotkey(config.CAPTURE_HOTKEY, on_capture)
    try:
        keyboard.wait(config.QUIT_HOTKEY)
    except KeyboardInterrupt:
        pass

    # Session summary
    print("\n" + "=" * 70)
    print(f"  SESSION COMPLETE — {len(scans)} scan(s) recorded")
    print("=" * 70)
    if scans:
        print()
        print(f"  {'Y (ly)':>10}  {'Visible':>8}  {'System'}")
        print(f"  {'-'*10}  {'-'*8}  {'-'*35}")
        for s in scans:
            print(f"  {s['y']:>10.2f}  {s['visible_count']:>8}  {s['star_system']}")
        print()
        print(f"  Full results : {scan_path}")
        print(f"  SB samples   : {sample_path}  ← fill in true_total, then run:")
        print(f"                 python scrollbar_calibrate.py analyse")
    print("\n  o7  Fly safe, Commander.")


if __name__ == '__main__':
    main()
