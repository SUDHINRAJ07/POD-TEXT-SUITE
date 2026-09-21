import sys
import re
import traceback
from io import BytesIO
from pathlib import Path
import fitz
from PIL import Image

DPI = 600
ZOOM = DPI / 72.0
# Page sizes rarely map to whole pixels at 600 DPI, so a page authored at 600
# can measure 598.99. Allow that much shortfall before rebuilding the page.
DPI_TOLERANCE = 2.0
# How far an image may fall short of the page edges and still count as full page.
COVER_TOLERANCE = 1.0


NUMBER = rb"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
CMYK_OPERATOR = re.compile(
    rb"(?<!\S)" + NUMBER + rb"\s+" + NUMBER + rb"\s+" + NUMBER + rb"\s+" + NUMBER + rb"\s+[kK](?!\S)"
)
RGB_OPERATOR = re.compile(
    rb"(?<!\S)" + NUMBER + rb"\s+" + NUMBER + rb"\s+" + NUMBER + rb"\s+(?:rg|RG)(?!\S)"
)
GRAY_OPERATOR = re.compile(rb"(?<!\S)" + NUMBER + rb"\s+[gG](?!\S)")


def detect_page_mode(page):
    """Choose BITMAP, GRAY, RGB, or CMYK from the page's source content."""
    modes = set()

    # Image XObjects retain their original PDF color-space name here.
    try:
        for image in page.get_images(full=True):
            color_name = str(image[5]).upper()
            bits_per_component = image[4]
            if "CMYK" in color_name:
                modes.add("CMYK")
            elif "RGB" in color_name or "LAB" in color_name:
                modes.add("RGB")
            elif "GRAY" in color_name or "GREY" in color_name:
                modes.add("BITMAP" if bits_per_component == 1 else "GRAY")
            else:
                pix = fitz.Pixmap(page.parent, image[0])
                channels = pix.n - pix.alpha
                if channels >= 4:
                    modes.add("CMYK")
                elif channels == 3:
                    modes.add("RGB")
                elif channels == 1:
                    modes.add("BITMAP" if bits_per_component == 1 else "GRAY")
                pix = None
    except Exception:
        pass

    # Inspect painting operators so vector art and colored text are not
    # incorrectly classified as grayscale.
    try:
        content = b"\n".join(
            page.parent.xref_stream(xref) for xref in page.get_contents()
        )
        if CMYK_OPERATOR.search(content):
            modes.add("CMYK")
        if RGB_OPERATOR.search(content):
            modes.add("RGB")
        if GRAY_OPERATOR.search(content):
            modes.add("GRAY")
    except Exception:
        pass

    # A flattened raster page can have only one color space and bit depth.
    # Use the richest source mode present so mixed pages do not lose tones/color.
    if "CMYK" in modes:
        return "CMYK"
    if "RGB" in modes:
        return "RGB"
    if "GRAY" in modes:
        return "GRAY"
    if "BITMAP" in modes:
        return "BITMAP"
    return "GRAY"


def full_page_source_image(page):
    """Return (width, height, bpc) when one image alone fills the whole page."""
    images = page.get_images(full=True)
    if len(images) != 1:
        return None
    if page.get_text("text").strip() or page.get_drawings():
        return None

    xref, _, width, height, bpc = images[0][:5]
    rect = page.rect
    if not width or not height or rect.is_empty:
        return None

    placements = page.get_image_rects(xref)
    if len(placements) != 1:
        return None

    placed = fitz.Rect(placements[0])
    covers_page = (
        placed.x0 <= rect.x0 + COVER_TOLERANCE
        and placed.y0 <= rect.y0 + COVER_TOLERANCE
        and placed.x1 >= rect.x1 - COVER_TOLERANCE
        and placed.y1 >= rect.y1 - COVER_TOLERANCE
    )
    if not covers_page:
        return None

    return width, height, bpc


def render_page(page):
    mode = detect_page_mode(page)
    colorspace = {
        "BITMAP": fitz.csGRAY,
        "GRAY": fitz.csGRAY,
        "RGB": fitz.csRGB,
        "CMYK": fitz.csCMYK,
    }[mode]
    mat = fitz.Matrix(ZOOM, ZOOM)

    # Anti-aliasing turns crisp 1-bit strokes into gray edges, which then read
    # as faded text once the page is flattened back to a bitmap.
    previous_aa = fitz.TOOLS.show_aa_level()
    if mode == "BITMAP":
        fitz.TOOLS.set_aa_level(0)
    try:
        pix = page.get_pixmap(
            matrix=mat, colorspace=colorspace, alpha=False, annots=True
        )
    finally:
        if mode == "BITMAP":
            fitz.TOOLS.set_aa_level(previous_aa["graphics"])
    return pix, mode


def pixmap_to_1bit_png(pix):
    """Encode a grayscale render as a true 1-bit monochrome PNG."""
    gray = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    bitmap = gray.convert("1", dither=Image.Dither.NONE)
    stream = BytesIO()
    bitmap.save(stream, format="PNG", dpi=(DPI, DPI), optimize=True)
    bitmap.close()
    gray.close()
    return stream.getvalue()


