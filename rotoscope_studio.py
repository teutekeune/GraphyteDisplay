#!/usr/bin/env python3
"""
rotoscope_studio.py

A frame-by-frame rotoscoping tool for tiny (default 80x16) black & white video.

Workflow:
  1. Load a source video. It's auto-converted to an 80x16 (or whatever size
     you choose) black & white frame sequence using the same kind of
     downscale/threshold/dither pipeline as vidbw_studio.py.
  2. Step through the frames one at a time and hand-paint pixels black or
     white directly on a zoomed-in pixel grid -- this is the "rotoscoping"
     step, for touching up or completely redrawing frames that don't survive
     the automatic downscale well.
  3. Use onion-skinning (a faint ghost of the previous frame) to keep motion
     consistent between frames.
  4. Play back your edited frames at the source fps to preview the result
     before exporting.
  5. Export to PNG sequence, animated GIF, MP4, or a bit-packed C header
     (compatible in spirit with vidbw_studio.py's --format h output) for
     embedded playback (SSD1306-style OLEDs, LED matrices, etc).
  6. Save/load your work-in-progress as a project file at any time.

Usage:
    python3 rotoscope_studio.py                      # launch, then File > Open Video
    python3 rotoscope_studio.py input.mp4             # launch + auto-convert this video
    python3 rotoscope_studio.py --project my_edit.json  # resume a saved project

Requires: opencv-python, pillow, numpy  (tkinter ships with most Python installs)
    pip install opencv-python pillow numpy
"""

import argparse
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import List, Optional

import cv2
import numpy as np
from PIL import Image

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from PIL import ImageTk
    _TK_AVAILABLE = True
except Exception:
    _TK_AVAILABLE = False


# =====================================================================
# Core B/W conversion (same approach as vidbw_studio.py, trimmed down)
# =====================================================================

def floyd_steinberg(gray):
    """Floyd-Steinberg dither. gray: HxW array, values 0-255. Returns 0/1 array, 1=white."""
    h, w = gray.shape
    img = gray.astype(np.float32).copy()
    out = np.zeros((h, w), dtype=np.uint8)
    for y in range(h):
        for x in range(w):
            old = img[y, x]
            new = 255.0 if old >= 128 else 0.0
            out[y, x] = 1 if new >= 128 else 0
            err = old - new
            if x + 1 < w:
                img[y, x + 1] += err * 7 / 16
            if y + 1 < h:
                if x - 1 >= 0:
                    img[y + 1, x - 1] += err * 3 / 16
                img[y + 1, x] += err * 5 / 16
                if x + 1 < w:
                    img[y + 1, x + 1] += err * 1 / 16
    return out  # 1 = white


def enhance_for_tiny(gray, strength=0.6):
    """Local contrast boost + unsharp mask on the full-res frame before it gets
    crushed down to a tiny resolution, so edges/silhouettes survive better."""
    if strength <= 0:
        return gray
    gray = gray.astype(np.uint8)
    clip = 1.5 + 2.5 * strength
    clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(8, 8))
    eq = clahe.apply(gray)
    sigma = max(1.0, gray.shape[1] / 160.0)
    blur = cv2.GaussianBlur(eq, (0, 0), sigmaX=sigma)
    amount = 0.6 + 0.9 * strength
    sharpened = cv2.addWeighted(eq, 1 + amount, blur, -amount, 0)
    return np.clip(sharpened, 0, 255).astype(np.uint8)


def process_frame_bits(bgr_frame, width, height, threshold, dither, invert,
                        enhance=False, enhance_strength=0.6):
    """Resize + quantize a BGR frame. Returns HxW uint8 array of 0/1, 1=white."""
    gray = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2GRAY)
    if enhance:
        gray = enhance_for_tiny(gray, strength=enhance_strength)
    gray = cv2.resize(gray, (width, height), interpolation=cv2.INTER_AREA)
    if dither:
        bits = floyd_steinberg(gray)
    else:
        bits = (gray >= threshold).astype(np.uint8)
    if invert:
        bits = 1 - bits
    return bits


def get_video_info(path):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()
    return total, fps


