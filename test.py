import render_image

render_image.create_svg('./Test Files/Test1',
                        '_33-job.gbrjob',
                        './output_images',
                        uniform_color="#FFFFFF",
                        glue_mode="keep",
                        include_assembly_in_main=True,
                        type_composite=True,
                        fill_background=True,
                        category_override_colors={"GLUE":"#FFFFFF"}
                        )