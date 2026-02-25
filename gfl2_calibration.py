"""
GFL2 Attachment Calibration Auto-Reroller
==========================================
Automatically rerolls attachment stats until the AVERAGE calibration
percentage across all stats meets your defined threshold.

How the average is calculated:
    Each stat shows a percentage in a box like:
        [Attack] increases by an additional 130%
        [Attack Boost] increases by an additional 80%
        [Crit Rate] increases by an additional 120%
    Average = (130 + 80 + 120) / 3 = 110%
    If your threshold is 130%, this roll would be RESTORED.

Requirements:
    pip install pyautogui pydirectinput pillow pytesseract mss numpy keyboard

You also need Tesseract OCR installed:
    Windows: https://github.com/UB-Mannheim/tesseract/wiki
    Set TESSERACT_PATH below if it's not on your system PATH.

Usage:
    python gfl2_calibration.py               # Run the reroller
    python gfl2_calibration.py --calibrate   # Interactive setup (hover + Enter to mark positions)
    python gfl2_calibration.py --test-ocr    # Test OCR reads without clicking
    python gfl2_calibration.py --find-coords # Raw coordinate hover display
"""

import pyautogui
import pydirectinput
import pytesseract
import mss
import numpy as np
import time
import sys
import threading
import json
import os
import keyboard
from PIL import Image

# pydirectinput sends inputs at the DirectInput level which games recognize.
# pyautogui is kept only for reading mouse position during calibration.
pyautogui.PAUSE = 0

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG FILE — Positions are saved here by --calibrate and loaded on startup
# ─────────────────────────────────────────────────────────────────────────────

CONFIG_FILE = "gfl2_config.json"


def load_config():
    """Load saved button and region positions from the config file if it exists."""
    if not os.path.exists(CONFIG_FILE):
        return None
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)


def save_config(buttons, regions, name_regions):
    """Persist button, region, and stat-name-region positions to the config file."""
    data = {"buttons": buttons, "regions": regions, "name_regions": name_regions}
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\n  Config saved to {CONFIG_FILE}")


def apply_config(cfg):
    """Overlay a loaded config onto the live BUTTONS, PERCENTAGE_REGIONS, and STAT_NAME_REGIONS dicts."""
    global BUTTONS, PERCENTAGE_REGIONS, STAT_NAME_REGIONS
    if "buttons" in cfg:
        BUTTONS.update({k: tuple(v) for k, v in cfg["buttons"].items()})
    if "regions" in cfg:
        PERCENTAGE_REGIONS.update({k: tuple(v) for k, v in cfg["regions"].items()})
    if "name_regions" in cfg:
        STAT_NAME_REGIONS.update({k: tuple(v) for k, v in cfg["name_regions"].items()})

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION — Edit this section to match your setup
# ─────────────────────────────────────────────────────────────────────────────

# Path to Tesseract executable
# Leave as None if tesseract is on your system PATH
TESSERACT_PATH = None
# TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# ── Stat Name → Internal Key Mapping ─────────────────────────────────────────
# Maps OCR'd text fragments from the stat name regions to internal stat keys.
# Add or adjust entries if the game uses different label text.
STAT_NAME_MAP = {
    "attack boost":    "attack_boost",
    "attack":          "attack",       # must come AFTER "attack boost"
    "crit rate":       "crit_rate",
    "critical rate":   "crit_rate",
    "crit damage":     "crit_damage",
    "critical damage": "crit_damage",
    "defense":         "defense",
    "hp":              "hp",
    "hit":             "hit",
    "evasion":         "evasion",
    "reload":          "reload",
}

# Does this attachment have 4 stats (including Crit Damage)?
HAS_CRIT_DAMAGE = True

# ── Average Threshold ─────────────────────────────────────────────────────────
# The script confirms a roll when the average of all stat percentages
# meets or exceeds this value.
# Example: threshold of 130 means the average % across all stats must be >= 130%
AVERAGE_THRESHOLD = 150  # percent

# ── Optional Per-Stat Minimums ────────────────────────────────────────────────
# Hard floor for individual stats. Keys must match the internal stat names from
# STAT_NAME_MAP (e.g. "attack", "attack_boost", "crit_rate", "crit_damage").
# Set any stat to None to ignore it.
PER_STAT_MINIMUMS = {
    "attack":       100,
    "attack_boost": 100,
    "crit_rate":    100,
    "crit_damage":  100,
}

