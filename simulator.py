import tkinter as tk
from tkinter import filedialog, simpledialog
import math
import copy

WIDTH, HEIGHT = 80, 16
PIXEL_SIZE = 20

# -----------------------------
# Embedded 5x8 font, extracted from font5x8.h (font10x16[] table).
# Each glyph is 8 rows; each row byte holds 5 pixel columns in its
# top 5 bits (bit7 = col0 ... bit3 = col4), matching FONT_WIDTH=5 /
# FONT_HEIGHT=8 from the header. Covers ASCII 32 (' ') to 126 ('~').
FONT_FIRST = 32
FONT_LAST = 126
FONT_DATA = [
    (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00),  # ' ' (32)
    (0x00, 0x80, 0x80, 0x80, 0x80, 0x80, 0x00, 0x80),  # '!' (33)
    (0x00, 0xA0, 0xA0, 0x00, 0x00, 0x00, 0x00, 0x00),  # '"' (34)
    (0x00, 0x00, 0x50, 0xF8, 0x50, 0xF8, 0x50, 0x00),  # '#' (35)
    (0x00, 0x20, 0x70, 0x80, 0x60, 0x10, 0xE0, 0x40),  # '$' (36)
    (0x00, 0xC8, 0xC8, 0x10, 0x20, 0x40, 0x98, 0x98),  # '%' (37)
    (0x00, 0x40, 0xA0, 0xA0, 0x40, 0xA8, 0x90, 0x68),  # '&' (38)
    (0x80, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00),  # "'" (39)
    (0x00, 0x20, 0x40, 0x80, 0x80, 0x80, 0x40, 0x20),  # '(' (40)
    (0x00, 0x80, 0x40, 0x20, 0x20, 0x20, 0x40, 0x80),  # ')' (41)
    (0x00, 0x00, 0x20, 0x20, 0xF8, 0x50, 0x88, 0x00),  # '*' (42)
    (0x00, 0x00, 0x20, 0x20, 0xF8, 0x20, 0x20, 0x00),  # '+' (43)
    (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xC0, 0xC0),  # ',' (44)
    (0x00, 0x00, 0x00, 0x00, 0xF0, 0x00, 0x00, 0x00),  # '-' (45)
    (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xC0, 0xC0),  # '.' (46)
    (0x00, 0x08, 0x08, 0x10, 0x20, 0x40, 0x80, 0x80),  # '/' (47)
    (0x00, 0x60, 0x90, 0x90, 0x90, 0x90, 0x90, 0x60),  # '0' (48)
    (0x00, 0x40, 0xC0, 0x40, 0x40, 0x40, 0x40, 0xE0),  # '1' (49)
    (0x00, 0x60, 0x90, 0x10, 0x20, 0x40, 0x80, 0xF0),  # '2' (50)
    (0x00, 0x60, 0x90, 0x10, 0x20, 0x10, 0x90, 0x60),  # '3' (51)
    (0x00, 0x30, 0x50, 0x90, 0xF0, 0x10, 0x10, 0x10),  # '4' (52)
    (0x00, 0xF0, 0x80, 0xE0, 0x10, 0x10, 0x90, 0x60),  # '5' (53)
    (0x00, 0x60, 0x80, 0xE0, 0x90, 0x90, 0x90, 0x60),  # '6' (54)
    (0x00, 0xF0, 0x10, 0x10, 0x20, 0x40, 0x80, 0x80),  # '7' (55)
    (0x00, 0x60, 0x90, 0x90, 0x60, 0x90, 0x90, 0x60),  # '8' (56)
    (0x00, 0x60, 0x90, 0x90, 0x90, 0x70, 0x10, 0x60),  # '9' (57)
    (0x00, 0x00, 0x00, 0x00, 0x80, 0x00, 0x00, 0x80),  # ':' (58)
    (0x00, 0x00, 0x00, 0x00, 0x40, 0x00, 0x00, 0x40),  # ';' (59)
    (0x00, 0x00, 0x00, 0x20, 0x40, 0x80, 0x40, 0x20),  # '<' (60)
    (0x00, 0x00, 0x00, 0xE0, 0x00, 0xE0, 0x00, 0x00),  # '=' (61)
    (0x00, 0x00, 0x00, 0x80, 0x40, 0x20, 0x40, 0x80),  # '>' (62)
    (0x00, 0x60, 0x90, 0x10, 0x60, 0x40, 0x00, 0x40),  # '?' (63)
    (0x00, 0x78, 0x80, 0xB8, 0xA8, 0xB8, 0x80, 0x78),  # '@' (64)
    (0x00, 0x60, 0x90, 0x90, 0xF0, 0x90, 0x90, 0x90),  # 'A' (65)
    (0x00, 0xE0, 0x90, 0x90, 0xE0, 0x90, 0x90, 0xE0),  # 'B' (66)
    (0x00, 0x60, 0x90, 0x80, 0x80, 0x80, 0x90, 0x60),  # 'C' (67)
    (0x00, 0xE0, 0x90, 0x90, 0x90, 0x90, 0x90, 0xE0),  # 'D' (68)
    (0x00, 0xF0, 0x80, 0x80, 0xE0, 0x80, 0x80, 0xF0),  # 'E' (69)
    (0x00, 0xF0, 0x80, 0x80, 0xE0, 0x80, 0x80, 0x80),  # 'F' (70)
    (0x00, 0x60, 0x90, 0x80, 0xB0, 0x90, 0x90, 0x70),  # 'G' (71)
    (0x00, 0x90, 0x90, 0x90, 0xF0, 0x90, 0x90, 0x90),  # 'H' (72)
    (0x00, 0xE0, 0x40, 0x40, 0x40, 0x40, 0x40, 0xE0),  # 'I' (73)
    (0x00, 0x70, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20),  # 'J' (74)
    (0x00, 0x90, 0x90, 0xA0, 0xC0, 0xA0, 0x90, 0x90),  # 'K' (75)
    (0x00, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0xF0),  # 'L' (76)
    (0x00, 0x88, 0xD8, 0xA8, 0x88, 0x88, 0x88, 0x88),  # 'M' (77)
    (0x00, 0x88, 0xC8, 0xC8, 0xA8, 0x98, 0x98, 0x88),  # 'N' (78)
    (0x00, 0x60, 0x90, 0x90, 0x90, 0x90, 0x90, 0x60),  # 'O' (79)
    (0x00, 0xE0, 0x90, 0x90, 0xE0, 0x80, 0x80, 0x80),  # 'P' (80)
    (0x00, 0x60, 0x90, 0x90, 0x90, 0x90, 0x90, 0x60),  # 'Q' (81)
    (0x00, 0xE0, 0x90, 0x90, 0xE0, 0x90, 0x90, 0x90),  # 'R' (82)
    (0x00, 0x70, 0x80, 0x80, 0x60, 0x10, 0x10, 0xE0),  # 'S' (83)
    (0x00, 0xF8, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20),  # 'T' (84)
    (0x00, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x60),  # 'U' (85)
    (0x00, 0x88, 0x88, 0x88, 0x88, 0x50, 0x50, 0x20),  # 'V' (86)
    (0x00, 0x88, 0x88, 0x88, 0x88, 0xA8, 0xD8, 0x88),  # 'W' (87)
    (0x00, 0x88, 0x88, 0x50, 0x20, 0x50, 0x88, 0x88),  # 'X' (88)
    (0x00, 0x88, 0x88, 0x50, 0x20, 0x40, 0x80, 0x80),  # 'Y' (89)
    (0x00, 0xF0, 0x10, 0x10, 0x20, 0x40, 0x80, 0xF0),  # 'Z' (90)
    (0x00, 0xC0, 0x80, 0x80, 0x80, 0x80, 0x80, 0xC0),  # '[' (91)
    (0x00, 0x80, 0x80, 0x40, 0x20, 0x10, 0x08, 0x08),  # '\\' (92)
    (0x00, 0xC0, 0x40, 0x40, 0x40, 0x40, 0x40, 0xC0),  # ']' (93)
    (0x00, 0x40, 0xA0, 0x00, 0x00, 0x00, 0x00, 0x00),  # '^' (94)
    (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xF0),  # '_' (95)
    (0x00, 0x40, 0x20, 0x10, 0x00, 0x00, 0x00, 0x00),  # '`' (96)
    (0x00, 0x00, 0x00, 0x60, 0x10, 0x70, 0x90, 0x70),  # 'a' (97)
    (0x00, 0x80, 0x80, 0xE0, 0x90, 0x90, 0x90, 0xE0),  # 'b' (98)
    (0x00, 0x00, 0x00, 0x60, 0x90, 0x80, 0x90, 0x60),  # 'c' (99)
    (0x00, 0x10, 0x10, 0x70, 0x90, 0x90, 0x90, 0x70),  # 'd' (100)
    (0x00, 0x00, 0x00, 0x60, 0x90, 0xF0, 0x80, 0x60),  # 'e' (101)
    (0x00, 0x20, 0x40, 0x40, 0xE0, 0x40, 0x40, 0x40),  # 'f' (102)
    (0x00, 0x00, 0x60, 0x90, 0x90, 0x70, 0x10, 0x70),  # 'g' (103)
    (0x00, 0x80, 0x80, 0xE0, 0x90, 0x90, 0x90, 0x90),  # 'h' (104)
    (0x00, 0x40, 0x00, 0xC0, 0x40, 0x40, 0x40, 0xE0),  # 'i' (105)
    (0x00, 0x20, 0x00, 0x60, 0x20, 0x20, 0x20, 0x20),  # 'j' (106)
    (0x00, 0x80, 0x80, 0x90, 0xA0, 0xC0, 0xA0, 0x90),  # 'k' (107)
    (0x00, 0xC0, 0x40, 0x40, 0x40, 0x40, 0x40, 0xE0),  # 'l' (108)
    (0x00, 0x00, 0x00, 0xD0, 0xA8, 0xA8, 0xA8, 0xA8),  # 'm' (109)
    (0x00, 0x00, 0x00, 0xE0, 0x90, 0x90, 0x90, 0x90),  # 'n' (110)
    (0x00, 0x00, 0x00, 0x60, 0x90, 0x90, 0x90, 0x60),  # 'o' (111)
    (0x00, 0x00, 0x60, 0x90, 0x90, 0xE0, 0x80, 0x80),  # 'p' (112)
    (0x00, 0x00, 0x60, 0x90, 0x90, 0x70, 0x10, 0x10),  # 'q' (113)
    (0x00, 0x00, 0x00, 0xB0, 0xC0, 0x80, 0x80, 0x80),  # 'r' (114)
    (0x00, 0x00, 0x00, 0x70, 0x80, 0x60, 0x10, 0xE0),  # 's' (115)
    (0x00, 0x40, 0x40, 0x40, 0xE0, 0x40, 0x40, 0x20),  # 't' (116)
    (0x00, 0x00, 0x00, 0x90, 0x90, 0x90, 0x90, 0x70),  # 'u' (117)
    (0x00, 0x00, 0x00, 0x88, 0x88, 0x88, 0x50, 0x20),  # 'v' (118)
    (0x00, 0x00, 0x00, 0x88, 0x88, 0xA8, 0xD8, 0x88),  # 'w' (119)
    (0x00, 0x00, 0x00, 0x88, 0x50, 0x20, 0x50, 0x88),  # 'x' (120)
    (0x00, 0x00, 0x00, 0x90, 0x90, 0x90, 0x90, 0x70),  # 'y' (121)
    (0x00, 0x00, 0x00, 0xE0, 0x20, 0x40, 0x80, 0xE0),  # 'z' (122)
    (0x00, 0x20, 0x40, 0x40, 0x80, 0x40, 0x40, 0x20),  # '{' (123)
    (0x00, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80, 0x80),  # '|' (124)
    (0x00, 0x80, 0x40, 0x40, 0x20, 0x40, 0x40, 0x80),  # '}' (125)
    (0x00, 0x50, 0xA0, 0x00, 0x00, 0x00, 0x00, 0x00),  # '~' (126)
]


