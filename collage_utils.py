from pathlib import Path
from typing import List, Optional, Dict
import datetime
from PIL import Image, ImageOps

def generate_collage(image_paths: List[Path], output_folder: Path, spacing: int = 0, slot_configs: List[Dict] = None) -> Optional[Path]:
    """
    Generates a collage from 2-4 images using Pillow.
    Args:
        image_paths: List of image paths.
        output_folder: Where to save the result.
        spacing: Gap between images in pixels.
        slot_configs: List of dicts with 'center_x', 'center_y', 'zoom' for each slot.
                     Defaults to center (0.5, 0.5) and zoom 1.0.
    Returns path to the temporary collage file.
    """
    if not image_paths:
        return None
        
    # Standard output size for the collage (4:3 ratio high res)
    W, H = 1600, 1200
    
    # Validation
    process_paths = image_paths[:4] 
    qty = len(process_paths)
    
    # Defaults
    if slot_configs is None:
        slot_configs = [{'center_x': 0.5, 'center_y': 0.5, 'zoom': 1.0} for _ in range(qty)]
    
    # Ensure slot_configs has enough entries
    while len(slot_configs) < qty:
        slot_configs.append({'center_x': 0.5, 'center_y': 0.5, 'zoom': 1.0})

    # Create canvas
    canvas = Image.new('RGB', (W, H), 'white')
    
    # Helper to apply crop & zoom
    def process_image_for_slot(img_path, target_w, target_h, config):
        img = Image.open(img_path)
        
        # Convert to RGB if needed
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
            
        cx = config.get('center_x', 0.5)
        cy = config.get('center_y', 0.5)
        zoom = config.get('zoom', 1.0)
        
        # 1. Calculate the Aspect Ratio required
        target_aspect = target_w / target_h
        img_w, img_h = img.size
        img_aspect = img_w / img_h
        
        # 2. Determine "Cover" crop (zoom=1.0) logic
        # If img is wider than target (img_aspect > target_aspect):
        #   Height matches, Width is cropped.
        #   Visible width = img_h * target_aspect
        # If img is taller than target (img_aspect < target_aspect):
        #   Width matches, Height is cropped.
        #   Visible height = img_w / target_aspect
        
        if img_aspect > target_aspect:
            # Source is Wider
            base_visible_h = img_h
            base_visible_w = img_h * target_aspect
        else:
            # Source is Taller
            base_visible_w = img_w
            base_visible_h = img_w / target_aspect
            
        # 3. Apply Zoom
        # Zoom > 1.0 means we see LESS, so visible area is smaller.
        visible_w = base_visible_w / zoom
        visible_h = base_visible_h / zoom
        
        # 4. Calculate Crop Box based on Center (cx, cy)
        # cx, cy are valid from 0.0 to 1.0. 
        # But we need to map them to the *available range* of movement?
        # Actually simplest interpretation: (cx, cy) is the point in the SOURCE image 
        # that should be at the center of the crop info.
        
        center_x_px = img_w * cx
        center_y_px = img_h * cy
        
        left = center_x_px - (visible_w / 2)
        top = center_y_px - (visible_h / 2)
        right = left + visible_w
        bottom = top + visible_h
        
        # 5. Clamp to image bounds? 
        # User wants to drag freely? Maybe clamp so we don't show white?
        # Let's simple clamp to keep crop inside image if possible.
        # OR: Just let Pillow handle it (it might pad with black). 
        # Better to clamp `left` such that `left >= 0` and `right <= img_w` IF `visible_w <= img_w`.
        # If `visible_w > img_w` (zoomed out too much), we center it.
        
        # Simple clamping logic
        if visible_w <= img_w:
            if left < 0: left = 0; right = visible_w
            if right > img_w: right = img_w; left = img_w - visible_w
        else:
            # Zoomed out -> center
            offset = (visible_w - img_w) / 2
            left = -offset
            right = img_w + offset
            
        if visible_h <= img_h:
            if top < 0: top = 0; bottom = visible_h
            if bottom > img_h: bottom = img_h; top = img_h - visible_h
        else:
            offset = (visible_h - img_h) / 2
            top = -offset
            bottom = img_h + offset
            
        # Crop & Resize
        box = (left, top, right, bottom)
        # resize expects integer size
        img = img.resize((target_w, target_h), box=box, resample=Image.LANCZOS)
        return img

    try:
        # Spacing implementation: Reduce width/height of slots slightly
        
        if qty == 2:
            # Split 50/50 Vertically
            w_slot = (W - spacing) // 2
            h_slot = H
            
            # Left
            img1 = process_image_for_slot(process_paths[0], w_slot, h_slot, slot_configs[0])
            canvas.paste(img1, (0, 0))
            
            # Right
            img2 = process_image_for_slot(process_paths[1], w_slot, h_slot, slot_configs[1])
            canvas.paste(img2, (w_slot + spacing, 0))
            
        elif qty == 3:
            # One large Left (50%), Two small stacked Right (50%)
            # Left
            w_left = (W - spacing) // 2
            h_left = H
            
            img1 = process_image_for_slot(process_paths[0], w_left, h_left, slot_configs[0])
            canvas.paste(img1, (0, 0))
            
            # Right Calculation
            x_right = w_left + spacing
            w_right = W - x_right # Remaining width
            
            h_top = (H - spacing) // 2
            h_bot = H - spacing - h_top
            
            # Right Top
            img2 = process_image_for_slot(process_paths[1], w_right, h_top, slot_configs[1])
            canvas.paste(img2, (x_right, 0))
            
            # Right Bottom
            img3 = process_image_for_slot(process_paths[2], w_right, h_bot, slot_configs[2])
            canvas.paste(img3, (x_right, h_top + spacing))
            
        elif qty >= 4:
            # 2x2 Grid
            w_half = (W - spacing) // 2
            h_half = (H - spacing) // 2
            
            coords = [
                (0, 0), (w_half + spacing, 0),
                (0, h_half + spacing), (w_half + spacing, h_half + spacing)
            ]
            
            for i, p in enumerate(process_paths):
                img = process_image_for_slot(p, w_half, h_half, slot_configs[i])
                canvas.paste(img, coords[i])
                
        else:
            # 1 image
            img = process_image_for_slot(process_paths[0], W, H, slot_configs[0])
            canvas.paste(img, (0, 0))

    except Exception as e:
        print(f"Error generating collage: {e}")
        if image_paths:
             return image_paths[0] 
        return None

    # Save to temp dir
    temp_dir = output_folder / 'temp_collages'
    temp_dir.mkdir(exist_ok=True)
    
    # Unique name using timestamp
    filename = f"collage_{int(datetime.datetime.now().timestamp())}.jpg"
    out_path = temp_dir / filename
    
    canvas.save(out_path, quality=90)
    return out_path
