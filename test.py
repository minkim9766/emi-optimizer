# import render_image
#
# render_image.create_svg('./Test Files/Test1',
#                         '_33-job.gbrjob',
#                         './output_images',
#                         uniform_color="#FFFFFF",
#                         glue_mode="keep",
#                         include_assembly_in_main=True,
#                         type_composite=True,
#                         fill_background=True,
#                         category_override_colors={"GLUE":"#FFFFFF"}
#                         )

# test.py
from convert_to_unity import flatten_preserve_holes
from pathlib import Path

src = Path("./output_images/bot_soldermask.svg")
dst = Path("./output_images/bot_soldermask_edit.svg")

# fill-rule="evenodd" 적용 + stroke 추가
flatten_preserve_holes(src, dst, use_evenodd=True, add_stroke=True)

print("변환 완료:", dst)

