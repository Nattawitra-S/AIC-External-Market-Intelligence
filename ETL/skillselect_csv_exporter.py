"""
skillselect_csv_exporter.py
============================
Playwright automation: SkillSelect wizard → Export CSV (loop for each column pair)
Australian Centre of English (AIC) - Market Intelligence Project

HOW IT WORKS:
    1. Opens SkillSelect wizard (starts on Dashboard Overview)
    2. Clicks Next → EOI Parameters sheet
       - Visa Type / EOI Status / As At Month are PRE-SELECTED at top (not interactive filters)
    3. For each column pair:
       a. Click "Yes" under col1 tile
       b. Click "Yes" under col2 tile
       c. Click Next → Dashboard Results Table
       d. Export CSV
       e. Go Back → deselect tiles → repeat for next pair
    4. Saves to raw_data/skillselect_exports/

USAGE:
    python ETL/skillselect_csv_exporter.py
    python ETL/skillselect_csv_exporter.py --month "06/2026" --visa 189 --status Submitted

OUTPUT:
    raw_data/skillselect_exports/
    ├── 2026-06_189_Submitted_Occupations_Points.csv
    ├── 2026-06_189_Submitted_Occupations_English.csv
    ├── 2026-06_189_Submitted_Occupations_State.csv
    ├── 2026-06_189_Submitted_Occupations_AustralianStudy.csv
    └── 2026-06_189_Submitted_Points_English.csv
"""

import argparse
import asyncio
import re
import shutil
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright, Page, Download

# ── Config ──────────────────────────────────────────────────────────────────
SKILLSELECT_URL = (
    "https://api.dynamic.reports.employment.gov.au/anonap/extensions/"
    "hSKLS02_SkillSelect_EOI_Data/hSKLS02_SkillSelect_EOI_Data.html"
)
OUTPUT_DIR = Path(__file__).parent.parent / "raw_data" / "skillselect_exports"

# ── Column pairs to export (in priority order) ─────────────────────────────
# Format: (label_in_ui_col1, label_in_ui_col2, short_name_for_filename)
COLUMN_PAIRS = [
    # Priority 1 — Must have
    ("Occupations",        "Points",             "Occupations_Points"),
    ("Occupations",        "English Test Score",  "Occupations_English"),
    ("Occupations",        "Nominated State",     "Occupations_State"),
    ("Occupations",        "Occupation Groups",   "Occupations_Groups"),
    # Priority 2
    ("Points",             "English Test Score",  "Points_English"),
    ("Occupations",        "Australian Study",    "Occupations_AustralianStudy"),
    ("Occupations",        "Regional Study",      "Occupations_RegionalStudy"),
    # Priority 3 (optional — comment out to skip)
    # ("Occupations",      "Professional Year",   "Occupations_ProfYear"),
    # ("Occupations",      "Community Language",  "Occupations_CommunityLang"),
]

# ── Fixed context defaults ──────────────────────────────────────────────────
DEFAULT_MONTH  = None   # None = use latest available month (first option in dropdown)
DEFAULT_VISA   = "189"  # "189" | "190" | "491" | "All"
DEFAULT_STATUS = "Submitted"  # "Submitted" | "Active" | "All"


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════

DEBUG_DIR = Path(__file__).parent.parent / "raw_data" / "skillselect_debug"

def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", s)


async def screenshot(page: Page, label: str):
    """Save a debug screenshot."""
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    ts  = datetime.now().strftime("%H%M%S")
    out = DEBUG_DIR / f"{ts}_{safe_name(label)}.png"
    await page.screenshot(path=str(out), full_page=False)
    print(f"    📸 {out.name}")
    return out


async def dump_iframe_html(page: Page, label: str):
    """
    Dump the HTML INSIDE iframe#f1 — page.content() only shows the outer shell.
    This is the only way to see Qlik filter/listbox DOM structure.
    """
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    ts  = datetime.now().strftime("%H%M%S")
    out = DEBUG_DIR / f"{ts}_{safe_name(label)}_iframe.html"
    try:
        html = await page.evaluate(
            "() => { const f = document.querySelector('iframe#f1'); "
            "return f ? f.contentDocument.documentElement.outerHTML : 'iframe not found'; }"
        )
        out.write_text(html, encoding="utf-8")
        print(f"    🗒  iframe HTML → {out.name}")
    except Exception as e:
        print(f"    ⚠️  Could not dump iframe HTML: {e}")
    return out