# ── Timing (seconds) ──────────────────────────────────────────────────────────
DELAY_AFTER_QUICK_SELECT = 0.8
DELAY_AFTER_CALIBRATE    = 3.0
DELAY_AFTER_RESTORE      = 1.2
DELAY_BETWEEN_CLICKS     = 0.3

# ── Screen Regions ────────────────────────────────────────────────────────────
# These capture the percentage value inside each "[Stat] increases by..." box.
# The percentage is the NEW value shown after calibration (orange or white text).
# Format: (left, top, width, height) in pixels.
#
# Use --find-coords mode to identify these for your screen resolution.
# These regions target just the percentage number on the right of each box.

PERCENTAGE_REGIONS = {
    "slot_tl":      (638, 537, 80, 28),   # top-left stat percentage
    "slot_bl":      (638, 585, 80, 28),   # bottom-left stat percentage
    "slot_tr":      (1065, 537, 95, 28),  # top-right stat percentage
    "slot_br":      (1065, 585, 95, 28),  # bottom-right stat percentage
}

# Regions that capture the stat *name* text for each slot.
# Format: (left, top, width, height) in pixels.
# These should cover just the label, e.g. "Attack" or "Critical Damage".
# Use --calibrate to set these for your resolution.
STAT_NAME_REGIONS = {
    "slot_tl": (299, 447, 200, 28),   # top-left label
    "slot_bl": (299, 489, 200, 28),   # bottom-left label
    "slot_tr": (739, 447, 230, 28),   # top-right label
    "slot_br": (739, 489, 230, 28),   # bottom-right label
}

# ── Button Positions ──────────────────────────────────────────────────────────
BUTTONS = {
    "quick_select": (576, 1361),
    "calibrate":    (2161, 1348),
    "restore":      (1092, 1150),
    "confirm":      (1640, 1150),
}

# ── Safety ────────────────────────────────────────────────────────────────────
pyautogui.FAILSAFE = False

MAX_ATTEMPTS = 25  # Set to None for unlimited

PLAY_SOUND_ON_SUCCESS = False
SUCCESS_SOUND_PATH    = "success.wav"

# ── Global Stop Flag ──────────────────────────────────────────────────────────
# Set to True by the Ctrl+C global hotkey to cleanly stop the loop.
_stop_requested = threading.Event()


def _on_stop_hotkey():
    """Called by the global keyboard hook when Ctrl+C is pressed anywhere."""
    if not _stop_requested.is_set():
        print("\n\n  Ctrl+C detected — stopping after current action...\n")
        _stop_requested.set()


def register_global_hotkey():
    """Register Ctrl+C as a global hotkey that works even out of focus."""
    keyboard.add_hotkey("ctrl+c", _on_stop_hotkey, suppress=False)


def stop_requested():
    return _stop_requested.is_set()

# ─────────────────────────────────────────────────────────────────────────────
# CORE FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def setup_tesseract():
    if TESSERACT_PATH:
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH


def capture_region(region):
    with mss.mss() as sct:
        monitor = {
            "left":   region[0],
            "top":    region[1],
            "width":  region[2],
            "height": region[3],
        }
        raw = sct.grab(monitor)
    return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def preview_region(region, stat_name):
    """
    Save a screenshot of the region so the user can verify what Tesseract receives.
    """
    img = capture_region(region)
    img.save(f"preview_{stat_name}.png")
    print(f"      Preview saved: preview_{stat_name}.png")


def ocr_percentage(region, debug_name=None):
    """
    Capture a region, scale it up for better Tesseract recognition, and
    extract the number from the result.
    If debug_name is set, saves the raw image to disk for inspection.
    Returns a float or None on failure.
    """
    import re
    img = capture_region(region)

    if debug_name:
        img.save(f"debug_{debug_name}.png")

    # Scale up 4x — Tesseract reads small game text much more reliably at larger sizes
    w, h = img.size
    img = img.resize((w * 4, h * 4), Image.LANCZOS)

    config  = "--psm 6 -c tessedit_char_whitelist=0123456789"
    raw     = pytesseract.image_to_string(img, config=config)
    # Only keep numbers with 2+ digits — valid values are 10-200,
    # single digits are always misreads of the % symbol
    numbers = [n for n in re.findall(r"\d+", raw) if len(n) >= 2]

    if not numbers:
        return None

    try:
        value = float(max(numbers, key=lambda n: int(n)))
        if value < 10 or value > 200:
            return None
        return value
    except ValueError:
        return None


