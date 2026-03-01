"""
Configuration for VSDS Helper
Elite Dangerous Vertical Stellar Density Scanner
"""

# ─── Paths ────────────────────────────────────────────────────────────────────

JOURNAL_DIR = r"C:\Users\celeg\Saved Games\Frontier Developments\Elite Dangerous"
SCREENSHOTS_DIR = "screenshots"
OUTPUT_DIR = "output"
DEBUG_DIR = "debug"
OUTPUT_FILE = "vsds_scans.csv"
SCROLLBAR_SAMPLES_FILE = "output/scrollbar_samples.csv"

# ─── Hotkeys ──────────────────────────────────────────────────────────────────

CAPTURE_HOTKEY = 'f9'    # triggers the full automated scan
QUIT_HOTKEY    = 'esc'

# ─── Auto-scroll ───────────────────────────────────────────────────────────────
# F9 auto-scrolls the nav panel using keypresses to count all entries.
SCROLL_KEY          = 's'    # key that moves the selection down one entry
SCROLL_PRESS_DELAY  = 0.02   # seconds between consecutive key presses
SCROLL_SETTLE_DELAY = 0.07   # seconds to wait after a press before screenshotting

# ─── Screen resolution ────────────────────────────────────────────────────────

# Reference resolution: 5120×1440 (32:9).
# X coordinates are stored in 16:9-zone-relative space at the reference height.
# The 32:9 screen has a centered 2560px-wide 16:9 zone with 1280px offset each side.
#
# Runtime conversion:
#   scale    = actual_height / EXPECTED_SCREEN_HEIGHT
#   x_offset = max(0, (actual_width - actual_height * 16/9) / 2)
#   actual_x = int(x_offset + cfg_x * scale)
#   actual_y = int(cfg_y * scale)

EXPECTED_SCREEN_HEIGHT = 1440

# ─── Nav panel — absolute pixels on the 5120×1440 reference screen ───────────
# Measured from "Screenshot 2026-02-28 121805.png"
# Raw screenshot coords: left=2006, top=545, right=3036, bottom=1148
# 16:9-zone-relative x = raw_x - 1280  (subtracting the 32:9 ultrawide offset)

NAV_PANEL_LEFT   = 726    # 2006 - 1280
NAV_PANEL_TOP    = 545
NAV_PANEL_RIGHT  = 1756   # 3036 - 1280
NAV_PANEL_BOTTOM = 1148
# Panel size at reference resolution: 1030 × 603 px

# ─── Deskew ───────────────────────────────────────────────────────────────────
# The nav panel is physically tilted in the cockpit view.
# Measured via Hough line detection on the panel crop.
NAV_PANEL_TILT_DEGREES = -6.034   # rotate crop by this angle to correct

# ─── Scrollbar — coordinates relative to the DESKEWED panel crop ──────────────
# Measured from the deskewed panel (1030 × 603 px at reference resolution).
# The scrollbar is a fading-gradient indicator on the right edge of the list.
#
# Column range (x within deskewed crop):
SCROLLBAR_COL_LEFT  = 1012
SCROLLBAR_COL_RIGHT = 1022   # 10 px wide strip
#
# Row range where the scrollbar can appear (y within deskewed crop):
SCROLLBAR_ROW_TOP    = 64    # first row with any scrollbar signal
SCROLLBAR_ROW_BOTTOM = 354   # last row with any scrollbar signal
#
# NOTE: The scrollbar is a decorative position indicator only.
# Empirical analysis (18 samples) shows no reliable correlation between
# any brightness metric and total system count.  Total count is obtained
# instead by auto-scrolling the list with keypresses (see SCROLL_KEY).

# ─── List area — coordinates relative to the DESKEWED panel crop ──────────────
# This is the region containing the system-name entries, used for OCR.
# Icon column is excluded (left edge), distance numbers and scrollbar excluded (right edge).
LIST_LEFT   = 70     # skip the circular icon on the left (icon ends ~col 65)
LIST_RIGHT  = 800    # stop before distance numbers (e.g. "7.61Ly") start
LIST_TOP    = 0      # entries begin at the very top of the crop
LIST_BOTTOM = 540    # last visible entry bottom; below this is cockpit frame

# Distance column — used when reading the highlighted entry's distance label
# (e.g. '13.6Ly' from a bottom-scrolled _end screenshot)
DIST_COL_LEFT  = 820   # distance numbers start here
DIST_COL_RIGHT = 990   # right edge of the distance field

# ─── Highlighted (selected) entry detection ───────────────────────────────────
# The selected entry has a bright amber background (dark text on bright bg).
# OCR misses it, so it is detected by scanning row means across the full list.
# Amber highlight rows have means ~120-150; normal dark-entry rows peak at ~40-50.
HIGHLIGHT_CHECK_LEFT  = 70    # x start for the row-mean scan (past the icon)
HIGHLIGHT_CHECK_RIGHT = 600   # x end for the row-mean scan
HIGHLIGHT_THRESHOLD   = 90    # row mean above this → part of the highlighted entry

# ─── OCR ──────────────────────────────────────────────────────────────────────

TESSERACT_PATH = None   # set to e.g. r'C:\Tools\Tesseract-OCR\tesseract.exe' if needed
OCR_UPSCALE_FACTOR = 2
OCR_THRESHOLD = 80      # binarisation threshold (0-255); ED orange text on dark bg

# ─── Debugging ────────────────────────────────────────────────────────────────

SAVE_DEBUG_IMAGES = True
