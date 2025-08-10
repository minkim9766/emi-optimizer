import turtle
import math

pcb_vertex = [[-150, -150], [150, -150], [150, 150], [-150, 150]]
track = [[[10, 10], [-100, -100]]]

for pcb_vertex_index in range(len(pcb_vertex)):
    pcb_edge_inclination = (pcb_vertex[pcb_vertex_index][0] - pcb_vertex[pcb_vertex_index - 1][0]) / (pcb_vertex[pcb_vertex_index][1] - pcb_vertex[pcb_vertex_index - 1][1])
