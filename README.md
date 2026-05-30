# Weekly Photo Organizer

A desktop app for organizing photos into a 53-week yearly calendar. Drag photos from a source folder onto week slots, auto-generate collages for weeks with multiple photos, and export everything as sequentially-named JPEG files.

## Features

- **Drag & drop** photos onto weekly slots (supports JPG, PNG, HEIC/HEIF)
- **Auto collage** — dropping 2–4 photos on a week generates a composite image (2-up, 3-up, or 2×2 grid)
- **Collage editor** — pan and zoom each slot independently; adjust spacing between frames
- **EXIF-aware sorting** — source photos sorted by capture date (falls back to file modification time)
- **Save / Load session** — persists work to `~/.weekly_photo_organizer_state.json`
- **Export** — converts assigned weeks to `001.jpg`–`053.jpg` in a `Sorted_<year>/` subfolder

## Requirements

- Python 3.9+
- macOS (folder picker uses `osascript`)

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```bash
python main.py
```

1. Set the **Year** in the header.
2. Click **Select Source** to choose a folder of photos.
3. Drag photos from the left panel onto week cards on the right.
4. Right-click a week card to **Reset** it or **Adjust Collage** (for multi-photo weeks).
5. Click **Save** to checkpoint progress; **Load** to restore a previous session.
6. Click **Process & Rename** to export all assigned weeks to `Sorted_<year>/`.

## Project Structure

```
main.py          — NiceGUI app, UI layout, drag-and-drop logic, state management
collage_utils.py — Pillow-based collage generator (pan/zoom/crop per slot)
requirements.txt — Python dependencies
```

## Dependencies

| Package | Purpose |
|---|---|
| `nicegui[native]` | Desktop UI framework |
| `pillow` | Image processing and JPEG export |
| `pillow-heif` | HEIC/HEIF file support |
