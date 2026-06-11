import os
import shutil
import datetime
import subprocess
import asyncio
import json
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from nicegui import ui, app, native
from PIL import Image, ExifTags, ImageOps
from pillow_heif import register_heif_opener
# import easygui # Removed due to Mac tkinter issues

# 1. Register HEIC opener
register_heif_opener()

# Import helper
from collage_utils import generate_collage

# State file path for save/load functionality
STATE_FILE_PATH = Path.home() / '.weekly_photo_organizer_state.json'


# --- Global State ---
state = {
    'year': datetime.date.today().year + 1,
    'source_folder': '',
    'images': [],  # List of Path objects
    'weeks_data': {}, # Key: Week Number (0-52), Value: Path or None (Display Image)
    'weeks_originals': {}, # Key: Week Number, Value: List[Path] (Original Source Images)
    'dragged_image': None,
    'drag_source': None, # 'source' or int (week number)
    'preview_image': None, # current preview path
    'weeks_collage_config': {}, # Key: Week Number, Value: {'spacing': int, 'slots': [configs...]}
}

# --- Helper Functions ---

_creation_date_cache: Dict[Path, datetime.datetime] = {}

def get_image_creation_date(file_path: Path) -> datetime.datetime:
    """Extracts creation date from EXIF or falls back to file modification time."""
    cached = _creation_date_cache.get(file_path)
    if cached:
        return cached

    result = None
    try:
        with Image.open(file_path) as image:
            exif = image.getexif()
            # 36867 (DateTimeOriginal) lives in the Exif sub-IFD; 306 (DateTime) in IFD0
            date_str = exif.get_ifd(0x8769).get(36867) or exif.get(306)

        if date_str:
            result = datetime.datetime.strptime(date_str, '%Y:%m:%d %H:%M:%S')
    except Exception as e:
        print(f"Error reading EXIF for {file_path.name}: {e}")

    if result is None:
        # Fallback to file modification time
        result = datetime.datetime.fromtimestamp(file_path.stat().st_mtime)

    _creation_date_cache[file_path] = result
    return result

def get_display_size(file_path: Path) -> Tuple[int, int]:
    """Image size after EXIF rotation (swaps w/h for rotated photos), without decoding pixels."""
    with Image.open(file_path) as img:
        w, h = img.size
        orientation = img.getexif().get(0x0112, 1)
    if orientation in (5, 6, 7, 8):
        w, h = h, w
    return w, h

def get_weeks_for_year(year: int) -> List[Tuple[datetime.date, datetime.date]]:
    """
    Returns a list of 53 tuples (start_date, end_date) representing weeks.
    Week 1 starts on the Sunday of the week containing Jan 1st.
    """
    jan1 = datetime.date(year, 1, 1)
    # weekday(): Mon=0, Sun=6.
    # We want Start Date to be the Sunday.
    # If Jan 1 is Sunday (6), offset is 0.
    # If Jan 1 is Monday (0), offset is -1 (previous day is Sunday?) 
    # Wait, "Week 1 starts on SUNDAY ... containing Jan 1"
    # Logic: Go back to the nearest Sunday.
    
    # Python weekday: Mon=0, Tue=1, ... Sun=6
    # (jan1.weekday() + 1) % 7 gives days since Sunday?
    # Sun(6) + 1 = 7 % 7 = 0. Perfect.
    # Mon(0) + 1 = 1. We need to subtract 1 day.
    
    days_since_sunday = (jan1.weekday() + 1) % 7
    start_date = jan1 - datetime.timedelta(days=days_since_sunday)
    
    weeks = []
    current_start = start_date
    for _ in range(53):
        current_end = current_start + datetime.timedelta(days=6)
        weeks.append((current_start, current_end))
        current_start = current_end + datetime.timedelta(days=1)
        
    return weeks

def load_images():
    """Loads images from source folder, sorts by creation date."""
    folder = state['source_folder']
    if not folder or not os.path.isdir(folder):
        ui.notify('Invalid source folder')
        return

    supported_exts = {'.jpg', '.jpeg', '.png', '.heic', '.heif', '.hif'}
    files = [
        p for p in Path(folder).iterdir() 
        if p.is_file() and p.suffix.lower() in supported_exts
    ]
    
    # Sort with key caching
    files_with_dates = []
    for f in files:
        files_with_dates.append((f, get_image_creation_date(f)))
        
    files_with_dates.sort(key=lambda x: x[1])
    state['images'] = [x[0] for x in files_with_dates]
    refresh_drawer_ui()

