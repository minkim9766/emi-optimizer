from PIL import Image
import Obs_Mask

img = Image.new('1', (128, 128))
pixels = img.load()

for a in range(4):
    data = Obs_Mask.image_to_map(f'./output_images/resized/b_layer_{a}.png')
    print(data)

    for i in range(img.size[0]):
       for j in range(img.size[1]):
           pixels[i, j] = data[i][j]

    img.show()