def iter_sampled_frames(path, target_fps, max_frames=None):
    """Yield frames from `path`, sampled so the result plays at ~target_fps."""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {path}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or target_fps or 25.0
    step = max(src_fps / (target_fps or src_fps), 1.0)
    read_idx = 0
    next_grab = 0.0
    count = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if read_idx >= next_grab:
            yield frame
            next_grab += step
            count += 1
            if max_frames and count >= max_frames:
                break
        read_idx += 1
    cap.release()


def pack_row(bits, width, height):
    """Row-major packing: each row -> ceil(width/8) bytes, MSB first, top->bottom."""
    row_bytes = (width + 7) // 8
    out = bytearray(row_bytes * height)
    idx = 0
    for y in range(height):
        byte = 0
        bitcount = 0
        for x in range(width):
            byte = (byte << 1) | int(bits[y, x])
            bitcount += 1
            if bitcount == 8:
                out[idx] = byte
                idx += 1
                byte = 0
                bitcount = 0
        if bitcount:
            byte <<= (8 - bitcount)
            out[idx] = byte
            idx += 1
    return bytes(out)


def pack_page(bits, width, height):
    """SSD1306-style page packing: 8px-tall pages, one byte per column, LSB = top pixel."""
    pages = (height + 7) // 8
    out = bytearray(width * pages)
    idx = 0
    for page in range(pages):
        y0 = page * 8
        for x in range(width):
            byte = 0
            for b in range(8):
                y = y0 + b
                if y < height:
                    byte |= (int(bits[y, x]) << b)
            out[idx] = byte
            idx += 1
    return bytes(out)


# =====================================================================
# Project: the frame sequence being rotoscoped, with save/load
# =====================================================================

@dataclass
class ConvertSettings:
    width: int = 80
    height: int = 16
    fps: float = 12.0
    threshold: int = 128
    dither: bool = False
    invert: bool = False
    enhance: bool = True
    enhance_strength: float = 0.6
    max_frames: Optional[int] = None


class Project:
    def __init__(self, settings: ConvertSettings, source_path: str = ""):
        self.settings = settings
        self.source_path = source_path
        self.frames: List[np.ndarray] = []  # each HxW uint8, 1=white

    @property
    def width(self):
        return self.settings.width

    @property
    def height(self):
        return self.settings.height

    def convert_from_source(self, log=print, progress=None):
        """(Re)generate ALL frames from the source video. Discards edits."""
        if not self.source_path:
            raise RuntimeError("No source video set.")
        total, src_fps = get_video_info(self.source_path)
        target_fps = self.settings.fps or src_fps
        frames = []
        for frame in iter_sampled_frames(self.source_path, target_fps, self.settings.max_frames):
            bits = process_frame_bits(
                frame, self.settings.width, self.settings.height,
                self.settings.threshold, self.settings.dither, self.settings.invert,
                enhance=self.settings.enhance, enhance_strength=self.settings.enhance_strength,
            )
            frames.append(bits)
            if progress:
                progress(len(frames), total)
        if not frames:
            raise RuntimeError("No frames were read from the source video.")
        self.frames = frames
        self.settings.fps = target_fps
        log(f"Converted {len(frames)} frames at {self.settings.width}x{self.settings.height}, "
            f"{target_fps:.2f} fps")

    def regenerate_single(self, index):
        """Re-run auto conversion on just one frame, using its position in the
        sampled sequence. Only works while the source video is reachable."""
        if not self.source_path:
            raise RuntimeError("No source video set.")
        target_fps = self.settings.fps
        for i, frame in enumerate(iter_sampled_frames(self.source_path, target_fps, index + 1)):
            if i == index:
                bits = process_frame_bits(
                    frame, self.settings.width, self.settings.height,
                    self.settings.threshold, self.settings.dither, self.settings.invert,
                    enhance=self.settings.enhance, enhance_strength=self.settings.enhance_strength,
                )
                self.frames[index] = bits
                return
        raise RuntimeError("Could not re-read that frame from the source video.")

    # ---- persistence ----

    def to_json_dict(self):
        rows = []
        for bits in self.frames:
            rows.append("".join(str(int(v)) for v in bits.flatten()))
        return {
            "width": self.settings.width,
            "height": self.settings.height,
            "fps": self.settings.fps,
            "threshold": self.settings.threshold,
            "dither": self.settings.dither,
            "invert": self.settings.invert,
            "enhance": self.settings.enhance,
            "enhance_strength": self.settings.enhance_strength,
            "max_frames": self.settings.max_frames,
            "source_path": self.source_path,
            "frame_count": len(self.frames),
            "frames": rows,  # each a string of '0'/'1', row-major, len = width*height
        }

    def save(self, path):
        with open(path, "w") as f:
            json.dump(self.to_json_dict(), f)

    @classmethod
    def load(cls, path):
        with open(path) as f:
            d = json.load(f)
        settings = ConvertSettings(
            width=d["width"], height=d["height"], fps=d["fps"],
            threshold=d.get("threshold", 128), dither=d.get("dither", False),
            invert=d.get("invert", False), enhance=d.get("enhance", True),
            enhance_strength=d.get("enhance_strength", 0.6),
            max_frames=d.get("max_frames"),
        )
        proj = cls(settings, source_path=d.get("source_path", ""))
        w, h = d["width"], d["height"]
        frames = []
        for row in d["frames"]:
            arr = np.array([int(c) for c in row], dtype=np.uint8).reshape(h, w)
            frames.append(arr)
        proj.frames = frames
        return proj