def clear_drag_state():
    state['dragged_image'] = None
    state['drag_source'] = None

def refresh_week_display(week_num: int):
    """Recomputes a week's display image from its assigned originals."""
    originals = state['weeks_originals'].get(week_num, [])
    if not originals:
        state['weeks_data'][week_num] = None
        state['weeks_collage_config'].pop(week_num, None)
    elif len(originals) == 1:
        state['weeks_data'][week_num] = originals[0]
        state['weeks_collage_config'].pop(week_num, None)
    else:
        config = state['weeks_collage_config'].get(week_num, {})
        state['weeks_data'][week_num] = generate_collage(
            originals,
            Path(state['source_folder']),
            spacing=config.get('spacing', 0),
            slot_configs=config.get('slots'),
        )

# Editor preview canvas: same 4:3 ratio as the 1600x1200 collage output,
# small enough to fit the 1200x800 native window without clipping
EDITOR_W, EDITOR_H = 720, 540
PREVIEW_SCALE = EDITOR_W / 1600.0

def get_editor_slot_dims(qty: int, spacing: int, idx: int) -> Tuple[float, float]:
    """Slot size (w, h) in editor-preview pixels, mirroring the collage layouts."""
    sp = spacing * PREVIEW_SCALE  # spacing is in collage pixels
    if qty == 2:
        return ((EDITOR_W - sp) / 2, EDITOR_H)
    if qty == 3:
        w = (EDITOR_W - sp) / 2
        if idx == 0:
            return (w, EDITOR_H)
        return (w, (EDITOR_H - sp) / 2)
    if qty >= 4:
        return ((EDITOR_W - sp) / 2, (EDITOR_H - sp) / 2)
    return (EDITOR_W, EDITOR_H)

def save_state():
    """Saves current state to a JSON file for later resumption."""
    try:
        # Convert Path objects to strings for JSON serialization
        save_data = {
            'year': state['year'],
            'source_folder': state['source_folder'],
            'images': [str(p) for p in state['images']],
            'weeks_data': {str(k): str(v) if v else None for k, v in state['weeks_data'].items()},
            'weeks_originals': {str(k): [str(p) for p in v] for k, v in state['weeks_originals'].items()},
            'weeks_collage_config': {str(k): v for k, v in state.get('weeks_collage_config', {}).items()},
        }
        
        with open(STATE_FILE_PATH, 'w') as f:
            json.dump(save_data, f, indent=2)
        
        ui.notify('Progress saved successfully!', type='positive')
    except Exception as e:
        ui.notify(f'Error saving state: {e}', type='negative')

def load_state():
    """Loads state from JSON file and restores the session."""
    global folder_input, year_input

    if not STATE_FILE_PATH.exists():
        ui.notify('No saved session found.', type='warning')
        return
    
    try:
        with open(STATE_FILE_PATH, 'r') as f:
            save_data = json.load(f)
        
        # Restore state
        state['year'] = save_data.get('year', datetime.date.today().year + 1)
        state['source_folder'] = save_data.get('source_folder', '')
        state['images'] = [Path(p) for p in save_data.get('images', [])]
        state['weeks_data'] = {int(k): Path(v) if v else None for k, v in save_data.get('weeks_data', {}).items()}
        state['weeks_originals'] = {int(k): [Path(p) for p in v] for k, v in save_data.get('weeks_originals', {}).items()}
        
        # Restore collage config
        # Just direct copy for now since it is basic types (int, float, dict)
        if 'weeks_collage_config' in save_data:
             state['weeks_collage_config'] = {int(k): v for k, v in save_data['weeks_collage_config'].items()}
        else:
             state['weeks_collage_config'] = {}

        # Update UI
        if hasattr(folder_input, 'value'):
            folder_input.value = state['source_folder']
        if hasattr(year_input, 'value'):
            year_input.value = state['year']

        refresh_drawer_ui()
        refresh_grid_ui()
        
        ui.notify('Session restored successfully!', type='positive')
    except Exception as e:
        ui.notify(f'Error loading state: {e}', type='negative')