def ocr_stat_name(region, debug_name=None):
    """
    Capture a stat label region and return the cleaned lowercase text.
    Returns an empty string on failure.
    """
    img = capture_region(region)

    if debug_name:
        img.save(f"debug_name_{debug_name}.png")

    # Scale up for better OCR on small game text
    w, h = img.size
    img = img.resize((w * 4, h * 4), Image.LANCZOS)

    config = "--psm 7"   # single text line
    raw = pytesseract.image_to_string(img, config=config)
    return raw.strip().lower()


def match_stat_name(raw_text):
    """
    Fuzzy-match OCR'd label text to an internal stat key.
    Checks each entry in STAT_NAME_MAP (longest first to avoid 'attack'
    matching before 'attack boost'). Returns None if nothing matches.
    """
    text = raw_text.lower()
    # Sort by length descending so longer (more specific) keys are tried first
    for label in sorted(STAT_NAME_MAP.keys(), key=len, reverse=True):
        if label in text:
            return STAT_NAME_MAP[label]
    return None


# Cache so we only OCR names once per calibration screen visit
_detected_stats = None


def detect_stat_slots(debug=False):
    """
    Read all four stat name regions and return an ordered list of
    (slot_key, internal_stat_key) pairs for slots that were recognised.
    Falls back to the HAS_CRIT_DAMAGE default layout on total failure.
    """
    global _detected_stats
    if _detected_stats is not None:
        return _detected_stats

    slots = ["slot_tl", "slot_bl", "slot_tr", "slot_br"]
    result = []
    any_success = False

    for slot in slots:
        region = STAT_NAME_REGIONS.get(slot)
        if region is None:
            continue
        raw = ocr_stat_name(region, debug_name=slot if debug else None)
        key = match_stat_name(raw)
        if key:
            result.append((slot, key))
            any_success = True
        else:
            print(f"  WARNING: Could not identify stat name for {slot} (OCR: '{raw}')")

    if not any_success:
        # Fall back to the old static layout
        print("  WARNING: Stat name OCR failed for all slots — using default layout.")
        default = ["attack", "attack_boost", "crit_rate"]
        if HAS_CRIT_DAMAGE:
            default.append("crit_damage")
        result = list(zip(slots, default))

    _detected_stats = result
    return result


def reset_stat_detection():
    """Call this between calibration cycles so names are re-read each roll."""
    global _detected_stats
    _detected_stats = None




def read_percentage_with_retry(slot_key, stat_name, attempts=3, debug=False):
    """
    Try to read a percentage up to `attempts` times.
    Returns 0.0 on persistent failure (safe default = bad roll).
    """
    region = PERCENTAGE_REGIONS[slot_key]
    for attempt in range(1, attempts + 1):
        debug_name = stat_name if (debug and attempt == 1) else None
        value = ocr_percentage(region, debug_name=debug_name)
        if value is not None:
            return value
        if attempt < attempts:
            time.sleep(0.25)

    print(f"  WARNING: Could not read '{stat_name}' after {attempts} attempts — treating as 0%")
    return 0.0


def get_active_stats():
    """Return list of stat keys that are active for this attachment (detected dynamically)."""
    return [stat for _, stat in detect_stat_slots()]


def read_all_percentages(debug=False):
    """Read all calibration percentages and return as a dict keyed by stat name."""
    slots = detect_stat_slots(debug=debug)
    return {stat: read_percentage_with_retry(slot, stat, debug=debug) for slot, stat in slots}


def evaluate_roll(percentages):
    """
    Evaluate a roll against the average threshold and per-stat minimums.

    Returns:
        (passes: bool, average: float, breakdown: str)
    """
    values  = list(percentages.values())
    average = sum(values) / len(values)

    # Check average threshold
    passes = average >= AVERAGE_THRESHOLD

    # Check per-stat minimums (if any are set)
    per_stat_failures = []
    for stat, value in percentages.items():
        minimum = PER_STAT_MINIMUMS.get(stat)
        if minimum is not None and value < minimum:
            passes = False
            per_stat_failures.append(f"{stat}={value}% (min {minimum}%)")

    # Build a readable breakdown string
    stat_parts = [f"{s}={v}%" for s, v in percentages.items()]
    breakdown  = f"avg={average:.1f}% | " + " | ".join(stat_parts)
    if per_stat_failures:
        breakdown += f" | FLOOR FAIL: {', '.join(per_stat_failures)}"

    return passes, average, breakdown


