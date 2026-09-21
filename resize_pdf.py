import sys
import os
import fitz  # PyMuPDF

TEMPLATES = {
    "1": (148, 210, "148x210"),
    "2": (152, 229, "152x229"),
    "3": (156, 234, "156x234"),
    "4": (170, 244, "170x244"),
    "5": (178, 254, "178x254"),
    "6": (210, 297, "210x297"),
}

MM_TO_PT = 72 / 25.4


def main():
    print("=========================================")
    print(" PDF TEMPLATE RESIZER")
    print("=========================================")
    print()

    if len(sys.argv) < 2:
        print("ERROR: No PDF file provided.")
        print("Drag and drop a PDF file onto the BAT launcher.")
        input("\nPress any key to exit...")
        sys.exit(1)

    input_path = sys.argv[1]

    if not os.path.isfile(input_path):
        print(f"ERROR: File not found:\n{input_path}")
        input("\nPress any key to exit...")
        sys.exit(1)

    if not input_path.lower().endswith(".pdf"):
        print(f"ERROR: File is not a PDF:\n{input_path}")
        input("\nPress any key to exit...")
        if sys.stdin and sys.stdin.isatty():
            input("\nPress any key to exit...")
        sys.exit(1)

    input_name = os.path.basename(input_path)
    print(f"Input:\n{input_name}")
    print()

    if len(sys.argv) > 2:
        choice = sys.argv[2].strip()
    else:
        print("Select Template:")
        print()
        for key, (w, h, label) in TEMPLATES.items():
            print(f"  {key}) {w} x {h} mm")
        print()
        choice = input("Choice: ").strip()

    if choice not in TEMPLATES:
        print("\nERROR: Invalid choice.")
        if sys.stdin and sys.stdin.isatty():
            input("\nPress any key to exit...")
        sys.exit(1)

    w_mm, h_mm, label = TEMPLATES[choice]
    w_pt = w_mm * MM_TO_PT
    h_pt = h_mm * MM_TO_PT
    new_rect = fitz.Rect(0, 0, w_pt, h_pt)

    base, _ = os.path.splitext(input_path)
    output_path = f"{base}_{label}.pdf"

    try:
        src = fitz.open(input_path)
    except Exception as e:
        print(f"ERROR: Could not open PDF:\n{e}")
        if sys.stdin and sys.stdin.isatty():
            input("\nPress any key to exit...")
        sys.exit(1)

    out = fitz.open()

    total_pages = len(src)
    print(f"Pages : {total_pages}", flush=True)
    print(f"Total Pages : {total_pages}", flush=True)
    print(f"Loaded {total_pages} pages.", flush=True)

    for i, page in enumerate(src, start=1):
        print(f"Processing page {i} of {total_pages}...")
        orig_rect = page.rect
        orig_w = orig_rect.width
        orig_h = orig_rect.height

        new_page = out.new_page(width=w_pt, height=h_pt)
        x_offset = (w_pt - orig_w) / 2
        y_offset = (h_pt - orig_h) / 2
        target_rect = fitz.Rect(
            x_offset,
            y_offset,
            x_offset + orig_w,
            y_offset + orig_h,
        )
        new_page.show_pdf_page(target_rect, src, page.number)

    try:
        out.save(
            output_path,
            garbage=0,
            deflate=False,
            clean=False,
        )
    except Exception as e:
        print(f"\nERROR: Could not save output PDF:\n{e}")
        src.close()
        out.close()
        if sys.stdin and sys.stdin.isatty():
            input("\nPress any key to exit...")
        sys.exit(1)

    src.close()
    out.close()

    output_name = os.path.basename(output_path)
    print()
    print("Completed.")
    print()
    print(f"Output:\n{output_name}")
    if sys.stdin and sys.stdin.isatty():
        input("\nPress any key to exit...")


if __name__ == "__main__":
    main()
