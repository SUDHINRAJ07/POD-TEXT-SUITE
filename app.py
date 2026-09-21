import os
import sys
import time
import shutil
import socket
import asyncio
import threading
import subprocess
import urllib.request
from pathlib import Path
from typing import Optional
from queue import Queue, Empty

import fitz
import uvicorn
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

# Resolve base directory (handles PyInstaller bundle MEIPASS vs standard python)
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys._MEIPASS)
else:
    BASE_DIR = Path(__file__).resolve().parent

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Import PDF tool engines
import align_pdf
import border_pdf
import convert_600dpi
import pdf_crop_engine
import resize_pdf

# Setup FastAPI App
app = FastAPI(title="POD Text Suite - PDF Processing Suite")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Workspace configuration
DEFAULT_OUTPUT_DIR = Path.home() / "Pictures" / "pod text suite" / "outputs"
DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

class WorkspaceConfig:
    def __init__(self):
        self.outputs_dir = str(DEFAULT_OUTPUT_DIR)

workspace_config = WorkspaceConfig()


# Stdout capture helper for streaming tool logs
class OutputLogger:
    def __init__(self, queue):
        self.queue = queue
        self._stdout = sys.stdout
        self._stderr = sys.stderr

    def write(self, data):
        if data:
            for line in data.splitlines():
                stripped = line.strip()
                if stripped:
                    self.queue.put(f"LOG: {stripped}\n")
        try:
            self._stdout.write(data)
        except Exception:
            pass

    def flush(self):
        try:
            self._stdout.flush()
        except Exception:
            pass


def run_func_in_thread(func, log_queue, *args, **kwargs):
    logger = OutputLogger(log_queue)
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = logger
    sys.stderr = logger
    try:
        func(*args, **kwargs)
    except Exception as e:
        log_queue.put(f"ERROR: {str(e)}\n")
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        log_queue.put(None)


async def stream_tool_execution(func, *args, success_msg="", **kwargs):
    log_queue = Queue()
    thread = threading.Thread(
        target=run_func_in_thread,
        args=(func, log_queue, *args),
        kwargs=kwargs,
        daemon=True
    )
    thread.start()

    finished = False
    has_error = False

    while not finished:
        try:
            item = log_queue.get_nowait()
            if item is None:
                finished = True
                if not has_error:
                    yield f"SUCCESS: {success_msg}\n"
            else:
                if item.startswith("ERROR:"):
                    has_error = True
                yield item
        except Empty:
            if not thread.is_alive() and log_queue.empty():
                finished = True
                if not has_error:
                    yield f"SUCCESS: {success_msg}\n"
            else:
                await asyncio.sleep(0.05)


# API Endpoints
@app.get("/api/workspace")
async def get_workspace():
    return {"outputs_dir": workspace_config.outputs_dir}


@app.post("/api/workspace")
async def update_workspace(data: dict):
    new_dir = data.get("outputs_dir", "").strip()
    if new_dir:
        os.makedirs(new_dir, exist_ok=True)
        workspace_config.outputs_dir = new_dir
    return {"outputs_dir": workspace_config.outputs_dir}