async def dump_html(page: Page, label: str):
    """Save full page HTML for DOM inspection on failure."""
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    ts  = datetime.now().strftime("%H%M%S")
    out = DEBUG_DIR / f"{ts}_{safe_name(label)}.html"
    out.write_text(await page.content(), encoding="utf-8")
    print(f"    🗒  HTML → {out.name}")
    return out


async def first_visible(root, text: str, exact: bool = False, timeout: int = 10000):
    """
    Return the first VISIBLE element whose text matches, searching inside `root`.
    `root` can be a Page or a FrameLocator — both support get_by_text().
    Raises TimeoutError if none found within timeout ms.
    """
    deadline = asyncio.get_event_loop().time() + timeout / 1000
    while True:
        locator = root.get_by_text(text, exact=exact)
        count   = await locator.count()
        for i in range(count):
            el = locator.nth(i)
            try:
                if await el.is_visible():
                    return el
            except Exception:
                pass
        if asyncio.get_event_loop().time() > deadline:
            raise TimeoutError(
                f"No visible element with text={text!r} found after {timeout}ms "
                f"({count} hidden matches in root)"
            )
        await asyncio.sleep(0.3)


def get_qlik_frame(page: Page):
    """
    The entire Qlik app is embedded inside <iframe id="f1">.
    page.get_by_text() only searches the outer shell and finds hidden navigation
    copies of filter labels.  All filter interactions must use this frame locator.
    Navigation buttons (Back/Next) are in the outer shell so use page directly.
    """
    return page.frame_locator("iframe#f1")


async def navigate_to_eoi_params(page: Page):
    """
    The outer shell starts on Dashboard Overview sheet.
    Click the outer Next link to switch iframe to EOI Parameters, then confirm
    by checking for any visible content inside the iframe.
    """
    print("    → Navigating: Dashboard Overview → EOI Parameters")
    await click_next_button(page)
    await asyncio.sleep(2)   # give iframe time to reload

    frame = get_qlik_frame(page)

    # Confirm iframe loaded by waiting for ANY visible text in it
    try:
        # "As At Month" filter pane title is on EOI Parameters sheet
        await first_visible(frame, "As At Month", exact=False, timeout=15000)
        await screenshot(page, "01_eoi_params_ready")
        print("    ✅ On EOI Parameters (iframe confirmed)")
    except TimeoutError:
        await screenshot(page, "01_eoi_params_FAIL")
        await dump_html(page, "01_eoi_params_FAIL")
        print("    ⚠️  Could not confirm iframe content — will try anyway")


async def qlik_select(page: Page, filter_label: str, option_text: str):
    """
    Select a value in a Qlik filter pane inside <iframe id="f1">.

    Qlik listboxes often virtualize long lists AND have a search input.
    Strategy:
      1. Click filter header to open listbox
      2. Try typing in the search box (Qlik adds one for most filter panes)
      3. Click the first visible matching option
      4. Escape to close
    """
    print(f"    [filter] {filter_label!r} → {option_text!r}")
    frame = get_qlik_frame(page)

    # ── 1. Open filter pane ───────────────────────────────────────────────
    header = await first_visible(frame, filter_label, exact=False, timeout=12000)
    await header.scroll_into_view_if_needed()
    await header.click()
    await asyncio.sleep(1.2)  # wait for listbox animation + render

    # ── 2. Type in Qlik search box (filters list, works even for short lists)
    search_selectors = [
        "input[aria-label*='Search']",
        "input[placeholder*='Search']",
        "input[placeholder*='search']",
        "input.search-field",
        "input[type='search']",
        # Qlik Sense: search input inside opened filter
        ".qv-listbox .qv-search-field input",
        ".qv-filterpane-popup input",
        # Generic: any visible input that appeared after clicking
        "input[type='text']",
    ]
    typed_in_search = False
    for sel in search_selectors:
        loc = frame.locator(sel).first
        try:
            if await loc.count() > 0 and await loc.is_visible():
                await loc.fill(option_text)
                await asyncio.sleep(0.6)
                typed_in_search = True
                break
        except Exception:
            pass

    await screenshot(page, f"after_open_{safe_name(filter_label)}")

    # ── 3. Click the matching option ──────────────────────────────────────
    option = None

    # a) role="option" (Qlik Sense listbox standard)
    for exact in [True, False]:
        loc = frame.get_by_role("option", name=option_text, exact=exact)
        count = await loc.count()
        for i in range(count):
            el = loc.nth(i)
            try:
                if await el.is_visible():
                    option = el
                    break
            except Exception:
                pass
        if option:
            break

    # b) Checkbox items — Qlik sometimes uses role="checkbox" per option
    if option is None:
        loc = frame.get_by_role("checkbox", name=option_text, exact=False)
        count = await loc.count()
        for i in range(count):
            el = loc.nth(i)
            try:
                if await el.is_visible():
                    option = el
                    break
            except Exception:
                pass

    # c) Visible text match inside iframe
    if option is None:
        try:
            option = await first_visible(frame, option_text, exact=True, timeout=4000)
        except TimeoutError:
            pass

    # d) Partial text match (catches "189 Skilled Independent" etc.)
    if option is None:
        try:
            option = await first_visible(frame, option_text, exact=False, timeout=3000)
        except TimeoutError:
            pass

    # e) If typed in search and typed the right thing, try pressing Enter
    if option is None and typed_in_search:
        print(f"    [fallback] pressing Enter after typing {option_text!r} in search")
        await page.keyboard.press("Enter")
        await asyncio.sleep(0.5)
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.3)
        return   # assume it worked; next step will catch if not

    if option is None:
        await screenshot(page, f"FAIL_option_{safe_name(filter_label)}_{safe_name(option_text)}")
        await dump_iframe_html(page, f"FAIL_option_{safe_name(filter_label)}_{safe_name(option_text)}")
        raise RuntimeError(
            f"Could not find visible option {option_text!r} in filter {filter_label!r}. "
            f"Check iframe HTML in raw_data/skillselect_debug/"
        )

    await option.click()
    await asyncio.sleep(0.5)

    # ── 4. Close listbox ──────────────────────────────────────────────────
    await page.keyboard.press("Escape")
    await asyncio.sleep(0.3)