def reset_cell(week_num: int):
    """Resets a week cell, returning all assigned photos back to the source panel."""
    # Get all original images from this cell
    originals = state['weeks_originals'].get(week_num, [])
    
    # Return them to state['images']
    for img_path in originals:
        if img_path not in state['images']:
            state['images'].append(img_path)
    
    # Re-sort images by date
    state['images'].sort(key=lambda x: get_image_creation_date(x))
    
    # Clear the cell
    state['weeks_data'][week_num] = None
    state['weeks_originals'][week_num] = []
    state['weeks_collage_config'].pop(week_num, None)

    # Refresh UI
    refresh_drawer_ui()
    refresh_grid_ui()
    
    ui.notify(f'Week {week_num} reset')


def remove_image_from_source(img_path: Path):
    """Removes an image from the source list (but not from disk)."""
    if img_path in state['images']:
        state['images'].remove(img_path)
        # Also clear any selection or drag state if needed
        if state['dragged_image'] == img_path:
            state['dragged_image'] = None
            state['drag_source'] = None
        refresh_drawer_ui()
        ui.notify('Image removed from list')

# --- UI Components ---

@ui.refreshable
def refresh_drawer_ui():
    """Refreshes the left column with draggable image cards."""
    left_drawer.clear()
    with left_drawer:
        # Source is also a Drop Zone for returning images
        left_drawer.classes('relative')
        
        # Overlay for drop indication or just handle on the container
        def on_drop(e):
            dragged = state['dragged_image']
            source = state['drag_source']
            if dragged and source != 'source':
                # Figure out what to return. Dragging a collage thumbnail
                # (a generated temp file, not one of the originals) returns
                # all of that week's photos.
                returned = [dragged]
                if isinstance(source, int):
                    originals = state['weeks_originals'].get(source, [])
                    if originals and dragged not in originals:
                        returned = list(originals)
                    for img in returned:
                        if img in originals:
                            originals.remove(img)
                    refresh_week_display(source)
                    refresh_grid_ui()

                # Return to source (never the generated collage file itself)
                changed = False
                for img in returned:
                    if img not in state['images'] and img.parent.name != 'temp_collages':
                        state['images'].append(img)
                        changed = True
                if changed:
                    # Resort by date
                    state['images'].sort(key=get_image_creation_date)

                clear_drag_state()
                refresh_drawer_ui()
                ui.notify('Image returned to source')

        left_drawer.on('drop', on_drop)
        # Client side prop for smooth drop
        left_drawer.props('ondragover="event.preventDefault()"')

        if not state['images']:
            ui.label('No images found or all assigned.').classes('text-gray-400 italic')
            return
            
        # Grid Layout
        with ui.grid(columns=3).classes('w-full gap-2 p-1'): 
            for img_path in state['images']:
                # Draggable Card
                # Use a specific container for each to be neat
                with ui.card().classes('p-0 cursor-move border-0 shadow-none bg-transparent relative group') as card:
                    card.props('draggable')
                    
                    def on_drag_start(e, p=img_path):
                        state['dragged_image'] = p
                        state['drag_source'] = 'source'

                    card.on('dragstart', on_drag_start)
                    # Clear drag state even if the drop never lands on a target,
                    # otherwise the zoom preview stays blocked
                    card.on('dragend', lambda e: clear_drag_state())
                    
                    # Display Date & Square Thumb
                    c_date = get_image_creation_date(img_path)
                    date_str = c_date.strftime('%Y-%m-%d %H:%M')
                    
                    with ui.column().classes('w-full items-center p-0 gap-0'):
                        # Image is standard, draggable via parent
                        ui.image(img_path).classes('w-full h-24 object-cover rounded')
                        
                        ui.label(date_str).classes('text-[10px] text-gray-600 leading-tight text-center')

                    # Zoom Icon Overlay
                    # Use 'absolute' positioning
                    with ui.icon('zoom_in', color='white').classes('absolute top-1 right-1 bg-black/50 rounded-full p-1 cursor-pointer hover:bg-blue-600 transition-colors') as zoom_btn:
                         zoom_btn.on('click', lambda e, p=img_path: open_preview(p))
                         # Prevent drag start on the icon itself
                         # We only stop mousedown to prevent drag. We ALLOW click to bubble (or handling it here is enough).
                         zoom_btn.props('draggable="false" onmousedown="event.stopPropagation()"')

                    # Context Menu for Delete
                    with card:
                        with ui.context_menu():
                            ui.menu_item('Delete', on_click=lambda p=img_path: remove_image_from_source(p))

weeks_grid = None