def build_font():
    """Convert FONT_DATA (byte-per-row, top 5 bits = columns) into the
    {char_code: [[0/1]*5 for row in range(8)]} format used by the app."""
    font = {}
    for i, rows in enumerate(FONT_DATA):
        code = FONT_FIRST + i
        glyph = []
        for byte in rows:
            glyph.append([(byte >> (7 - col)) & 1 for col in range(5)])
        font[code] = glyph
    return font

class LCDSimulator:
    def __init__(self, master):
        self.master = master
        self.master.title("GRAPHYTE-Designer")
        self.pixels = [[0]*WIDTH for _ in range(HEIGHT)]
        self.commands = []
        self.font = build_font()  # embedded 5x8 font from font5x8.h

        self.undo_stack = []
        self.redo_stack = []

        self.tool = tk.StringVar(value="line")  # default tool
        self.device_var = tk.StringVar(value="gfx")  # name of the GraphyteDisplay instance in the sketch

        self.canvas = tk.Canvas(master, width=WIDTH*PIXEL_SIZE, height=HEIGHT*PIXEL_SIZE, bg="white")
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self.click)
        self.canvas.bind("<B1-Motion>", self.update_draw)
        self.canvas.bind("<ButtonRelease-1>", self.finish_draw)
        self.canvas.bind("<Motion>", self.update_cursor_text)

        frame = tk.Frame(master)
        frame.pack(fill="x")
        tk.Button(frame, text="Temizle Komutu", command=self.clear).pack(side="left")
        tk.Button(frame, text="Herşeyi temizle", command=self.clear_all_instructions).pack(side="left")
        tk.Button(frame, text="Yazı Ekle", command=self.start_text_input).pack(side="left")
        tk.Button(frame, text="Geri Al", command=self.undo).pack(side="left")
        tk.Button(frame, text="İleri Al", command=self.redo).pack(side="left")
        tk.Button(frame, text="Komut Listesi çıktısı (Arduino)", command=self.export_arduino_commands).pack(side="left")
        tk.Label(frame, text="Nesne adı:").pack(side="left")
        tk.Entry(frame, textvariable=self.device_var, width=6).pack(side="left")

        # Available tools (removed pixel/invert_pixel)
        tools = ["line", "invert_line", "rect", "invert_rect",
                 "filled_rect", "invert_filled_rect", "circle", "invert_circle",
                 "filled_circle", "invert_filled_circle", "text"]
        for t in tools:
            tk.Radiobutton(frame, text=t, variable=self.tool, value=t).pack(side="left")

        self.start_x = self.start_y = None
        self.preview_pixels = [[0]*WIDTH for _ in range(HEIGHT)]
        self.cursor_text = ""
        self.cursor_pos = (0, 0)

        self.draw_grid()

    # -----------------------------
    def draw_grid(self, preview=False):
        self.canvas.delete("all")
        data = self.preview_pixels if preview else self.pixels
        for y in range(HEIGHT):
            for x in range(WIDTH):
                color = "black" if data[y][x] else "white"
                self.canvas.create_rectangle(
                    x*PIXEL_SIZE, y*PIXEL_SIZE, (x+1)*PIXEL_SIZE, (y+1)*PIXEL_SIZE,
                    fill=color, outline="gray"
                )
        if self.tool.get() == "text" and self.cursor_text:
            x0, y0 = self.cursor_pos
            for idx,ch in enumerate(self.cursor_text):
                code = ord(ch)
                if code in self.font:
                    bitmap = self.font[code]
                    for row in range(8):
                        for col in range(5):
                            px, py = x0 + idx*6 + col, y0 + row
                            if 0<=px<WIDTH and 0<=py<HEIGHT and bitmap[row][col]:
                                self.canvas.create_rectangle(
                                    px*PIXEL_SIZE, py*PIXEL_SIZE,
                                    (px+1)*PIXEL_SIZE, (py+1)*PIXEL_SIZE,
                                    fill="gray", outline="gray"
                                )

    # -----------------------------
    def click(self, event):
        if self.tool.get() == "text" and self.cursor_text:
            self.place_text(event.x//PIXEL_SIZE, event.y//PIXEL_SIZE)
        else:
            self.start_draw(event)

    def start_draw(self, event):
        self.start_x = event.x // PIXEL_SIZE
        self.start_y = event.y // PIXEL_SIZE
        self.push_undo()
        self.preview_pixels = [row[:] for row in self.pixels]

    def update_draw(self, event):
        if self.start_x is None or self.start_y is None:
            return
        end_x = event.x // PIXEL_SIZE
        end_y = event.y // PIXEL_SIZE
        self.preview_pixels = [row[:] for row in self.pixels]
        tool = self.tool.get()
        invert = "invert" in tool
        if "line" in tool:
            self.draw_line(self.start_x,self.start_y,end_x,end_y,temp=True,invert=invert)
        elif "rect" in tool:
            filled = "filled" in tool
            self.draw_rect(self.start_x,self.start_y,end_x,end_y,filled=filled,temp=True,invert=invert)
        elif "circle" in tool:
            filled = "filled" in tool
            self.draw_circle(self.start_x,self.start_y,end_x,end_y,filled=filled,temp=True,invert=invert)
        self.draw_grid(preview=True)

    def finish_draw(self, event):
        if self.start_x is None or self.start_y is None:
            return
        end_x = event.x // PIXEL_SIZE
        end_y = event.y // PIXEL_SIZE
        tool = self.tool.get()
        invert = "invert" in tool
        cmd = ""
        dev = self.device_var.get().strip() or "gfx"
        if tool in ["line","invert_line"]:
            self.draw_line(self.start_x,self.start_y,end_x,end_y,temp=False,invert=invert)
            cmd = f"{dev}.invertLine({self.start_x}, {self.start_y}, {end_x}, {end_y});" if invert else f"{dev}.drawLine({self.start_x}, {self.start_y}, {end_x}, {end_y});"
        elif tool in ["rect","invert_rect"]:
            filled=False
            self.draw_rect(self.start_x,self.start_y,end_x,end_y,filled=filled,temp=False,invert=invert)
            w,h = abs(end_x-self.start_x)+1, abs(end_y-self.start_y)+1
            cmd = f"{dev}.invertRect({min(self.start_x,end_x)}, {min(self.start_y,end_y)}, {w}, {h});" if invert else f"{dev}.drawRect({min(self.start_x,end_x)}, {min(self.start_y,end_y)}, {w}, {h});"
        elif tool in ["filled_rect","invert_filled_rect"]:
            filled=True
            self.draw_rect(self.start_x,self.start_y,end_x,end_y,filled=filled,temp=False,invert=invert)
            w,h = abs(end_x-self.start_x)+1, abs(end_y-self.start_y)+1
            cmd = f"{dev}.invertRectFilled({min(self.start_x,end_x)}, {min(self.start_y,end_y)}, {w}, {h});" if invert else f"{dev}.drawRectFilled({min(self.start_x,end_x)}, {min(self.start_y,end_y)}, {w}, {h});"
        elif tool in ["circle","invert_circle"]:
            r=int(math.hypot(end_x-self.start_x,end_y-self.start_y))
            self.draw_circle(self.start_x,self.start_y,end_x,end_y,filled=False,temp=False,invert=invert)
            cmd = f"{dev}.invertCircle({self.start_x}, {self.start_y}, {r});" if invert else f"{dev}.drawCircle({self.start_x}, {self.start_y}, {r});"
        elif tool in ["filled_circle","invert_filled_circle"]:
            r=int(math.hypot(end_x-self.start_x,end_y-self.start_y))
            self.draw_circle(self.start_x,self.start_y,end_x,end_y,filled=True,temp=False,invert=invert)
            cmd = f"{dev}.invertCircleFilled({self.start_x}, {self.start_y}, {r});" if invert else f"{dev}.drawCircleFilled({self.start_x}, {self.start_y}, {r});"
        if cmd:
            self.commands.append(cmd)
        self.draw_grid()
        self.start_x = self.start_y = None

    # -----------------------------
    def draw_line(self,x0,y0,x1,y1,temp=False,invert=False):
        dx=abs(x1-x0); sx=1 if x0<x1 else -1
        dy=-abs(y1-y0); sy=1 if y0<y1 else -1
        err=dx+dy
        target = self.preview_pixels if temp else self.pixels
        while True:
            if 0<=x0<WIDTH and 0<=y0<HEIGHT:
                target[y0][x0] = target[y0][x0]^1 if invert else 1
            if x0==x1 and y0==y1: break
            e2=2*err
            if e2>=dy: err+=dy; x0+=sx
            if e2<=dx: err+=dx; y0+=sy

    def draw_rect(self,x0,y0,x1,y1,filled=False,temp=False,invert=False):
        x_min,x_max = min(x0,x1), max(x0,x1)
        y_min,y_max = min(y0,y1), max(y0,y1)
        target = self.preview_pixels if temp else self.pixels
        if filled:
            for y in range(y_min,y_max+1):
                for x in range(x_min,x_max+1):
                    if 0<=x<WIDTH and 0<=y<HEIGHT:
                        target[y][x] = target[y][x]^1 if invert else 1
        else:
            for x in range(x_min,x_max+1):
                for y in [y_min,y_max]:
                    if 0<=x<WIDTH and 0<=y<HEIGHT:
                        target[y][x] = target[y][x]^1 if invert else 1
            for y in range(y_min,y_max+1):
                for x in [x_min,x_max]:
                    if 0<=x<WIDTH and 0<=y<HEIGHT:
                        target[y][x] = target[y][x]^1 if invert else 1

    def draw_circle(self,xc,yc,x1,y1,filled=False,temp=False,invert=False):
        r=int(math.hypot(x1-xc,y1-yc))
        target = self.preview_pixels if temp else self.pixels
        if filled:
            for y in range(yc-r,yc+r+1):
                for x in range(xc-r,xc+r+1):
                    if 0<=x<WIDTH and 0<=y<HEIGHT and (x-xc)**2+(y-yc)**2 <= r**2:
                        target[y][x] = target[y][x]^1 if invert else 1
        else:
            x,y=0,r; d=1-r
            while x<=y:
                pts=[(xc+x,yc+y),(xc-x,yc+y),(xc+x,yc-y),(xc-x,yc-y),
                     (xc+y,yc+x),(xc-y,yc+x),(xc+y,yc-x),(xc-y,yc-x)]
                for px,py in pts:
                    if 0<=px<WIDTH and 0<=py<HEIGHT:
                        target[py][px] = target[py][px]^1 if invert else 1
                if d<0: d+=2*x+3
                else: d+=2*(x-y)+5; y-=1
                x+=1

    # -----------------------------
    @staticmethod
    def escape_c_string(text):
        return text.replace("\\", "\\\\").replace('"', '\\"')

    # -----------------------------
    def start_text_input(self):
        self.cursor_text = simpledialog.askstring("Text","Enter text:")
        self.draw_grid()

    def update_cursor_text(self,event):
        self.cursor_pos = (event.x//PIXEL_SIZE, event.y//PIXEL_SIZE)
        if self.tool.get()=="text" and self.cursor_text:
            self.draw_grid(preview=True)

    def place_text(self,x,y):
        x0, y0 = x, y
        for idx,ch in enumerate(self.cursor_text):
            code = ord(ch)
            if code in self.font:
                bitmap = self.font[code]
                for row in range(8):
                    for col in range(5):
                        px, py = x0 + idx*6 + col, y0 + row
                        if 0<=px<WIDTH and 0<=py<HEIGHT and bitmap[row][col]:
                            self.pixels[py][px]=1
        dev = self.device_var.get().strip() or "gfx"
        escaped = self.escape_c_string(self.cursor_text)
        # Note: the library doesn't take a font argument - the slave's own
        # font table (font5x8.h) is used at render time on the device.
        cmd = f'{dev}.drawText({x0}, {y0}, "{escaped}");'
        self.commands.append(cmd)
        self.cursor_text = ""
        self.draw_grid()
        self.push_undo()

    # -----------------------------
    def clear(self):
        self.push_undo()
        self.pixels = [[0]*WIDTH for _ in range(HEIGHT)]
        self.draw_grid()
        dev = self.device_var.get().strip() or "gfx"
        self.commands.append(f"{dev}.clear();")

    def clear_all_instructions(self):
        self.push_undo()
        self.pixels = [[0]*WIDTH for _ in range(HEIGHT)]
        self.commands = []
        self.draw_grid()

    # -----------------------------
    def export_arduino_commands(self):
        path = filedialog.asksaveasfilename(title="Save commands", defaultextension=".txt", filetypes=[("Text files","*.txt"),("Arduino source","*.ino")])
        if not path: return
        with open(path,"w") as f:
            for cmd in self.commands:
                f.write(cmd+"\n")
        tk.messagebox.showinfo("Saved",f"Commands saved to {path}")

    # -----------------------------
    def push_undo(self):
        self.undo_stack.append((copy.deepcopy(self.pixels), copy.deepcopy(self.commands)))
        self.redo_stack.clear()

    def undo(self):
        if not self.undo_stack: return
        state = self.undo_stack.pop()
        self.redo_stack.append((copy.deepcopy(self.pixels), copy.deepcopy(self.commands)))
        self.pixels, self.commands = state
        self.draw_grid()

    def redo(self):
        if not self.redo_stack: return
        state = self.redo_stack.pop()
        self.undo_stack.append((copy.deepcopy(self.pixels), copy.deepcopy(self.commands)))
        self.pixels, self.commands = state
        self.draw_grid()

if __name__=="__main__":
    root=tk.Tk()
    app=LCDSimulator(root)
    root.mainloop()
