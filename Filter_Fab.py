

def keep_blocks_with_thickness(input_file:str, output_file:str, min_thickness:float=0.1, max_thickness:float=0.1):


    def get_file(file):
        with open(file) as f:
            return list(f.readlines())


    def get_header(lines):
        header = []
        is_aperture_found = False
        for line in lines:
            if 'G04' != line[:3] and line[0] != '%' and is_aperture_found:
                return header
            if line[:4] == '%ADD':
                is_aperture_found = True
            header.append(line)
        return Exception("헤더 이후 유효한 라인이 없습니다.")


    def get_aperture_list(headers):
        aperture_list = {}
        for line in headers:
            if line[:4] == '%ADD':
                parts = line[4:-2].split(',')
                aperture_id = parts[0][:-1]
                aperture_def = float(parts[1][:-1])
                aperture_list[aperture_id] = aperture_def
        return aperture_list


    def remove_thickness_blocks(footer_lines, aperture_list:dict, min_thickness=0.1, max_thickness=0.1):
        output = []
        is_aperture_accepted = False

        accepted_aperture = []
        for aperture in range(len(aperture_list.values())):
            if min_thickness <= list(aperture_list.values())[aperture] <= max_thickness:
                accepted_aperture.append(list(aperture_list.keys())[aperture])
        for line in footer_lines:
            if line[0] == 'D':
                if line[:3][1:] in accepted_aperture:
                    is_aperture_accepted = True
                else:
                    is_aperture_accepted = False
                output.append(line)
            elif is_aperture_accepted or line[0] != 'X':
                output.append(line)
            else:
                if 'I' in line:
                    output.append(line[:line.index('I')]+'D02*\n')
                else:
                    output.append(line[:-5]+'D02*\n')

        return output

    def save_file(file_path, header_lines, footer_lines):
        with open(file_path, 'w') as f:
            for line in header_lines:
                f.write(line)
            for line in footer_lines:
                f.write(line)

    file_lines = get_file(input_file)
    header_lines = get_header(file_lines)
    aperture_list = get_aperture_list(header_lines)
    footer_lines = file_lines[len(header_lines):]
    filtered_footer = remove_thickness_blocks(footer_lines, aperture_list, min_thickness, max_thickness)
    save_file(output_file, header_lines, filtered_footer)
    return 'Saved filtered file to ' + output_file

def delete_file(folder_path):
    import os
    for filename in os.listdir(folder_path):
        if filename[:5] == 'edit_':
            os.remove(os.path.join(folder_path, filename))
            print('Deleted:', filename)
    import shutil
    shutil.rmtree('./output_images')
    print('Deleted folder: output_images')