def bits_to_pil(bits):
    """0/1 (1=white) array -> PIL 'L' image with values 0/255."""
    return Image.fromarray((bits * 255).astype(np.uint8), mode="L")


# =====================================================================
# Exporters
# =====================================================================

def export_png_sequence(project: Project, out_dir, log=print):
    os.makedirs(out_dir, exist_ok=True)
    digits = max(4, len(str(len(project.frames))))
    for i, bits in enumerate(project.frames):
        img = bits_to_pil(bits)
        img.save(os.path.join(out_dir, f"frame_{i:0{digits}d}.png"))
    log(f"Wrote {len(project.frames)} PNGs to {out_dir}")


def export_gif(project: Project, out_path, log=print):
    gif_frames = [bits_to_pil(b).convert("P", palette=Image.ADAPTIVE, colors=2) for b in project.frames]
    if not gif_frames:
        raise RuntimeError("No frames to export.")
    duration_ms = int(1000 / (project.settings.fps or 12.0))
    gif_frames[0].save(out_path, save_all=True, append_images=gif_frames[1:],
                        duration=duration_ms, loop=0, disposal=2)
    log(f"Wrote GIF ({len(gif_frames)} frames) to {out_path}")


def export_mp4(project: Project, out_path, log=print):
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    fps = project.settings.fps or 12.0
    writer = cv2.VideoWriter(out_path, fourcc, fps, (project.width, project.height))
    try:
        for bits in project.frames:
            arr = (bits * 255).astype(np.uint8)
            bgr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
            writer.write(bgr)
    finally:
        writer.release()
    log(f"Wrote MP4 ({len(project.frames)} frames) to {out_path}")


def export_header(project: Project, out_path, pack="row", array_name="video", log=print):
    frames = []
    for bits in project.frames:
        packed = pack_row(bits, project.width, project.height) if pack == "row" \
            else pack_page(bits, project.width, project.height)
        frames.append(packed)
    if not frames:
        raise RuntimeError("No frames to export.")
    frame_bytes = len(frames[0])
    name = array_name
    guard = f"{name.upper()}_H"
    all_bytes = bytearray()
    for f in frames:
        all_bytes.extend(f)
    with open(out_path, "w") as f:
        f.write(f"// Auto-generated by rotoscope_studio.py\n")
        f.write(f"// {len(frames)} frames, {project.width}x{project.height}, "
                f"{frame_bytes} bytes/frame, pack='{pack}', fps={project.settings.fps}\n\n")
        f.write(f"#ifndef {guard}\n#define {guard}\n\n")
        f.write("#include <Arduino.h>\n\n")
        f.write(f"#define {name.upper()}_WIDTH {project.width}\n")
        f.write(f"#define {name.upper()}_HEIGHT {project.height}\n")
        f.write(f"#define {name.upper()}_FRAME_BYTES {frame_bytes}\n")
        f.write(f"#define {name.upper()}_FRAME_COUNT {len(frames)}\n")
        f.write(f"#define {name.upper()}_FPS {project.settings.fps}\n\n")
        f.write(f"const uint8_t {name}_data[{len(all_bytes)}] PROGMEM = {{\n")
        for i in range(0, len(all_bytes), 20):
            row = ", ".join(str(b) for b in all_bytes[i:i + 20])
            f.write(f"  {row},\n")
        f.write("};\n\n")
        f.write(f"""inline void {name}_get_frame(uint16_t frame_index, uint8_t* dest) {{
  uint32_t offset = (uint32_t)frame_index * {name.upper()}_FRAME_BYTES;
  for (uint32_t i = 0; i < {name.upper()}_FRAME_BYTES; i++) {{
    dest[i] = pgm_read_byte(&{name}_data[offset + i]);
  }}
}}

#endif // {guard}
""")
    log(f"Wrote C header ({len(frames)} frames, {len(all_bytes)} bytes) to {out_path}")


