import argparse
import io
import os
import re
import shutil
import sys

def convert_mm_to_pt(mm_value):
    """Convert millimeters to PDF points (1 inch = 25.4 mm = 72 pt)."""
    return float(mm_value) * (72.0 / 25.4)

def add_borders(input_path, output_path, top_mm, right_mm, left_mm, bottom_mm):
    try:
        import fitz  # PyMuPDF
        from PIL import Image
    except ImportError as e:
        print(f"Error: Required library missing ({e}). Please install: pip install pymupdf Pillow", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(input_path):
        print(f"Error: Input file does not exist: {input_path}", file=sys.stderr)
        sys.exit(1)

    # Convert border values from mm to points
    top_pt = convert_mm_to_pt(top_mm)
    right_pt = convert_mm_to_pt(right_mm)
    left_pt = convert_mm_to_pt(left_mm)
    bottom_pt = convert_mm_to_pt(bottom_mm)

    print(f"Opening input PDF: {input_path}")
    print(f"Specified margins (mm): Top={top_mm}, Right={right_mm}, Left={left_mm}, Bottom={bottom_mm}")
    print(f"Calculated margins (pt): Top={top_pt:.2f}, Right={right_pt:.2f}, Left={left_pt:.2f}, Bottom={bottom_pt:.2f}")

    # Copy the input PDF to the output path to prepare for incremental saving.
    # This ensures that the original file's structure, metadata, fonts, and images
    # remain 100% untouched and we only append the new vector overlays.
    try:
        if os.path.exists(output_path):
            os.remove(output_path)
        shutil.copy2(input_path, output_path)
    except Exception as e:
        print(f"Error preparing output file: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        doc = fitz.open(output_path)
    except Exception as e:
        print(f"Error opening copy of PDF for editing: {e}", file=sys.stderr)
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except:
                pass
        sys.exit(1)

    try:
        num_pages = len(doc)
        print(f"Pages : {num_pages}", flush=True)
        print(f"Total Pages : {num_pages}", flush=True)
        print(f"Processing {num_pages} page(s)...", flush=True)

        for page_num in range(num_pages):
            print(f"  p{page_num+1:03d}: Adding margin borders... @600dpi", flush=True)
            page = doc[page_num]
            
            # page.rect reflects the rotated page size as displayed
            rect = page.rect
            w = rect.width
            h = rect.height
            x0, y0, x1, y1 = rect.x0, rect.y0, rect.x1, rect.y1

            # Validate that margins don't cover the entire page
            if (left_pt + right_pt) >= w:
                print(f"Warning: Page {page_num + 1}: Left & Right margins ({left_mm}mm + {right_mm}mm) exceed page width ({w * 25.4 / 72.0:.1f}mm). Skipping horizontal borders.", file=sys.stderr)
                page_left_pt = 0
                page_right_pt = 0
            else:
                page_left_pt = left_pt
                page_right_pt = right_pt

            if (top_pt + bottom_pt) >= h:
                print(f"Warning: Page {page_num + 1}: Top & Bottom margins ({top_mm}mm + {bottom_mm}mm) exceed page height ({h * 25.4 / 72.0:.1f}mm). Skipping vertical borders.", file=sys.stderr)
                page_top_pt = 0
                page_bottom_pt = 0
            else:
                page_top_pt = top_pt
                page_bottom_pt = bottom_pt

            # Define the visual rectangles (coordinates relative to the page as visually displayed)
            rects_to_draw = []

            # Left border
            if page_left_pt > 0:
                rects_to_draw.append(fitz.Rect(x0, y0, x0 + page_left_pt, y1))

            # Right border
            if page_right_pt > 0:
                rects_to_draw.append(fitz.Rect(x1 - page_right_pt, y0, x1, y1))

            # Top border
            if page_top_pt > 0:
                rects_to_draw.append(fitz.Rect(x0, y0, x1, y0 + page_top_pt))

            # Bottom border
            if page_bottom_pt > 0:
                rects_to_draw.append(fitz.Rect(x0, y1 - page_bottom_pt, x1, y1))

            # Draw each rectangle. If the page is rotated, we must convert the coordinates
            # from rotated visual space to the unrotated physical coordinates that insert_image expects.
            derot = page.derotation_matrix
            has_rotation = page.rotation != 0

            for r_rotated in rects_to_draw:
                if has_rotation:
                    r_unrotated = r_rotated * derot
                else:
                    r_unrotated = r_rotated

                # Create a 1-bit white image using Pillow at 600 DPI
                # PDF points are 72 points per inch, so we scale by 600 / 72
                scale_factor = 600.0 / 72.0
                img_w = max(1, int(round(r_rotated.width * scale_factor)))
                img_h = max(1, int(round(r_rotated.height * scale_factor)))
                size = (img_w, img_h)
                canvas = Image.new("1", size, 1)  # bitmap white
                
                # Save to in-memory bytes stream in PNG format (keeps mode "1" as 1-bit PNG)
                img_stream = io.BytesIO()
                canvas.save(img_stream, format="PNG", dpi=(600, 600))
                img_bytes = img_stream.getvalue()

                # Insert the 1-bit white image overlay
                img_xref = page.insert_image(r_unrotated, stream=img_bytes, overlay=True)

                # Update the image object's color space to DeviceGray (removes ICCBased color space)
                if img_xref > 0:
                    obj_str = doc.xref_object(img_xref)
                    new_obj_str = re.sub(r'/ColorSpace\s+\d+\s+\d+\s+R', '/ColorSpace /DeviceGray', obj_str)
                    doc.update_object(img_xref, new_obj_str)

        # Save the modifications incrementally to preserve original PDF fidelity and structure.
        doc.save(doc.name, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
        doc.close()
        print(f"Successfully processed PDF. Output saved at: {output_path}")

    except Exception as e:
        print(f"Error processing PDF: {e}", file=sys.stderr)
        try:
            doc.close()
        except:
            pass
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except:
                pass
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Add white borders to PDF pages.")
    parser.add_argument("--input", required=True, help="Path to input PDF file")
    parser.add_argument("--top", type=float, default=0.0, help="Top border in mm")
    parser.add_argument("--right", type=float, default=0.0, help="Right border in mm")
    parser.add_argument("--left", type=float, default=0.0, help="Left border in mm")
    parser.add_argument("--bottom", type=float, default=0.0, help="Bottom border in mm")
    
    args = parser.parse_args()

    # Generate output path: originalname_border.pdf in the same directory
    input_dir, input_file = os.path.split(args.input)
    name, ext = os.path.splitext(input_file)
    output_filename = f"{name}_border{ext}"
    output_path = os.path.join(input_dir, output_filename)

    add_borders(args.input, output_path, args.top, args.right, args.left, args.bottom)

if __name__ == "__main__":
    main()
