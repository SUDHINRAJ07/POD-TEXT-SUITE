"""
PDF Crop Engine
---------------
Per-page pipeline:
  1. Detect ink
  2. Remove edge scanner lines on all 4 sides
  3. Remove isolated dust using 3 mm distance
  4. Build real content bbox
  5. Add SAFE_MARGIN_MM crop padding (default 0.0 = tight crop)
  6. Export 600 DPI 1-bit BMP

Usage:
    python pdf_crop_engine.py <input.pdf> [output_dir]

Output:
    output_dir/page_001.bmp, page_002.bmp, ...
"""

import sys
import os
import fitz                 # PyMuPDF
import numpy as np
from PIL import Image
from scipy import ndimage
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing


# ── Constants ────────────────────────────────────────────────────────────────

OUTPUT_DPI        = 600
BINARIZE_THRESH   = 200     # pixels darker than this are ink (0-255)

# Step 2 — edge scanner line rejection
EDGE_ZONE_MM               = 5.0
EDGE_ZONE_PX               = int(round(EDGE_ZONE_MM               * OUTPUT_DPI / 25.4))  # ~118 px
EDGE_LINE_MAX_THICKNESS_MM = 2.0
EDGE_LINE_MAX_THICKNESS_PX = int(round(EDGE_LINE_MAX_THICKNESS_MM * OUTPUT_DPI / 25.4))  # ~47 px

# Step 3 — isolated dust rejection
DUST_MAX_AREA_PX  = 50
DUST_DISTANCE_MM  = 3.0
DUST_DISTANCE_PX  = int(round(DUST_DISTANCE_MM  * OUTPUT_DPI / 25.4))  # ~71 px

# Step 5 — crop padding
# Set to 0.0 for a tight crop (default).
# Increase to add white space around the detected content box.
# Examples: 0.0 = tight, 1.0 = 1 mm, 2.0 = 2 mm, 3.0 = 3 mm
SAFE_MARGIN_MM = 0.0
SAFE_MARGIN_PX = int(round(SAFE_MARGIN_MM * OUTPUT_DPI / 25.4))         # 0 px when default


# ── Step 1 — Detect ink ───────────────────────────────────────────────────────

def _render_page_to_array(pdf_path: str, page_idx: int) -> tuple[np.ndarray, int, int]:
    """
    Open the PDF, render one page to a grayscale uint8 array at OUTPUT_DPI,
    and close immediately. Returns (array, width, height).
    Opening per-worker avoids pickling fitz objects across process boundaries.
    """
    doc = fitz.open(pdf_path)
    page   = doc[page_idx]
    zoom   = OUTPUT_DPI / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    pix    = page.get_pixmap(matrix=matrix, colorspace=fitz.csGRAY, alpha=False)
    arr    = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    arr    = arr.copy()   # detach from pix before doc closes
    doc.close()
    return arr, pix.width, pix.height


def _label_components(ink: np.ndarray):
    """8-connected component labeling. Returns (labeled_array, n_components)."""
    struct = ndimage.generate_binary_structure(2, 2)
    return ndimage.label(ink, structure=struct)


# ── Step 2 — Remove edge scanner lines on all 4 sides ────────────────────────

def _remove_edge_lines(ink: np.ndarray) -> np.ndarray:
    """
    Erase thin scanner/border lines touching any of the four page edges.

    A component is removed when ALL three conditions hold:
      1. Touches an edge zone (within EDGE_ZONE_PX of top/bottom/left/right).
      2. Thin perpendicular to that edge (<= EDGE_LINE_MAX_THICKNESS_PX).
      3. Spans at least half the page parallel to that edge.
    """
    h, w       = ink.shape
    labeled, n = _label_components(ink)
    if n == 0:
        return ink.copy()

    result = ink.copy()
    half_w = w // 2
    half_h = h // 2

    # Batch bounding-box computation via ndimage — avoids per-label np.where loops
    slices = ndimage.find_objects(labeled)

    for lbl_idx, sl in enumerate(slices):
        if sl is None:
            continue
        lbl    = lbl_idx + 1
        r_sl, c_sl = sl
        r_min, r_max = r_sl.start, r_sl.stop - 1
        c_min, c_max = c_sl.start, c_sl.stop - 1
        comp_h = r_max - r_min + 1
        comp_w = c_max - c_min + 1

        # Horizontal lines (top / bottom)
        if r_min < EDGE_ZONE_PX or r_max >= (h - EDGE_ZONE_PX):
            if comp_h <= EDGE_LINE_MAX_THICKNESS_PX and comp_w >= half_w:
                result[labeled == lbl] = False
                continue

        # Vertical lines (left / right)
        if c_min < EDGE_ZONE_PX or c_max >= (w - EDGE_ZONE_PX):
            if comp_w <= EDGE_LINE_MAX_THICKNESS_PX and comp_h >= half_h:
                result[labeled == lbl] = False

    return result


