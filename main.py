import render_image

render_image.create_png('./Test Files/Test1', '_33-job.gbrjob', './output_images',type_composite=True,uniform_color="#FFFFFF",background_color="#000000")


import resize_raster
import os

folder_path = './output_images'
save_path = './output_images/resized'

for filename in os.listdir(folder_path):
    if 'resized' not in filename:
        resize_raster.resize_raster(os.path.join(folder_path, filename), os.path.join(save_path, filename), 128)


import To_MLAgents
observation = To_MLAgents.create_observation('./output_images/resized','top')
print(observation)

with open('./result.txt', 'w') as f:
    f.write(str(observation))
print('Observation saved to result.txt')


import Filter_Fab
Filter_Fab.delete_file('./Test Files/Test1/')