def _device_from_icc_stream(doc, icc_xref):
    """Map an ICCBased stream to DeviceGray / DeviceRGB / DeviceCMYK."""
    alt = doc.xref_get_key(icc_xref, "Alternate")
    if alt[0] == "name":
        return alt[1]
    n = doc.xref_get_key(icc_xref, "N")
    if n[0] == "int":
        return {1: "/DeviceGray", 3: "/DeviceRGB", 4: "/DeviceCMYK"}.get(
            int(n[1]), "/DeviceGray"
        )
    return "/DeviceGray"


def _icc_array_to_device(doc, pdf_obj):
    """If obj text is [ /ICCBased N 0 R ], return the matching Device* name."""
    match = re.search(r"/ICCBased\s+(\d+)", pdf_obj)
    if not match:
        return None
    return _device_from_icc_stream(doc, int(match.group(1)))


def strip_icc_profiles(doc):
    """Replace ICCBased image color spaces with plain Device* (no ICC).

    PyMuPDF's insert_image embeds ICCBased even for DeviceGray/RGB/CMYK
    pixmaps. Kept source pages may also carry ICC. Strip both so Acrobat
    reports Gray / RGB / CMYK without an (ICC) tag.
    """
    for xref in range(1, doc.xref_length()):
        if doc.xref_get_key(xref, "Subtype") != ("name", "/Image"):
            continue

        cs = doc.xref_get_key(xref, "ColorSpace")
        device = None

        if cs[0] == "xref":
            cs_xref = int(cs[1].split()[0])
            device = _icc_array_to_device(doc, doc.xref_object(cs_xref))
        elif cs[0] == "array":
            device = _icc_array_to_device(doc, cs[1])

        if device:
            doc.xref_set_key(xref, "ColorSpace", device)

    # Drop any document-level output intent ICC as well.
    if doc.xref_get_key(doc.pdf_catalog(), "OutputIntents")[0] != "null":
        doc.xref_set_key(doc.pdf_catalog(), "OutputIntents", "null")


def convert_pdf(input_pdf: Path):
    if not input_pdf.exists():
        raise FileNotFoundError(input_pdf)
    if input_pdf.suffix.lower() != ".pdf":
        raise ValueError("Only PDF files are supported")

    output_pdf = input_pdf.with_name(input_pdf.stem + "_600dpi.pdf")
    report = input_pdf.with_name(input_pdf.stem + "_600dpi_report.txt")

    src = fitz.open(str(input_pdf))
    out = fitz.open()
    lines = []
    lines.append(f"Input: {input_pdf}")
    lines.append(f"Output: {output_pdf}")
    lines.append(f"DPI: {DPI}")
    lines.append(f"Pages: {src.page_count}")
    lines.append("ICC profile: not embedded by this tool")
    lines.append("")

    print(f"\nInput : {input_pdf}")
    print(f"Pages : {src.page_count}")
    print(f"DPI   : {DPI}\n")

    try:
        for i, page in enumerate(src, start=1):
            rect = page.rect
            source = full_page_source_image(page)
            source_dpi = 0.0
            if source:
                source_dpi = min(
                    source[0] * 72 / rect.width, source[1] * 72 / rect.height
                )

            if source and source_dpi >= DPI - DPI_TOLERANCE:
                # Already at target resolution: copy the original page so its
                # pixels, bit depth, and color space stay untouched.
                out.insert_pdf(src, from_page=i - 1, to_page=i - 1)
                width, height, bit_depth = source
                mode = detect_page_mode(page)
                origin = "kept"
            else:
                pix, mode = render_page(page)
                new_page = out.new_page(width=rect.width, height=rect.height)
                if mode == "BITMAP":
                    new_page.insert_image(new_page.rect, stream=pixmap_to_1bit_png(pix))
                    bit_depth = 1
                else:
                    new_page.insert_image(new_page.rect, pixmap=pix)
                    bit_depth = 8
                width, height = pix.width, pix.height
                origin = "rendered"
                pix = None

            x_dpi = width * 72 / rect.width
            y_dpi = height * 72 / rect.height
            info = (
                f"Page {i:04d}: Bitmap image {rect.width:.2f}x{rect.height:.2f} pt "
                f"| {x_dpi:.1f}x{y_dpi:.1f} ppi | mode={mode} "
                f"| bpc={bit_depth} | {origin} | rotation={page.rotation}"
            )
            lines.append(info)
            print(info)

        strip_icc_profiles(out)
        out.save(str(output_pdf), garbage=4, deflate=True, clean=True)
    finally:
        out.close()
        src.close()

    report.write_text("\n".join(lines), encoding="utf-8")
    print("\nDONE")
    print(f"Output : {output_pdf}")
    print(f"Report : {report}")
    return output_pdf


def main():
    if len(sys.argv) < 2:
        print("Drag and drop a PDF onto Convert_600_DPI.bat")
        if sys.stdin and sys.stdin.isatty():
            input("\nPress Enter to close...")
        return 1
    code = 0
    for arg in sys.argv[1:]:
        try:
            convert_pdf(Path(arg.strip('"')).resolve())
        except Exception as e:
            code = 1
            print("\nERROR:", e)
            traceback.print_exc()
    if sys.stdin and sys.stdin.isatty():
        input("\nPress Enter to close...")
    return code

if __name__ == "__main__":
    raise SystemExit(main())
