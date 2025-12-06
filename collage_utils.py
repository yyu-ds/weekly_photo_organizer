from pathlib import Path
from typing import List, Optional
import datetime
from PIL import Image, ImageOps

def generate_smart_collage(image_paths: List[Path], output_folder: Path) -> Optional[Path]:
    """
    Generates a collage from 2-4 images using Pillow.
    Returns path to the temporary collage file.
    """
    if not image_paths:
        return None
        
    # Standard output size for the collage (4:3 ratio high res)
    W, H = 1600, 1200
    
    # Validation: Cap at 4 mainly, or handle 4+ as 4
    process_paths = image_paths[:4] 
    qty = len(process_paths)
    
    # Create canvas
    canvas = Image.new('RGB', (W, H), 'white')
    
    try:
        if qty == 2:
            # Split 50/50 Vertically
            # Left
            img1 = Image.open(process_paths[0])
            img1 = ImageOps.fit(img1, (W // 2, H), centering=(0.5, 0.5))
            canvas.paste(img1, (0, 0))
            
            # Right
            img2 = Image.open(process_paths[1])
            img2 = ImageOps.fit(img2, (W // 2, H), centering=(0.5, 0.5))
            canvas.paste(img2, (W // 2, 0))
            
        elif qty == 3:
            # One large Left (50%), Two small stacked Right (50%)
            # Left
            img1 = Image.open(process_paths[0])
            img1 = ImageOps.fit(img1, (W // 2, H), centering=(0.5, 0.5))
            canvas.paste(img1, (0, 0))
            
            # Right Top
            img2 = Image.open(process_paths[1])
            img2 = ImageOps.fit(img2, (W // 2, H // 2), centering=(0.5, 0.5))
            canvas.paste(img2, (W // 2, 0))
            
            # Right Bottom
            img3 = Image.open(process_paths[2])
            img3 = ImageOps.fit(img3, (W // 2, H // 2), centering=(0.5, 0.5))
            canvas.paste(img3, (W // 2, H // 2))
            
        elif qty >= 4:
            # 2x2 Grid
            w_half = W // 2
            h_half = H // 2
            
            coords = [
                (0, 0), (w_half, 0),
                (0, h_half), (w_half, h_half)
            ]
            
            for i, p in enumerate(process_paths):
                img = Image.open(p)
                img = ImageOps.fit(img, (w_half, h_half), centering=(0.5, 0.5))
                canvas.paste(img, coords[i])
                
        else:
            # Fallback for 1 image (shouldn't happen in collage flow usually, but good for safety)
            img = Image.open(process_paths[0])
            img = ImageOps.fit(img, (W, H), centering=(0.5, 0.5))
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
