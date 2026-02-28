# VSDS Helper — Elite Dangerous Vertical Stellar Density Scanner

A Python tool for automating **Vertical Stellar Density Scans (VSDS)** in Elite Dangerous.

## What is a VSDS?

A VSDS is a citizen-science technique where commanders fly to progressively
higher (or lower) galactic latitudes and count the number of star systems visible
in the navigation panel at each altitude.  The resulting curve shows how stellar
density varies with height above/below the galactic plane.

The nav panel (key `1`) can be filtered to show only star systems.  Counting
the entries manually is tedious — this tool automates the count from a screenshot
and simultaneously reads your galactic coordinates from the ED journal file.

## Features

- **F9 hotkey capture** — press once while the nav panel is open; the tool does the rest
- **OCR-based entry count** — reads all visible system names from the list (up to 11)
- **Highlighted-entry detection** — the selected (amber-background) top entry is detected
  and OCR'd separately with inverted thresholding
- **Galactic position from journal** — reads `StarPos [x, y, z]` from the latest
  `Journal.*.log`; the **Y coordinate** (galactic height) is the key VSDS axis
- **CSV output** — every scan appended to `output/vsds_scans.csv`
- **Scrollbar calibration workflow** — stores raw scrollbar measurements alongside
  each capture so a total-count formula can be fitted once enough samples exist

## Prerequisites

### Software

- **Python 3.12+**
- **Tesseract OCR**
  - Windows: download from <https://github.com/UB-Mannheim/tesseract/wiki>

### Python packages

```
pip install -r requirements.txt
```

Packages: `pytesseract`, `Pillow`, `keyboard`, `pyautogui`, `opencv-python`, `numpy`

## Installation

1. Clone this repository
2. Install Tesseract OCR (add to PATH, or set `TESSERACT_PATH` in `config.py`)
3. Install Python dependencies: `pip install -r requirements.txt`
4. Verify journal path in `config.py` matches your ED install

## Usage

### Live capture session

```
python vsds_capture.py
```

1. Launch Elite Dangerous
2. Open the nav panel (`1`), apply the **star systems only** filter
3. Fly to your first scan altitude and wait for the list to settle
4. Press **F9** — the tool captures a screenshot, reads the count and your position,
   and appends a row to `output/vsds_scans.csv`
5. Fly to the next altitude and repeat
6. Press **ESC** to quit; a session summary is printed

### Output columns (vsds_scans.csv)

| Column | Description |
|--------|-------------|
| `capture_time` | Local timestamp of the capture |
| `journal_timestamp` | UTC timestamp of the last FSDJump/Location event |
| `star_system` | Current star system name |
| `x`, `y`, `z` | Galactic coordinates in ly (Y = galactic height) |
| `visible_count` | Number of systems visible in the nav panel (OCR) |
| `system_names` | Pipe-separated list of OCR'd system names |
| `sb_bright_sum` | Scrollbar brightness sum (for calibration) |
| `sb_peak_val` | Scrollbar peak row brightness |
| `sb_peak_row` | Row of peak brightness in the scrollbar |

## Scrollbar calibration

The nav panel scrollbar uses a fading gradient rather than a discrete proportional
thumb.  A formula to convert the scrollbar signal to a **total** system count
(not just the visible 11) has not yet been established.

To help calibrate it:

1. Take a screenshot where you can verify the true total count
   (e.g. a short list that fits entirely on screen, or one you scrolled through manually)
2. Add it as a sample:
   ```
   python scrollbar_calibrate.py add "screenshot.png" <true_total>
   ```
3. After gathering several samples across different densities:
   ```
   python scrollbar_calibrate.py analyse
   ```
   This fits a linear model and saves it to `output/scrollbar_model.json`.
   Once a model exists, `vsds_capture.py` will display estimated totals automatically.

Other commands:
```
python scrollbar_calibrate.py list    # show all stored samples
```

## How it works

### Image pipeline

```
Full screenshot (5120×1440)
  └─ Crop nav panel  [2006, 545] → [3036, 1148]  (raw px)
       └─ Deskew  (rotate –6.034° to correct cockpit tilt)
            ├─ Detect highlighted entry  (amber background → max row-mean > 80)
            │    └─ OCR with THRESH_BINARY_INV (dark text on bright bg)
            ├─ OCR list rows below highlight  (PSM 6, THRESH_BINARY)
            └─ Measure scrollbar column [1012–1022]  (brightness profile)
```

### Coordinate system

All X coordinates in `config.py` are stored in **16:9-zone-relative space at 1440
height**, identical to the system used in
[PowerplayParser](../PowerplayParser/README.md).  They scale automatically to any
screen resolution and aspect ratio at runtime.

### Journal reader

Reads `FSDJump`, `Location`, and `CarrierJump` events from the latest
`Journal.*.log` in the ED saved-games directory.  The most recent event gives
`StarPos [x, y, z]` — the galactic coordinates of the current system.

## Project structure

```
VSDS-Helper/
├── config.py                # All coordinates, paths, thresholds
├── journal_reader.py        # Read galactic position from ED journal
├── nav_panel_ocr.py         # Screenshot → crop → deskew → OCR + scrollbar
├── vsds_capture.py          # Main F9 hotkey loop
├── scrollbar_calibrate.py   # Sample collector + model fitter
├── requirements.txt
├── output/                  # Created at runtime
│   ├── vsds_scans.csv       # Accumulated scan results
│   ├── scrollbar_samples.csv
│   └── scrollbar_model.json (once calibrated)
├── screenshots/             # Runtime screenshots (not committed)
└── debug/                   # Runtime debug images (not committed)
```

## Configuration

Edit `config.py` to change:

| Setting | Default | Description |
|---------|---------|-------------|
| `JOURNAL_DIR` | `C:\Users\celeg\Saved Games\...` | Path to ED journal folder |
| `CAPTURE_HOTKEY` | `f9` | Trigger key |
| `QUIT_HOTKEY` | `esc` | Exit key |
| `TESSERACT_PATH` | `None` | Set if Tesseract is not in PATH |
| `SAVE_DEBUG_IMAGES` | `True` | Save cropped/preprocessed images to `debug/` |
| `NAV_PANEL_TILT_DEGREES` | `-6.034` | Deskew angle (cockpit tilt) |

## Calibration reference screenshot

`Screenshot 2026-02-28 121805.png` — 5120×1440, 35 systems in list, 11 visible.
Used to calibrate all panel coordinates and verify OCR accuracy.

## Tips

- Apply the **star systems only** filter in the nav panel before capturing
- Make sure the nav panel is fully loaded and stable before pressing F9
- The Y coordinate from the journal is the system's galactic position,
  not your exact in-system position — good enough for VSDS
- If OCR accuracy degrades, check `debug/scan_NNN_ocr_input.png` to see
  what Tesseract received

## Credits

Inspired by the ED explorer community's VSDS methodology.
Built on techniques from [PowerplayParser](../PowerplayParser/).

o7 Commanders!