async def click_tile_button(page: Page, col_name: str, button_label: str):
    """
    Click a Yes or No button inside the tile that matches col_name on EOI Parameters.

    EOI Parameters page shows column tiles, each with a label and Yes/No toggle buttons.
    We find the Yes or No text element that lives inside the same container as col_name.

    Strategy (no CSS class dependency):
    1. Find all visible elements with button_label text ("Yes" or "No")
    2. For each candidate, walk up the DOM and check if col_name text exists in the same subtree
       using JS `closest` traversal
    3. Fallback: use Playwright :has-text() CSS chains
    """
    frame = get_qlik_frame(page)
    print(f"    → Tile: {col_name!r} → click {button_label!r}")

    # Strategy A: Use JS to find the Yes/No element inside a container that has col_name text.
    # We inject JS into the iframe to locate the element, then use a data attribute to target it.
    js = f"""
    () => {{
        const f = document.querySelector('iframe#f1');
        if (!f) return null;
        const doc = f.contentDocument;
        if (!doc) return null;

        // Walk all visible text nodes that match button_label
        const walker = document.createTreeWalker(
            doc.body,
            NodeFilter.SHOW_ELEMENT,
            null
        );
        let node;
        const targets = [];
        while ((node = walker.nextNode())) {{
            const text = node.textContent.trim();
            if (text === '{button_label}' || text === '{button_label.upper()}') {{
                targets.push(node);
            }}
        }}

        // For each target, check if any ancestor also contains col_name text
        for (const el of targets) {{
            let ancestor = el.parentElement;
            let depth = 0;
            while (ancestor && depth < 10) {{
                if (ancestor.textContent.includes('{col_name}') &&
                    ancestor.textContent.includes('{button_label}')) {{
                    // Mark this element so Playwright can find it
                    el.setAttribute('data-tile-target', 'true');
                    return true;
                }}
                ancestor = ancestor.parentElement;
                depth++;
            }}
        }}
        return false;
    }}
    """

    # Try JS approach first
    try:
        found = await page.evaluate(js)
        if found:
            target = frame.locator("[data-tile-target='true']").first
            if await target.count() > 0 and await target.is_visible():
                await target.click()
                # Clean up the attribute
                await page.evaluate(
                    "() => { const f = document.querySelector('iframe#f1'); "
                    "if (f) f.contentDocument.querySelectorAll('[data-tile-target]').forEach(el => el.removeAttribute('data-tile-target')); }"
                )
                await asyncio.sleep(0.5)
                return
    except Exception as e:
        print(f"    [js approach] {e}")

    # Strategy B: CSS :has-text() chain — find narrowest container with both texts, then button inside
    # Try progressively broader containers until we find one that works
    deadline = asyncio.get_event_loop().time() + 10
    while asyncio.get_event_loop().time() < deadline:
        # Find elements that contain col_name text
        col_els = frame.get_by_text(col_name, exact=True)
        col_count = await col_els.count()

        for i in range(col_count):
            col_el = col_els.nth(i)
            if not await col_el.is_visible():
                continue

            # Get bounding box of the col_name element
            bbox = await col_el.bounding_box()
            if not bbox:
                continue

            # Find all visible button_label elements
            btn_els = frame.get_by_text(button_label, exact=True)
            btn_count = await btn_els.count()

            best = None
            best_dist = float("inf")
            for j in range(btn_count):
                btn = btn_els.nth(j)
                try:
                    if not await btn.is_visible():
                        continue
                    btn_bbox = await btn.bounding_box()
                    if not btn_bbox:
                        continue
                    # Manhattan distance between centers
                    dist = abs((btn_bbox["x"] + btn_bbox["width"] / 2) - (bbox["x"] + bbox["width"] / 2)) + \
                           abs((btn_bbox["y"] + btn_bbox["height"] / 2) - (bbox["y"] + bbox["height"] / 2))
                    if dist < best_dist:
                        best_dist = dist
                        best = btn
                except Exception:
                    pass

            if best and best_dist < 300:  # must be within 300px of tile label
                await best.click()
                await asyncio.sleep(0.5)
                print(f"    ✅ Clicked {button_label!r} for tile {col_name!r} (dist={best_dist:.0f}px)")
                return

        await asyncio.sleep(0.4)

    await screenshot(page, f"FAIL_tile_{safe_name(button_label)}_{safe_name(col_name)}")
    await dump_iframe_html(page, f"FAIL_tile_{safe_name(button_label)}_{safe_name(col_name)}")
    raise RuntimeError(
        f"Could not click {button_label!r} button for tile {col_name!r}. "
        f"Check debug files in raw_data/skillselect_debug/"
    )


