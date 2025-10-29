import Obs_Mask
import os

def _list_to_string(input_list):
    string = ""
    for inner_list in input_list:
        string += "".join(str(a) for a in inner_list)
    return string

def create_observation(location,side='top'):
    order_list = ['glue', 'soldermask', 'solderpaste']
    #glue where the agent can move, soldermask where it can't, solderpaste where it's departure or destination is
    result = ""
    for a in order_list:
        if f'{side}_{a}.png' in os.listdir(location):
            result += _list_to_string(Obs_Mask.image_to_map(os.path.join(location, f'{side}_{a}.png')))
        else:
            pass
    return result