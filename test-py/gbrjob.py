import json
from gerber.render.cairo_backend import GerberCairoContext
from pygerber.gerberx3.api.v2 import FileTypeEnum, GerberFile, Project
import os

folder_path = '../Test Files/Test1'
ctx = GerberCairoContext()
for a in os.listdir(folder_path):
    if a.endswith('.gbrjob'):
        print(f"{os.listdir(folder_path).index(a) + 1}/{len(os.listdir(folder_path))}, 처리중:", a)
        gerber_layer_list = []
        file_path = os.path.join(folder_path, a)
        with open(file_path, "r") as f:
            gbrjob = json.load(f)

            # 각 레이어 렌더링
            for layer in gbrjob['FilesAttributes']:
                print(layer['Path'])
                gbr_file_path = os.path.join(folder_path, layer['Path'])
                print(layer['FileFunction'])
                gerber_type = list(map(str, layer['FileFunction'].split(',')))[0]
                try:
                    gerber_type_enum = FileTypeEnum[gerber_type.upper()]
                except KeyError:
                    gerber_type_enum = FileTypeEnum.UNDEFINED
                gerber_layer_list.append(GerberFile.from_file(gbr_file_path, gerber_type_enum))
        print("파일 처리 완료, 프로젝트 분석 중...")
        layer_project_parsed = Project(
            gerber_layer_list,
        ).parse()
        print('파일 분석 완료, 렌더링 중...')
        layer_project_parsed.render_svg(f'{a}.svg')
        print('SVG 렌더링 완료, 래스터 렌더링 중...')
        layer_project_parsed.render_raster(f'{a}.png', dpmm=40)
        print(f"렌더링 완료:{a}.svg, {a}.png")