def click(button_name):
    """
    Move to a button and click it using pydirectinput.
    pydirectinput sends DirectInput events that games recognize,
    unlike pyautogui which many games ignore.
    """
    x, y = BUTTONS[button_name]
    pydirectinput.moveTo(x, y)
    time.sleep(0.05)  # brief pause after move before clicking
    pydirectinput.click(x, y)
    time.sleep(DELAY_BETWEEN_CLICKS)


def notify_success(percentages, average):
    print("\n" + "═" * 60)
    print("GOOD ROLL FOUND — Script stopped")
    print("═" * 60)
    print(f"  Average : {average:.1f}%  (threshold: {AVERAGE_THRESHOLD}%)")
    for stat, value in percentages.items():
        print(f"  {stat:15s}: {value}%")
    print("═" * 60)
    print("\n  The result screen is open. Click Confirm to keep it.\n")

    if PLAY_SOUND_ON_SUCCESS:
        try:
            from playsound import playsound
            playsound(SUCCESS_SOUND_PATH)
        except Exception as e:
            print(f"  (Could not play sound: {e})")


# ─────────────────────────────────────────────────────────────────────────────
# INTERACTIVE CALIBRATION
# ─────────────────────────────────────────────────────────────────────────────

def wait_for_enter(prompt):
    """
    Print a prompt, then block until Enter is pressed globally.
    Returns the mouse position at the moment Enter was pressed, or None if
    Escape was pressed to cancel.
    """
    print(f"\n  {prompt}")
    print("      Hover your mouse over the target, then press  Enter  to confirm.")
    print("      Press  Escape  to cancel the whole calibration.\n")

    done      = threading.Event()
    result    = {"pos": None, "cancelled": False}

    def on_enter():
        result["pos"] = pyautogui.position()
        done.set()

    def on_escape():
        result["cancelled"] = True
        done.set()

    # Register temporary one-shot global hotkeys
    keyboard.add_hotkey("enter",  on_enter,  suppress=True)
    keyboard.add_hotkey("escape", on_escape, suppress=True)

    done.wait()

    keyboard.remove_hotkey("enter")
    keyboard.remove_hotkey("escape")

    if result["cancelled"]:
        return None

    x, y = result["pos"]
    print(f"      Captured x={x}, y={y}")
    return (x, y)


def capture_two_corners(label):
    """
    Ask the user to hover-and-Enter on the top-left then bottom-right corner
    of a region. Returns (left, top, width, height) or None on cancel.
    """
    p1 = wait_for_enter(f"Hover over the TOP-LEFT corner of the {label} number.")
    if p1 is None:
        return None

    p2 = wait_for_enter(f"Hover over the BOTTOM-RIGHT corner of the {label} number.")
    if p2 is None:
        return None

    left   = min(p1[0], p2[0])
    top    = min(p1[1], p2[1])
    width  = abs(p2[0] - p1[0])
    height = abs(p2[1] - p1[1])
    region = (left, top, width, height)
    print(f"      Region: left={left}, top={top}, width={width}, height={height}")
    return region