@ui.refreshable
def refresh_grid_ui():
    """Refreshes the right grid of weeks."""
    if weeks_grid:
        weeks_grid.clear()
        
    year = int(state['year'])
    weeks = get_weeks_for_year(year)
    
    with weeks_grid:
        for i, (start, end) in enumerate(weeks):
            week_num = i + 1
            wk_date_str = f"{start.strftime('%b %d')} - {end.strftime('%b %d')}"
            
            # Drop Zone Card
            with ui.card().classes('w-full h-32 p-1 relative border-2').style('border-color: #e5e7eb') as drop_card:
                drop_card.classes('hover:bg-blue-50 transition-colors')
                
                # Header
                with ui.row().classes('w-full justify-between items-start px-1'):
                    ui.label(f"W {week_num:02d}").classes('font-bold text-xs text-blue-800')
                    ui.label(wk_date_str).classes('text-xs text-gray-500')
                
                # Dropped Content Container
                content_area = ui.column().classes('w-full h-full justify-center items-center overflow-hidden')
                
                # Check if we have an image for this week
                current_img = state['weeks_data'].get(week_num)
                
                def render_assigned_image(img_p, container):
                    with container:
                        container.clear()
                        # Make assigned image draggable too (to move to another week or back source)
                        with ui.image(img_p).classes('w-full h-20 object-contain rounded cursor-move') as img_el:
                            img_el.props('draggable')
                            def on_drag_start_assigned(e, p=img_p, w=week_num):
                                state['dragged_image'] = p
                                state['drag_source'] = w
                            img_el.on('dragstart', on_drag_start_assigned)
                            img_el.on('dragend', lambda e: clear_drag_state())
                        
                if current_img:
                    render_assigned_image(current_img, content_area)
                    # Add right-click context menu for reset
                    with drop_card:
                        with ui.context_menu():
                            ui.menu_item('Reset Cell', on_click=lambda w=week_num: reset_cell(w))
                            # Add Adjust Collage Option if multi-image
                            if week_num in state['weeks_originals'] and len(state['weeks_originals'][week_num]) > 1:
                                ui.menu_item('Adjust Collage', on_click=lambda w=week_num: open_collage_editor(w))
                else:
                    with content_area:
                        ui.icon('add_photo_alternate', size='2em', color='grey-300')
                
                # Drop Logic
                # Crucial: 'ondragover' must prevent default to allow dropping. 
                # Doing this via props prevents server roundtrip latency issues.
                drop_card.props('ondragover="event.preventDefault()"')
                    
                def on_drop(e, w=week_num, c=content_area):
                    dragged = state['dragged_image']

                    if dragged:
                        # 1. Figure out which photos are moving. Dragging a collage
                        # thumbnail (a generated temp file, not one of the originals)
                        # moves all of that week's photos.
                        moved = [dragged]
                        for k in list(state['weeks_data'].keys()):
                            if k != w and state['weeks_data'][k] == dragged:
                                originals_k = state['weeks_originals'].get(k, [])
                                if dragged not in originals_k:
                                    if originals_k:
                                        moved = list(originals_k)
                                    else:
                                        state['weeks_data'][k] = None
                                break

                        # 2. Remove the moved photos from the source list and any other week
                        for img in moved:
                            if img in state['images']:
                                state['images'].remove(img)
                            for k in list(state['weeks_originals'].keys()):
                                if k != w and img in state['weeks_originals'][k]:
                                    state['weeks_originals'][k].remove(img)
                                    refresh_week_display(k)

                        # 3. Add to New Week (Accumulate)
                        current_originals = state['weeks_originals'].setdefault(w, [])
                        added = False
                        for img in moved:
                            # Avoid duplicates; never adopt a generated collage file as a photo
                            if img not in current_originals and img.parent.name != 'temp_collages':
                                current_originals.append(img)
                                added = True

                        # 4. Determine Display Image
                        if added:
                            if len(current_originals) > 1:
                                ui.notify(f'Generating collage for {len(current_originals)} images...')
                            # Layout changes when the photo count changes, so drop saved adjustments
                            state['weeks_collage_config'].pop(w, None)
                            refresh_week_display(w)

                        # 5. Global Refresh to ensure UI consistency
                        # This is slightly heavier but guarantees 0 duplication visual bugs
                        refresh_grid_ui()
                        refresh_drawer_ui()

                        ui.notify(f'Assigned to Week {w}')
                        clear_drag_state()

                # drop_card.on('dragover', on_dragover) # Removed server-side handler
                drop_card.on('drop', on_drop)
                

# --- Processing Logic ---

def get_sorted_folder() -> Path:
    return Path(state['source_folder']) / f"Sorted_{state['year']}"

