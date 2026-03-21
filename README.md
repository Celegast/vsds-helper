# VSDS Helper — Elite Dangerous Vertical Stellar Density Scanner

A Python tool that assists **Vertical Stellar Density Scanning (VSDS)** in Elite Dangerous by automating the nav panel scroll-and-count and recording galactic position.

## Disclaimer

This tool occupies a **grey area** in the Elite Dangerous
[End User Licence Agreement](https://www.frontierstore.net/ed-eula/).

| Component | Status |
|-----------|--------|
| Reading ED journal files | **Fine** — Frontier designed the journal system specifically for third-party tools (EDMC, Inara, EDSM all do the same) |
| Screenshot + OCR | **Fine** — passive screen reading, no different from overlay tools |
| Simulated keypresses (`s` to scroll the nav panel) | **Grey area** — the EULA prohibits "automation software"; Frontier has never actioned against benign, non-competitive tools of this type, and it is functionally equivalent to a VoiceAttack macro |

The tool provides **no competitive advantage** — it navigates a UI list and counts entries for citizen-science data collection.  Frontier has historically embraced the VSDS research programme.  That said, **you use this tool at your own discretion and risk.**

> **General disclaimer:** This software is provided "as is", without warranty of any kind, express or implied.  The author accepts no responsibility or liability for any damage, data loss, account action, or other harm arising from its use.  By using this tool you agree that you do so entirely at your own risk.

---

## What is a VSDS?

A VSDS is a citizen-science technique where commanders fly to progressively
higher (or lower) galactic latitudes and count the number of star systems visible
in the navigation panel at each altitude.  The resulting curve shows how stellar
density varies with height above/below the galactic plane.

The nav panel (key `1`) can be filtered to show only star systems.  Counting
the entries manually is tedious — this tool automates the count and simultaneously
reads your galactic coordinates from the ED journal file.

## Features

- **Single-key capture** — press F9; the tool auto-scrolls the entire list and records everything
- **Exact total count** — scrolls entry-by-entry using keypresses, detects wrap-around, no estimation
- **OCR-based visible names** — reads all system names from the initial visible window
- **Max Distance** — distance to the furthest system, read from the last entry at the end of the scroll
- **Galactic position from journal** — reads `StarPos [x, y, z]` from the latest
  `Journal.*.log`; the **Y coordinate** (galactic height) is the key VSDS axis
- **Confirm / correct prompt** — after each scan, review and fix parsed values before they are saved
- **CSV output** — every confirmed scan appended to `output/vsds_scans.csv`
- **Excel paste file** — `output/vsds_paste.tsv` with four separate paste blocks
  matching the VSDS spreadsheet column layout (formula columns left blank)
- **Screenshots auto-cleared** — the `screenshots/` folder is emptied at startup

## Prerequisites

### Software

- **Python 3.12+**
- **Tesseract OCR**
  - Windows: download from <https://github.com/UB-Mannheim/tesseract/wiki>

### Python packages

```
pip install -r requirements.txt
```

Packages: `pytesseract`, `Pillow`, `keyboard`, `pyautogui`, `pydirectinput`, `opencv-python`, `numpy`

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

Per altitude:

1. Open the nav panel (`1`), apply the **star systems only** filter
2. Fly to the scan altitude and wait for the list to settle
3. Make sure the **first entry is selected** (list at the top)
4. Press **F9** — the tool auto-scrolls the full list and counts every entry
5. **Alt-Tab** to the terminal when you hear the three descending confirmation tones
6. Press **Enter** to accept each parsed value, or type a correction and press Enter
7. The scan is saved; switch back to ED for the next altitude

Press **ESC** to quit; a session summary is printed.

### Correcting parsed values

After each scan the tool prompts:

```
  [?] Confirm — press Enter to accept, or type a correction:
      System count [49]:
      Max dist (ly) [13.6]:
```

- Press **Enter** to keep the parsed value.
- Type a number and press **Enter** to override it (both `.` and `,` work as the decimal separator).

### Excel paste file (`output/vsds_paste.tsv`)

The file is rewritten after every confirmed scan.  It contains a single table
with all nine spreadsheet columns pre-calculated, sorted by galactic height:

```
System	Z Sample	System Count	Corrected n	Max Distance	Rho	X	Z	Y
Preae Chroa OX-L c7-65	50	49	50	19.9	0.00151	5403.34	49207.97	0.09
...
```

Paste the whole table (including the header row) into column A.  All columns
are filled — formula columns (`Z Sample`, `Corrected n`, `Rho`) are
pre-calculated so you can paste over any existing formulas without loss.

| Column | Source |
|--------|--------|
| System | Star system name from journal |
| Z Sample | `y` snapped to nearest scan step (linear 0–1000 in 50 ly steps, or logarithmic ±250) |
| System Count | Confirmed total entry count |
| Corrected n | System Count + 1 |
| Max Distance | OCR'd distance to furthest system (ly) |
| Rho | `corrected_n / ((4π/3)·r³)` where r = max_dist if n ≥ 50, else 20 ly |
| X | ED x coordinate |
| Z | ED y coordinate (galactic height) |
| Y | ED z coordinate |

### Output columns (vsds_scans.csv)

| Column | Description |
|--------|-------------|
| `capture_time` | Local timestamp of the capture |
| `journal_timestamp` | UTC timestamp of the last FSDJump/Location event |
| `star_system` | Current star system name |
| `x`, `y`, `z` | Galactic coordinates in ly (Y = galactic height) |
| `visible_count` | Systems visible in the initial window (up to 11) |
| `total_count` | Exact total systems in the list (confirmed by user) |
| `system_names` | Pipe-separated OCR'd names from the initial visible window |
| `max_distance_ly` | Distance to the furthest system in ly (confirmed by user) |
| `sb_bright_sum` | Number of orange scrollbar rows × 100 (diagnostic) |
| `sb_peak_val` | 100.0 if scrollbar thumb detected, 0.0 if absent (diagnostic) |
| `sb_peak_row` | Absolute row of detected scrollbar thumb top (diagnostic) |

## How it works

### Auto-scroll algorithm

F9 triggers a sequence:

1. **Initial screenshot** — OCR the visible window (up to 11 entries); measure scrollbar thumb
   to estimate total count for the bulk skip
2. **Phase 1** — send `visible_count − 1` keypresses (`s`); no wrap possible in this range;
   take one screenshot (entry `visible_count` selected)
3. **Bulk phase** — skip most remaining entries without screenshots using the scrollbar
   geometry estimate (`thumb_h / track_h ≈ visible_count / total_count`); stops
   `BULK_SAFETY` (8) entries before the estimated end
4. **Phase 2** — press `s` once per step, screenshot, check where the highlighted entry is.
   When it returns to the top of the window the list has wrapped → **total = press count**
5. **Max distance** — press `w` twice (→ entry N, → entry N−1), screenshot: entry N appears
   as a normal unselected row (orange-on-dark) below the highlight.  OCR its distance column
   with OTSU thresholding.  A domain sanity check rejects values > 20 ly (the nav panel
   maximum); slashed-zero (Ø→8) artefacts in that range are corrected automatically.
   Falls back to reading the amber-highlighted entry N if the normal path returns nothing.
6. **Confirmation prompt** — review and optionally correct `total_count` and `max_distance_ly`
   before anything is written to disk

For a 49-entry list with 11 visible, the bulk phase skips ~30 keypresses without screenshots,
leaving only the final ~8 entries for wrap detection (~1-2 seconds total).

### Audio cues

| Sound | Meaning |
|-------|---------|
| Single tone (1000 Hz) | Scan started (F9 accepted) |
| Three descending tones (1200→1000→800 Hz) | Scan complete — Alt-Tab to confirm |
| Rising two-tone (1000→1400 Hz) | Scan saved successfully |
| Low tone (400 Hz, long) | Error |

### Image pipeline

```
Full screenshot (5120×1440)
  └─ Crop nav panel  [2006, 545] → [3036, 1148]  (raw px)
       ├─ Rotate –2° (SCROLLBAR_TILT_DEGREES)
       │    └─ Crop scrollbar strip [992–1006, 34–494]
       │         └─ HSV orange detection → thumb height → bulk estimate
       └─ Deskew  (rotate –4.5° to correct cockpit tilt)
            ├─ Detect highlighted entry  (amber background → row mean > 90)
            │    └─ OCR with THRESH_BINARY_INV (dark text on amber bg)
            ├─ Read distance of row below highlight [820–990]  (OTSU, orange-on-dark)
            │    └─ Fallback: read highlighted entry with THRESH_BINARY_INV if above fails
            └─ OCR list area [70–800]  (PSM 6, THRESH_BINARY)
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

### Note on the scrollbar

The nav panel scrollbar thumb is detected by HSV orange/amber colour segmentation
(H 10–30, S > 60, V > 80) on a –2°-rotated crop of the raw panel.  The thumb's
height relative to the track gives a geometry estimate
(`thumb_h / track_h ≈ visible_count / total_count`) used to skip most of the
list in the bulk phase.

The scrollbar provides only a rough estimate — the exact total is always
determined by keypresses (one press per entry, wrap back to entry 1 signals the end).
Earlier brightness-based analysis (18 samples) showed no reliable correlation
with total count; that approach has been replaced by colour segmentation.

## Project structure

```
VSDS-Helper/
├── config.py                # All coordinates, paths, thresholds
├── journal_reader.py        # Read galactic position from ED journal
├── nav_panel_ocr.py         # Screenshot → crop → deskew → OCR + auto-scroll
├── vsds_capture.py          # Main F9 hotkey loop + confirmation prompt
├── scrollbar_calibrate.py   # Legacy scrollbar sample tool
├── requirements.txt
├── output/                  # Created at runtime
│   ├── vsds_scans.csv       # Accumulated scan results
│   ├── vsds_paste.tsv       # Four-block Excel paste file (rewritten each scan)
│   └── scrollbar_samples.csv
├── screenshots/             # Cleared at startup; holds current-session screenshots
└── debug/                   # Runtime debug images (not committed)
```

## Configuration

Edit `config.py` to change:

| Setting | Default | Description |
|---------|---------|-------------|
| `JOURNAL_DIR` | `C:\Users\celeg\Saved Games\...` | Path to ED journal folder |
| `CAPTURE_HOTKEY` | `f9` | Scan trigger key |
| `QUIT_HOTKEY` | `esc` | Exit key |
| `SCROLL_KEY` | `s` | Key that moves nav panel selection down |
| `SCROLL_PRESS_DELAY` | `0.02` | Seconds between keypresses |
| `SCROLL_SETTLE_DELAY` | `0.07` | Seconds to wait after a press before screenshotting |
| `TESSERACT_PATH` | `None` | Set if Tesseract is not in PATH |
| `SAVE_DEBUG_IMAGES` | `True` | Save cropped/preprocessed images to `debug/` |
| `NAV_PANEL_TILT_DEGREES` | `-4.5` | Deskew angle applied to panel crop for OCR |
| `SCROLLBAR_TILT_DEGREES` | `-2.0` | Separate rotation applied to raw panel before scrollbar measurement |

## Tips

- Apply the **star systems only** filter in the nav panel before capturing
- Make sure the **first entry is selected** before pressing F9 — the tool starts
  counting from wherever the selection is
- Keep the terminal visible (or on a second screen) so you can confirm values quickly
- If OCR accuracy degrades, check `debug/scan_NNN_init_ocr_input.png`
- The Y coordinate from the journal is the system's galactic position,
  not your exact in-system position — good enough for VSDS

## Credits

Inspired by the ED explorer community's VSDS methodology.
Built on techniques from [PowerplayParser](../PowerplayParser/).

o7 Commanders!
