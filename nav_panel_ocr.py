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

        # Detect screen resolution and scale all panel-internal pixel coordinates.
        # config.py stores them at the reference height (1440 px); multiply by _s
        # so every method works correctly on any resolution.
        import pyautogui
        self.screen_w, self.screen_h = pyautogui.size()
        _s = self.screen_h / config.EXPECTED_SCREEN_HEIGHT

        self.list_left             = int(config.LIST_LEFT             * _s)
        self.list_right            = int(config.LIST_RIGHT            * _s)
        self.list_top              = int(config.LIST_TOP              * _s)
        self.list_bottom           = int(config.LIST_BOTTOM           * _s)
        self.dist_col_left         = int(config.DIST_COL_LEFT         * _s)
        self.dist_col_right        = int(config.DIST_COL_RIGHT        * _s)
        self.highlight_check_left  = int(config.HIGHLIGHT_CHECK_LEFT  * _s)
        self.highlight_check_right = int(config.HIGHLIGHT_CHECK_RIGHT * _s)
        self.scrollbar_col_left    = int(config.SCROLLBAR_COL_LEFT    * _s)
        self.scrollbar_col_right   = int(config.SCROLLBAR_COL_RIGHT   * _s)
        self.scrollbar_row_top     = int(config.SCROLLBAR_ROW_TOP     * _s)
        self.scrollbar_row_bottom  = int(config.SCROLLBAR_ROW_BOTTOM  * _s)
        self._wrap_tol             = int(15                           * _s)
        self._scrollbar_tilt       = config.SCROLLBAR_TILT_DEGREES

    # ── Screenshot ────────────────────────────────────────────────────────────

    def take_screenshot(self, filename: str = None) -> str:
        import pyautogui
        if filename is None:
            filename = f"vsds_{time.strftime('%Y%m%d_%H%M%S')}.png"
        path = os.path.join(config.SCREENSHOTS_DIR, filename)
        pyautogui.screenshot().save(path)
        return path

    def _capture_panel(self, save_scrollbar_strip: bool = False) -> tuple:
        """
        Take a full screenshot, crop to the nav panel, deskew, then overwrite
        the saved file with the small deskewed panel crop (much easier to inspect
        than the full 5120×1440 image).  The raw (pre-deskew) crop is also saved
        as '<name>_raw.png'.

        If save_scrollbar_strip is True, also saves an extract of
        the scrollbar measurement region (taken from the RAW panel, where the
        scrollbar is nearly vertical) as '<name>_sb.png'.

        Returns (deskewed, panel_bgr) — both as BGR numpy arrays.
        """
        path      = self.take_screenshot()
        panel_bgr = self.crop_nav_panel(path)
        deskewed  = self.deskew(panel_bgr)
        # Save raw panel (before deskew) as _raw.png — scrollbar is vertical here.
        cv2.imwrite(path.replace('.png', '_raw.png'), panel_bgr)
        cv2.imwrite(path, deskewed)          # replace full screenshot with deskewed crop
        if save_scrollbar_strip:
            # Apply the same scrollbar-specific rotation used in measure_scrollbar,
            # then extract the strip — so _sb.png matches exactly what's measured.
            h, w = panel_bgr.shape[:2]
            M = cv2.getRotationMatrix2D((w / 2, h / 2), self._scrollbar_tilt, 1.0)
            panel_rot = cv2.warpAffine(panel_bgr, M, (w, h), borderValue=(0, 0, 0))
            # Save the full rotated panel so the scrollbar region can be verified.
            cv2.imwrite(path.replace('.png', '_sbpanel.png'), panel_rot)
            sb = panel_rot[
                self.scrollbar_row_top:self.scrollbar_row_bottom,
                self.scrollbar_col_left:self.scrollbar_col_right,
            ]
            cv2.imwrite(path.replace('.png', '_sb.png'), sb)
        return deskewed, panel_bgr

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
            self.list_top : self.list_bottom,
            self.highlight_check_left : self.highlight_check_right,
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

        row_top    = self.list_top + run_start
        row_bottom = self.list_top + run_end + 1
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
          1. Detect the highlighted (selected) entry via brightness
          2. OCR dark-background rows below the highlight (avoids amber noise)
          3. OCR the highlighted row separately with THRESH_BINARY_INV
          4. Combine and clean
        Returns (count, [name, ...]).
        """
        found_hl, hl_top, hl_bottom = self._find_highlighted_entry(deskewed)

        # OCR the dark-background entries only.  Start below the highlighted
        # row (if found) so its amber background doesn't produce garbled text
        # that slips through _clean_names and inflates the count.
        ocr_top = hl_bottom if found_hl else self.list_top
        list_crop = deskewed[
            ocr_top : self.list_bottom,
            self.list_left : self.list_right,
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
            hl_crop = deskewed[hl_top:hl_bottom, self.list_left:self.list_right]
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

    def measure_scrollbar(self, panel_raw: np.ndarray,
                          debug_prefix: str = None) -> dict:
        """
        Detect the scrollbar thumb by HSV orange-colour detection.

        Applies SCROLLBAR_TILT_DEGREES rotation to the raw panel, then finds
        orange/amber pixels within the configured column/row strip.

        Returns a dict with:
            thumb_h     - height of the detected orange thumb (px); 0 if not found
            track_h     - total track height (px)
            peak_val    - 100.0 if thumb detected, 0.0 if not
                          (< 10 → short list / no scrollbar; used in scan_with_scroll)
            peak_row    - absolute row of thumb top
            track_start - absolute row of thumb top
            track_end   - absolute row of thumb bottom
            bright_sum  - len(orange_rows) * 100  (kept for CSV compatibility)
            profile     - [(row, 100.0|0.0), ...]  (kept for CSV compatibility)
        """
        h, w = panel_raw.shape[:2]
        M = cv2.getRotationMatrix2D((w / 2, h / 2), self._scrollbar_tilt, 1.0)
        panel_rot = cv2.warpAffine(panel_raw, M, (w, h), borderValue=(0, 0, 0))

        strip = panel_rot[
            self.scrollbar_row_top:self.scrollbar_row_bottom,
            self.scrollbar_col_left:self.scrollbar_col_right,
        ]

        # Detect the orange/amber thumb by hue.
        # OpenCV HSV: H 0-179 (×2 = standard degrees), S/V 0-255.
        # ED's scrollbar amber: roughly H 10-30, S > 60, V > 80.
        hsv  = cv2.cvtColor(strip, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv,
                           np.array([10,  60,  80]),
                           np.array([30, 255, 255]))

        # Count orange pixels per row across the strip width.
        row_orange_count = (mask > 0).sum(axis=1)

        # Any orange in a row → scrollbar present (used for short-list detection).
        row_has_orange = row_orange_count > 0
        orange_idx     = np.where(row_has_orange)[0]

        # Thick-thumb rows: must have at least half the strip width orange.
        # The thick thumb fills ~8 of 14 columns; the thin indicator line fills
        # only 1–2 columns.  This threshold separates them cleanly.
        strip_w        = strip.shape[1]
        thick_min      = max(3, strip_w // 2)
        row_is_thick   = row_orange_count >= thick_min
        thick_idx      = np.where(row_is_thick)[0]

        track_h = self.scrollbar_row_bottom - self.scrollbar_row_top

        # First contiguous run of thick rows = the thumb.
        first_top = first_bot = first_len = 0
        if len(thick_idx) >= 1:
            run_start = run_end = int(thick_idx[0])
            for idx in thick_idx[1:]:
                if int(idx) == run_end + 1:
                    run_end = int(idx)
                else:
                    break
            first_top = run_start
            first_bot = run_end
            first_len = run_end - run_start

        if first_len >= 2:
            thumb_top_rel = first_top
            thumb_bot_rel = first_bot
            thumb_h  = first_len
            peak_val = 100.0
        elif len(orange_idx) > 0:
            # Scrollbar present but thumb too thin to measure — treat as no signal.
            thumb_top_rel = thumb_bot_rel = 0
            thumb_h  = 0
            peak_val = 100.0   # still a scrollbar, just can't estimate count
        else:
            thumb_top_rel = thumb_bot_rel = 0
            thumb_h  = 0
            peak_val = 0.0

        abs_top = self.scrollbar_row_top + thumb_top_rel
        abs_bot = self.scrollbar_row_top + thumb_bot_rel

        row_vals = np.where(row_has_orange, 100.0, 0.0)
        profile  = [(self.scrollbar_row_top + i, float(row_vals[i]))
                    for i in range(len(row_vals))]

        if config.SAVE_DEBUG_IMAGES and debug_prefix:
            cv2.imwrite(os.path.join(config.DEBUG_DIR,
                                     f"{debug_prefix}_scrollbar.png"), strip)
            cv2.imwrite(os.path.join(config.DEBUG_DIR,
                                     f"{debug_prefix}_scrollbar_mask.png"), mask)

        return {
            'thumb_h':     thumb_h,
            'track_h':     track_h,
            'peak_val':    peak_val,
            'peak_row':    abs_top,
            'track_start': abs_top,
            'track_end':   abs_bot,
            'bright_sum':  float(len(orange_idx) * 100),
            'profile':     profile,
        }

    # ── Max-distance from _end screenshot ────────────────────────────────────

    # Common OCR character confusions in amber-background (BINARY_INV) crops.
    # Digit-like letters that Tesseract confuses with digits when reading
    # dark text on a bright amber background.
    # Note: '8' → '0' was removed — it destroyed real '8' digits (e.g. 16.8, 18.8).
    # Slashed zeros (Ø) are read correctly as '0' by Tesseract without any help.
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
        pad = int(4 * (self.screen_h / config.EXPECTED_SCREEN_HEIGHT))
        crop_top = max(0, hl_top - pad)
        dist_crop = deskewed[crop_top:hl_bottom, self.dist_col_left:self.dist_col_right]
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

        raw = pytesseract.image_to_string(
            Image.fromarray(binary),
            config='--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789.,')

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

    def read_last_normal_distance(self, deskewed: np.ndarray,
                                  debug_prefix: str | None = None) -> float | None:
        """
        Read the distance of the LAST (unselected) entry when the second-to-last
        entry is highlighted.

        After navigating to entry N-1 (one UP press from entry N), entry N appears
        as the last visible row with its normal orange-on-dark appearance.
        Uses standard THRESH_BINARY OCR — same reliable path as all other entries.

        Returns the distance as a float, or None if not found (e.g. N=1).
        """
        import re

        found, hl_top, hl_bottom = self._find_highlighted_entry(deskewed)
        if not found:
            return None

        row_h = max(1, hl_bottom - hl_top)
        pad = int(8 * (self.screen_h / config.EXPECTED_SCREEN_HEIGHT))
        last_top = max(0, hl_bottom - pad)
        last_bot = min(self.list_bottom, hl_bottom + row_h)

        if last_top >= last_bot:
            return None  # N=1: no row below the only highlighted entry

        dist_crop = deskewed[last_top:last_bot, self.dist_col_left:self.dist_col_right]
        if dist_crop.size == 0:
            return None

        h, w = dist_crop.shape[:2]
        up = cv2.resize(dist_crop, (w * config.OCR_UPSCALE_FACTOR,
                                    h * config.OCR_UPSCALE_FACTOR),
                        interpolation=cv2.INTER_CUBIC)
        gray = cv2.cvtColor(up, cv2.COLOR_BGR2GRAY)
        # OTSU picks the best threshold for the bimodal dark-bg / orange-text histogram,
        # instead of a fixed value that can fragment characters when brightness varies.
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)

        if config.SAVE_DEBUG_IMAGES and debug_prefix:
            cv2.imwrite(os.path.join(config.DEBUG_DIR, f"{debug_prefix}_dist_crop.png"), dist_crop)
            Image.fromarray(binary).save(
                os.path.join(config.DEBUG_DIR, f"{debug_prefix}_dist_bin.png"))

        raw = pytesseract.image_to_string(
            Image.fromarray(binary),
            config='--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789.,')

        raw_clean = raw.strip().replace(' ', '')
        raw_clean = re.sub(r'\.{2,}', '.', raw_clean)

        m = re.search(r'(\d{1,4}[.,]\d)', raw_clean)
        if m:
            val = float(m.group(0).replace(',', '.'))
            if val <= 20.5:
                return val
            # Nav panel never shows systems beyond 20 ly — value is invalid.
            # Slashed zero (Ø) misread as '8' is the typical cause (e.g. 20.8 → 20.0).
            # Try replacing all '8's with '0' and re-evaluate.
            corrected = float(m.group(0).replace(',', '.').replace('8', '0'))
            if corrected <= 20.5:
                return corrected
            # Still out of range (e.g. 4x.x from top-clipped '1') → fall through to fallback.
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
            hl_crop = deskewed[hl_top:hl_bottom, self.list_left:self.list_right]
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

        WRAP_TOL = self._wrap_tol   # pixels: hl_top must be within this of hl_top_init

        # ── Initial screenshot + full OCR ─────────────────────────────────
        # Saves panel crop + scrollbar strip to screenshots/ for easy inspection.
        # panel_bgr is the RAW (pre-deskew) crop — used for scrollbar measurement
        # because the scrollbar is nearly vertical there (diagonal after deskew).
        deskewed, panel_bgr = self._capture_panel(save_scrollbar_strip=True)

        visible_count, system_names = self.count_visible_entries(
            deskewed, debug_prefix and f"{debug_prefix}_init")
        scrollbar = self.measure_scrollbar(
            panel_bgr, debug_prefix and f"{debug_prefix}_init")
        found_hl, hl_top_init, _ = self._find_highlighted_entry(deskewed)

        print(f"  Scrollbar init: peak={scrollbar['peak_val']:.0f}"
              f"  thumb_h={scrollbar['thumb_h']}  track_h={scrollbar['track_h']}"
              f"  found_hl={found_hl}  visible_count={visible_count}")

        # ── Short list: everything fits on screen (no scrollbar signal) ───
        # peak_val < 10 means the scrollbar gradient is effectively absent.
        # total_count = visible_count from OCR.  Jump straight to entry N
        # by pressing UP once from entry 1 — the list wraps to the last
        # entry regardless of how many entries there are.
        if not found_hl or scrollbar['peak_val'] < 10:
            # Press UP twice: entry N → entry N-1 selected, entry N unselected at bottom.
            pydirectinput.press(config.SCROLL_KEY_UP)
            time.sleep(config.SCROLL_SETTLE_DELAY)
            pydirectinput.press(config.SCROLL_KEY_UP)
            time.sleep(config.SCROLL_SETTLE_DELAY)
            end_deskewed, _ = self._capture_panel()
            max_dist = self.read_last_normal_distance(
                end_deskewed, debug_prefix and f"{debug_prefix}_end")
            # Navigate forward to entry N for the confirmation prompt.
            pydirectinput.press(config.SCROLL_KEY)
            time.sleep(config.SCROLL_SETTLE_DELAY)
            if max_dist is None:
                # Fallback: N=1 (or normal-path OCR failed).
                # Read from entry N now highlighted — fresh screenshot, correct entry.
                n_deskewed, _ = self._capture_panel()
                max_dist = self.read_max_distance(n_deskewed)
            return {
                'visible_count':   visible_count,
                'total_count':     visible_count,
                'system_names':    system_names,
                'max_distance_ly': max_dist,
                'scrollbar':       scrollbar,
            }

        # ── Phase 1: move selection to bottom of visible window ───────────
        # visible_count − 1 presses: no wrap is possible in this range.
        for _ in range(visible_count - 1):
            pydirectinput.press(config.SCROLL_KEY)
            time.sleep(config.SCROLL_PRESS_DELAY)

        time.sleep(config.SCROLL_SETTLE_DELAY)
        self._capture_panel()

        # ── Bulk phase: jump most of the remaining distance without screenshots ──
        # Geometrically: thumb_h / track_h ≈ visible_count / total_count.
        # Leave BULK_SAFETY entries before the end for one-at-a-time wrap detection.
        BULK_SAFETY  = 8
        bulk_presses = 0
        est_total    = visible_count   # fallback if thumb not detected

        thumb_h = scrollbar['thumb_h']
        track_h = scrollbar['track_h']
        if thumb_h > 5 and track_h > 0:
            est_total    = round(visible_count * track_h / thumb_h)
            est_total    = max(visible_count, min(55, est_total))
            bulk_presses = max(0, est_total - visible_count - BULK_SAFETY)

        print(f"  Scrollbar bulk: thumb_h={thumb_h}  track_h={track_h}"
              f"  → est_total={est_total}  bulk={bulk_presses}")
        if bulk_presses > 0:
            for _ in range(bulk_presses):
                pydirectinput.press(config.SCROLL_KEY)
                time.sleep(config.SCROLL_PRESS_DELAY)
            time.sleep(config.SCROLL_SETTLE_DELAY)
            self._capture_panel()

        # ── Phase 2: scroll one entry at a time, detect wrap ─────────────
        presses = visible_count - 1 + bulk_presses

        while presses < 55:    # hard cap: nav panel shows at most 49 systems
            pydirectinput.press(config.SCROLL_KEY)
            presses += 1
            time.sleep(config.SCROLL_PRESS_DELAY + config.SCROLL_SETTLE_DELAY)

            curr_deskewed, _ = self._capture_panel()
            found, hl_top, _ = self._find_highlighted_entry(curr_deskewed)

            if found and abs(hl_top - hl_top_init) < WRAP_TOL:
                # List wrapped back to entry 1.
                break

        total_count = presses   # one full cycle = total entries

        # Navigate to entry N-1 so entry N appears as an unselected (normal) row.
        # Reading orange-on-dark is more reliable than dark-on-amber (BINARY_INV).
        # UP from entry 1 → entry N; UP again → entry N-1; entry N is last unselected.
        pydirectinput.press(config.SCROLL_KEY_UP)
        time.sleep(config.SCROLL_SETTLE_DELAY)
        pydirectinput.press(config.SCROLL_KEY_UP)
        time.sleep(config.SCROLL_SETTLE_DELAY)
        end_deskewed, _ = self._capture_panel()
        max_dist = self.read_last_normal_distance(
            end_deskewed,
            debug_prefix and f"{debug_prefix}_end")

        # Leave selection on entry N for the confirmation prompt.
        pydirectinput.press(config.SCROLL_KEY)
        time.sleep(config.SCROLL_SETTLE_DELAY)

        if max_dist is None:
            # Fallback: normal-path OCR failed (or N=1).
            # Read from entry N now highlighted — fresh screenshot, correct entry.
            n_deskewed, _ = self._capture_panel()
            max_dist = self.read_max_distance(n_deskewed)

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