@app.post("/api/pdf_info")
async def pdf_info(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        doc = fitz.open(stream=contents, filetype="pdf")
        pages = len(doc)
        doc.close()
        return {"pages": pages}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/align")
async def api_align(
    file: UploadFile = File(...),
    output_dir: Optional[str] = Form(None),
    horizontal_align: Optional[str] = Form(None),
    tpl_w_mm: Optional[float] = Form(None),
    tpl_h_mm: Optional[float] = Form(None)
):
    out_dir = Path(output_dir.strip()) if (output_dir and output_dir.strip()) else Path(workspace_config.outputs_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    input_path = out_dir / file.filename
    with open(input_path, "wb") as f:
        f.write(await file.read())

    stem = Path(file.filename).stem
    out_path = out_dir / f"{stem}_aligned.pdf"

    tpl_w = int(tpl_w_mm) if tpl_w_mm else None
    tpl_h = int(tpl_h_mm) if tpl_h_mm else None

    return StreamingResponse(
        stream_tool_execution(
            align_pdf.run,
            input_path,
            out_path,
            tpl_w=tpl_w,
            tpl_h=tpl_h,
            success_msg=f"Output saved at {out_path}"
        ),
        media_type="text/plain"
    )


@app.post("/api/border")
async def api_border(
    file: UploadFile = File(...),
    output_dir: Optional[str] = Form(None),
    top: float = Form(0.0),
    bottom: float = Form(0.0),
    left: float = Form(0.0),
    right: float = Form(0.0)
):
    out_dir = Path(output_dir.strip()) if (output_dir and output_dir.strip()) else Path(workspace_config.outputs_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    input_path = out_dir / file.filename
    with open(input_path, "wb") as f:
        f.write(await file.read())

    stem = Path(file.filename).stem
    out_path = out_dir / f"{stem}_border.pdf"

    return StreamingResponse(
        stream_tool_execution(
            border_pdf.add_borders,
            str(input_path),
            str(out_path),
            top,
            right,
            left,
            bottom,
            success_msg=f"Output saved at {out_path}"
        ),
        media_type="text/plain"
    )


@app.post("/api/convert")
async def api_convert(
    file: UploadFile = File(...),
    output_dir: Optional[str] = Form(None)
):
    out_dir = Path(output_dir.strip()) if (output_dir and output_dir.strip()) else Path(workspace_config.outputs_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    input_path = out_dir / file.filename
    with open(input_path, "wb") as f:
        f.write(await file.read())

    def run_convert():
        res_path = convert_600dpi.convert_pdf(input_path)
        final_path = out_dir / res_path.name
        if res_path != final_path and res_path.exists():
            shutil.move(str(res_path), str(final_path))

    stem = Path(file.filename).stem
    expected_out = out_dir / f"{stem}_600dpi.pdf"

    return StreamingResponse(
        stream_tool_execution(
            run_convert,
            success_msg=f"Output saved at {expected_out}"
        ),
        media_type="text/plain"
    )


@app.post("/api/crop")
async def api_crop(
    file: UploadFile = File(...),
    output_dir: Optional[str] = Form(None)
):
    out_dir = Path(output_dir.strip()) if (output_dir and output_dir.strip()) else Path(workspace_config.outputs_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    input_path = out_dir / file.filename
    with open(input_path, "wb") as f:
        f.write(await file.read())

    stem = Path(file.filename).stem
    crop_out_dir = out_dir / f"{stem}_cropped"

    return StreamingResponse(
        stream_tool_execution(
            pdf_crop_engine.process_pdf,
            str(input_path),
            str(crop_out_dir),
            success_msg=f"Output saved at {crop_out_dir}"
        ),
        media_type="text/plain"
    )


def resize_helper(input_path: Path, output_dir: Path, template_id: str):
    if template_id not in resize_pdf.TEMPLATES:
        template_id = "1"
    w_mm, h_mm, label = resize_pdf.TEMPLATES[template_id]
    w_pt = w_mm * resize_pdf.MM_TO_PT
    h_pt = h_mm * resize_pdf.MM_TO_PT

    stem = input_path.stem
    output_path = output_dir / f"{stem}_{label}.pdf"

    src = fitz.open(str(input_path))
    out = fitz.open()

    total_pages = len(src)
    print(f"Pages : {total_pages}", flush=True)
    print(f"Total Pages : {total_pages}", flush=True)
    print(f"Loaded {total_pages} pages.", flush=True)

    for i, page in enumerate(src, start=1):
        print(f"Processing page {i} of {total_pages}...", flush=True)
        orig_rect = page.rect
        orig_w = orig_rect.width
        orig_h = orig_rect.height

        new_page = out.new_page(width=w_pt, height=h_pt)
        x_offset = (w_pt - orig_w) / 2
        y_offset = (h_pt - orig_h) / 2
        target_rect = fitz.Rect(
            x_offset, y_offset, x_offset + orig_w, y_offset + orig_h
        )
        new_page.show_pdf_page(target_rect, src, page.number)

    out.save(str(output_path), garbage=0, deflate=False, clean=False)
    src.close()
    out.close()
    print(f"Output saved at: {output_path}", flush=True)


@app.post("/api/resize")
async def api_resize(
    file: UploadFile = File(...),
    output_dir: Optional[str] = Form(None),
    template_id: Optional[str] = Form("1")
):
    out_dir = Path(output_dir.strip()) if (output_dir and output_dir.strip()) else Path(workspace_config.outputs_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    input_path = out_dir / file.filename
    with open(input_path, "wb") as f:
        f.write(await file.read())

    stem = Path(file.filename).stem
    label = resize_pdf.TEMPLATES.get(template_id, resize_pdf.TEMPLATES["1"])[2]
    expected_out = out_dir / f"{stem}_{label}.pdf"

    return StreamingResponse(
        stream_tool_execution(
            resize_helper,
            input_path,
            out_dir,
            template_id or "1",
            success_msg=f"Output saved at {expected_out}"
        ),
        media_type="text/plain"
    )


# Serve root HTML and static files with Cache-Control headers disabled
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0"
}

@app.get("/")
async def get_index():
    index_file = BASE_DIR / "index.html"
    return HTMLResponse(
        content=index_file.read_text(encoding="utf-8"),
        headers=NO_CACHE_HEADERS
    )


@app.get("/{filename}")
async def get_root_file(filename: str):
    file_path = BASE_DIR / filename
    if file_path.exists() and file_path.is_file():
        return FileResponse(file_path, headers=NO_CACHE_HEADERS)
    raise HTTPException(status_code=404, detail="File not found")


# Server & Desktop GUI App Launcher with Dynamic Port and Readiness Verification
def find_free_port(start_port=8085):
    for p in range(start_port, start_port + 50):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', p))
                return p
        except OSError:
            continue
    return start_port


def start_server(port):
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", loop="asyncio")
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None  # No-op to avoid signal errors in background threads
        loop.run_until_complete(server.serve())
    except Exception as e:
        print(f"Server execution error on port {port}: {e}")


def wait_for_server(port, timeout=15.0):
    url = f"http://127.0.0.1:{port}/api/workspace"
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'PODTextSuite/1.0'})
            with urllib.request.urlopen(req, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.1)
    return False


def launch_app():
    # Clear old Edge profile cache directory
    profile_dir = Path.home() / ".pod_text_suite_profile"
    try:
        if profile_dir.exists():
            shutil.rmtree(profile_dir, ignore_errors=True)
    except Exception:
        pass

    # Find free port starting at 8085
    port = find_free_port(8085)

    # Start FastAPI server in a background daemon thread
    server_thread = threading.Thread(target=start_server, args=(port,), daemon=True)
    server_thread.start()

    # Wait until socket is listening and server responds HTTP 200
    server_ready = wait_for_server(port, timeout=15.0)
    if not server_ready:
        print(f"Warning: Server initialization timeout on port {port}. Proceeding with launch...")

    url = f"http://127.0.0.1:{port}"

    # Priority 1: Try PyWebView native desktop window first
    try:
        import webview
        icon_path = str(BASE_DIR / "app_icon.ico")
        window = webview.create_window(
            title="POD Text Suite - PDF Processing Suite",
            url=url,
            width=1340,
            height=900,
            resizable=True,
            min_size=(900, 600)
        )
        webview.start(icon=icon_path if os.path.exists(icon_path) else None)
        return
    except Exception as err:
        print(f"pywebview window initialization skipped ({err}). Launching Edge app mode...")

    # Priority 2: Try MS Edge App Mode
    try:
        edge_paths = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            shutil.which("msedge")
        ]
        edge_bin = next((p for p in edge_paths if p and os.path.exists(p)), None)
        if edge_bin:
            subprocess.run([
                edge_bin,
                f"--app={url}",
                f"--window-name=POD Text Suite",
                f"--user-data-dir={profile_dir}",
                "--disable-http-cache",
                "--disk-cache-size=1"
            ])
            return
    except Exception as e:
        print(f"Edge App Mode launch error: {e}")

    # Priority 3: Fallback to default web browser
    import webbrowser
    webbrowser.open(url)


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    launch_app()