# ── Step 3 — Remove isolated dust using 3 mm distance ────────────────────────

def _remove_isolated_dust(ink: np.ndarray) -> np.ndarray:
    """
    Keep only real content pixels.

    Large components (area > DUST_MAX_AREA_PX) are always kept.
    Small components are kept only if within DUST_DISTANCE_PX of a large one,
    preserving i-dots, punctuation, accents while discarding margin specks.
    """
    labeled, n = _label_components(ink)
    if n == 0:
        return ink.copy()

    sizes        = np.array(ndimage.sum(ink, labeled, range(1, n + 1)), dtype=np.int64)
    large_labels = np.where(sizes >  DUST_MAX_AREA_PX)[0] + 1
    small_labels = np.where(sizes <= DUST_MAX_AREA_PX)[0] + 1

    if large_labels.size == 0:
        return ink.copy()           # sparse/blank page — keep everything

    large_mask = np.isin(labeled, large_labels)

    if small_labels.size == 0:
        return large_mask

    dist_to_large = ndimage.distance_transform_edt(~large_mask)
    small_mask    = np.isin(labeled, small_labels)
    close_small   = small_mask & (dist_to_large <= DUST_DISTANCE_PX)

    return large_mask | close_small


# ── Steps 4 + 5 — Build bbox and add padding ─────────────────────────────────

def _bbox_with_padding(real: np.ndarray, img_w: int, img_h: int) -> tuple[int, int, int, int]:
    """
    Tight bounding box around True pixels in `real`, then SAFE_MARGIN_PX padding.
    When SAFE_MARGIN_MM = 0.0 (default) the result is a pixel-tight crop.
    """
    rows = np.any(real, axis=1)
    cols = np.any(real, axis=0)
    top    = int(np.argmax(rows))
    bottom = int(len(rows) - np.argmax(rows[::-1]))
    left   = int(np.argmax(cols))
    right  = int(len(cols) - np.argmax(cols[::-1]))
    return (
        max(0,     left   - SAFE_MARGIN_PX),
        max(0,     top    - SAFE_MARGIN_PX),
        min(img_w, right  + SAFE_MARGIN_PX),
        min(img_h, bottom + SAFE_MARGIN_PX),
    )


# ── Full per-page pipeline ────────────────────────────────────────────────────

def _process_page(pdf_path: str, page_idx: int, out_path: str) -> tuple[int, str, int, int, int, int]:
    """
    Run the complete 6-step pipeline for one page.
    Returns (page_idx, out_path, img_w, img_h, crop_w, crop_h).
    Designed to run inside a worker process.
    """
    # Steps 1 — render to grayscale array (PDF opened and closed here)
    arr, img_w, img_h = _render_page_to_array(pdf_path, page_idx)

    # Step 1 — detect ink
    ink = arr < BINARIZE_THRESH

    if not ink.any():
        # Blank page — full image, no further processing
        bbox = (0, 0, img_w, img_h)
    else:
        # Step 2 — remove edge scanner lines (all 4 sides)
        ink = _remove_edge_lines(ink)

        # Step 3 — remove isolated dust (3 mm threshold)
        real = _remove_isolated_dust(ink)
        if not real.any():
            real = ink              # fallback

        # Steps 4 + 5 — tight bbox + 3 mm padding
        bbox = _bbox_with_padding(real, img_w, img_h)

    # Step 6 — export 600 DPI 1-bit BMP
    # Build PIL Image directly from the already-rendered array (no re-render)
    gray    = Image.fromarray(arr, mode="L")
    cropped = gray.crop(bbox)
    bw      = cropped.convert("1")
    bw.save(out_path, format="BMP", dpi=(OUTPUT_DPI, OUTPUT_DPI))

    crop_w = bbox[2] - bbox[0]
    crop_h = bbox[3] - bbox[1]
    return (page_idx, out_path, img_w, img_h, crop_w, crop_h)


