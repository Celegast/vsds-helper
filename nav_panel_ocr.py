"""
Nav Panel OCR for VSDS Helper

Two capture modes:

  scan(screenshot)      - top-of-list screenshot:
      crop → deskew → count visible entries via OCR → scrollbar measurement

  scan_end(screenshot)  - bottom-of-list screenshot:
      crop → deskew → find highlighted last entry → OCR its distance (Max Distance)

Note on scrollbar: calibration data (scrollbar_samples.csv) shows the fading
gradient does NOT reliably encode the total count.  visible_count from OCR is the
primary metric.  Max Distance from the _end screenshot is the recommended
supplementary metric.
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

    def _find_highlighted_entry(self, deskewed: np.ndarray) -> tuple[bool, int, int]:
        """
        Locate the selected (amber-background) entry anywhere in the list.

        Scans row means across the full list height.  The highlighted entry's
        amber background produces row means ~120-150, while dark entry rows
        peak at ~40-50.  A row mean above HIGHLIGHT_THRESHOLD (default 90)
        that also has a global peak above 100 identifies the entry.

        Returns (found, row_top, row_bottom) in deskewed-panel coordinates.
        If not found, returns (False, 0, 0).
        """
        region = deskewed[
            config.LIST_TOP : config.LIST_BOTTOM,
            config.HIGHLIGHT_CHECK_LEFT : config.HIGHLIGHT_CHECK_RIGHT,
        ]
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        row_means = np.mean(gray, axis=1)

        if float(np.max(row_means)) < 100:
            return False, 0, 0          # no highlight; brightest row is just text glow

        above = row_means > config.HIGHLIGHT_THRESHOLD
        bright_rows = np.where(above)[0]
        if len(bright_rows) == 0:
            return False, 0, 0

        # Use only the FIRST contiguous run of bright rows.
        # Stray isolated rows later in the list (panel edges, cockpit reflections)
        # are excluded by stopping at the first gap.
        run_start = int(bright_rows[0])
        run_end   = run_start
        for i in range(run_start + 1, len(row_means)):
            if row_means[i] > config.HIGHLIGHT_THRESHOLD:
                run_end = i
            else:
                break   # first gap → end of the highlight entry

        row_top    = config.LIST_TOP + run_start
        row_bottom = config.LIST_TOP + run_end + 1
        return True, row_top, row_bottom

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
        found_hl, hl_top, hl_bottom = self._find_highlighted_entry(deskewed)

        # OCR the full list area.  The amber-background highlighted row is
        # reliably invisible to Tesseract under normal binary thresholding, so
        # all dark entries (above and below the highlight) are captured correctly.
        list_crop = deskewed[
            config.LIST_TOP : config.LIST_BOTTOM,
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

        # If a highlighted entry was found, OCR it separately using THRESH_BINARY_INV:
        # the highlighted row has dark text on bright amber background — the inverse
        # of all other rows — so we invert the threshold to make its text readable.
        if found_hl:
            hl_crop = deskewed[hl_top:hl_bottom, config.LIST_LEFT:config.LIST_RIGHT]
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

    # ── Max-distance from _end screenshot ────────────────────────────────────

    # Common OCR character confusions in amber-background (BINARY_INV) crops.
    # Digit-like letters that Tesseract confuses with digits when reading
    # dark text on a bright amber background.
    _DIST_CHAR_SUB = str.maketrans('SOoIlgBs', '50011989')

    def read_max_distance(self, deskewed: np.ndarray,
                          debug_prefix: str = None) -> float | None:
        """
        Read the distance of the last (highlighted) entry from a bottom-scrolled
        nav panel screenshot.

        The highlighted bottom entry has dark text on an amber background.
        Its distance label (e.g. '13.6Ly') sits in the rightmost column of the row.
        Uses THRESH_BINARY_INV so that dark text becomes white for Tesseract.

        Returns the distance in light-years as a float, or None if not found.
        """
        import re

        found, hl_top, hl_bottom = self._find_highlighted_entry(deskewed)
        if not found:
            return None

        # Crop the distance column of the highlighted row.
        # Use a lower binarisation threshold (60) than the normal OCR path (80)
        # because the amber background sits at ~130 gray and the dark text at ~30-60.
        # Threshold 60 keeps only the darkest (most reliable) ink pixels.
        dist_crop = deskewed[hl_top:hl_bottom, config.DIST_COL_LEFT:config.DIST_COL_RIGHT]
        h, w = dist_crop.shape[:2]
        up = cv2.resize(dist_crop, (w * config.OCR_UPSCALE_FACTOR,
                                    h * config.OCR_UPSCALE_FACTOR),
                        interpolation=cv2.INTER_CUBIC)
        gray = cv2.cvtColor(up, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 60, 255, cv2.THRESH_BINARY_INV)

        if config.SAVE_DEBUG_IMAGES and debug_prefix:
            cv2.imwrite(os.path.join(config.DEBUG_DIR, f"{debug_prefix}_dist_crop.png"), dist_crop)
            Image.fromarray(binary).save(
                os.path.join(config.DEBUG_DIR, f"{debug_prefix}_dist_bin.png"))

        raw = pytesseract.image_to_string(Image.fromarray(binary), config='--oem 3 --psm 7')

        # Normalise OCR noise:
        #   1. commas OCR'd in place of periods → comma+period → single period
        #   2. strip spaces and letter-digit confusions (S→5, g→9, etc.)
        #   3. collapse double-dot from comma→period substitution (13,.6 → 13..6 → 13.6)
        raw_clean = (raw.strip()
                     .replace(',', '.')
                     .replace(' ', '')
                     .translate(self._DIST_CHAR_SUB))
        raw_clean = re.sub(r'\.{2,}', '.', raw_clean)

        m = re.search(r'(\d{1,4}[.]\d)', raw_clean)
        if m:
            return float(m.group(1))
        return None

    def scan_end(self, screenshot_path: str, debug_prefix: str = None) -> dict:
        """
        Process a bottom-scrolled (_end) nav panel screenshot.

        Returns:
            max_distance_ly  - distance to the furthest visible system (float or None)
            last_system_name - OCR'd name of the highlighted last entry (str or None)
        """
        panel_bgr = self.crop_nav_panel(screenshot_path)
        deskewed  = self.deskew(panel_bgr)

        if config.SAVE_DEBUG_IMAGES and debug_prefix:
            cv2.imwrite(os.path.join(config.DEBUG_DIR, f"{debug_prefix}_deskewed.png"), deskewed)

        max_dist = self.read_max_distance(deskewed, debug_prefix)

        # Also try to read the system name of the last entry
        found, hl_top, hl_bottom = self._find_highlighted_entry(deskewed)
        last_name = None
        if found:
            hl_crop = deskewed[hl_top:hl_bottom, config.LIST_LEFT:config.LIST_RIGHT]
            h, w = hl_crop.shape[:2]
            up = cv2.resize(hl_crop, (w * config.OCR_UPSCALE_FACTOR,
                                      h * config.OCR_UPSCALE_FACTOR),
                            interpolation=cv2.INTER_CUBIC)
            gray = cv2.cvtColor(up, cv2.COLOR_BGR2GRAY)
            _, hl_bin = cv2.threshold(gray, config.OCR_THRESHOLD, 255, cv2.THRESH_BINARY_INV)
            hl_raw = pytesseract.image_to_string(Image.fromarray(hl_bin), config='--oem 3 --psm 7')
            names = self._clean_names([hl_raw.strip()])
            if names:
                last_name = names[0]

        return {
            'max_distance_ly':  max_dist,
            'last_system_name': last_name,
        }

    # ── Automated full-list scan via keypress scrolling ───────────────────

    def scan_with_scroll(self, debug_prefix: str = None) -> dict:
        """
        Full automated scan: take an initial screenshot, then scroll the nav
        panel list entry-by-entry using keypresses, count every entry, and
        read the max distance from the last entry.

        Assumes the nav panel is open with the FIRST entry selected (list at top).
        One press of SCROLL_KEY moves the selection down by one entry.
        Pressing past the last entry wraps back to the first.

        Algorithm:
          Phase 1 – press SCROLL_KEY (visible_count − 1) times with no screenshots.
                    This moves the selection from entry 1 to entry visible_count.
          Phase 1 end – take one screenshot (entry visible_count selected).
          Phase 2 – press SCROLL_KEY once, screenshot, detect hl_top.
                    When hl_top returns to ≈ hl_top_init the list has wrapped;
                    the previous frame held entry N (max-distance read from it).
          Total count = total SCROLL_KEY presses for one full cycle.

        Returns:
            visible_count   – entries visible in the initial screenshot
            total_count     – total entries in the list
            system_names    – OCR'd names from the initial visible window
            max_distance_ly – distance to the furthest system (float or None)
            scrollbar       – raw scrollbar measurements from initial screenshot
        """
        import time
        import pydirectinput

        # pydirectinput uses SendInput with scan codes, which works with
        # DirectInput games like Elite Dangerous (pyautogui's VK-code
        # approach is ignored by DirectInput).

        WRAP_TOL = 50    # pixels: hl_top must be within this of hl_top_init

        # ── Initial screenshot + full OCR ─────────────────────────────────
        initial_path = self.take_screenshot()
        panel_bgr    = self.crop_nav_panel(initial_path)
        deskewed     = self.deskew(panel_bgr)

        if config.SAVE_DEBUG_IMAGES and debug_prefix:
            cv2.imwrite(os.path.join(config.DEBUG_DIR,
                                     f"{debug_prefix}_init_deskewed.png"), deskewed)

        visible_count, system_names = self.count_visible_entries(
            deskewed, debug_prefix and f"{debug_prefix}_init")
        scrollbar = self.measure_scrollbar(
            deskewed, debug_prefix and f"{debug_prefix}_init")
        found_hl, hl_top_init, _ = self._find_highlighted_entry(deskewed)

        # ── Short list: everything fits on screen (no scrollbar signal) ───
        # peak_val < 10 means the scrollbar gradient is effectively absent.
        if not found_hl or scrollbar['peak_val'] < 10:
            return {
                'visible_count':   visible_count,
                'total_count':     visible_count,
                'system_names':    system_names,
                'max_distance_ly': self.read_max_distance(deskewed),
                'scrollbar':       scrollbar,
            }

        # ── Phase 1: move selection to bottom of visible window ───────────
        # visible_count − 1 presses: no wrap is possible in this range.
        for _ in range(visible_count - 1):
            pydirectinput.press(config.SCROLL_KEY)
            time.sleep(config.SCROLL_PRESS_DELAY)

        time.sleep(config.SCROLL_SETTLE_DELAY)
        p1_path       = self.take_screenshot()
        prev_deskewed = self.deskew(self.crop_nav_panel(p1_path))

        # ── Phase 2: scroll one entry at a time, detect wrap ─────────────
        presses = visible_count - 1

        while presses < 55:    # hard cap: nav panel shows at most 49 systems
            pydirectinput.press(config.SCROLL_KEY)
            presses += 1
            time.sleep(config.SCROLL_PRESS_DELAY + config.SCROLL_SETTLE_DELAY)

            path          = self.take_screenshot()
            curr_deskewed = self.deskew(self.crop_nav_panel(path))
            found, hl_top, _ = self._find_highlighted_entry(curr_deskewed)

            if found and abs(hl_top - hl_top_init) < WRAP_TOL:
                # List wrapped back to entry 1.
                # prev_deskewed is the frame BEFORE this press → entry N selected.
                break

            prev_deskewed = curr_deskewed

        total_count = presses   # one full cycle = total entries
        max_dist    = self.read_max_distance(
            prev_deskewed,
            debug_prefix and f"{debug_prefix}_end")

        return {
            'visible_count':   visible_count,
            'total_count':     total_count,
            'system_names':    system_names,
            'max_distance_ly': max_dist,
            'scrollbar':       scrollbar,
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