async def click_tile_yes(page: Page, col_name: str):
    """Select a column tile by clicking its 'Yes' button."""
    await click_tile_button(page, col_name, "Yes")


async def deselect_tiles(page: Page, col1: str, col2: str):
    """
    Reset column tiles after export — click 'No' for previously selected columns.
    This clears the selection so the next pair can be selected fresh.
    """
    print(f"    → Deselecting tiles: {col1!r}, {col2!r}")

    # Try a "Clear All" / "Reset" button first (faster than toggling individually)
    frame = get_qlik_frame(page)
    for reset_label in ["Clear all", "Clear All", "Reset", "Clear selections"]:
        try:
            el = await first_visible(frame, reset_label, exact=False, timeout=1500)
            await el.click()
            await asyncio.sleep(0.5)
            print(f"    ✅ Cleared via {reset_label!r} button")
            return
        except Exception:
            pass

    # Also try outer page clear button
    for reset_label in ["Clear all", "Clear All", "Reset"]:
        try:
            el = await first_visible(page, reset_label, exact=False, timeout=1000)
            await el.click()
            await asyncio.sleep(0.5)
            print(f"    ✅ Cleared via outer {reset_label!r}")
            return
        except Exception:
            pass

    # Fall back to clicking No for each tile
    for col_name in [col1, col2]:
        try:
            await click_tile_button(page, col_name, "No")
        except Exception as e:
            print(f"    ⚠️  Could not deselect {col_name!r}: {e}")


async def click_next_button(page: Page):
    """
    Click the outer-shell Next navigation link.
    The HTML shows: <a class="qcmd" data-qcmd="navNext">Next ...</a>
    This is in the OUTER PAGE (not inside the iframe).
    """
    await screenshot(page, "before_NEXT")

    # The actual element: <a data-qcmd="navNext"> in outer shell
    nav_next = page.locator("[data-qcmd='navNext']").first
    if await nav_next.count() > 0 and await nav_next.is_visible():
        await nav_next.click()
        await asyncio.sleep(2)   # iframe reloads — no networkidle event
        await screenshot(page, "after_NEXT")
        return

    # Fallbacks for button/text variants in outer shell
    for name in ["Next", "NEXT"]:
        btn = page.get_by_role("button", name=name, exact=True).first
        if await btn.count() > 0 and await btn.is_visible():
            await btn.click()
            await asyncio.sleep(2)
            await screenshot(page, "after_NEXT")
            return

    try:
        el = await first_visible(page, "Next", exact=False, timeout=3000)
        await el.click()
        await asyncio.sleep(2)
        await screenshot(page, "after_NEXT")
        return
    except Exception:
        pass

    await dump_html(page, "FAIL_NEXT_button")
    print("    ⚠️  Next button not found in outer shell")