def export_week(w_num: int, sorted_folder: Path):
    """Exports one week's display image as Sorted_<year>/NNN.jpg."""
    img_path = state['weeks_data'].get(w_num)
    if not img_path:
        return

    # Target Name: 001.jpg, 053.jpg
    target_path = sorted_folder / f"{w_num:03d}.jpg"

    with Image.open(img_path) as im:
        # Apply EXIF rotation so portrait phone photos aren't sideways
        im = ImageOps.exif_transpose(im)

        # Convert to RGB if necessary (e.g. from RGBA or CMYK)
        if im.mode != 'RGB':
            im = im.convert('RGB')

        im.save(target_path, 'JPEG', quality=95)

def process_and_organize():
    if not state['weeks_data']:
        ui.notify('No photos assigned to weeks!', type='warning')
        return

    folder = Path(state['source_folder'])
    if not folder.exists():
        ui.notify('Source folder seems missing.')
        return

    sorted_folder = get_sorted_folder()
    sorted_folder.mkdir(exist_ok=True)

    count = 0
    for w_num, img_path in state['weeks_data'].items():
        if not img_path: continue
        try:
            export_week(w_num, sorted_folder)
            count += 1
        except Exception as e:
            ui.notify(f"Error processing Week {w_num}: {e}", type='negative')

    ui.notify(f"Success! Processed {count} files into {sorted_folder.name}", type='positive')
    # Open folder
    # os.system(f'open "{sorted_folder}"') # Mac specific
    
    
# --- Main Layout ---

