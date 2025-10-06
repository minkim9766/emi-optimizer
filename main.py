from math import *

grid_size = [5, 5]
start_node = [0, 0]
end_node = [4, 4]

class Grid():
    def __init__(self, size, start, end):
        self.grid = [[self.Node(x, y) for x in range(size[1])] for y in range(size[0])]
        self.size = size
        self.grid[start[0]][start[1]].start = True
        self.grid[end[0]][end[1]].end = True

    def display_grid(self):
        for row in self.grid:
            print(" ".join(str(cell) for cell in row))

    class Node:
        def __init__(self, x, y, start=False, end=False):
            self.x = x
            self.y = y
            self.start = start
            self.end = end
            self.g_cost = None
            self.h_cost = None

        def g_cost(self, current):
            self.g_cost = sqrt((current.x - self.x)**2 + (current.y - self.y)**2)
            return self.g_cost

        def h_cost(self):
            self.h_cost = sqrt((self.x - end_node[0])**2 + (self.y - end_node[1])**2)
            return self.h_cost

        def f_cost(self, current):
            return self.g_cost(current) + self.h_cost()

        def __str__(self):
            if self.start:
                return f'S:{self.x},{self.y}'
            elif self.end:
                return f'E:{self.x},{self.y}'
            else:
                return f'.:{self.x},{self.y}'


class A_Star_Path(Grid):

    def __init__(self, size, start, end):
        super().__init__(size, start, end)
        self.open = []
        self.closed = []
        super().current = Grid[start_node[0]][start_node[1]]

grid = Grid(grid_size, start_node, end_node)
grid.display_grid()




