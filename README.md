# POD Text Suite - Print Optimization Studio

A comprehensive, automated PDF processing and optimization suite designed for Print-on-Demand (POD) workflows. Built with Python, FastAPI, PyMuPDF, OpenCV, and modern responsive UI.

## Features

- **Crop Engine**: Automatically detect ink boundaries, remove scanner edge noise/lines, reject dust, and export tight cropped pages.
- **600 DPI Conversion**: Convert pages to high-resolution 600 DPI print-ready formats (monochrome / grayscale / CMYK).
- **Template Alignment**: Align PDF pages to standardized print book templates with custom margins and spreads.
- **Border Adder**: Add custom top, bottom, left, and right page borders.
- **Resize Tool**: Scale pages to industry-standard book trimming dimensions.
- **Web Interface**: Clean, responsive UI with real-time streaming progress logs and dark mode support.

## Tech Stack

- **Backend**: FastAPI, Uvicorn, Python 3.10+
- **PDF & Image Processing**: PyMuPDF (`fitz`), Pillow, NumPy, SciPy, OpenCV
- **Frontend**: HTML5, Tailwind CSS, Vanilla JS

---

## Local Setup & Running

1. **Clone the repository**:
   ```bash
   git clone https://github.com/SUDHINRAJ07/POD-TEXT-SUITE.git
   cd POD-TEXT-SUITE
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the application**:
   ```bash
   python app.py
   ```
   Or run directly with Uvicorn:
   ```bash
   uvicorn app:app --host 127.0.0.1 --port 8085 --reload
   ```

4. Open your browser and navigate to:
   ```
   http://127.0.0.1:8085
   ```

---

## Deploy Live (Render.com)

1. Sign up / Log in to [Render.com](https://render.com).
2. Click **New +** -> **Web Service**.
3. Connect your GitHub account and select repository: `SUDHINRAJ07/POD-TEXT-SUITE`.
4. Render will automatically detect `render.yaml` or use:
   - **Environment**: `Python`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn app:app --host 0.0.0.0 --port $PORT`
5. Click **Create Web Service**. Your live URL will be active in 2-3 minutes!