with ui.column().classes('w-full h-screen p-0'):
    
    # 0. Global Preview Overlay (Using ui.dialog for proper z-index)
    with ui.dialog() as preview_dialog, ui.card().classes('p-0 bg-transparent border-0 shadow-none items-center justify-center w-full h-full'):
        # Click background to close is default for dialog
        preview_image_el = ui.image().classes('max-w-[90vw] max-h-[90vh] object-contain cursor-pointer')
        preview_image_el.on('click', preview_dialog.close)
        
    def open_preview(path):
        if state['dragged_image']: return
        
        if 'preview_dialog' in globals() and preview_dialog:
            preview_image_el.set_source(str(path))
            preview_dialog.open()
        else:
             ui.notify("Error: Preview dialog not initialized", type='negative')

    # --- Collage Editor ---
    # We maintain a reference to the active editor state
    editor_state = {
        'week_num': None,
        'images': [], # list of paths
        'img_sizes': [], # (w, h) per image, after EXIF rotation
        'temp_configs': [], # list of {zoom, center_x, center_y}
        'spacing': 0,
        'dialog': None,
        'image_elements': [], # UI references to update styles
        'dragging_idx': None,
        'drag_start': (0, 0), # x, y
        'current_pan': [], # (tx, ty) for each slot
    }
    
    async def open_collage_editor(week_num):
        """Opens the interactive collage editor for a specific week."""
        ui.notify('Opening editor...')
        
        originals = state['weeks_originals'].get(week_num, [])
        if not originals or len(originals) < 2:
            ui.notify('Not enough images to edit collage')
            return
            
        config = state['weeks_collage_config'].get(week_num, {})
        
        # Init State
        editor_state['week_num'] = week_num
        editor_state['images'] = originals
        editor_state['spacing'] = config.get('spacing', 0)
        
        # Deep copy slots or init defaults
        existing_slots = config.get('slots', [])
        
        editor_state['temp_configs'] = []
        editor_state['current_pan'] = []
        editor_state['img_sizes'] = []

        # Calculate in thread to avoid blocking
        loop = asyncio.get_running_loop()
        qty = len(originals)

        for i in range(qty):
            if i < len(existing_slots):
                cfg = existing_slots[i].copy()
            else:
                cfg = {'center_x': 0.5, 'center_y': 0.5, 'zoom': 1.0}
            editor_state['temp_configs'].append(cfg)

            try:
                w, h = await loop.run_in_executor(None, get_display_size, originals[i])

                # The preview image covers its slot, so one source pixel spans
                # s_cover * zoom preview pixels. CSS applies translate(tx, ty)
                # scale(zoom), i.e. tx is in unscaled slot pixels.
                slot_w, slot_h = get_editor_slot_dims(qty, editor_state['spacing'], i)
                s_cover = max(slot_w / w, slot_h / h)
                tx = (0.5 - cfg['center_x']) * w * s_cover * cfg['zoom']
                ty = (0.5 - cfg['center_y']) * h * s_cover * cfg['zoom']
                editor_state['img_sizes'].append((w, h))
                editor_state['current_pan'].append([tx, ty])
            except Exception as e:
                print(f"Error reading image {originals[i]}: {e}")
                editor_state['img_sizes'].append(None)
                editor_state['current_pan'].append([0, 0])

        render_editor_content.refresh()
        editor_dialog.open()

    def render_editor_layout(container):
        with container:
            qty = len(editor_state['images'])
            spacing = editor_state['spacing']
            
            # Reset UI refs
            editor_state['image_elements'] = [None] * qty
            
            # Fixed aspect ratio container 4:3, scaled down from the
            # 1600x1200 output. Specific px makes translation math easier.
            W_preview = EDITOR_W
            H_preview = EDITOR_H
            
            with ui.element('div').style(f'width: {W_preview}px; height: {H_preview}px; background: white; position: relative;') as canvas:
                
                # Helper to create slot div
                def create_slot(idx, x, y, w, h):
                    with ui.element('div').style(f'position: absolute; left: {x}px; top: {y}px; width: {w}px; height: {h}px; overflow: hidden; border: 1px solid #ddd;') as slot:
                        # Event handlers on the SLOT (container) to handle mouse events
                        
                        img_path = editor_state['images'][idx]
                        tx, ty = editor_state['current_pan'][idx]
                        zoom = editor_state['temp_configs'][idx]['zoom']

                        # Size the element to the full cover-scaled photo (not the
                        # slot), centered, so panning reveals real photo content
                        # beyond the slot edges — matching the generated collage.
                        # Pass the Path directly so NiceGUI serves it itself
                        # (handles spaces/special chars in paths).
                        size = editor_state['img_sizes'][idx]
                        if size:
                            img_w, img_h = size
                            s_cover = max(w / img_w, h / img_h)
                            content_w = img_w * s_cover
                            content_h = img_h * s_cover
                            sizing = (f'position: absolute; left: 50%; top: 50%; '
                                      f'width: {content_w}px; height: {content_h}px; '
                                      f'margin-left: {-content_w / 2}px; margin-top: {-content_h / 2}px;')
                            im = ui.image(img_path).style(sizing)
                        else:
                            im = ui.image(img_path).classes('w-full h-full')
                        im.style(f'transform: translate({tx}px, {ty}px) scale({zoom}); transform-origin: center center; cursor: grab;')
                        im.props('draggable="false"') # Prevent native ghost drag

                        editor_state['image_elements'][idx] = im

                        # Interaction Handlers
                        # Generic .on() events deliver a GenericEventArguments whose
                        # payload lives in e.args (clientX, deltaY, ...), not as attributes.
                        def handle_mousedown(e, i=idx):
                            editor_state['dragging_idx'] = i
                            editor_state['drag_start'] = (e.args['clientX'], e.args['clientY'])

                        def handle_mousemove(e):
                            i = editor_state['dragging_idx']
                            if i is not None:
                                dx = e.args['clientX'] - editor_state['drag_start'][0]
                                dy = e.args['clientY'] - editor_state['drag_start'][1]

                                # Update Pan
                                c_pan = editor_state['current_pan'][i]
                                c_pan[0] += dx
                                c_pan[1] += dy

                                editor_state['drag_start'] = (e.args['clientX'], e.args['clientY'])

                                # Update UI
                                update_slot_transform(i)

                        def handle_mouseup(e):
                            editor_state['dragging_idx'] = None

                        # Scroll for Zoom (Ctrl/Cmd + wheel)
                        def handle_wheel(e, i=idx):
                            if e.args.get('ctrlKey') or e.args.get('metaKey'):  # Meta is Cmd on Mac
                                current_zoom = editor_state['temp_configs'][i]['zoom']
                                factor = 0.95 if e.args['deltaY'] > 0 else 1.05
                                new_zoom = max(0.1, min(5.0, current_zoom * factor))
                                editor_state['temp_configs'][i]['zoom'] = new_zoom
                                update_slot_transform(i)

                        im.on('mousedown', handle_mousedown, args=['clientX', 'clientY'])
                        # Move/up on the slot so fast drags aren't lost; throttle to
                        # keep the websocket traffic reasonable.
                        slot.on('mousemove', handle_mousemove, args=['clientX', 'clientY'], throttle=0.05)
                        slot.on('mouseup', handle_mouseup)
                        slot.on('mouseleave', handle_mouseup) # Safety
                        # .prevent stops the page from scrolling while zooming
                        slot.on('wheel.prevent', handle_wheel, args=['deltaY', 'ctrlKey', 'metaKey'])
                        
                        
                # Define Geometry based on Qty & Spacing
                sp = spacing * PREVIEW_SCALE

                if qty == 2:
                    w, h = get_editor_slot_dims(qty, spacing, 0)
                    create_slot(0, 0, 0, w, h)
                    create_slot(1, w + sp, 0, w, h)
                elif qty == 3:
                    w_left, _ = get_editor_slot_dims(qty, spacing, 0)
                    _, h_top = get_editor_slot_dims(qty, spacing, 1)
                    create_slot(0, 0, 0, w_left, H_preview)
                    create_slot(1, w_left + sp, 0, w_left, h_top)
                    create_slot(2, w_left + sp, h_top + sp, w_left, h_top)
                elif qty >= 4:
                     w, h = get_editor_slot_dims(qty, spacing, 0)
                     create_slot(0, 0, 0, w, h)
                     create_slot(1, w + sp, 0, w, h)
                     create_slot(2, 0, h + sp, w, h)
                     create_slot(3, w + sp, h + sp, w, h)

    def update_slot_transform(idx):
        im_el = editor_state['image_elements'][idx]
        if im_el:
            tx, ty = editor_state['current_pan'][idx]
            z = editor_state['temp_configs'][idx]['zoom']
            im_el.style(f'transform: translate({tx}px, {ty}px) scale({z}); transform-origin: center center;')
    
    async def save_collage_edits():
        ui.notify('Saving collage...')
        w_num = editor_state['week_num']
        originals = editor_state['images']
        qty = len(originals)
        spacing = editor_state['spacing']

        # 1. Convert Pan/Zoom back to normalized CenterX/Y.
        # Inverse of calculate_pan: the preview image covers its slot
        # (object-fit: cover), so one source pixel spans s_cover * zoom
        # preview pixels, and cx = 0.5 - tx / (orig_w * s_cover * zoom).
        final_configs = []
        for i, config_data in enumerate(editor_state['temp_configs']):
            tx, ty = editor_state['current_pan'][i]
            zoom = config_data['zoom']

            try:
                orig_w, orig_h = get_display_size(originals[i])
                slot_w, slot_h = get_editor_slot_dims(qty, spacing, i)
                s_cover = max(slot_w / orig_w, slot_h / orig_h)

                cx = 0.5 - tx / (orig_w * s_cover * zoom)
                cy = 0.5 - ty / (orig_h * s_cover * zoom)

                # Clamp the crop window inside the photo, mirroring the
                # generator, so reopening the editor matches the output
                target_aspect = slot_w / slot_h
                if orig_w / orig_h > target_aspect:
                    vis_w = orig_h * target_aspect / zoom
                    vis_h = orig_h / zoom
                else:
                    vis_w = orig_w / zoom
                    vis_h = (orig_w / target_aspect) / zoom

                if vis_w < orig_w:
                    half = (vis_w / 2) / orig_w
                    cx = min(max(cx, half), 1 - half)
                else:
                    cx = 0.5
                if vis_h < orig_h:
                    half = (vis_h / 2) / orig_h
                    cy = min(max(cy, half), 1 - half)
                else:
                    cy = 0.5

                final_configs.append({
                    'center_x': cx,
                    'center_y': cy,
                    'zoom': zoom
                })
            except Exception as e:
                print(f"Error calcing config for {i}: {e}")
                final_configs.append({'center_x': 0.5, 'center_y': 0.5, 'zoom': 1.0})

        # 2. Update State & Regenerate
        state['weeks_collage_config'][w_num] = {
            'spacing': spacing,
            'slots': final_configs
        }
        refresh_week_display(w_num)

        refresh_grid_ui()
        if editor_state['dialog']:
            editor_state['dialog'].close()
        ui.notify('Collage updated!')

        # 3. If this week was already exported, update the exported file too
        sorted_folder = get_sorted_folder()
        target = sorted_folder / f"{w_num:03d}.jpg"
        if target.exists():
            try:
                export_week(w_num, sorted_folder)
                ui.notify(f'Updated {sorted_folder.name}/{target.name}', type='positive')
            except Exception as e:
                ui.notify(f'Could not update {target.name}: {e}', type='negative')

    # 3. Define the Dialog ONCE (Global Scope in layout)
    # 'no-wrap' is essential: Quasar's .flex class adds flex-wrap: wrap, which
    # in short windows wraps the canvas into a second column off-screen,
    # making the dialog look blank.
    with ui.dialog() as editor_dialog, ui.card().classes('w-full max-w-7xl h-[90vh] p-0 flex flex-col no-wrap'):
         editor_state['dialog'] = editor_dialog
         
         # Header
         with ui.row().classes('w-full bg-gray-100 p-2 items-center justify-between'):
             ui.label('Adjust Collage (Drag to Pan, Cmd+Scroll to Zoom)').classes('font-bold')
             with ui.row().classes('gap-2'):
                 ui.button('Cancel', on_click=editor_dialog.close).classes('bg-red-400')
                 ui.button('Save', on_click=save_collage_edits).classes('bg-green-600')
         
         # Refreshable Content Area
         @ui.refreshable
         def render_editor_content():
             # Controls
             with ui.row().classes('px-4 py-2 gap-4 items-center'):
                 ui.label('Frame Spacing:')
                 def update_spacing(e):
                     editor_state['spacing'] = int(e.value)
                     render_editor_content.refresh()
                     
                 ui.slider(min=0, max=50, value=editor_state['spacing'], on_change=update_spacing).classes('w-48')

             # Canvas Area
             # 1. Ensure refreshable container fills space
             # 2. Add border to verify visibility
             with ui.element('div').classes('w-full flex-grow relative bg-gray-200 overflow-hidden flex items-center justify-center') as container:
                 render_editor_layout(container)
                 
         with ui.column().classes('w-full flex-grow p-0 gap-0'):
             render_editor_content()


    # 1. Header
    with ui.row().classes('w-full bg-blue-100 p-4 items-center gap-4'):
        ui.label('Weekly Photo Organizer').classes('text-xl font-bold text-blue-900')
        
        def on_year_change(e):
            if e.value is None:
                return
            state['year'] = int(e.value)
            refresh_grid_ui()

        year_input = ui.number(label='Year', value=state['year'], format='%.0f',
                               on_change=on_year_change).classes('w-24')
        

        async def pick_folder():
            # Use AppleScript to pick folder, works robustly on macOS without extra deps
            try:
                # Run async to avoid blocking UI loop
                # Command: choose folder with prompt "Select Source Folder"
                # 'POSIX path of' converts the AppleScript alias to standard path
                
                cmd = [
                    "osascript", "-e",
                    'return POSIX path of (choose folder with prompt "Select Source Folder")'
                ]
                
                # Execute in executor to be safe with blocking IO
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None, 
                    lambda: subprocess.run(cmd, capture_output=True, text=True)
                )
                
                if result.returncode == 0:
                    path = result.stdout.strip()
                    if path:
                        state['source_folder'] = path
                        folder_input.value = path
                        load_images()
                else:
                    # User cancelled (usually returncode 1) or error
                    pass 
                    
            except Exception as e:
                ui.notify(f"Error picking folder: {e}", type='negative')

        folder_input = ui.input('Source Directory').classes('w-96').props('readonly')
        ui.button('Select Source', icon='folder', on_click=pick_folder)
        
        ui.space()
        ui.button('Save', icon='bookmark', on_click=save_state).classes('bg-yellow-600')
        ui.button('Load', icon='restore', on_click=load_state).classes('bg-orange-600')
        ui.button('Process & Rename', icon='save', on_click=process_and_organize).classes('bg-green-600')

    # 2. Main Split Area
    with ui.splitter(value=25).classes('w-full h-full') as splitter:
        
        # Left Drawer (Source)
        with splitter.before:
            with ui.column().classes('w-full h-full p-2 bg-gray-50 overflow-y-auto'):
                ui.label('Source Photos').classes('font-bold text-gray-700 mb-2')
                left_drawer = ui.column().classes('w-full')
                refresh_drawer_ui()

        # Right Grid (Destination)
        with splitter.after:
            with ui.column().classes('w-full h-full p-4 bg-white overflow-y-auto'):
                ui.label('Weekly Plan').classes('font-bold text-gray-700 mb-2')
                # Grid wrapper
                weeks_grid = ui.grid(columns=4).classes('w-full gap-4')
                refresh_grid_ui()

# Start the App
if __name__ in {"__main__", "__mp_main__"}:
    # Note: 'native=True' creates a standalone window.
    ui.run(title='Weekly Photo Organizer', native=True, window_size=(1200, 800))
