"""
Align PDF - Smart Page Number Alignment Tool
============================================
Takes any image-based PDF, detects page numbers in all 6 margin zones,
locks them to a consistent position, and outputs a clean aligned PDF.

Features
--------
* All 6 zones: top-left / top-center / top-right /
               bottom-left / bottom-center / bottom-right
* Cross-page consistency: largest Y cluster = true page-number baseline
  (rejects stray years, footnote refs, running headers)
* LEFT / CENTER / RIGHT lock groups with separate X positions
* Blank pages: kept, but never special-cased — same canvas/embed rules as normal
  pages (no content-recenter; dust must not invent a page-number lock)
* Pages without page numbers (with content): horizontal centering only
* Output preserves original page size and DPI; always embeds DeviceGray
  (never Color Image / DeviceRGB — excluded from output and results)
* No OCR. No font changes. No cropping. Only whole-page translation.

Usage
-----
    python align_pdf.py  input.pdf  output.pdf

Or drag a PDF onto  "Align PDF.bat"  — output saved next to input as
  <name>_aligned.pdf
"""

from __future__ import annotations
import sys, io, argparse
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import numpy as np
import cv2
import fitz

# ── tunables ─────────────────────────────────────────────────────────────────
RENDER_DPI        = 200   # DPI used only for page-number detection (lower = faster)
OUTPUT_DPI        = 600   # Fixed output DPI — always 600, regardless of source PDF
TOP_FRAC          = 0.18
BOT_FRAC          = 0.18
LEFT_FRAC         = 0.38
RIGHT_FRAC        = 0.62
CENTER_X0         = 0.28
CENTER_X1         = 0.72
MIN_BLOB_AREA     = 6
MAX_BLOB_SPAN_F   = 0.30
MIN_CLUSTER_AREA  = 60
MAX_CLUSTER_SPAN  = 0.08
EDGE_MARGIN_FRAC  = 0.04
LINE_Y_TOL        = 20
MAX_OUTSIDE_INK   = 50
Y_TOLERANCE       = 80
MAX_SHIFT         = 150
# ─────────────────────────────────────────────────────────────────────────────

ZONE_TO_GROUP = {
    "top_left": "LEFT",    "bottom_left":  "LEFT",
    "top_center":"CENTER", "bottom_center":"CENTER",
    "top_right": "RIGHT",  "bottom_right": "RIGHT",
}


@dataclass
class _Cluster:
    cy: int; cx: int; x0: int; x1: int; area: int
    @property
    def span(self): return self.x1 - self.x0


@dataclass
class Detection:
    zone: str; group: str; cx: int; cy: int; span: int


@dataclass
class Locks:
    global_y_ratio: Optional[float] = None
    left_x_ratio:   Optional[float] = None
    right_x_ratio:  Optional[float] = None