# =====================================================================
# GUI
# =====================================================================

if _TK_AVAILABLE:

    class RotoscopeApp(tk.Tk):
        CELL = 10          # pixel cell size on screen, in canvas units (scaled by zoom)
        BG = "#1e1e26"

        def __init__(self, initial_input=None, initial_project=None):
            super().__init__()
            self.title("Rotoscope Studio")
            self.configure(bg=self.BG)
            self.geometry("1000x560")

            self.project: Optional[Project] = None
            self.frame_idx = 0
            self.pen_color = 1  # 1 = white, 0 = black
            self.brush_size = 1
            self.onion_skin = tk.BooleanVar(value=True)
            self.zoom = tk.IntVar(value=8)

            self._painting = False
            self._paint_color_active = 1

            self._anim_running = False
            self._anim_job = None

            self._build_menu()
            self._build_ui()
            self.bind("<Left>", lambda e: self._step_frame(-1))
            self.bind("<Right>", lambda e: self._step_frame(1))
            self.bind("<space>", lambda e: self._toggle_play())
            self.bind("b", lambda e: self._set_pen(0))
            self.bind("w", lambda e: self._set_pen(1))
            self.bind("<Control-s>", lambda e: self._save_project())

            if initial_project:
                self._load_project_path(initial_project)
            elif initial_input:
                self._open_video_path(initial_input)

        # ---------------- menu ----------------

        def _build_menu(self):
            menubar = tk.Menu(self)
            filemenu = tk.Menu(menubar, tearoff=0)
            filemenu.add_command(label="Open Video...", command=self._open_video_dialog)
            filemenu.add_separator()
            filemenu.add_command(label="Save Project...", command=self._save_project)
            filemenu.add_command(label="Load Project...", command=self._load_project_dialog)
            filemenu.add_separator()
            exportmenu = tk.Menu(filemenu, tearoff=0)
            exportmenu.add_command(label="PNG Sequence...", command=self._export_png)
            exportmenu.add_command(label="Animated GIF...", command=self._export_gif)
            exportmenu.add_command(label="MP4...", command=self._export_mp4)
            exportmenu.add_command(label="C Header (.h)...", command=self._export_header)
            filemenu.add_cascade(label="Export", menu=exportmenu)
            filemenu.add_separator()
            filemenu.add_command(label="Quit", command=self.destroy)
            menubar.add_cascade(label="File", menu=filemenu)
            self.config(menu=menubar)

        # ---------------- layout ----------------

        def _build_ui(self):
            outer = tk.Frame(self, bg=self.BG)
            outer.pack(fill="both", expand=True, padx=8, pady=8)

            # --- left: conversion settings ---
            left = tk.LabelFrame(outer, text="Conversion settings", bg=self.BG, fg="white")
            left.pack(side="left", fill="y", padx=(0, 8))

            self.var_width = tk.IntVar(value=80)
            self.var_height = tk.IntVar(value=16)
            self.var_fps = tk.DoubleVar(value=12.0)
            self.var_threshold = tk.IntVar(value=128)
            self.var_dither = tk.BooleanVar(value=False)
            self.var_invert = tk.BooleanVar(value=False)
            self.var_enhance = tk.BooleanVar(value=True)
            self.var_enhance_strength = tk.DoubleVar(value=0.6)
            self.var_max_frames = tk.StringVar(value="")

            def row(label, widget):
                r = tk.Frame(left, bg=self.BG)
                r.pack(fill="x", padx=6, pady=2)
                tk.Label(r, text=label, bg=self.BG, fg="white", width=14, anchor="w").pack(side="left")
                widget.pack(side="left", fill="x", expand=True)

            row("Width", tk.Spinbox(left, from_=8, to=256, textvariable=self.var_width, width=6))
            row("Height", tk.Spinbox(left, from_=4, to=256, textvariable=self.var_height, width=6))
            row("FPS", tk.Spinbox(left, from_=1, to=60, textvariable=self.var_fps, width=6))
            row("Threshold", tk.Spinbox(left, from_=0, to=255, textvariable=self.var_threshold, width=6))
            row("Max frames", tk.Entry(left, textvariable=self.var_max_frames, width=6))
            tk.Checkbutton(left, text="Dither (Floyd-Steinberg)", variable=self.var_dither,
                            bg=self.BG, fg="white", selectcolor="#333").pack(anchor="w", padx=6)
            tk.Checkbutton(left, text="Invert", variable=self.var_invert,
                            bg=self.BG, fg="white", selectcolor="#333").pack(anchor="w", padx=6)
            tk.Checkbutton(left, text="Enhance edges before downscale", variable=self.var_enhance,
                            bg=self.BG, fg="white", selectcolor="#333").pack(anchor="w", padx=6)

            tk.Button(left, text="Convert Video -> Frames\n(discards edits)",
                      command=self._on_convert).pack(fill="x", padx=6, pady=(10, 2))
            tk.Button(left, text="Reset THIS frame to auto",
                      command=self._on_regen_frame).pack(fill="x", padx=6, pady=2)

            self.var_status = tk.StringVar(value="Open a video to begin.")
            tk.Label(left, textvariable=self.var_status, bg=self.BG, fg="#8fd18f",
                     wraplength=200, justify="left").pack(fill="x", padx=6, pady=(14, 2))

            # --- center: canvas + controls ---
            center = tk.Frame(outer, bg=self.BG)
            center.pack(side="left", fill="both", expand=True)

            canvas_wrap = tk.Frame(center, bg=self.BG)
            canvas_wrap.pack(fill="both", expand=True)
            self.canvas = tk.Canvas(canvas_wrap, bg="#000000", highlightthickness=1,
                                     highlightbackground="#555")
            self.canvas.pack(fill="both", expand=True)
            self.canvas.bind("<Button-1>", self._on_paint_start_white)
            self.canvas.bind("<B1-Motion>", self._on_paint_drag)
            self.canvas.bind("<ButtonRelease-1>", self._on_paint_end)
            self.canvas.bind("<Button-3>", self._on_paint_start_black)
            self.canvas.bind("<B3-Motion>", self._on_paint_drag)
            self.canvas.bind("<ButtonRelease-3>", self._on_paint_end)

            toolbar = tk.Frame(center, bg=self.BG)
            toolbar.pack(fill="x", pady=(6, 0))

            self.pen_btn = tk.Button(toolbar, text="Pen: WHITE (w)", command=self._toggle_pen)
            self.pen_btn.pack(side="left", padx=2)
            tk.Label(toolbar, text="Brush", bg=self.BG, fg="white").pack(side="left", padx=(10, 2))
            self.var_brush = tk.IntVar(value=1)
            tk.Spinbox(toolbar, from_=1, to=80, width=3, textvariable=self.var_brush,
                       command=self._on_brush_change).pack(side="left")
            tk.Checkbutton(toolbar, text="Onion skin (prev frame)", variable=self.onion_skin,
                            bg=self.BG, fg="white", selectcolor="#333",
                            command=self._redraw).pack(side="left", padx=(10, 2))
            tk.Label(toolbar, text="Zoom", bg=self.BG, fg="white").pack(side="left", padx=(10, 2))
            tk.Scale(toolbar, from_=2, to=20, orient="horizontal", variable=self.zoom,
                     bg=self.BG, fg="white", troughcolor="#333", length=100,
                     command=lambda e: self._redraw()).pack(side="left")
            tk.Button(toolbar, text="Copy Previous Frame",
                      command=self._on_copy_prev).pack(side="left", padx=(10, 2))
            tk.Button(toolbar, text="Clear Frame (black)",
                      command=self._on_clear_frame).pack(side="left", padx=2)

            navbar = tk.Frame(center, bg=self.BG)
            navbar.pack(fill="x", pady=6)
            tk.Button(navbar, text="<< Prev", command=lambda: self._step_frame(-1)).pack(side="left")
            self.var_frame_label = tk.StringVar(value="Frame 0 / 0")
            tk.Label(navbar, textvariable=self.var_frame_label, bg=self.BG, fg="white",
                     width=16).pack(side="left", padx=6)
            tk.Button(navbar, text="Next >>", command=lambda: self._step_frame(1)).pack(side="left")
            self.frame_slider = tk.Scale(navbar, from_=0, to=0, orient="horizontal",
                                          bg=self.BG, fg="white", troughcolor="#333",
                                          showvalue=False, command=self._on_slider)
            self.frame_slider.pack(side="left", fill="x", expand=True, padx=10)
            self.play_btn = tk.Button(navbar, text="Play Preview", command=self._toggle_play)
            self.play_btn.pack(side="left", padx=6)

            hint = tk.Label(center, bg=self.BG, fg="#888",
                             text="Left-click/drag: paint with pen  |  Right-click/drag: paint opposite  |  "
                                  "Arrow keys: prev/next frame  |  Space: play/pause  |  b/w: set pen")
            hint.pack(fill="x")

        # ---------------- video / conversion ----------------

        def _open_video_dialog(self):
            path = filedialog.askopenfilename(
                filetypes=[("Video files", "*.mp4 *.mov *.avi *.mkv *.webm"), ("All files", "*.*")])
            if path:
                self._open_video_path(path)

        def _open_video_path(self, path):
            try:
                _, src_fps = get_video_info(path)
            except Exception as e:
                messagebox.showerror("Error", str(e))
                return
            self.var_fps.set(round(src_fps, 2))
            settings = self._collect_settings()
            self.project = Project(settings, source_path=path)
            self._on_convert()

        def _collect_settings(self):
            max_frames = None
            if self.var_max_frames.get().strip():
                try:
                    max_frames = int(self.var_max_frames.get())
                except ValueError:
                    max_frames = None
            return ConvertSettings(
                width=int(self.var_width.get()),
                height=int(self.var_height.get()),
                fps=float(self.var_fps.get()),
                threshold=int(self.var_threshold.get()),
                dither=bool(self.var_dither.get()),
                invert=bool(self.var_invert.get()),
                enhance=bool(self.var_enhance.get()),
                enhance_strength=float(self.var_enhance_strength.get()),
                max_frames=max_frames,
            )

        def _on_convert(self):
            if self.project is None or not self.project.source_path:
                messagebox.showinfo("No video", "Open a video first (File > Open Video).")
                return
            if self.project.frames:
                if not messagebox.askyesno("Discard edits?",
                                            "Re-converting will discard any hand-painted edits. Continue?"):
                    return
            self.project.settings = self._collect_settings()
            self.var_status.set("Converting...")
            self.update_idletasks()

            def worker():
                try:
                    self.project.convert_from_source(log=lambda m: None)
                    self.after(0, self._on_convert_done)
                except Exception as e:
                    self.after(0, lambda: messagebox.showerror("Conversion failed", str(e)))

            threading.Thread(target=worker, daemon=True).start()

        def _on_convert_done(self):
            self.frame_idx = 0
            self.frame_slider.configure(to=max(0, len(self.project.frames) - 1))
            self.var_status.set(f"{len(self.project.frames)} frames at "
                                 f"{self.project.width}x{self.project.height}, "
                                 f"{self.project.settings.fps:.2f} fps. Ready to rotoscope.")
            self._redraw()

        def _on_regen_frame(self):
            if not self.project or not self.project.frames:
                return
            try:
                self.project.regenerate_single(self.frame_idx)
                self._redraw()
            except Exception as e:
                messagebox.showerror("Error", str(e))

        # ---------------- frame navigation ----------------

        def _step_frame(self, delta):
            if not self.project or not self.project.frames:
                return
            self.frame_idx = max(0, min(len(self.project.frames) - 1, self.frame_idx + delta))
            self.frame_slider.set(self.frame_idx)
            self._redraw()

        def _on_slider(self, value):
            if not self.project or not self.project.frames:
                return
            idx = int(float(value))
            if idx != self.frame_idx:
                self.frame_idx = idx
                self._redraw()

        def _on_copy_prev(self):
            if not self.project or self.frame_idx == 0:
                return
            self.project.frames[self.frame_idx] = self.project.frames[self.frame_idx - 1].copy()
            self._redraw()

        def _on_clear_frame(self):
            if not self.project or not self.project.frames:
                return
            self.project.frames[self.frame_idx][:] = 0
            self._redraw()

        # ---------------- painting ----------------

        def _set_pen(self, color):
            self.pen_color = color
            self.pen_btn.configure(text=f"Pen: {'WHITE' if color == 1 else 'BLACK'} (w/b)")

        def _toggle_pen(self):
            self._set_pen(1 - self.pen_color)

        def _on_brush_change(self):
            self.brush_size = int(self.var_brush.get())

        def _canvas_to_cell(self, x, y):
            cell = self.CELL * self.zoom.get() / 10
            ox, oy = self._origin
            col = int((x - ox) // cell)
            row = int((y - oy) // cell)
            return col, row

        def _paint_at(self, x, y, color):
            if not self.project or not self.project.frames:
                return
            col, row = self._canvas_to_cell(x, y)
            r = self.brush_size // 2
            w, h = self.project.width, self.project.height
            bits = self.project.frames[self.frame_idx]
            changed = False
            for dy in range(-r, self.brush_size - r):
                for dx in range(-r, self.brush_size - r):
                    cx, cy = col + dx, row + dy
                    if 0 <= cx < w and 0 <= cy < h and bits[cy, cx] != color:
                        bits[cy, cx] = color
                        changed = True
            if changed:
                self._redraw()

        def _on_paint_start_white(self, event):
            self._painting = True
            self._paint_color_active = self.pen_color
            self._paint_at(event.x, event.y, self._paint_color_active)

        def _on_paint_start_black(self, event):
            self._painting = True
            self._paint_color_active = 1 - self.pen_color
            self._paint_at(event.x, event.y, self._paint_color_active)

        def _on_paint_drag(self, event):
            if self._painting:
                self._paint_at(event.x, event.y, self._paint_color_active)

        def _on_paint_end(self, event):
            self._painting = False

        # ---------------- drawing ----------------

        def _redraw(self):
            if not self.project or not self.project.frames:
                self.canvas.delete("all")
                self.var_frame_label.set("Frame 0 / 0")
                return
            bits = self.project.frames[self.frame_idx]
            prev = self.project.frames[self.frame_idx - 1] if (self.onion_skin.get() and self.frame_idx > 0) else None
            w, h = self.project.width, self.project.height
            cell = self.CELL * self.zoom.get() / 10

            self.canvas.delete("all")
            cw = self.canvas.winfo_width() or (w * cell)
            ch = self.canvas.winfo_height() or (h * cell)
            ox = max(0, (cw - w * cell) / 2)
            oy = max(0, (ch - h * cell) / 2)
            self._origin = (ox, oy)

            for y in range(h):
                for x in range(w):
                    v = bits[y, x]
                    if v:
                        color = "#ffffff"
                    elif prev is not None and prev[y, x]:
                        color = "#3a3a55"  # onion-skin ghost of previous frame's white pixel
                    else:
                        color = "#000000"
                    x0 = ox + x * cell
                    y0 = oy + y * cell
                    self.canvas.create_rectangle(x0, y0, x0 + cell, y0 + cell,
                                                  fill=color, outline="#101010")
            self.var_frame_label.set(f"Frame {self.frame_idx} / {len(self.project.frames) - 1}")

        # ---------------- playback preview ----------------

        def _toggle_play(self):
            if not self.project or not self.project.frames:
                return
            if self._anim_running:
                self._anim_running = False
                if self._anim_job:
                    self.after_cancel(self._anim_job)
                    self._anim_job = None
                self.play_btn.configure(text="Play Preview")
                self._redraw()
            else:
                self._anim_running = True
                self.play_btn.configure(text="Stop Preview")
                self._animate_step()

        def _animate_step(self):
            if not self._anim_running:
                return
            self._redraw_playback_frame()
            self.frame_idx = (self.frame_idx + 1) % len(self.project.frames)
            self.frame_slider.set(self.frame_idx)
            interval = max(20, int(1000 / (self.project.settings.fps or 12.0)))
            self._anim_job = self.after(interval, self._animate_step)

        def _redraw_playback_frame(self):
            # Same as _redraw but without onion skin, for a clean preview.
            bits = self.project.frames[self.frame_idx]
            w, h = self.project.width, self.project.height
            cell = self.CELL * self.zoom.get() / 10
            self.canvas.delete("all")
            cw = self.canvas.winfo_width() or (w * cell)
            ch = self.canvas.winfo_height() or (h * cell)
            ox = max(0, (cw - w * cell) / 2)
            oy = max(0, (ch - h * cell) / 2)
            for y in range(h):
                for x in range(w):
                    color = "#ffffff" if bits[y, x] else "#000000"
                    x0 = ox + x * cell
                    y0 = oy + y * cell
                    self.canvas.create_rectangle(x0, y0, x0 + cell, y0 + cell,
                                                  fill=color, outline="")
            self.var_frame_label.set(f"Frame {self.frame_idx} / {len(self.project.frames) - 1} (playing)")

        # ---------------- project save/load ----------------

        def _save_project(self):
            if not self.project or not self.project.frames:
                messagebox.showinfo("Nothing to save", "Convert a video first.")
                return
            path = filedialog.asksaveasfilename(defaultextension=".json",
                                                 filetypes=[("Rotoscope project", "*.json")])
            if not path:
                return
            self.project.save(path)
            self.var_status.set(f"Saved project to {path}")

        def _load_project_dialog(self):
            path = filedialog.askopenfilename(filetypes=[("Rotoscope project", "*.json")])
            if path:
                self._load_project_path(path)

        def _load_project_path(self, path):
            try:
                self.project = Project.load(path)
            except Exception as e:
                messagebox.showerror("Load failed", str(e))
                return
            s = self.project.settings
            self.var_width.set(s.width)
            self.var_height.set(s.height)
            self.var_fps.set(s.fps)
            self.var_threshold.set(s.threshold)
            self.var_dither.set(s.dither)
            self.var_invert.set(s.invert)
            self.var_enhance.set(s.enhance)
            self.frame_idx = 0
            self.frame_slider.configure(to=max(0, len(self.project.frames) - 1))
            self.var_status.set(f"Loaded {len(self.project.frames)} frames from {path}")
            self._redraw()

        # ---------------- export ----------------

        def _require_project(self):
            if not self.project or not self.project.frames:
                messagebox.showinfo("Nothing to export", "Convert a video first.")
                return False
            return True

        def _export_png(self):
            if not self._require_project():
                return
            d = filedialog.askdirectory()
            if d:
                export_png_sequence(self.project, d)
                messagebox.showinfo("Done", f"PNG sequence written to {d}")

        def _export_gif(self):
            if not self._require_project():
                return
            path = filedialog.asksaveasfilename(defaultextension=".gif", filetypes=[("GIF", "*.gif")])
            if path:
                export_gif(self.project, path)
                messagebox.showinfo("Done", f"GIF written to {path}")

        def _export_mp4(self):
            if not self._require_project():
                return
            path = filedialog.asksaveasfilename(defaultextension=".mp4", filetypes=[("MP4", "*.mp4")])
            if path:
                export_mp4(self.project, path)
                messagebox.showinfo("Done", f"MP4 written to {path}")

        def _export_header(self):
            if not self._require_project():
                return
            path = filedialog.asksaveasfilename(defaultextension=".h", filetypes=[("C header", "*.h")])
            if path:
                export_header(self.project, path)
                messagebox.showinfo("Done", f"Header written to {path}")


def launch_gui(initial_input=None, initial_project=None):
    if not _TK_AVAILABLE:
        sys.exit("Tkinter is not available in this Python environment; cannot launch the GUI.")
    app = RotoscopeApp(initial_input=initial_input, initial_project=initial_project)
    app.mainloop()


def main():
    parser = argparse.ArgumentParser(description="Frame-by-frame rotoscoping tool for tiny B/W video.")
    parser.add_argument("input", nargs="?", help="Video file to open and auto-convert on launch.")
    parser.add_argument("--project", help="Rotoscope project (.json) to resume on launch.")
    args = parser.parse_args()
    launch_gui(initial_input=args.input, initial_project=args.project)


if __name__ == "__main__":
    main()
