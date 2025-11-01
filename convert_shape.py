from svg_print import list_svg_flat, save_svg_json

type = 'solderpaste'

shapes = list_svg_flat(f'./output_images/bot_{type}.svg',expand_use=True)
save_svg_json(shapes, f'./output_images/bot_{type}.json')

from TEST_SVG import json_to_svg, save_svg
svg = json_to_svg(shapes, min_circle_r_px=0.0, round_ndigits=6)
save_svg(svg, f'bot_{type}_converted.svg')