"""
Nav Panel OCR for VSDS Helper

Pipeline per capture:
  1. Crop nav panel from the full screenshot (resolution-aware scaling)
  2. Deskew (rotate) the crop to straighten the tilted cockpit panel
  3. OCR the list area → count visible entries + read their names
  4. Sample the scrollbar column → store brightness profile for later calibration
     (the scrollbar→total mapping is not yet established; see scrollbar_calibrate.py)
"""

import os
import time

import cv2
import numpy as np
import pytesseract
from PIL import Image

import config


# ─── Coordinate helpers ───────────────────────────────────────────────────────

def _screen_to_px(cfg_x: int, cfg_y: int, actual_w: int, actual_h: int) -> tuple[int, int]:
    """Convert 16:9-zone-relative reference coords to actual screen pixels."""
    scale = actual_h / config.EXPECTED_SCREEN_HEIGHT
    x_offset = max(0, (actual_w - actual_h * 16 / 9) / 2)
    return int(x_offset + cfg_x * scale), int(cfg_y * scale)


# ─── Main class ───────────────────────────────────────────────────────────────

class NavPanelOCR:

    def __init__(self):
        if config.TESSERACT_PATH:
            pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_PATH
        os.makedirs(config.SCREENSHOTS_DIR, exist_ok=True)
        os.makedirs(config.DEBUG_DIR, exist_ok=True)

    # ── Screenshot ────────────────────────────────────────────────────────────

    def take_screenshot(self, filename: str = None) -> str:
        import pyautogui
        if filename is None:
            filename = f"vsds_{time.strftime('%Y%m%d_%H%M%S')}.png"
        path = os.path.join(config.SCREENSHOTS_DIR, filename)
        pyautogui.screenshot().save(path)
        return path

    # ── Step 1: crop panel ────────────────────────────────────────────────────

    def crop_nav_panel(self, image_path: str) -> np.ndarray:
        """
        Crop the nav panel from a full-screen screenshot (BGR numpy array).
        Applies the same resolution-aware scaling as PowerplayParser.
        """
        img = cv2.imread(image_path)
        if img is None:
            raise FileNotFoundError(f"Cannot load: {image_path}")
        actual_h, actual_w = img.shape[:2]
        x1, y1 = _screen_to_px(config.NAV_PANEL_LEFT,  config.NAV_PANEL_TOP,    actual_w, actual_h)
        x2, y2 = _screen_to_px(config.NAV_PANEL_RIGHT, config.NAV_PANEL_BOTTOM, actual_w, actual_h)
        return img[y1:y2, x1:x2]

    # ── Step 2: deskew ────────────────────────────────────────────────────────

    def deskew(self, panel_bgr: np.ndarray) -> np.ndarray:
        """Rotate the panel crop to correct for the cockpit tilt."""
        h, w = panel_bgr.shape[:2]
        M = cv2.getRotationMatrix2D((w / 2, h / 2), config.NAV_PANEL_TILT_DEGREES, 1.0)
        return cv2.warpAffine(panel_bgr, M, (w, h), borderValue=(0, 0, 0))

    # ── Step 3: OCR → visible entry count ────────────────────────────────────

    def _preprocess_for_ocr(self, bgr: np.ndarray) -> np.ndarray:
        """Upscale → grayscale → threshold, same approach as PowerplayParser."""
        h, w = bgr.shape[:2]
        up = cv2.resize(bgr,
                        (w * config.OCR_UPSCALE_FACTOR, h * config.OCR_UPSCALE_FACTOR),
                        interpolation=cv2.INTER_CUBIC)
        gray = cv2.cvtColor(up, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, config.OCR_THRESHOLD, 255, cv2.THRESH_BINARY)
        return binary

    def _detect_highlighted_entry(self, deskewed: np.ndarray) -> bool:
        """
        Return True if a selected (highlighted) entry exists at the top of the list.

        The selected entry has a bright amber background (inverted vs the dark list
        rows). OCR misses it, so we detect it via average pixel brightness instead.
        """
        region = deskewed[
            config.LIST_TOP : config.LIST_TOP + config.HIGHLIGHT_CHECK_ROWS,
            config.HIGHLIGHT_CHECK_LEFT : config.HIGHLIGHT_CHECK_RIGHT,
        ]
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        # Use the maximum per-row mean: the amber highlight row has a high
        # row-mean even when the surrounding header rows are dark.
        row_means = np.mean(gray, axis=1)
        return float(np.max(row_means)) > config.HIGHLIGHT_THRESHOLD

    @staticmethod
    def _clean_names(raw_lines: list[str]) -> list[str]:
        """
        Strip icon debris and distance fragments from OCR output lines,
        keeping only lines that look like ED star system names.

        ED system names contain letters, digits, spaces, and hyphens,
        and are at least ~8 characters long.
        """
        import re
        # Remove common icon-OCR artefacts from the start of each line
        debris_re = re.compile(r'^[\W_@#<>(){}[\]\\/*|]+')
        # ED system name pattern: at least two word-like tokens separated by spaces
        name_re = re.compile(r'[A-Za-z]{2,}')

        cleaned = []
        for line in raw_lines:
            line = debris_re.sub('', line).strip()
            # Drop trailing digits-only fragments (distance number bleed)
            line = re.sub(r'\s+\d{1,3}$', '', line).strip()
            # Keep only lines with enough alpha content
            if len(line) >= 6 and name_re.search(line):
                cleaned.append(line)
        return cleaned

    def count_visible_entries(self, deskewed: np.ndarray,
                              debug_prefix: str = None) -> tuple[int, list[str]]:
        """
        Count and read all visible nav-panel entries:
          1. Detect the highlighted (selected) top entry via brightness
          2. OCR the remaining dark-background rows
          3. Clean up OCR output and combine
        Returns (count, [name, ...]).
        """
        has_highlight = self._detect_highlighted_entry(deskewed)

        # OCR the list area (excluding the top highlighted rows when present,
        # to avoid the inverted background confusing Tesseract's block model)
        ocr_top = (config.LIST_TOP + config.HIGHLIGHT_CHECK_ROWS
                   if has_highlight else config.LIST_TOP)

        list_crop = deskewed[
            ocr_top : config.LIST_BOTTOM,
            config.LIST_LEFT : config.LIST_RIGHT,
        ]
        binary = self._preprocess_for_ocr(list_crop)
        pil = Image.fromarray(binary)

        if config.SAVE_DEBUG_IMAGES and debug_prefix:
            pil.save(os.path.join(config.DEBUG_DIR, f"{debug_prefix}_ocr_input.png"))

        raw = pytesseract.image_to_string(pil, config='--oem 3 --psm 6')

        if config.SAVE_DEBUG_IMAGES and debug_prefix:
            with open(os.path.join(config.DEBUG_DIR, f"{debug_prefix}_ocr_raw.txt"),
                      'w', encoding='utf-8') as f:
                f.write(raw)

        raw_lines = [l.strip() for l in raw.splitlines() if l.strip()]
        ocr_names = self._clean_names(raw_lines)

        # If a highlighted entry was detected, read its name separately.
        # The highlighted row has dark text on a bright amber background —
        # the opposite of every other row.  Use THRESH_BINARY_INV so the dark
        # text becomes white (readable by Tesseract) and the bright background
        # becomes black.
        if has_highlight:
            hl_crop = deskewed[
                config.LIST_TOP : config.LIST_TOP + config.HIGHLIGHT_CHECK_ROWS,
                config.LIST_LEFT : config.LIST_RIGHT,
            ]
            h, w = hl_crop.shape[:2]
            up = cv2.resize(hl_crop,
                            (w * config.OCR_UPSCALE_FACTOR,
                             h * config.OCR_UPSCALE_FACTOR),
                            interpolation=cv2.INTER_CUBIC)
            gray = cv2.cvtColor(up, cv2.COLOR_BGR2GRAY)
            _, hl_bin = cv2.threshold(gray, config.OCR_THRESHOLD, 255,
                                      cv2.THRESH_BINARY_INV)
            hl_raw = pytesseract.image_to_string(
                Image.fromarray(hl_bin), config='--oem 3 --psm 7'  # single line
            )
            hl_names = self._clean_names([hl_raw.strip()])
            if config.SAVE_DEBUG_IMAGES and debug_prefix:
                Image.fromarray(hl_bin).save(
                    os.path.join(config.DEBUG_DIR, f"{debug_prefix}_ocr_highlight.png"))
            names = hl_names + ocr_names
        else:
            names = ocr_names

        return len(names), names

    # ── Step 4: scrollbar measurement ────────────────────────────────────────

    def measure_scrollbar(self, deskewed: np.ndarray,
                          debug_prefix: str = None) -> dict:
        """
        Sample the scrollbar column and return its brightness profile.

        The raw profile is stored in scrollbar_samples.csv alongside the
        capture metadata.  A calibration script (scrollbar_calibrate.py)
        will later fit a formula once enough samples with known counts exist.

        Returns a dict with:
            profile     - list of (row, mean_brightness) within the track window
            track_start - first row with any signal
            track_end   - last row with any signal
            bright_sum  - sum of all brightness values (a simple 1-D descriptor)
            peak_row    - row with maximum brightness
            peak_val    - brightness at the peak
        """
        sb = deskewed[
            config.SCROLLBAR_ROW_TOP:config.SCROLLBAR_ROW_BOTTOM,
            config.SCROLLBAR_COL_LEFT:config.SCROLLBAR_COL_RIGHT,
        ]
        gray = cv2.cvtColor(sb, cv2.COLOR_BGR2GRAY)
        row_mean = np.mean(gray, axis=1)   # one value per row

        profile = [(config.SCROLLBAR_ROW_TOP + i, float(v))
                   for i, v in enumerate(row_mean)]

        # Find rows with any meaningful signal
        sig_rows = [r for r, v in profile if v > 5.0]
        track_start = sig_rows[0] if sig_rows else config.SCROLLBAR_ROW_TOP
        track_end   = sig_rows[-1] if sig_rows else config.SCROLLBAR_ROW_BOTTOM

        bright_sum = float(np.sum(row_mean))
        peak_idx   = int(np.argmax(row_mean))
        peak_val   = float(row_mean[peak_idx])
        peak_row   = config.SCROLLBAR_ROW_TOP + peak_idx

        if config.SAVE_DEBUG_IMAGES and debug_prefix:
            # Save the raw scrollbar strip enlarged for inspection
            strip_big = cv2.resize(sb, (sb.shape[1] * 10, sb.shape[0]),
                                   interpolation=cv2.INTER_NEAREST)
            cv2.imwrite(os.path.join(config.DEBUG_DIR, f"{debug_prefix}_scrollbar.png"),
                        strip_big)

        return {
            'profile':     profile,
            'track_start': track_start,
            'track_end':   track_end,
            'bright_sum':  bright_sum,
            'peak_row':    peak_row,
            'peak_val':    peak_val,
        }

    # ── Full scan ─────────────────────────────────────────────────────────────

    def scan(self, screenshot_path: str, debug_prefix: str = None) -> dict:
        """
        Full pipeline: screenshot → crop → deskew → OCR + scrollbar measurement.

        Returns:
            visible_count   - number of entries visible (from OCR)
            system_names    - OCR'd names of visible entries
            scrollbar       - raw scrollbar measurements (for later calibration)
        """
        panel_bgr  = self.crop_nav_panel(screenshot_path)
        deskewed   = self.deskew(panel_bgr)

        if config.SAVE_DEBUG_IMAGES and debug_prefix:
            cv2.imwrite(os.path.join(config.DEBUG_DIR, f"{debug_prefix}_panel.png"), panel_bgr)
            cv2.imwrite(os.path.join(config.DEBUG_DIR, f"{debug_prefix}_deskewed.png"), deskewed)

        visible_count, system_names = self.count_visible_entries(deskewed, debug_prefix)
        scrollbar = self.measure_scrollbar(deskewed, debug_prefix)

        return {
            'visible_count': visible_count,
            'system_names':  system_names,
            'scrollbar':     scrollbar,
        }


# ─── Quick test ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else 'Screenshot 2026-02-28 121805.png'
    ocr = NavPanelOCR()
    result = ocr.scan(path, debug_prefix='test')
    print(f"Visible entries : {result['visible_count']}")
    print(f"System names    :")
    for name in result['system_names']:
        print(f"  {name}")
    sb = result['scrollbar']
    print(f"Scrollbar peak  : row {sb['peak_row']} brightness {sb['peak_val']:.1f}")
    print(f"Scrollbar sum   : {sb['bright_sum']:.1f}")
    print(f"Track rows      : {sb['track_start']}..{sb['track_end']}")
