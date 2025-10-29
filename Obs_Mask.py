# rl_obs_maps/maps.py
from __future__ import annotations
import numpy as np
from PIL import Image

def image_to_binary(img):
    result = []
    for h in range(img.shape[0]):
        result.append([])
        for w in range(img.shape[1]):
            result[h].append([])
            r, g, b = img[w][h]
            # print(r, g, b)
            if (r + g + b) / (3 * 255) < 0.28:
                result[h][w] = 0
            else:
                result[h][w] = 1
    return result

def image_to_map(img_path):
    img = np.asarray(Image.open(img_path),dtype=np.uint64)
    # print(img.shape)

    return image_to_binary(img)

# print(image_to_map('./output_images/resized/b_layer_0.png'))
