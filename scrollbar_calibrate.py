"""
Scrollbar Calibration Tool
==========================
Run this script to:
  1. Add a new sample: provide a screenshot + the true system count you verified manually
  2. Analyse existing samples: fit / inspect the relationship between scrollbar
     brightness metrics and the true total count

Usage
-----
  # Add a sample (you know there were 35 systems):
  python scrollbar_calibrate.py add "Screenshot 2026-02-28 121805.png" 35

  # List all stored samples:
  python scrollbar_calibrate.py list

  # Analyse samples and print a proposed formula:
  python scrollbar_calibrate.py analyse

The samples are stored in output/scrollbar_samples.csv.
Once enough samples exist, the calibrate command fits a simple linear model:
  total_count ≈ A * bright_sum + B
and writes the coefficients to output/scrollbar_model.json.
"""

import csv
import json
import os
import sys

import config
from nav_panel_ocr import NavPanelOCR

SAMPLES_PATH = config.SCROLLBAR_SAMPLES_FILE
MODEL_PATH   = "output/scrollbar_model.json"

SAMPLE_HEADER = [
    'screenshot', 'true_total', 'visible_count',
    'bright_sum', 'peak_val', 'peak_row',
    'track_start', 'track_end',
]


def _ensure_samples_file():
    os.makedirs("output", exist_ok=True)
    if not os.path.exists(SAMPLES_PATH):
        with open(SAMPLES_PATH, 'w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(SAMPLE_HEADER)


def cmd_add(screenshot_path: str, true_total: int):
    """Measure the scrollbar from a screenshot and store alongside the known count."""
    if not os.path.exists(screenshot_path):
        print(f"File not found: {screenshot_path}")
        sys.exit(1)

    ocr = NavPanelOCR()
    result = ocr.scan(screenshot_path, debug_prefix=f"cal_{os.path.basename(screenshot_path)}")
    sb = result['scrollbar']

    row = {
        'screenshot':    screenshot_path,
        'true_total':    true_total,
        'visible_count': result['visible_count'],
        'bright_sum':    round(sb['bright_sum'], 2),
        'peak_val':      round(sb['peak_val'], 2),
        'peak_row':      sb['peak_row'],
        'track_start':   sb['track_start'],
        'track_end':     sb['track_end'],
    }

    _ensure_samples_file()
    with open(SAMPLES_PATH, 'a', newline='', encoding='utf-8') as f:
        csv.DictWriter(f, fieldnames=SAMPLE_HEADER).writerow(row)

    print(f"Sample added:")
    print(f"  Screenshot   : {screenshot_path}")
    print(f"  True total   : {true_total}")
    print(f"  Visible (OCR): {result['visible_count']}")
    print(f"  Scrollbar sum: {row['bright_sum']}")
    print(f"  Peak val/row : {row['peak_val']} @ row {row['peak_row']}")
    print(f"Saved to: {SAMPLES_PATH}")


def cmd_list():
    """Print all stored samples."""
    if not os.path.exists(SAMPLES_PATH):
        print("No samples yet. Run: python scrollbar_calibrate.py add <screenshot> <count>")
        return

    with open(SAMPLES_PATH, newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    if not rows:
        print("No samples yet.")
        return

    print(f"{'#':>3}  {'True':>5}  {'Vis':>4}  {'BrightSum':>10}  {'PeakVal':>8}  {'Screenshot'}")
    print("-" * 80)
    for i, r in enumerate(rows, 1):
        print(f"{i:>3}  {r['true_total']:>5}  {r['visible_count']:>4}  "
              f"{r['bright_sum']:>10}  {r['peak_val']:>8}  {r['screenshot']}")


def cmd_analyse():
    """
    Inspect the relationship between scrollbar metrics and true total count.
    With enough samples, fits a simple linear model: total ≈ A * bright_sum + B
    """
    if not os.path.exists(SAMPLES_PATH):
        print("No samples yet.")
        return

    with open(SAMPLES_PATH, newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    if len(rows) < 2:
        print(f"Need at least 2 samples to analyse (have {len(rows)}).")
        return

    import numpy as np

    totals    = np.array([float(r['true_total'])    for r in rows])
    visible   = np.array([float(r['visible_count']) for r in rows])
    b_sum     = np.array([float(r['bright_sum'])    for r in rows])
    peak_vals = np.array([float(r['peak_val'])      for r in rows])

    print(f"Samples: {len(rows)}")
    print()

    # ── Ratio method (if scrollbar is proportional) ──
    # ratio = visible / total  →  total = visible / ratio
    # If the scrollbar encodes ratio linearly via bright_sum, we'd see:
    #   ratio * C = bright_sum  for some constant C
    ratios = visible / totals
    print("Ratio (visible/total) per sample:")
    for r, row in zip(ratios, rows):
        print(f"  total={row['true_total']:>4}  vis={row['visible_count']:>3}  "
              f"ratio={r:.4f}  bright_sum={row['bright_sum']:>10}")

    print()

    # ── Correlation ──
    for label, x in [('bright_sum', b_sum), ('peak_val', peak_vals)]:
        if np.std(x) > 0:
            corr = np.corrcoef(x, totals)[0, 1]
            print(f"Correlation({label}, true_total) = {corr:.4f}")

    print()

    # ── Linear fit: total ~ A * bright_sum + B ──
    if len(rows) >= 2:
        X = np.column_stack([b_sum, np.ones(len(b_sum))])
        A_B, _, _, _ = np.linalg.lstsq(X, totals, rcond=None)
        A, B = A_B
        predicted = A * b_sum + B
        residuals = totals - predicted
        rmse = float(np.sqrt(np.mean(residuals**2)))
        print(f"Linear fit: total ~= {A:.6f} * bright_sum + {B:.2f}")
        print(f"RMSE: {rmse:.2f}")
        print()
        print("Predictions vs truth:")
        for r, p, t in zip(rows, predicted, totals):
            print(f"  predicted={p:6.1f}  true={t:4.0f}  err={p-t:+.1f}  ({r['screenshot']})")

        # Save model
        model = {'A': float(A), 'B': float(B), 'rmse': rmse, 'n_samples': len(rows)}
        os.makedirs("output", exist_ok=True)
        with open(MODEL_PATH, 'w') as f:
            json.dump(model, f, indent=2)
        print(f"\nModel saved to: {MODEL_PATH}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == 'add':
        if len(sys.argv) < 4:
            print("Usage: python scrollbar_calibrate.py add <screenshot> <true_count>")
            sys.exit(1)
        cmd_add(sys.argv[2], int(sys.argv[3]))

    elif cmd == 'list':
        cmd_list()

    elif cmd == 'analyse':
        cmd_analyse()

    else:
        print(f"Unknown command: {cmd}")
        print("Commands: add, list, analyse")
        sys.exit(1)


if __name__ == '__main__':
    main()
