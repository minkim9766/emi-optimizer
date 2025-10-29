import Filter_Fab
import Gerber_polygon
import os

def divide(file_path:str, project_path:str, delete_text:bool=False):
    import json

    global gerber_type

    try:
        with open(file_path, "r") as f:
            gbrjob = json.load(f)
            front_layer = []
            bottom_layer = []
            black_list = []
            for layer in gbrjob['FilesAttributes']:
                gerber_type = list(map(str, layer['FileFunction'].split(',')))
                if gerber_type[0] =='AssemblyDrawing' and delete_text:
                    Filter_Fab.keep_blocks_with_thickness(str(os.path.join(project_path, layer['Path'])), str(os.path.join(project_path, 'edit_'+layer['Path'])), 0.1, 0.1)
                    black_list.append(layer['Path'])
                if gerber_type[0] == 'Glue':
                    Gerber_polygon.fill_gerber_outline_to_region(str(os.path.join(project_path, layer['Path'])), str(os.path.join(project_path, 'edit_'+layer['Path'])),snap_tol_mm=0.05,max_seg_len_mm=0.1,max_angle_deg=3.0)
                    black_list.append(layer['Path'])
                if len(gerber_type) != 0 and layer['Path']:
                    if gerber_type[-1] == 'Top':
                        if gerber_type[0] not in  ['Legend', 'Copper']:
                            if layer['Path'] in black_list:
                                edited_path = 'edit_'+layer['Path']
                            else:
                                edited_path = layer['Path']
                            if gerber_type[0] == 'Glue':
                                edited_path = 'edit_'+layer['Path']
                            front_layer.append(edited_path)
                            if gerber_type[0] == 'SolderMask':
                                bottom_layer.append(edited_path)
                    elif gerber_type[-1] == 'Bot':
                        if gerber_type[0] not in ['Legend', 'Copper']:
                            if layer['Path'] in black_list:
                                edited_path = 'edit_'+layer['Path']
                            else:
                                edited_path = layer['Path']
                            if gerber_type[0] == 'Glue':
                                edited_path = 'edit_'+layer['Path']
                            bottom_layer.append(edited_path)
                            if gerber_type[0] == 'SolderMask':
                                front_layer.append(edited_path)
                else: # Edge_Cuts and blacklist
                    pass
        return front_layer, bottom_layer
    except Exception as e:
        raise e