async def click_export_button(page: Page) -> Download | None:
    """
    Qlik does not show a normal Export button.
    Export is opened by right-clicking the results table, then choosing CSV/export from the context menu.
    """
    await screenshot(page, "before_Export")

    frame = get_qlik_frame(page)

    # 1) Right-click inside the results table area
    # From screenshot, table starts around x=30, y=150 in the iframe/page.
    # Use a safe point inside the table body.
    try:
        await page.mouse.click(600, 300, button="right")
        await asyncio.sleep(1)
        await screenshot(page, "after_right_click_table")
    except Exception as e:
        print(f"    ⚠️ right-click failed: {e}")

    # 2) Try context menu items that Qlik may show
    menu_labels = [
        "Export data",
        "Export",
        "Download",
        "Download as",
        "CSV",
        "Data",
        "Export as CSV",
        "Download CSV",
    ]

    # Some Qlik menus are in the outer page, some inside iframe.
    search_contexts = [page, frame]

    for ctx in search_contexts:
        for label in menu_labels:
            try:
                loc = ctx.get_by_text(label, exact=False)
                count = await loc.count()
                for i in range(count):
                    el = loc.nth(i)
                    try:
                        if await el.is_visible():
                            print(f"    → Clicking export menu item: {label}")
                            async with page.expect_download(timeout=30000) as dl:
                                await el.click(force=True)
                            download = await dl.value
                            await screenshot(page, "after_Export_download")
                            return download
                    except Exception:
                        pass
            except Exception:
                pass

    # 3) If clicking first menu opens a second menu/dialog, try CSV again after opening export
    for ctx in search_contexts:
        for first_label in ["Export data", "Export", "Download"]:
            try:
                loc = ctx.get_by_text(first_label, exact=False)
                count = await loc.count()
                for i in range(count):
                    el = loc.nth(i)
                    if await el.is_visible():
                        print(f"    → Opening export submenu/dialog: {first_label}")
                        await el.click(force=True)
                        await asyncio.sleep(1)
                        await screenshot(page, "after_open_export_dialog")

                        for second_label in ["CSV", "Export", "Download", "Click here to download your data file"]:
                            try:
                                loc2 = page.get_by_text(second_label, exact=False)
                                for j in range(await loc2.count()):
                                    el2 = loc2.nth(j)
                                    if await el2.is_visible():
                                        print(f"    → Clicking final download item: {second_label}")
                                        async with page.expect_download(timeout=30000) as dl:
                                            await el2.click(force=True)
                                        download = await dl.value
                                        await screenshot(page, "after_Export_download")
                                        return download
                            except Exception:
                                pass
            except Exception:
                pass

    await screenshot(page, "FAIL_Export_context_menu")
    await dump_html(page, "FAIL_Export_context_menu")
    print("    ⚠️ Export via right-click context menu failed — check debug screenshots/html")
    return None

async def go_back_to_wizard(page: Page):
    """
    Return to EOI Parameters page for next pair.
    Back navigation link is in the outer shell: <a data-qcmd="navBack">.
    After clicking, check if we're on EOI Parameters or went back further.
    """
    # Use outer-shell Back link (same pattern as Next)
    nav_back = page.locator("[data-qcmd='navBack']").first
    if await nav_back.count() > 0 and await nav_back.is_visible():
        await nav_back.click()
        await asyncio.sleep(2)
    else:
        # Fallback
        for label in ["Back", "BACK"]:
            try:
                el = await first_visible(page, label, exact=False, timeout=2000)
                await el.click()
                await asyncio.sleep(2)
                break
            except Exception:
                pass

    # Confirm we're on EOI Parameters (iframe has "As At Month" filter)
    frame = get_qlik_frame(page)
    try:
        await first_visible(frame, "As At Month", exact=False, timeout=4000)
        print("    → Back on EOI Parameters ✅")
    except TimeoutError:
        # Went back to Dashboard Overview — need to click Next again
        print("    → Back on Overview, clicking Next to EOI Parameters...")
        await navigate_to_eoi_params(page)


# ═══════════════════════════════════════════════════════════════════════════
# MAIN EXPORT LOOP
# ═══════════════════════════════════════════════════════════════════════════