def interactive_calibrate():
    """
    Walk the user through defining every button position and stat region by
    hovering the mouse and pressing Enter.
    Saves results to gfl2_config.json for automatic loading on future runs.
    """
    print("\n" + "═" * 60)
    print("  GFL2 Interactive Calibration Setup")
    print("═" * 60)
    print("""
  HOW IT WORKS:
    - Hover your mouse over the target element in-game.
    - Press  Enter  (anywhere — terminal does not need focus).
    - The script records your mouse position at that moment.
    - Press  Escape  at any prompt to abort.

  You will be asked to define:
    BUTTONS  — hover over the centre of each button and press Enter.
    REGIONS  — hover over the TOP-LEFT corner, Enter, then the
               BOTTOM-RIGHT corner, Enter, for each percentage number.

  TIP: For regions, aim for just the number itself (e.g. "130"),
  not the arrow or percent sign — a tight box gives better OCR.
""")
    input("  Press Enter here to begin (terminal must be focused just for this)...\n")

    buttons = {}
    regions = {}

    # ── BUTTONS ───────────────────────────────────────────────────────────────
    print("─" * 60)
    print("  PART 1 — Buttons")
    print("  Navigate to the calibration screen in-game (before clicking Calibrate).")
    print("─" * 60)

    button_steps = [
        ("quick_select", "the QUICK SELECT button"),
        ("calibrate",    "the CALIBRATE button"),
        ("restore",      "the RESTORE button  (do one manual calibration first\n"
                         "                     so this button is visible, then come back)"),
        ("confirm",      "the CONFIRM button"),
    ]

    for key, label in button_steps:
        pos = wait_for_enter(f"Hover over the centre of {label}.")
        if pos is None:
            print("\n  Calibration cancelled — nothing was saved.")
            return
        buttons[key] = list(pos)

    # ── REGIONS ───────────────────────────────────────────────────────────────
    print("\n" + "─" * 60)
    print("  PART 2 — Stat Name Regions")
    print("  Trigger a calibration so the result screen is showing.")
    print("  You will mark the top-left and bottom-right of each stat LABEL")
    print("  (the text like 'Attack', 'Crit Rate', not the number).")
    print("─" * 60)

    slot_labels_name = {
        "slot_tl": "TOP-LEFT stat label     (e.g. 'Attack')",
        "slot_bl": "BOTTOM-LEFT stat label  (e.g. 'Critical Damage')",
        "slot_tr": "TOP-RIGHT stat label    (e.g. 'Crit Rate')",
        "slot_br": "BOTTOM-RIGHT stat label (e.g. 'Attack Boost')",
    }

    name_regions = {}
    for slot, label in slot_labels_name.items():
        print(f"\n  ── {label}")
        region = capture_two_corners(label)
        if region is None:
            print("\n  Calibration cancelled — nothing was saved.")
            return
        name_regions[slot] = list(region)

        # Quick OCR test
        preview_region(region, f"name_{slot}")
        raw = ocr_stat_name(region)
        key = match_stat_name(raw)
        if key:
            print(f"      OCR test: '{raw}' → {key}  OK")
        else:
            print(f"      OCR test: '{raw}' → unrecognised  WARNING")
            print(f"      Check preview_name_{slot}.png — adjust STAT_NAME_MAP if needed.")

    # ── PERCENTAGE REGIONS ────────────────────────────────────────────────────
    print("\n" + "─" * 60)
    print("  PART 3 — Stat Percentage Regions")
    print("  Mark the top-left and bottom-right of each percentage NUMBER")
    print("  in the coloured boxes (e.g. the '130' part of '► 130% ↑').")
    print("─" * 60)

    slot_labels_pct = {
        "slot_tl": "TOP-LEFT percentage     (left column, top box)",
        "slot_bl": "BOTTOM-LEFT percentage  (left column, bottom box)",
        "slot_tr": "TOP-RIGHT percentage    (right column, top box)",
        "slot_br": "BOTTOM-RIGHT percentage (right column, bottom box)",
    }

    active_stats = ["attack", "attack_boost", "crit_rate"]
    if HAS_CRIT_DAMAGE:
        active_stats.append("crit_damage")

    stat_labels = {
        "attack":       "ATTACK percentage       (left column, top box)",
        "attack_boost": "ATTACK BOOST percentage  (left column, bottom box)",
        "crit_rate":    "CRIT RATE percentage    (right column, top box)",
        "crit_damage":  "CRIT DAMAGE percentage   (right column, bottom box)",
    }

    regions = {}
    for slot, label in slot_labels_pct.items():
        print(f"\n  ── {label}")
        region = capture_two_corners(label)
        if region is None:
            print("\n  Calibration cancelled — nothing was saved.")
            return
        regions[slot] = list(region)

        # Quick OCR test so user knows immediately if the region is good
        preview_region(region, slot)
        val = ocr_percentage(region)
        if val is not None:
            print(f"      OCR test: {val}%  OK")
        else:
            print(f"      OCR test: could not read a number  WARNING")
            print(f"      Check preview_{slot}.png — if it looks wrong, re-run --calibrate.")

    # ── SAVE ──────────────────────────────────────────────────────────────────
    save_config(buttons, regions, name_regions)

    print("\n" + "═" * 60)
    print("  All done! Run the script normally to start rerolling.")
    print("  Re-run --calibrate any time you move the game window.")
    print("═" * 60 + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# UTILITY MODES
# ─────────────────────────────────────────────────────────────────────────────

def coordinate_finder():
    print("\n── Coordinate Finder Mode ──────────────────────────────")
    print("  Hover over each button or stat region and note the (x, y).")
    print("  Press Ctrl+C to exit.\n")
    try:
        while True:
            x, y = pyautogui.position()
            print(f"  Mouse: x={x:4d}, y={y:4d}", end="\r")
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n\nExiting coordinate finder.")


def test_ocr():
    print("\n── OCR Test Mode ───────────────────────────────────────")
    print("  Open the Calibration Successful screen in-game, then")
    print("  press Enter here to capture and read the percentages.\n")
    input("  Press Enter when ready...")

    print("\n  Reading stat names from the label regions...")
    reset_stat_detection()
    slots = detect_stat_slots(debug=True)
    print(f"  Detected stats: {[f'{slot}={stat}' for slot, stat in slots]}")

    print("\n  Reading percentages from the stat boxes...")
    percentages                = read_all_percentages(debug=True)
    passes, average, breakdown = evaluate_roll(percentages)

    print(f"\n  {breakdown}")
    print(f"\n  Would {'CONFIRM OK' if passes else 'RESTORE FAIL'} this roll.")
    print(f"  (Threshold: {AVERAGE_THRESHOLD}%)")
    print(f"\n  Debug images saved:")
    for slot, stat in detect_stat_slots():
        print(f"    debug_{stat}.png  (name: debug_name_{slot}.png)")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────────────────────────────────────

def run():
    register_global_hotkey()

    print("\n" + "═" * 60)
    print("  GFL2 Calibration Auto-Reroller")
    print("═" * 60)
    print(f"  Average threshold : {AVERAGE_THRESHOLD}%")
    print(f"  Stats tracked     : {', '.join(get_active_stats())}")
    print(f"  Per-stat minimums : {PER_STAT_MINIMUMS}")
    print(f"  Max attempts      : {MAX_ATTEMPTS if MAX_ATTEMPTS else 'unlimited'}")
    print("\n  Failsafes:")
    print("       Ctrl+C anywhere  — graceful stop (finishes current action)")
    print("       MAX_ATTEMPTS cap — hard stop after N attempts")
    print("  Starting in 5 seconds — switch to the game now!\n")
    time.sleep(5)

    attempt = 0

    while True:
        # ── Check stop flag before starting a new attempt ─────────────────────
        if stop_requested():
            print("  Stopped by user request. Game screen left untouched.\n")
            break

        attempt += 1
        if MAX_ATTEMPTS and attempt > MAX_ATTEMPTS:
            print(f"\n  Reached maximum attempts ({MAX_ATTEMPTS}). Stopping.")
            break

        reset_stat_detection()   # re-read stat names for this roll
        print(f"  Attempt #{attempt}...", end=" ", flush=True)

        # Step 1: Quick Selection
        click("quick_select")
        time.sleep(DELAY_AFTER_QUICK_SELECT)

        if stop_requested():
            print("\n  Stopped during Quick Selection. Game screen left untouched.\n")
            break

        # Step 2: Calibrate
        click("calibrate")
        time.sleep(DELAY_AFTER_CALIBRATE)

        if stop_requested():
            print("\n  Stopped after Calibrate. Result screen is open — decide manually.\n")
            break

        # Step 3: Read percentages from the stat boxes
        percentages                = read_all_percentages()
        passes, average, breakdown = evaluate_roll(percentages)

        print(breakdown)

        # Step 3a / 3b: Restore or Confirm
        if passes:
            notify_success(percentages, average)
            break
        else:
            click("restore")
            time.sleep(DELAY_AFTER_RESTORE)

    print("  Script finished.\n")


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    setup_tesseract()
    args = sys.argv[1:]

    if "--calibrate" in args:
        interactive_calibrate()

    elif "--find-coords" in args:
        coordinate_finder()

    elif "--test-ocr" in args:
        cfg = load_config()
        if cfg:
            apply_config(cfg)
            print(f"  Loaded positions from {CONFIG_FILE}")
        else:
            print(f"  WARNING: No config file found — using hardcoded defaults.")
            print(f"     Run --calibrate first for accurate results.")
        test_ocr()

    else:
        cfg = load_config()
        if cfg:
            apply_config(cfg)
            print(f"  Loaded positions from {CONFIG_FILE}")
        else:
            print(f"  WARNING: No config file found ({CONFIG_FILE}).")
            print(f"     Run --calibrate first to set up your screen positions.")
            print(f"     Falling back to hardcoded defaults (likely won't work).\n")
        run()