# ── Driver ────────────────────────────────────────────────────────────────────

def process_pdf(pdf_path: str, output_dir: str) -> None:
    os.makedirs(output_dir, exist_ok=True)

    # Read page count without keeping the document open
    doc        = fitz.open(pdf_path)
    page_count = len(doc)
    doc.close()

    digits   = max(3, len(str(page_count)))
    n_workers = max(1, (multiprocessing.cpu_count() or 2) - 1)

    print(f"Input   : {pdf_path}")
    print(f"Pages   : {page_count}")
    print(f"Output  : {output_dir}")
    print(f"DPI     : {OUTPUT_DPI}")
    print(f"Workers : {n_workers}")
    print()

    # Build the full job list up front so we can track progress by page number
    jobs = []
    for i in range(page_count):
        label    = str(i + 1).zfill(digits)
        out_path = os.path.join(output_dir, f"page_{label}.bmp")
        jobs.append((i, out_path))

    completed = 0
    with ProcessPoolExecutor(max_workers=n_workers) as pool:
        future_map = {
            pool.submit(_process_page, pdf_path, page_idx, out_path): (page_idx, out_path)
            for page_idx, out_path in jobs
        }

        for future in as_completed(future_map):
            page_idx, out_path = future_map[future]
            page_num = page_idx + 1
            label    = str(page_num).zfill(digits)

            try:
                _, saved_path, img_w, img_h, cw, ch = future.result()
            except Exception as exc:
                print(f"  [ERROR] page {label}: {exc}")
                completed += 1
                continue

            completed += 1
            print(f"  Processing page {page_num}/{page_count}...  "
                  f"{img_w}x{img_h}px -> crop {cw}x{ch}px")
            print(f"  Saved {os.path.basename(saved_path)}")

    # Combine cropped BMPs into PDF
    print(f"\nCombining cropped images into PDF...")
    valid_images = []
    for i, out_path in jobs:
        if os.path.isfile(out_path):
            valid_images.append(out_path)
        else:
            print(f"  [WARNING] Missing cropped image for page {i + 1}: {out_path}")

    if not valid_images:
        print("  [ERROR] No cropped images found to combine into PDF.")
    else:
        pdf_basename = os.path.splitext(os.path.basename(pdf_path))[0]
        out_pdf_name = f"{pdf_basename}_crop.pdf"
        
        dest_pdf_1 = os.path.join(output_dir, out_pdf_name)
        pdf_dir = os.path.dirname(os.path.abspath(pdf_path))
        dest_pdf_2 = os.path.join(pdf_dir, out_pdf_name)

        try:
            out_doc = fitz.open()
            for img_path in valid_images:
                img_doc = fitz.open(img_path)
                pdf_bytes = img_doc.convert_to_pdf()
                img_doc.close()
                img_pdf = fitz.open("pdf", pdf_bytes)
                out_doc.insert_pdf(img_pdf)
                img_pdf.close()
            
            out_doc.save(dest_pdf_1, deflate=True)
            out_doc.close()
            print(f"  Saved combined PDF to: {dest_pdf_1}")
            
            if os.path.abspath(pdf_dir) != os.path.abspath(output_dir):
                import shutil
                try:
                    shutil.copy2(dest_pdf_1, dest_pdf_2)
                    print(f"  Saved combined PDF to: {dest_pdf_2}")
                except Exception as e:
                    print(f"  [WARNING] Could not copy PDF to {dest_pdf_2}: {e}")
        except Exception as e:
            print(f"  [ERROR] Failed to generate PDF: {e}")
            sys.exit(1)

    print(f"\nDone. Crop PDF processing complete for {page_count} page(s).")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python pdf_crop_engine.py <input.pdf> [output_dir]")
        sys.exit(1)

    pdf_path   = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 \
                 else os.path.splitext(pdf_path)[0] + "_cropped"

    if not os.path.isfile(pdf_path):
        print(f"Error: file not found: {pdf_path}")
        sys.exit(1)

    process_pdf(pdf_path, output_dir)


if __name__ == "__main__":
    # Required on Windows for ProcessPoolExecutor
    multiprocessing.freeze_support()
    main()