async def export_all(month: str | None, visa: str, status: str, pairs: list):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=300)
        context = await browser.new_context(accept_downloads=True)
        page = await context.new_page()

        print(f"\n🚀 Opening SkillSelect...")
        print(f"   Debug screenshots → {DEBUG_DIR}")
        await page.goto(SKILLSELECT_URL)
        await page.wait_for_load_state("networkidle", timeout=30000)
        await screenshot(page, "00_page_loaded")
        print(f"✅ Page loaded (Dashboard Overview)")

        # ── Navigate: Dashboard Overview → EOI Parameters ─────────────────
        # The app starts on an overview/dashboard page; Visa Type and other
        # filters are only visible after clicking NEXT into EOI Parameters.
        await navigate_to_eoi_params(page)

        for i, (col1, col2, file_suffix) in enumerate(pairs):
            print(f"\n{'─'*50}")
            print(f"[{i+1}/{len(pairs)}] {col1} + {col2}")
            print(f"{'─'*50}")

            try:
                # ── Step 1: Select column pair via tile Yes buttons ────────
                # EOI Parameters page: Visa Type / EOI Status / As At Month are
                # already pre-selected at the top (not interactive filters here).
                # The only interaction needed is clicking "Yes" under each column tile.
                await click_tile_yes(page, col1)
                await asyncio.sleep(0.5)
                await click_tile_yes(page, col2)
                await screenshot(page, f"{i+1:02d}_columns_set_{file_suffix}")

                # ── Step 2: Click Next → Results Table ───────────────────
                print("    → Clicking NEXT...")
                await click_next_button(page)
                await asyncio.sleep(2)  # wait for results table to render
                await screenshot(page, f"{i+1:02d}_after_next_{file_suffix}")

                # ── Step 3: Export CSV ────────────────────────────────────
                print("    → Exporting CSV...")
                download = await click_export_button(page)

                if download:
                    month_safe = safe_name(month or "latest")
                    out_name   = f"{month_safe}_{visa}_{status}_{file_suffix}.csv"
                    out_path   = OUTPUT_DIR / out_name
                    await download.save_as(str(out_path))
                    print(f"    ✅ Saved: {out_path.name}")
                    results.append({"pair": f"{col1}+{col2}", "file": str(out_path), "status": "ok"})
                else:
                    results.append({"pair": f"{col1}+{col2}", "file": None, "status": "export_failed"})

                # ── Step 4: Back to wizard, deselect tiles ───────────────
                await go_back_to_wizard(page)
                await asyncio.sleep(0.8)
                if i < len(pairs) - 1:  # no need to deselect after last pair
                    await deselect_tiles(page, col1, col2)
                    await asyncio.sleep(0.5)

            except Exception as e:
                print(f"    ❌ Error: {e}")
                try:
                    await screenshot(page, f"FAIL_{i+1:02d}_{file_suffix}")
                    await dump_html(page, f"FAIL_{i+1:02d}_{file_suffix}")
                except Exception:
                    pass
                results.append({"pair": f"{col1}+{col2}", "file": None, "status": f"error: {e}"})
                try:
                    await go_back_to_wizard(page)
                    await deselect_tiles(page, col1, col2)
                except Exception:
                    pass

        await context.close()
        await browser.close()

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\n{'='*50}")
    print("EXPORT SUMMARY")
    print(f"{'='*50}")
    ok    = [r for r in results if r["status"] == "ok"]
    fail  = [r for r in results if r["status"] != "ok"]
    print(f"✅ Success: {len(ok)}")
    print(f"❌ Failed:  {len(fail)}")
    for r in ok:   print(f"   ✅ {r['pair']}")
    for r in fail: print(f"   ❌ {r['pair']} — {r['status']}")
    print(f"\nFiles in: {OUTPUT_DIR}")

    return results


# ── CLI ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="AIC SkillSelect CSV Exporter")
    ap.add_argument("--month",  default=DEFAULT_MONTH,  help="As At Month (e.g. '06/2026'). Default: latest")
    ap.add_argument("--visa",   default=DEFAULT_VISA,   help="Visa type: 189 | 190 | 491 | All")
    ap.add_argument("--status", default=DEFAULT_STATUS, help="EOI Status: Submitted | Active | All")
    ap.add_argument("--pairs",  nargs="+",              help="Which pairs to export (indices, 1-based). Default: all")
    args = ap.parse_args()

    pairs = COLUMN_PAIRS
    if args.pairs:
        idxs  = [int(x)-1 for x in args.pairs]
        pairs = [COLUMN_PAIRS[i] for i in idxs if 0 <= i < len(COLUMN_PAIRS)]

    asyncio.run(export_all(args.month, args.visa, args.status, pairs))
