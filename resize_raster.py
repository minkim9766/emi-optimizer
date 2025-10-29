from PIL import Image
import numpy as np
import os

def resize_raster(src_path, dst_path, max_pixel):
    if not os.path.exists(dst_path):
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)

    img = Image.open(src_path)
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    w, h = img.size  # (W, H)
    scale = min(max_pixel / w, max_pixel / h, 1.0)  # 더 키우지 않음
    new_w, new_h = int(round(w * scale)), int(round(h * scale))
    img = img.resize((new_w, new_h), Image.LANCZOS)

    arr = np.array(img)              # (H, W, 3)
    H, W = arr.shape[:2]
    canvas = np.full((max_pixel, max_pixel, 3), 0, dtype=np.uint8)  # 흰 배경

    top  = (max_pixel - H) // 2
    left = (max_pixel - W) // 2
    canvas[top:top+H, left:left+W] = arr

    Image.fromarray(canvas).save(dst_path)

resize_raster('tmp/test.png', 'tmp/resized_test.png', 128)
