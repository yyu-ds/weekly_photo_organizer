import os
import shutil
import datetime
import subprocess
import asyncio
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from nicegui import ui, app, native
from PIL import Image, ExifTags
from pillow_heif import register_heif_opener
# import easygui # Removed due to Mac tkinter issues

# 1. Register HEIC opener
register_heif_opener()

# --- Global State ---
state = {
    'year': datetime.date.today().year + 1,
    'source_folder': '',
    'images': [],  # List of Path objects
    'weeks_data': {}, # Key: Week Number (0-52), Value: Path or None
    'dragged_image': None,
    'drag_source': None # 'source' or int (week number)
}

# --- Helper Functions ---

def get_image_creation_date(file_path: Path) -> datetime.datetime:
    """Extracts creation date from EXIF or falls back to file modification time."""
    try:
        image = Image.open(file_path)
        exif = image.getexif()
        # 36867 is DateTimeOriginal, 306 is DateTime
        date_str = exif.get(36867) or exif.get(306)
        
        if date_str:
            return datetime.datetime.strptime(date_str, '%Y:%m:%d %H:%M:%S')
    except Exception as e:
        print(f"Error reading EXIF for {file_path.name}: {e}")
    
    # Fallback to file creation/modification time
    stat = file_path.stat()
    return datetime.datetime.fromtimestamp(stat.st_mtime)

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

    supported_exts = {'.jpg', '.jpeg', '.png', '.heic', '.hif', '.hiff'}
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

def choose_folder():
    path = easygui.diropenbox(title="Select Source Folder")
    if path:
        state['source_folder'] = path
        folder_input.value = path
        load_images()

# --- UI Components ---

@ui.refreshable
def refresh_drawer_ui():
    """Refreshes the left column with draggable image cards."""
    left_drawer.clear()
    with left_drawer:
        # Source is also a Drop Zone for returning images
        left_drawer.classes('relative')
        
        # Overlay for drop indication or just handle on the container
        def on_dragover(e):
            e.sender.call_js('event.preventDefault()')
            
        def on_drop(e):
            dragged = state['dragged_image']
            source = state['drag_source']
            if dragged and source != 'source':
                # Return to source
                if dragged not in state['images']:
                    state['images'].append(dragged)
                    # Resort by date
                    state['images'].sort(key=lambda x: get_image_creation_date(x))
                
                # Remove from week if it came from a week
                if isinstance(source, int):
                    state['weeks_data'][source] = None
                    refresh_grid_ui()
                
                state['dragged_image'] = None
                state['drag_source'] = None
                refresh_drawer_ui()
                ui.notify('Image returned to source')

        left_drawer.on('dragover', on_dragover)
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
                with ui.card().classes('p-0 cursor-move border-0 shadow-none bg-transparent') as card:
                    card.props('draggable')
                    
                    def on_drag_start(e, p=img_path):
                        state['dragged_image'] = p
                        state['drag_source'] = 'source'
                        
                    card.on('dragstart', on_drag_start)
                    
                    # Display Date & Square Thumb
                    c_date = get_image_creation_date(img_path)
                    date_str = c_date.strftime('%Y-%m-%d %H:%M')
                    
                    with ui.column().classes('w-full items-center p-0 gap-0'):
                        ui.image(img_path).classes('w-full h-24 object-cover rounded')
                        ui.label(date_str).classes('text-[10px] text-gray-600 leading-tight text-center')

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
                        
                if current_img:
                    render_assigned_image(current_img, content_area)
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
                        # Improved Logic: Search and Remove "dragged" from ANYWHERE it currently is.
                        # This eliminates "drag_source" state dependency which can be buggy.
                        
                        # 1. Remove from Source List
                        if dragged in state['images']:
                            state['images'].remove(dragged)
                            
                        # 2. Remove from ANY other week
                        # We use list() to safely iterate while modifying (though we modify by key)
                        for k, v in list(state['weeks_data'].items()):
                            if v == dragged and k != w:
                                state['weeks_data'][k] = None
                        
                        # 3. Assign to New Week
                        state['weeks_data'][w] = dragged
                        
                        # 4. Global Refresh to ensure UI consistency
                        # This is slightly heavier but guarantees 0 duplication visual bugs
                        refresh_grid_ui()
                        refresh_drawer_ui()

                        ui.notify(f'Assigned to Week {w}')
                        state['dragged_image'] = None
                        state['drag_source'] = None

                # drop_card.on('dragover', on_dragover) # Removed server-side handler
                drop_card.on('drop', on_drop)
                

# --- Processing Logic ---

def process_and_organize():
    if not state['weeks_data']:
        ui.notify('No photos assigned to weeks!', type='warning')
        return
        
    folder = Path(state['source_folder'])
    if not folder.exists(): 
        ui.notify('Source folder seems missing.')
        return
        
    sorted_folder = folder / f"Sorted_{state['year']}"
    sorted_folder.mkdir(exist_ok=True)
    
    count = 0
    for w_num, img_path in state['weeks_data'].items():
        if not img_path: continue
        
        # Target Name: 001.jpg, 053.jpg
        new_name = f"{w_num:03d}.jpg"
        target_path = sorted_folder / new_name
        
        try:
            # Open and Convert
            with Image.open(img_path) as im:
                # Convert to RGB if necessary (e.g. from RGBA or CMYK)
                if im.mode in ('RGBA', 'P'):
                    im = im.convert('RGB')
                    
                im.save(target_path, 'JPEG', quality=95)
                count += 1
                
        except Exception as e:
            ui.notify(f"Error processing Week {w_num}: {e}", type='negative')
    
    ui.notify(f"Success! Processed {count} files into {sorted_folder.name}", type='positive')
    # Open folder
    # os.system(f'open "{sorted_folder}"') # Mac specific
    
    
# --- Main Layout ---

# Setup Static Files for displaying images roughly? 
# Warning: Exposing root / is dangerous on web, but okay for local desktop tool.
app.add_static_files('/files', '/') 
# Fix path mapping for Windows/Mac to use '/files/Users/...' if needed.
# For simplicity in NiceGUI, ui.image(path) works with local paths in native mode usually, 
# but in browser mode it needs serving.
# We will trust ui.image(path) handles local files in native desktop mode correctly or standard mode.
# If not, we might need a transformer.

with ui.column().classes('w-full h-screen p-0'):
    
    # 1. Header
    with ui.row().classes('w-full bg-blue-100 p-4 items-center gap-4'):
        ui.label('Weekly Photo Organizer').classes('text-xl font-bold text-blue-900')
        
        ui.number(label='Year', value=state['year'], format='%.0f', 
                  on_change=lambda e: (state.update({'year': int(e.value)}), refresh_grid_ui())).classes('w-24')
        

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
    # We use a relaxed approach for file serving if needed.
    ui.run(title='Weekly Photo Organizer', native=True, window_size=(1200, 800))