def render_page(doc, idx, dpi):
    page = doc[idx]
    pix  = page.get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72), alpha=False)
    arr  = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def ink_mask(gray):
    _, th = cv2.threshold(
        cv2.GaussianBlur(gray, (3,3), 0), 0, 255,
        cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    return th


def extract_clusters(gray, zy0, zy1, zx0, zx1, page_w):
    roi  = gray[zy0:zy1, zx0:zx1]
    mask = ink_mask(roi)
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    blobs = []
    for c in cnts:
        area = int(cv2.contourArea(c))
        if area < MIN_BLOB_AREA: continue
        bx, by, bw, bh = cv2.boundingRect(c)
        if bw > page_w * MAX_BLOB_SPAN_F: continue
        blobs.append((zy0+by+bh//2, zx0+bx+bw//2, zx0+bx, zx0+bx+bw, area))
    if not blobs: return []
    blobs.sort(key=lambda b: b[0])
    groups = [[blobs[0]]]
    for b in blobs[1:]:
        if abs(b[0]-groups[-1][-1][0]) <= LINE_Y_TOL: groups[-1].append(b)
        else: groups.append([b])
    clusters = []
    max_span = page_w * MAX_CLUSTER_SPAN
    for grp in groups:
        tots = sum(b[4] for b in grp)
        span = max(b[3] for b in grp) - min(b[2] for b in grp)
        if span > max_span or tots < MIN_CLUSTER_AREA: continue
        clusters.append(_Cluster(
            cy=int(round(sum(b[0] for b in grp)/len(grp))),
            cx=int(round(sum(b[1] for b in grp)/len(grp))),
            x0=min(b[2] for b in grp), x1=max(b[3] for b in grp), area=tots))
    return clusters


def is_isolated(gray, cy, zx0, zx1):
    h, w = gray.shape[:2]
    y0, y1 = max(0, cy-LINE_Y_TOL), min(h, cy+LINE_Y_TOL)
    outside = 0
    if zx0 > 0:  outside += int(np.count_nonzero(ink_mask(gray[y0:y1,:zx0])))
    if zx1 < w:  outside += int(np.count_nonzero(ink_mask(gray[y0:y1,zx1:])))
    return outside <= MAX_OUTSIDE_INK


def detect_page_number(bgr) -> Optional[Detection]:
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    ty0,ty1 = 0, int(h*TOP_FRAC)
    by0,by1 = int(h*(1-BOT_FRAC)), h
    lx0,lx1 = 0, int(w*LEFT_FRAC)
    rx0,rx1 = int(w*RIGHT_FRAC), w
    cx0,cx1 = int(w*CENTER_X0), int(w*CENTER_X1)
    zones = [
        ("top_left",ty0,ty1,lx0,lx1), ("top_center",ty0,ty1,cx0,cx1),
        ("top_right",ty0,ty1,rx0,rx1), ("bottom_left",by0,by1,lx0,lx1),
        ("bottom_center",by0,by1,cx0,cx1), ("bottom_right",by0,by1,rx0,rx1),
    ]
    best, best_area = None, 0
    edge_min, edge_max = w*EDGE_MARGIN_FRAC, w*(1-EDGE_MARGIN_FRAC)
    for zn, zy0, zy1, zx0, zx1 in zones:
        clusters = extract_clusters(gray, zy0, zy1, zx0, zx1, w)
        if not clusters: continue
        top_c = max(clusters, key=lambda c: c.area)
        if not is_isolated(gray, top_c.cy, zx0, zx1): continue
        if top_c.cx < edge_min or top_c.cx > edge_max: continue
        if top_c.area > best_area:
            best_area = top_c.area
            best = Detection(zn, ZONE_TO_GROUP[zn], top_c.cx, top_c.cy, top_c.span)
    return best


def build_locks(detections, pages_det) -> tuple[Locks, set]:
    y_buckets = defaultdict(list)
    for i, d in enumerate(detections):
        if d is None: continue
        placed = False
        for ry in list(y_buckets):
            if abs(d.cy - ry) <= Y_TOLERANCE:
                y_buckets[ry].append((i, d)); placed = True; break
        if not placed: y_buckets[d.cy].append((i, d))
    if not y_buckets:
        return Locks(), set()
    best_ry = max(y_buckets, key=lambda k: len(y_buckets[k]))
    consistent = sorted(y_buckets[best_ry], key=lambda x: x[0])
    valid_pages = {i for i, _ in consistent}
    first_i, first_d = consistent[0]
    ref_h = pages_det[first_i].shape[0]
    ref_w = pages_det[first_i].shape[1]
    locks = Locks(global_y_ratio=first_d.cy / ref_h)
    print(f"  Consistent Y cluster: {len(consistent)} pages near Y={best_ry} px")
    print(f"  >> GLOBAL_PAGE_NUMBER_Y = {first_d.cy} px  ratio={locks.global_y_ratio:.4f}"
          f"  (page {first_i+1}, zone={first_d.zone})")
    for i, d in consistent:
        w = pages_det[i].shape[1]
        if d.group == "LEFT" and locks.left_x_ratio is None:
            locks.left_x_ratio = d.cx / w
            print(f"  >> LEFT  LOCK X = {d.cx} px  ratio={locks.left_x_ratio:.4f}  (page {i+1})")
        elif d.group == "RIGHT" and locks.right_x_ratio is None:
            locks.right_x_ratio = d.cx / w
            print(f"  >> RIGHT LOCK X = {d.cx} px  ratio={locks.right_x_ratio:.4f}  (page {i+1})")
        if locks.left_x_ratio and locks.right_x_ratio: break
    return locks, valid_pages


def content_cx(bgr):
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    pts  = cv2.findNonZero(ink_mask(gray))
    if pts is None: return None
    x, _, ww, _ = cv2.boundingRect(pts)
    return x + ww // 2


# Ink ratio below this = blank page (scanner dust / noise only).
# Blank pages must follow the same no-shift path as normal pages — never content-center.
BLANK_INK_RATIO = 0.0008


def is_blank_page(bgr) -> bool:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    ink  = ink_mask(gray)
    return (np.count_nonzero(ink) / ink.size) < BLANK_INK_RATIO


def place(src, dx, dy, out_w, out_h):
    canvas = np.full((out_h, out_w, 3), 255, dtype=np.uint8)
    ih, iw = src.shape[:2]
    sx0 = max(0,-dx); dx0 = max(0, dx)
    sy0 = max(0,-dy); dy0 = max(0, dy)
    cw = min(iw-sx0, out_w-dx0); ch = min(ih-sy0, out_h-dy0)
    if cw > 0 and ch > 0:
        canvas[dy0:dy0+ch, dx0:dx0+cw] = src[sy0:sy0+ch, sx0:sx0+cw]
    return canvas


def get_page_image_props(doc, page_idx):
    """Return DPI, bpc, and colorspace component count of the dominant raster image."""
    page = doc[page_idx]
    images = page.get_images(full=True)
    if not images:
        return None
    # Pick the image with the most pixels (most likely the full-page scan)
    best_img = max(images, key=lambda img: img[2] * img[3])
    xref = best_img[0]
    try:
        img_dict = doc.extract_image(xref)
    except Exception:
        return None
    page_w_pt = page.rect.width
    img_w_px = img_dict.get('width', best_img[2])
    if page_w_pt <= 0 or img_w_px <= 0:
        return None
    dpi = round(img_w_px / (page_w_pt / 72))
    if dpi < 50 or dpi > 2400:
        return None
    return {
        'dpi':        dpi,
        'bpc':        img_dict.get('bpc', 8),
        'colorspace': img_dict.get('colorspace', 3),
    }


def image_to_bytes(bgr, dpi, bpc, colorspace=1):
    """Encode aligned BGR as grayscale PNG bytes (never Color Image / RGB)."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if bpc == 1:
        _, gray = cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY)
        pil_img = Image.fromarray(gray).convert('1')
    else:
        pil_img = Image.fromarray(gray)
    buf = io.BytesIO()
    pil_img.save(buf, format='PNG', dpi=(dpi, dpi))
    return buf.getvalue()


def aligned_to_pixmap(bgr, bpc, colorspace=1):
    """Convert aligned BGR to a plain DeviceGray Pixmap.

    Color Image / DeviceRGB is never produced — Acrobat Preflight will not
    list Color Image entries for Align tool output. No ICC profile.
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if bpc == 1:
        # Keep hard black/white values; store as 8-bit DeviceGray
        _, gray = cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY)
    h, w = gray.shape
    samples = gray.tobytes()
    cs = fitz.Colorspace(fitz.CS_GRAY)
    return fitz.Pixmap(cs, w, h, samples, False)


def run(input_pdf: Path, output_pdf: Path,
        tpl_w: Optional[int] = None, tpl_h: Optional[int] = None,
        border_mm: float = 3.0) -> None:
    print(f"\nInput  : {input_pdf}")
    print(f"Output : {output_pdf}")
    doc = fitz.open(str(input_pdf))
    n   = len(doc)
    border_str = f"{border_mm} mm" if border_mm > 0 else "disabled"
    print(f"Pages          : {n}")
    print(f"Processing DPI : {OUTPUT_DPI}")
    print(f"Output DPI     : {OUTPUT_DPI}")
    print(f"White Edge     : {border_str}")
    print(f"(Source PDF DPI is ignored — always outputs at {OUTPUT_DPI} DPI)\n")

    print("[1/4] Rendering pages for detection ...")
    pages_det = []
    for i in range(n):
        pages_det.append(render_page(doc, i, RENDER_DPI))
        print(f"  {i+1}/{n}", end="\r")
    print()

    print("\n[2/4] Detecting page numbers (non-image objects only) ...")
    print("  Color Image objects: excluded from detection and results")
    blank_flags = [is_blank_page(bgr) for bgr in pages_det]
    detections = []
    for i, bgr in enumerate(pages_det):
        if blank_flags[i]:
            detections.append(None)
            print(f"  p{i+1:03d}: blank (same rules, no detect)")
        else:
            d = detect_page_number(bgr)
            detections.append(d)
            if d: print(f"  p{i+1:03d}: {d.zone:14s} cx={d.cx:4d} cy={d.cy:4d} span={d.span:3d}px")
            else: print(f"  p{i+1:03d}: ---")

    blank_count = sum(blank_flags)
    if blank_count:
        print(f"  Blank pages: {blank_count} (same rules as normal — no extra shift)")

    locks, valid_pages = build_locks(detections, pages_det)
    if locks.global_y_ratio is None:
        print("  WARNING: no page numbers detected - centering all pages.")

    print("\n[3/4] Page embed mode (Color Image excluded) ...")
    page_props = []
    for i in range(n):
        props = get_page_image_props(doc, i)
        page_props.append(props)
        bpc = props['bpc'] if props else 8
        # Never display Color Image — Align results are DeviceGray only
        print(f"  p{i+1:03d}: {OUTPUT_DPI} DPI (forced)  {bpc}bpc, DeviceGray")

    print("\n[4/4] Aligning and writing PDF (DeviceGray only) ...")
    out_doc = fitz.open()

    for i, det in enumerate(detections):
        props = page_props[i]
        bpc   = props['bpc'] if props else 8
        cs    = 1  # DeviceGray only — Color Image completely excluded

        orig_rect = doc[i].rect                        # keep exact source page size
        scale     = OUTPUT_DPI / RENDER_DPI

        # Pixel dimensions of the output canvas and final PDF page size
        if tpl_w is not None and tpl_h is not None:
            # Template px are defined at 600 DPI; derive pt from that
            out_w_orig = tpl_w
            out_h_orig = tpl_h
            page_w_pt  = tpl_w * 72 / 600
            page_h_pt  = tpl_h * 72 / 600
        else:
            out_w_orig = int(round(orig_rect.width  * OUTPUT_DPI / 72))
            out_h_orig = int(round(orig_rect.height * OUTPUT_DPI / 72))
            page_w_pt  = orig_rect.width
            page_h_pt  = orig_rect.height

        use_det = (i in valid_pages) and (locks.global_y_ratio is not None) and (det is not None)

        if use_det:
            h_det, w_det = pages_det[i].shape[:2]
            target_y = int(round(locks.global_y_ratio * h_det))
            dy_d = target_y - det.cy
            if det.group == "LEFT":
                target_x = int(round(locks.left_x_ratio * w_det)) \
                           if locks.left_x_ratio is not None else det.cx
                dx_d = target_x - det.cx
            elif det.group == "RIGHT":
                target_x = int(round(locks.right_x_ratio * w_det)) \
                           if locks.right_x_ratio is not None else det.cx
                dx_d = target_x - det.cx
            else:  # CENTER — align to this page's own horizontal midpoint
                dx_d = (w_det // 2) - det.cx

            dy = int(round(dy_d * scale))
            dx = int(round(dx_d * scale))
            max_s = int(MAX_SHIFT * scale)
            rej = []
            if abs(dy) > max_s: rej.append("dy->0"); dy = 0
            if abs(dx) > max_s: rej.append("dx->0"); dx = 0
            rej_str = f" [REJECTED: {', '.join(rej)}]" if rej else ""
            print(f"  p{i+1:03d}: {det.zone:14s} {det.group:6s}  "
                  f"dx={dx:+4d}  dy={dy:+4d}  @{OUTPUT_DPI}dpi{rej_str}")
        else:
            # No usable page number. Blank pages: no-shift (same canvas rules).
            if blank_flags[i]:
                dx, dy = 0, 0
                print(f"  p{i+1:03d}: blank page      -> no shift  @{OUTPUT_DPI}dpi")
            else:
                w_det = pages_det[i].shape[1]
                ccx_d = content_cx(pages_det[i])
                dx_d  = ((w_det // 2) - ccx_d) if ccx_d else 0
                dx    = int(round(dx_d * scale))
                if abs(dx) > int(MAX_SHIFT * scale): dx = 0
                dy = 0
                print(f"  p{i+1:03d}: no page number  -> centre dx={dx:+4d}  @{OUTPUT_DPI}dpi")

        # Render at fixed OUTPUT_DPI (600) — source DPI is never used
        bgr_orig = render_page(doc, i, OUTPUT_DPI)

        # Centre source image on the canvas first, then add the alignment shift on top.
        # When template size equals source size both base offsets are 0, so nothing changes.
        base_x = (out_w_orig - bgr_orig.shape[1]) // 2
        base_y = (out_h_orig - bgr_orig.shape[0]) // 2
        aligned  = place(bgr_orig, base_x + dx, base_y + dy, out_w_orig, out_h_orig)

        # Final edge-cleanup: paint a configurable white border on all four sides.
        # border_mm=0 disables cleanup entirely.
        if border_mm > 0:
            border_px = max(1, int(round(border_mm * OUTPUT_DPI / 25.4)))
            aligned[:border_px,  :]  = 255   # top
            aligned[-border_px:, :]  = 255   # bottom
            aligned[:,  :border_px]  = 255   # left
            aligned[:, -border_px:]  = 255   # right

        # DeviceGray only — never Color Image / DeviceRGB / ICCBased.
        pix = aligned_to_pixmap(aligned, bpc, cs)

        pg = out_doc.new_page(width=page_w_pt, height=page_h_pt)
        pg.insert_image(fitz.Rect(0, 0, page_w_pt, page_h_pt), pixmap=pix)

        # PyMuPDF may wrap CS_GRAY as ICCBased; force plain /DeviceGray.
        img_xref = pg.get_images(full=True)[0][0]
        out_doc.xref_set_key(img_xref, "ColorSpace", "/DeviceGray")

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    # garbage=4: remove unreferenced objects (orphaned ICC streams) + compress xref
    out_doc.save(str(output_pdf), deflate=True, garbage=4)
    mb = output_pdf.stat().st_size / 1_048_576
    print(f"\nDone -> {output_pdf.resolve()}  ({mb:.1f} MB)")


if __name__ == "__main__":
    import traceback
    try:
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("input")
        parser.add_argument("output", nargs="?", default=None)
        parser.add_argument("--tpl-w",     type=int,   default=None)
        parser.add_argument("--tpl-h",     type=int,   default=None)
        parser.add_argument("--border-mm", type=float, default=3.0)
        args, _ = parser.parse_known_args()

        inp = Path(args.input)
        out = Path(args.output) if args.output \
              else inp.with_name(inp.stem + "_aligned.pdf")
        if not inp.exists():
            sys.exit(f"File not found: {inp}")
        run(inp, out, tpl_w=args.tpl_w, tpl_h=args.tpl_h, border_mm=args.border_mm)
    except SystemExit:
        raise
    except Exception:
        if sys.stdin and sys.stdin.isatty():
            input("\nPress Enter to exit...")
        sys.exit(1)
