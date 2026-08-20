import tkinter as tk
from tkinter import filedialog, simpledialog
import math
import copy

WIDTH, HEIGHT = 80, 16
PIXEL_SIZE = 20

class LCDSimulator:
    def __init__(self, master):
        self.master = master
        self.master.title("GRAPHYTE-Designer")
        self.pixels = [[0]*WIDTH for _ in range(HEIGHT)]
        self.commands = []
        self.font = {}
        self.font_file = None

        self.undo_stack = []
        self.redo_stack = []

        self.tool = tk.StringVar(value="line")  # default tool

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
        tk.Button(frame, text="Font Yükle .txt", command=self.load_font_file).pack(side="left")
        tk.Button(frame, text="Yazı Ekle", command=self.start_text_input).pack(side="left")
        tk.Button(frame, text="Geri Al", command=self.undo).pack(side="left")
        tk.Button(frame, text="İleri Al", command=self.redo).pack(side="left")
        tk.Button(frame, text="Komut Listesi çıktısı", command=self.export_python_commands).pack(side="left")

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
        if tool in ["line","invert_line"]:
            self.draw_line(self.start_x,self.start_y,end_x,end_y,temp=False,invert=invert)
            cmd = f"graphyte.invert_line({self.start_x},{self.start_y},{end_x},{end_y})" if invert else f"graphyte.draw_line({self.start_x},{self.start_y},{end_x},{end_y})"
        elif tool in ["rect","invert_rect"]:
            filled=False
            self.draw_rect(self.start_x,self.start_y,end_x,end_y,filled=filled,temp=False,invert=invert)
            w,h = abs(end_x-self.start_x)+1, abs(end_y-self.start_y)+1
            cmd = f"graphyte.invert_rect({min(self.start_x,end_x)},{min(self.start_y,end_y)},{w},{h})" if invert else f"graphyte.draw_rect({min(self.start_x,end_x)},{min(self.start_y,end_y)},{w},{h})"
        elif tool in ["filled_rect","invert_filled_rect"]:
            filled=True
            self.draw_rect(self.start_x,self.start_y,end_x,end_y,filled=filled,temp=False,invert=invert)
            w,h = abs(end_x-self.start_x)+1, abs(end_y-self.start_y)+1
            cmd = f"graphyte.invert_rect_filled({min(self.start_x,end_x)},{min(self.start_y,end_y)},{w},{h})" if invert else f"graphyte.draw_rect_filled({min(self.start_x,end_x)},{min(self.start_y,end_y)},{w},{h})"
        elif tool in ["circle","invert_circle"]:
            r=int(math.hypot(end_x-self.start_x,end_y-self.start_y))
            self.draw_circle(self.start_x,self.start_y,end_x,end_y,filled=False,temp=False,invert=invert)
            cmd = f"graphyte.invert_circle({self.start_x},{self.start_y},{r})" if invert else f"graphyte.draw_circle({self.start_x},{self.start_y},{r})"
        elif tool in ["filled_circle","invert_filled_circle"]:
            r=int(math.hypot(end_x-self.start_x,end_y-self.start_y))
            self.draw_circle(self.start_x,self.start_y,end_x,end_y,filled=True,temp=False,invert=invert)
            cmd = f"graphyte.invert_circle_filled({self.start_x},{self.start_y},{r})" if invert else f"graphyte.draw_circle_filled({self.start_x},{self.start_y},{r})"
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
    def load_font_file(self):
        path = filedialog.askopenfilename(title="Select 5x8 font file", filetypes=[("Text files","*.txt")])
        if not path: return
        self.font_file = path
        self.font = {}
        with open(path) as f:
            lines = f.readlines()
        char_code = None
        row = 0
        for line in lines:
            line=line.strip()
            if line.startswith("CHAR"):
                char_code = int(line.split()[1])
                self.font[char_code] = [[0]*5 for _ in range(8)]
                row=0
            elif char_code is not None and row<8:
                for col in range(5):
                    self.font[char_code][row][col]=1 if line[col]=='1' else 0
                row+=1

    # -----------------------------
    def start_text_input(self):
        if not self.font_file:
            tk.messagebox.showerror("Error","Load font first!")
            return
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
        if self.font_file:
            font_name = self.font_file.split("/")[-1]
            self.commands.append(f'graphyte.draw_text({x0},{y0},"{self.cursor_text}", font="{font_name}")')
        else:
            self.commands.append(f'graphyte.draw_text({x0},{y0},"{self.cursor_text}")')
        self.cursor_text = ""
        self.draw_grid()
        self.push_undo()

    # -----------------------------
    def clear(self):
        self.push_undo()
        self.pixels = [[0]*WIDTH for _ in range(HEIGHT)]
        self.draw_grid()
        self.commands.append("graphyte.clear()")

    def clear_all_instructions(self):
        self.push_undo()
        self.pixels = [[0]*WIDTH for _ in range(HEIGHT)]
        self.commands = []
        self.draw_grid()

    # -----------------------------
    def export_python_commands(self):
        path = filedialog.asksaveasfilename(title="Save commands", defaultextension=".txt", filetypes=[("Text files","*.txt")])
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
