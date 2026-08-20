#!/usr/bin/env python3
"""
vidbw_studio.py

Combined tool merging video_downscale_bw.py and vid2h.py into one script,
with a Tkinter GUI that lets you preview the tiny black & white conversion
(including an animated preview) before exporting.

Export formats:
  mp4   - tiny B&W video, pixels forced to pure black(0)/white(255)
  gif   - tiny B&W video with a real 2-color palette
  h     - bit-packed C header for Arduino/AVR playback (row or page packing)

GUI usage:
    python3 vidbw_studio.py
    python3 vidbw_studio.py --gui input.mp4

CLI usage (no window, scriptable):
    python3 vidbw_studio.py input.mp4 -o tiny.mp4 --format mp4
    python3 vidbw_studio.py input.mp4 -o tiny.gif --format gif --dither
    python3 vidbw_studio.py input.mp4 -o frames.h --format h --pack page --dither
    python3 vidbw_studio.py input.mp4 -o frames.h --format h --emit-ino sketch.ino \
        --preview-gif preview.gif

Requires: opencv-python, pillow, numpy  (tkinter ships with most Python installs)
    pip install opencv-python pillow numpy
"""

import argparse
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

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
# Core image / bit processing (shared by every export path)
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


def enhance_for_tiny(gray, strength=0.6, use_saliency=True):
    """
    Boost local contrast and edges on the FULL-RESOLUTION grayscale frame before
    it gets crushed down to a tiny resolution. At 80x16 (or smaller) almost all
    fine detail is lost on resize; sharpening/contrast at full res first means
    more of what survives the downscale is the stuff that actually matters
    (edges, silhouettes, the main subject) rather than noise averaging out to gray mush.

    strength: 0.0 (off) - 1.0 (aggressive)
    use_saliency: if True and cv2.saliency is available, detect the salient
        (visually important) region and push its contrast up further while
        flattening the background toward mid-gray, so the main subject reads
        clearly even at extreme downscale. Falls back silently if the
        saliency module isn't available (needs opencv-contrib-python) or
        errors on a particular frame.
    """
    if strength <= 0:
        return gray

    gray = gray.astype(np.uint8)

    # 1) Local contrast boost (CLAHE) -- brings out detail in flat/dark regions
    clip = 1.5 + 2.5 * strength
    clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(8, 8))
    eq = clahe.apply(gray)

    # 2) Unsharp mask -- exaggerates edges so they survive averaging on resize
    sigma = max(1.0, gray.shape[1] / 160.0)
    blur = cv2.GaussianBlur(eq, (0, 0), sigmaX=sigma)
    amount = 0.6 + 0.9 * strength
    sharpened = cv2.addWeighted(eq, 1 + amount, blur, -amount, 0)
    sharpened = np.clip(sharpened, 0, 255).astype(np.uint8)

    # 3) Saliency-weighted emphasis -- push the important region's contrast up,
    #    flatten background toward mid-gray so the subject dominates the tiny frame
    if use_saliency:
        try:
            if not hasattr(enhance_for_tiny, "_saliency_detector"):
                enhance_for_tiny._saliency_detector = cv2.saliency.StaticSaliencySpectralResidual_create()
            ok, sal_map = enhance_for_tiny._saliency_detector.computeSaliency(sharpened)
            if ok:
                sal_map = cv2.normalize(sal_map, None, 0.0, 1.0, cv2.NORM_MINMAX)
                sal_map = cv2.GaussianBlur(sal_map, (0, 0), sigmaX=sigma * 2)
                gain = 0.5 + 1.5 * strength * sal_map  # background ~0.5x, salient region up to 2x
                boosted = 128 + (sharpened.astype(np.float32) - 128) * gain
                sharpened = np.clip(boosted, 0, 255).astype(np.uint8)
        except Exception:
            pass  # saliency module unavailable or failed on this frame -- keep sharpened result

    return sharpened


def process_frame_bits(bgr_frame, width, height, threshold, dither, invert,
                        enhance=False, enhance_strength=0.6, use_saliency=True):
    """Resize + quantize a BGR frame. Returns HxW uint8 array of 0/1, 1=white."""
    gray = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2GRAY)
    if enhance:
        gray = enhance_for_tiny(gray, strength=enhance_strength, use_saliency=use_saliency)
    gray = cv2.resize(gray, (width, height), interpolation=cv2.INTER_AREA)
    if dither:
        bits = floyd_steinberg(gray)
    else:
        bits = (gray >= threshold).astype(np.uint8)
    if invert:
        bits = 1 - bits
    return bits


def bits_to_pil(bits):
    """0/1 (1=white) array -> PIL 'L' image with values 0/255."""
    return Image.fromarray((bits * 255).astype(np.uint8), mode="L")


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
# Video reading helpers
# =====================================================================

def get_video_info(path):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()
    return total, fps


def get_frame_at(path, index):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {path}")
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, index))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        return None
    return frame


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


# =====================================================================
# Params object shared between CLI and GUI
# =====================================================================

@dataclass
class Params:
    input: str = ""
    output: str = ""
    format: str = "mp4"          # mp4 | gif | h
    width: int = 80
    height: int = 16
    fps: Optional[float] = None
    dither: bool = False
    invert: bool = False
    threshold: int = 128
    enhance: bool = True
    enhance_strength: float = 0.6
    use_saliency: bool = True
    pack: str = "row"            # row | page  (h only)
    max_frames: Optional[int] = None
    array_name: str = "video"
    chunk_bytes: int = 32000
    preview_gif: Optional[str] = None   # h only: optional preview gif alongside header
    emit_ino: Optional[str] = None      # h only: optional example .ino sketch


# =====================================================================
# Exporters
# =====================================================================

def export_video(params: Params, log=print, progress=None):
    """Export to mp4 or gif."""
    total, src_fps = get_video_info(params.input)
    target_fps = params.fps or src_fps
    frames_written = 0
    gif_frames = []
    writer = None
    try:
        for frame in iter_sampled_frames(params.input, target_fps, params.max_frames):
            bits = process_frame_bits(frame, params.width, params.height,
                                       params.threshold, params.dither, params.invert,
                                       enhance=params.enhance, enhance_strength=params.enhance_strength,
                                       use_saliency=params.use_saliency)
            img = bits_to_pil(bits)
            if params.format == "gif":
                gif_frames.append(img.convert("P", palette=Image.ADAPTIVE, colors=2))
            else:
                if writer is None:
                    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                    writer = cv2.VideoWriter(params.output, fourcc, target_fps,
                                              (params.width, params.height))
                arr = np.array(img)
                bgr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
                writer.write(bgr)
            frames_written += 1
            if progress:
                progress(frames_written, total)
    finally:
        if writer is not None:
            writer.release()

    if params.format == "gif":
        if not gif_frames:
            raise RuntimeError("No frames were read from the input video.")
        duration_ms = int(1000 / target_fps)
        gif_frames[0].save(
            params.output, save_all=True, append_images=gif_frames[1:],
            duration=duration_ms, loop=0, disposal=2,
        )
    else:
        if frames_written == 0:
            raise RuntimeError("No frames were read from the input video.")

    log(f"Done. Wrote {frames_written} frames to {params.output}")
    return frames_written


def write_header_file(params: Params, frames, frame_bytes, fps_used):
    name = params.array_name
    guard = f"{name.upper()}_H"

    all_bytes = bytearray()
    for f in frames:
        all_bytes.extend(f)

    chunk_size = params.chunk_bytes
    chunks = [all_bytes[i:i + chunk_size] for i in range(0, len(all_bytes), chunk_size)]

    with open(params.output, "w") as f:
        f.write(f"// Auto-generated by vidbw_studio.py from '{os.path.basename(params.input)}'\n")
        f.write(f"// {len(frames)} frames, {params.width}x{params.height}, "
                f"{frame_bytes} bytes/frame, pack='{params.pack}', fps={fps_used}\n")
        f.write("// Do not edit by hand.\n\n")
        f.write(f"#ifndef {guard}\n#define {guard}\n\n")
        f.write("#include <Arduino.h>\n\n")

        f.write(f"#define {name.upper()}_WIDTH {params.width}\n")
        f.write(f"#define {name.upper()}_HEIGHT {params.height}\n")
        f.write(f"#define {name.upper()}_FRAME_BYTES {frame_bytes}\n")
        f.write(f"#define {name.upper()}_FRAME_COUNT {len(frames)}\n")
        f.write(f"#define {name.upper()}_FPS {fps_used}\n")
        f.write(f"#define {name.upper()}_PACK_{'ROW' if params.pack == 'row' else 'PAGE'} 1\n\n")

        for ci, chunk in enumerate(chunks):
            f.write(f"const uint8_t {name}_chunk{ci}[{len(chunk)}] PROGMEM = {{\n")
            for i in range(0, len(chunk), 20):
                row = ", ".join(str(b) for b in chunk[i:i + 20])
                f.write(f"  {row},\n")
            f.write("};\n\n")

        f.write(f"const uint8_t* const {name}_chunks[] PROGMEM = {{\n")
        f.write("  " + ", ".join(f"{name}_chunk{ci}" for ci in range(len(chunks))) + "\n")
        f.write("};\n\n")
        f.write(f"const uint32_t {name}_chunk_sizes[] PROGMEM = {{\n")
        f.write("  " + ", ".join(str(len(c)) for c in chunks) + "\n")
        f.write("};\n")
        f.write(f"const uint8_t {name}_num_chunks = {len(chunks)};\n\n")

        f.write(f"""// Copies `count` bytes starting at absolute byte offset `offset` in the
// {name} stream into `dest` (a RAM buffer you provide). Handles chunk
// boundaries transparently. Use this to pull out frame N:
//   {name}_read(N * {name.upper()}_FRAME_BYTES, {name.upper()}_FRAME_BYTES, buf);
inline void {name}_read(uint32_t offset, uint32_t count, uint8_t* dest) {{
  uint32_t pos = 0;
  for (uint8_t c = 0; c < {name}_num_chunks && count > 0; c++) {{
    uint32_t csize = pgm_read_dword(&{name}_chunk_sizes[c]);
    if (offset >= pos + csize) {{ pos += csize; continue; }}
    const uint8_t* base = (const uint8_t*)pgm_read_ptr(&{name}_chunks[c]);
    uint32_t start_in_chunk = offset - pos;
    while (start_in_chunk < csize && count > 0) {{
      *dest++ = pgm_read_byte(base + start_in_chunk);
      start_in_chunk++;
      offset++;
      count--;
    }}
    pos += csize;
  }}
}}

inline void {name}_get_frame(uint16_t frame_index, uint8_t* dest) {{
  {name}_read((uint32_t)frame_index * {name.upper()}_FRAME_BYTES, {name.upper()}_FRAME_BYTES, dest);
}}

#endif // {guard}
""")


def write_preview_gif(path, frames_gray, fps):
    try:
        import imageio.v2 as imageio
    except ImportError:
        os.system("pip install imageio --break-system-packages -q")
        import imageio.v2 as imageio
    imageio.mimsave(path, frames_gray, duration=1.0 / fps, loop=0)


def write_example_ino(params: Params, fps_used):
    name = params.array_name
    header_name = os.path.basename(params.output)
    if params.pack == "row":
        drawing_note = (
            "  // Row-packed: feed straight into Adafruit_GFX::drawBitmap(), e.g.\n"
            "  //   display.clearDisplay();\n"
            f"  //   display.drawBitmap(0, 0, framebuf, {name.upper()}_WIDTH, {name.upper()}_HEIGHT, WHITE);\n"
            "  //   display.display();"
        )
    else:
        drawing_note = (
            "  // Page-packed (SSD1306 style): write framebuf directly into the display's\n"
            "  // own buffer, e.g. with Adafruit_SSD1306:\n"
            "  //   memcpy(display.getBuffer(), framebuf, sizeof(framebuf));\n"
            "  //   display.display();"
        )

    sketch = f"""// Example playback sketch for {header_name}
// Generated alongside it by vidbw_studio.py -- adapt drawing calls to your display driver.

#include "{header_name}"

uint8_t framebuf[{name.upper()}_FRAME_BYTES];
uint16_t currentFrame = 0;
unsigned long lastFrameTime = 0;
const unsigned long frameIntervalMs = 1000UL / {name.upper()}_FPS;

void setup() {{
  Serial.begin(115200);
  // TODO: initialize your display here (display.begin(), etc.)
}}

void loop() {{
  unsigned long now = millis();
  if (now - lastFrameTime >= frameIntervalMs) {{
    lastFrameTime = now;

    {name}_get_frame(currentFrame, framebuf);

{drawing_note}

    currentFrame++;
    if (currentFrame >= {name.upper()}_FRAME_COUNT) {{
      currentFrame = 0; // loop the video
    }}
  }}
}}
"""
    with open(params.emit_ino, "w") as f:
        f.write(sketch)


def export_h(params: Params, log=print, progress=None):
    total, src_fps = get_video_info(params.input)
    target_fps = params.fps or src_fps
    frames = []
    preview_frames = []
    count = 0
    for frame in iter_sampled_frames(params.input, target_fps, params.max_frames):
        bits = process_frame_bits(frame, params.width, params.height,
                                   params.threshold, params.dither, params.invert,
                                       enhance=params.enhance, enhance_strength=params.enhance_strength,
                                       use_saliency=params.use_saliency)
        packed = pack_row(bits, params.width, params.height) if params.pack == "row" \
            else pack_page(bits, params.width, params.height)
        frames.append(packed)
        if params.preview_gif:
            preview_frames.append((bits * 255).astype(np.uint8))
        count += 1
        if progress:
            progress(count, total)

    if not frames:
        raise RuntimeError("No frames were decoded from the input video.")

    frame_bytes = len(frames[0])
    total_bytes = frame_bytes * len(frames)
    log(f"Decoded {len(frames)} frames @ {target_fps} fps target "
        f"(source ~{src_fps:.2f} fps)")
    log(f"Frame size: {frame_bytes} bytes | Total: {total_bytes} bytes "
        f"({total_bytes/1024:.1f} KB) pack='{params.pack}' {params.width}x{params.height}")

    write_header_file(params, frames, frame_bytes, target_fps)

    if params.preview_gif and preview_frames:
        write_preview_gif(params.preview_gif, preview_frames, target_fps)
        log(f"Preview gif written to {params.preview_gif}")

    if params.emit_ino:
        write_example_ino(params, target_fps)
        log(f"Example sketch written to {params.emit_ino}")

    log(f"Header written to {params.output}")
    return len(frames)


def run_export(params: Params, log=print, progress=None):
    if params.format == "h":
        return export_h(params, log=log, progress=progress)
    else:
        return export_video(params, log=log, progress=progress)


# =====================================================================
# CLI
# =====================================================================

def build_arg_parser():
    p = argparse.ArgumentParser(
        description="Downscale a video to tiny B&W mp4/gif, or a bit-packed Arduino .h header. "
                    "Run with no arguments to launch the GUI.")
    p.add_argument("input", nargs="?", help="Path to the input video file")
    p.add_argument("-o", "--output", default=None, help="Output file path")
    p.add_argument("--format", choices=["mp4", "gif", "h"], default=None,
                   help="Output format (inferred from --output extension if omitted; "
                        "'.h'/'.hpp' -> h, '.gif' -> gif, else mp4)")
    p.add_argument("-w", "--width", type=int, default=80, help="Output width in pixels (default 80)")
    p.add_argument("-H", "--height", type=int, default=16, help="Output height in pixels (default 16)")
    p.add_argument("--fps", type=float, default=None, help="Target playback fps (default: source fps)")
    p.add_argument("--dither", action="store_true",
                   help="Use Floyd-Steinberg dithering instead of a flat threshold")
    p.add_argument("-t", "--threshold", type=int, default=128,
                   help="0-255 threshold used when --dither is not set (default 128)")
    p.add_argument("--invert", action="store_true", help="Invert black/white")
    p.add_argument("--no-enhance", action="store_true",
                   help="Disable smart contrast/edge enhancement for tiny resolutions (on by default)")
    p.add_argument("--enhance-strength", type=float, default=0.6,
                   help="Enhancement strength 0.0-1.0 (default 0.6)")
    p.add_argument("--no-saliency", action="store_true",
                   help="Disable saliency-based subject emphasis (part of smart enhancement)")
    p.add_argument("--pack", choices=["row", "page"], default="row",
                   help="Bit packing layout for --format h (default: row)")
    p.add_argument("--max-frames", type=int, default=None, help="Limit number of frames encoded")
    p.add_argument("--array-name", default="video", help="Base C identifier name for --format h")
    p.add_argument("--chunk-bytes", type=int, default=32000,
                   help="Max bytes per PROGMEM chunk array for --format h (default 32000)")
    p.add_argument("--preview-gif", default=None,
                   help="For --format h: also write a .gif preview of the converted frames")
    p.add_argument("--emit-ino", default=None,
                   help="For --format h: also write an example playback .ino sketch")
    p.add_argument("--gui", action="store_true", help="Force-launch the GUI (optionally pre-filling input)")
    return p


def params_from_args(args) -> Params:
    fmt = args.format
    output = args.output
    if fmt is None:
        if output:
            ext = os.path.splitext(output)[1].lower()
            if ext in (".h", ".hpp"):
                fmt = "h"
            elif ext == ".gif":
                fmt = "gif"
            else:
                fmt = "mp4"
        else:
            fmt = "mp4"
    if output is None:
        base = os.path.splitext(os.path.basename(args.input))[0] if args.input else "output"
        ext = {"mp4": ".mp4", "gif": ".gif", "h": ".h"}[fmt]
        output = f"{base}_{args.width}x{args.height}bw{ext}"

    return Params(
        input=args.input or "",
        output=output,
        format=fmt,
        width=args.width,
        height=args.height,
        fps=args.fps,
        dither=args.dither,
        invert=args.invert,
        threshold=args.threshold,
        enhance=not args.no_enhance,
        enhance_strength=args.enhance_strength,
        use_saliency=not args.no_saliency,
        pack=args.pack,
        max_frames=args.max_frames,
        array_name=args.array_name,
        chunk_bytes=args.chunk_bytes,
        preview_gif=args.preview_gif,
        emit_ino=args.emit_ino,
    )


def run_cli(args):
    if not args.input:
        sys.exit("error: input video required in CLI mode (or run with no args / --gui for the GUI)")
    if not os.path.isfile(args.input):
        sys.exit(f"Input file not found: {args.input}")
    params = params_from_args(args)
    print(f"Processing '{params.input}' -> '{params.output}' "
          f"({params.width}x{params.height}, format={params.format}, dither={params.dither})")

    def progress(done, total):
        if total:
            print(f"\r  frame {done}/{total}", end="", flush=True)
        else:
            print(f"\r  frame {done}", end="", flush=True)

    run_export(params, log=print, progress=progress)
    print()


# =====================================================================
# GUI
# =====================================================================

if _TK_AVAILABLE:

    class VidBWStudioApp(tk.Tk):
        PREVIEW_MAX_DIM = 480      # max pixel size (scaled) of the still preview
        ANIM_SECONDS = 20           # how many seconds of footage to cache for animated preview
        ANIM_MAX_FRAMES = 900       # hard cap on cached animation frames

        def __init__(self, initial_input=None):
            super().__init__()
            self.title("VidBW Studio - video to tiny B&W / Arduino .h")
            self.geometry("980x680")
            self.minsize(860, 600)

            self.video_path = None
            self.total_frames = 0
            self.src_fps = 25.0
            self._preview_photo = None  # keep a reference alive
            self._anim_frames = []      # list of ImageTk.PhotoImage for animated preview
            self._anim_job = None
            self._anim_running = False
            self._export_queue = queue.Queue()
            self._export_thread = None

            self._build_vars()
            self._build_ui()

            if initial_input:
                self._load_video(initial_input)

        # ---------------- variable setup ----------------

        def _build_vars(self):
            self.var_input = tk.StringVar()
            self.var_output = tk.StringVar()
            self.var_format = tk.StringVar(value="mp4")
            self.var_width = tk.IntVar(value=80)
            self.var_height = tk.IntVar(value=16)
            self.var_fps = tk.StringVar(value="")       # blank = source fps
            self.var_dither = tk.BooleanVar(value=False)
            self.var_invert = tk.BooleanVar(value=False)
            self.var_threshold = tk.IntVar(value=128)
            self.var_enhance = tk.BooleanVar(value=True)
            self.var_enhance_strength = tk.DoubleVar(value=0.6)
            self.var_use_saliency = tk.BooleanVar(value=True)
            self.var_pack = tk.StringVar(value="row")
            self.var_max_frames = tk.StringVar(value="")  # blank = no limit
            self.var_array_name = tk.StringVar(value="video")
            self.var_chunk_bytes = tk.IntVar(value=32000)
            self.var_preview_gif = tk.StringVar()
            self.var_emit_ino = tk.StringVar()
            self.var_frame_index = tk.IntVar(value=0)
            self.var_status = tk.StringVar(value="Load a video to begin.")
            self.var_estimate = tk.StringVar(value="")

        # ---------------- UI layout ----------------

        def _build_ui(self):
            root = ttk.Frame(self, padding=8)
            root.pack(fill="both", expand=True)
            root.columnconfigure(0, weight=0)
            root.columnconfigure(1, weight=1)
            root.rowconfigure(0, weight=1)

            left = ttk.Frame(root)
            left.grid(row=0, column=0, sticky="ns", padx=(0, 8))
            right = ttk.Frame(root)
            right.grid(row=0, column=1, sticky="nsew")
            right.rowconfigure(1, weight=1)
            right.columnconfigure(0, weight=1)

            self._build_left_panel(left)
            self._build_right_panel(right)

        def _build_left_panel(self, parent):
            row = 0

            # --- File I/O ---
            io_frame = ttk.LabelFrame(parent, text="Input / Output", padding=6)
            io_frame.grid(row=row, column=0, sticky="ew", pady=(0, 6))
            row += 1
            io_frame.columnconfigure(1, weight=1)

            ttk.Label(io_frame, text="Input video:").grid(row=0, column=0, sticky="w")
            ttk.Entry(io_frame, textvariable=self.var_input, width=26).grid(row=0, column=1, sticky="ew", padx=4)
            ttk.Button(io_frame, text="Browse...", command=self._browse_input).grid(row=0, column=2)

            ttk.Label(io_frame, text="Output file:").grid(row=1, column=0, sticky="w", pady=(4, 0))
            ttk.Entry(io_frame, textvariable=self.var_output, width=26).grid(row=1, column=1, sticky="ew", padx=4, pady=(4, 0))
            ttk.Button(io_frame, text="Browse...", command=self._browse_output).grid(row=1, column=2, pady=(4, 0))

            ttk.Label(io_frame, text="Format:").grid(row=2, column=0, sticky="w", pady=(4, 0))
            fmt_row = ttk.Frame(io_frame)
            fmt_row.grid(row=2, column=1, columnspan=2, sticky="w", pady=(4, 0))
            for val, label in (("mp4", "MP4 video"), ("gif", "GIF video"), ("h", "Arduino .h header")):
                ttk.Radiobutton(fmt_row, text=label, value=val, variable=self.var_format,
                                command=self._on_format_change).pack(side="left", padx=(0, 8))

            # --- Common processing params ---
            proc_frame = ttk.LabelFrame(parent, text="Processing", padding=6)
            proc_frame.grid(row=row, column=0, sticky="ew", pady=(0, 6))
            row += 1

            ttk.Label(proc_frame, text="Width:").grid(row=0, column=0, sticky="w")
            ttk.Spinbox(proc_frame, from_=1, to=1024, textvariable=self.var_width, width=6,
                        command=self._schedule_preview).grid(row=0, column=1, sticky="w")
            ttk.Label(proc_frame, text="Height:").grid(row=0, column=2, sticky="w", padx=(10, 0))
            ttk.Spinbox(proc_frame, from_=1, to=1024, textvariable=self.var_height, width=6,
                        command=self._schedule_preview).grid(row=0, column=3, sticky="w")

            ttk.Label(proc_frame, text="Target FPS (blank=source):").grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
            fps_combo = ttk.Combobox(proc_frame, textvariable=self.var_fps, width=8,
                                      values=["", "3", "5", "8", "10", "12", "15", "20", "24", "30"])
            fps_combo.grid(row=1, column=2, columnspan=2, sticky="w", pady=(4, 0))
            fps_combo.bind("<<ComboboxSelected>>", lambda e: self._schedule_preview())
            fps_combo.bind("<KeyRelease>", lambda e: self._schedule_preview())
            ttk.Label(proc_frame, text="Lowering FPS drops frames but keeps the same real-world\n"
                                        "duration -- it will NOT play in slow motion.",
                      foreground="#555", font=("TkDefaultFont", 8)).grid(
                row=6, column=0, columnspan=4, sticky="w", pady=(2, 0))

            ttk.Checkbutton(proc_frame, text="Floyd-Steinberg dither", variable=self.var_dither,
                            command=self._on_dither_toggle).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
            ttk.Checkbutton(proc_frame, text="Invert B/W", variable=self.var_invert,
                            command=self._schedule_preview).grid(row=2, column=2, columnspan=2, sticky="w", pady=(4, 0))

            ttk.Label(proc_frame, text="Threshold (0-255):").grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))
            self.threshold_scale = ttk.Scale(proc_frame, from_=0, to=255, orient="horizontal",
                                              variable=self.var_threshold, command=lambda e: self._schedule_preview())
            self.threshold_scale.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(0, 4))

            ttk.Separator(proc_frame, orient="horizontal").grid(row=8, column=0, columnspan=4, sticky="ew", pady=(6, 4))
            ttk.Checkbutton(proc_frame, text="Smart enhance for tiny resolution (recommended)",
                            variable=self.var_enhance, command=self._on_enhance_toggle).grid(
                row=9, column=0, columnspan=4, sticky="w")
            ttk.Label(proc_frame, text="Sharpens edges + boosts local contrast at full res\n"
                                        "before the extreme downscale, so the subject stays\n"
                                        "readable instead of turning to gray mush.",
                      foreground="#555", font=("TkDefaultFont", 8)).grid(
                row=10, column=0, columnspan=4, sticky="w")
            ttk.Label(proc_frame, text="Strength:").grid(row=11, column=0, sticky="w", pady=(2, 0))
            self.enhance_scale = ttk.Scale(proc_frame, from_=0.0, to=1.0, orient="horizontal",
                                            variable=self.var_enhance_strength,
                                            command=lambda e: self._schedule_preview())
            self.enhance_scale.grid(row=11, column=1, columnspan=3, sticky="ew", pady=(2, 0))
            self.saliency_check = ttk.Checkbutton(
                proc_frame, text="Emphasize main subject, flatten background (saliency)",
                variable=self.var_use_saliency, command=self._schedule_preview)
            self.saliency_check.grid(row=12, column=0, columnspan=4, sticky="w", pady=(2, 0))

            ttk.Label(proc_frame, text="Max frames (blank=all):").grid(row=5, column=0, columnspan=2, sticky="w")
            mf_entry = ttk.Entry(proc_frame, textvariable=self.var_max_frames, width=8)
            mf_entry.grid(row=5, column=2, columnspan=2, sticky="w")
            mf_entry.bind("<KeyRelease>", lambda e: self._schedule_preview())

            ttk.Label(proc_frame, textvariable=self.var_estimate, foreground="#0a5",
                      font=("TkDefaultFont", 8, "bold")).grid(
                row=7, column=0, columnspan=4, sticky="w", pady=(4, 0))

            for i in range(4):
                proc_frame.columnconfigure(i, weight=1)

            # --- .h specific params ---
            self.h_frame = ttk.LabelFrame(parent, text="Arduino .h options", padding=6)
            self.h_frame.grid(row=row, column=0, sticky="ew", pady=(0, 6))
            row += 1

            ttk.Label(self.h_frame, text="Pack layout:").grid(row=0, column=0, sticky="w")
            pack_row_f = ttk.Frame(self.h_frame)
            pack_row_f.grid(row=0, column=1, sticky="w")
            ttk.Radiobutton(pack_row_f, text="row", value="row", variable=self.var_pack,
                            command=self._schedule_preview).pack(side="left")
            ttk.Radiobutton(pack_row_f, text="page (SSD1306)", value="page", variable=self.var_pack,
                            command=self._schedule_preview).pack(side="left")

            ttk.Label(self.h_frame, text="Array name:").grid(row=1, column=0, sticky="w", pady=(4, 0))
            ttk.Entry(self.h_frame, textvariable=self.var_array_name, width=16).grid(row=1, column=1, sticky="w", pady=(4, 0))

            ttk.Label(self.h_frame, text="Chunk bytes:").grid(row=2, column=0, sticky="w", pady=(4, 0))
            ttk.Entry(self.h_frame, textvariable=self.var_chunk_bytes, width=10).grid(row=2, column=1, sticky="w", pady=(4, 0))

            ttk.Label(self.h_frame, text="Preview gif (optional):").grid(row=3, column=0, sticky="w", pady=(4, 0))
            pg_row = ttk.Frame(self.h_frame)
            pg_row.grid(row=3, column=1, sticky="ew", pady=(4, 0))
            ttk.Entry(pg_row, textvariable=self.var_preview_gif, width=14).pack(side="left", fill="x", expand=True)
            ttk.Button(pg_row, text="...", width=3, command=self._browse_preview_gif).pack(side="left")

            ttk.Label(self.h_frame, text="Emit .ino (optional):").grid(row=4, column=0, sticky="w", pady=(4, 0))
            ino_row = ttk.Frame(self.h_frame)
            ino_row.grid(row=4, column=1, sticky="ew", pady=(4, 0))
            ttk.Entry(ino_row, textvariable=self.var_emit_ino, width=14).pack(side="left", fill="x", expand=True)
            ttk.Button(ino_row, text="...", width=3, command=self._browse_emit_ino).pack(side="left")

            # --- Export ---
            export_frame = ttk.Frame(parent)
            export_frame.grid(row=row, column=0, sticky="ew", pady=(4, 0))
            row += 1
            self.export_btn = ttk.Button(export_frame, text="Export", command=self._on_export)
            self.export_btn.pack(fill="x")
            self.progress = ttk.Progressbar(export_frame, mode="determinate")
            self.progress.pack(fill="x", pady=(6, 0))

            self._on_format_change()
            self._on_dither_toggle()
            self._on_enhance_toggle()

        def _build_right_panel(self, parent):
            top = ttk.LabelFrame(parent, text="Still preview", padding=6)
            top.grid(row=0, column=0, sticky="ew")
            top.columnconfigure(0, weight=1)

            self.canvas = tk.Canvas(top, background="#202020", height=340, highlightthickness=1,
                                     highlightbackground="#555")
            self.canvas.grid(row=0, column=0, sticky="ew")

            slider_row = ttk.Frame(top)
            slider_row.grid(row=1, column=0, sticky="ew", pady=(6, 0))
            slider_row.columnconfigure(0, weight=1)
            self.frame_slider = ttk.Scale(slider_row, from_=0, to=0, orient="horizontal",
                                           variable=self.var_frame_index, command=lambda e: self._schedule_preview())
            self.frame_slider.grid(row=0, column=0, sticky="ew")
            self.frame_label = ttk.Label(slider_row, text="frame 0 / 0")
            self.frame_label.grid(row=0, column=1, padx=(6, 0))

            btn_row = ttk.Frame(top)
            btn_row.grid(row=2, column=0, sticky="w", pady=(6, 0))
            ttk.Button(btn_row, text="Refresh still preview", command=self._schedule_preview).pack(side="left")
            self.anim_btn = ttk.Button(btn_row, text=f"Play {self.ANIM_SECONDS}s animated preview",
                                        command=self._toggle_animated_preview)
            self.anim_btn.pack(side="left", padx=(6, 0))

            log_frame = ttk.LabelFrame(parent, text="Log", padding=6)
            log_frame.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
            log_frame.columnconfigure(0, weight=1)
            log_frame.rowconfigure(0, weight=1)
            self.log_text = tk.Text(log_frame, height=10, wrap="word", state="disabled")
            self.log_text.grid(row=0, column=0, sticky="nsew")
            log_scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
            log_scroll.grid(row=0, column=1, sticky="ns")
            self.log_text["yscrollcommand"] = log_scroll.set

            status_bar = ttk.Label(parent, textvariable=self.var_status, relief="sunken", anchor="w")
            status_bar.grid(row=2, column=0, sticky="ew", pady=(6, 0))

        # ---------------- helpers ----------------

        def _log(self, msg):
            self.log_text["state"] = "normal"
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
            self.log_text["state"] = "disabled"

        def _on_format_change(self):
            is_h = self.var_format.get() == "h"
            state = "normal" if is_h else "disabled"
            for child in self.h_frame.winfo_children():
                self._set_widget_state(child, state)
            self._sync_output_ext()
            self._schedule_preview()

        def _set_widget_state(self, widget, state):
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass
            for child in widget.winfo_children():
                self._set_widget_state(child, state)

        def _sync_output_ext(self):
            out = self.var_output.get()
            if not out:
                return
            base, _ext = os.path.splitext(out)
            ext = {"mp4": ".mp4", "gif": ".gif", "h": ".h"}[self.var_format.get()]
            self.var_output.set(base + ext)

        def _on_dither_toggle(self):
            state = "disabled" if self.var_dither.get() else "normal"
            self.threshold_scale.configure(state=state)
            self._schedule_preview()

        def _on_enhance_toggle(self):
            state = "normal" if self.var_enhance.get() else "disabled"
            self.enhance_scale.configure(state=state)
            self.saliency_check.configure(state=state)
            self._schedule_preview()

        def _browse_input(self):
            path = filedialog.askopenfilename(
                title="Select input video",
                filetypes=[("Video files", "*.mp4 *.mov *.avi *.mkv *.webm"), ("All files", "*.*")])
            if path:
                self._load_video(path)

        def _browse_output(self):
            ext = {"mp4": ".mp4", "gif": ".gif", "h": ".h"}[self.var_format.get()]
            path = filedialog.asksaveasfilename(title="Save output as", defaultextension=ext)
            if path:
                self.var_output.set(path)

        def _browse_preview_gif(self):
            path = filedialog.asksaveasfilename(title="Preview gif path", defaultextension=".gif")
            if path:
                self.var_preview_gif.set(path)

        def _browse_emit_ino(self):
            path = filedialog.asksaveasfilename(title="Example .ino path", defaultextension=".ino")
            if path:
                self.var_emit_ino.set(path)

        def _load_video(self, path):
            if not os.path.isfile(path):
                messagebox.showerror("Error", f"File not found:\n{path}")
                return
            try:
                total, fps = get_video_info(path)
            except Exception as e:
                messagebox.showerror("Error", f"Could not open video:\n{e}")
                return
            self.video_path = path
            self.total_frames = total
            self.src_fps = fps
            self.var_input.set(path)
            base = os.path.splitext(os.path.basename(path))[0]
            ext = {"mp4": ".mp4", "gif": ".gif", "h": ".h"}[self.var_format.get()]
            self.var_output.set(f"{base}_{self.var_width.get()}x{self.var_height.get()}bw{ext}")
            self.frame_slider.configure(to=max(total - 1, 0))
            self.var_frame_index.set(0)
            self.var_status.set(f"Loaded {os.path.basename(path)}: {total} frames @ {fps:.2f} fps")
            self._log(f"Loaded '{path}' ({total} frames, {fps:.2f} fps)")
            self._schedule_preview()

        def _collect_params(self, for_export=False):
            fmt = self.var_format.get()
            fps_str = self.var_fps.get().strip()
            fps = float(fps_str) if fps_str else None
            mf_str = self.var_max_frames.get().strip()
            max_frames = int(mf_str) if mf_str else None

            output = self.var_output.get().strip()
            if for_export and not output:
                raise ValueError("Please choose an output file path.")

            return Params(
                input=self.video_path or "",
                output=output,
                format=fmt,
                width=max(1, self.var_width.get()),
                height=max(1, self.var_height.get()),
                fps=fps,
                dither=self.var_dither.get(),
                invert=self.var_invert.get(),
                threshold=int(self.var_threshold.get()),
                enhance=self.var_enhance.get(),
                enhance_strength=float(self.var_enhance_strength.get()),
                use_saliency=self.var_use_saliency.get(),
                pack=self.var_pack.get(),
                max_frames=max_frames,
                array_name=self.var_array_name.get().strip() or "video",
                chunk_bytes=int(self.var_chunk_bytes.get()),
                preview_gif=self.var_preview_gif.get().strip() or None,
                emit_ino=self.var_emit_ino.get().strip() or None,
            )

        # ---------------- still preview ----------------

        def _schedule_preview(self, *_):
            # cheap debounce: cancel any pending call and schedule a fresh one
            if hasattr(self, "_preview_after_id") and self._preview_after_id:
                try:
                    self.after_cancel(self._preview_after_id)
                except Exception:
                    pass
            self._preview_after_id = self.after(120, self._update_still_preview)

        def _update_still_preview(self):
            if not self.video_path:
                return
            idx = int(self.var_frame_index.get())
            self.frame_label.configure(text=f"frame {idx} / {max(self.total_frames - 1, 0)}")
            frame = get_frame_at(self.video_path, idx)
            if frame is None:
                return
            try:
                params = self._collect_params(for_export=False)
            except Exception:
                return
            bits = process_frame_bits(frame, params.width, params.height,
                                       params.threshold, params.dither, params.invert,
                                       enhance=params.enhance, enhance_strength=params.enhance_strength,
                                       use_saliency=params.use_saliency)
            img = bits_to_pil(bits)
            self._draw_preview_image(img)
            self._update_estimate(params)

        def _update_estimate(self, params):
            if not self.total_frames or not self.src_fps:
                self.var_estimate.set("")
                return
            target_fps = params.fps or self.src_fps
            duration_s = self.total_frames / self.src_fps
            est_frames = int(duration_s * target_fps)
            if params.max_frames:
                est_frames = min(est_frames, params.max_frames)
            est_frames = max(est_frames, 1)

            if params.format == "h":
                if params.pack == "row":
                    frame_bytes = ((params.width + 7) // 8) * params.height
                else:
                    frame_bytes = params.width * ((params.height + 7) // 8)
                total_bytes = frame_bytes * est_frames
                self.var_estimate.set(
                    f"Estimated: ~{est_frames} frames, {frame_bytes} bytes/frame, "
                    f"~{total_bytes/1024:.1f} KB total (duration ~{duration_s:.1f}s @ {target_fps:g} fps)")
            else:
                self.var_estimate.set(
                    f"Estimated: ~{est_frames} frames, duration ~{duration_s:.1f}s @ {target_fps:g} fps "
                    f"(same length as source, not slowed down)")

        def _draw_preview_image(self, img):
            scale = max(1, self.PREVIEW_MAX_DIM // max(img.width, img.height))
            big = img.resize((img.width * scale, img.height * scale), Image.NEAREST)
            photo = ImageTk.PhotoImage(big)
            self._preview_photo = photo  # keep alive
            self.canvas.delete("all")
            cw = self.canvas.winfo_width() or big.width
            ch = self.canvas.winfo_height() or big.height
            self.canvas.create_image(cw // 2, ch // 2, image=photo, anchor="center")

        # ---------------- animated preview ----------------

        def _toggle_animated_preview(self):
            if self._anim_running:
                self._stop_animation()
                return
            if not self.video_path:
                messagebox.showinfo("No video", "Load a video first.")
                return
            self.anim_btn.configure(state="disabled", text="Preparing preview...")
            threading.Thread(target=self._prepare_animation_frames, daemon=True).start()

        def _prepare_animation_frames(self):
            try:
                params = self._collect_params(for_export=False)
                target_fps = params.fps or self.src_fps
                max_frames = min(self.ANIM_MAX_FRAMES, max(1, int(target_fps * self.ANIM_SECONDS)))
                pil_frames = []
                for frame in iter_sampled_frames(self.video_path, target_fps, max_frames):
                    bits = process_frame_bits(frame, params.width, params.height,
                                               params.threshold, params.dither, params.invert,
                                       enhance=params.enhance, enhance_strength=params.enhance_strength,
                                       use_saliency=params.use_saliency)
                    pil_frames.append(bits_to_pil(bits))
                interval_ms = max(20, int(1000 / target_fps))
                self.after(0, lambda: self._start_animation(pil_frames, interval_ms))
            except Exception as e:
                self.after(0, lambda: self._animation_prep_failed(e))

        def _animation_prep_failed(self, e):
            self.anim_btn.configure(state="normal", text=f"Play {self.ANIM_SECONDS}s animated preview")
            messagebox.showerror("Preview error", str(e))

        def _start_animation(self, pil_frames, interval_ms):
            if not pil_frames:
                self.anim_btn.configure(state="normal", text=f"Play {self.ANIM_SECONDS}s animated preview")
                return
            scale = max(1, self.PREVIEW_MAX_DIM // max(pil_frames[0].width, pil_frames[0].height))
            self._anim_frames = [
                ImageTk.PhotoImage(f.resize((f.width * scale, f.height * scale), Image.NEAREST))
                for f in pil_frames
            ]
            self._anim_running = True
            self._anim_idx = 0
            self._anim_interval = interval_ms
            self.anim_btn.configure(state="normal", text="Stop animated preview")
            self._animate_step()

        def _animate_step(self):
            if not self._anim_running or not self._anim_frames:
                return
            photo = self._anim_frames[self._anim_idx % len(self._anim_frames)]
            self.canvas.delete("all")
            cw = self.canvas.winfo_width() or photo.width()
            ch = self.canvas.winfo_height() or photo.height()
            self.canvas.create_image(cw // 2, ch // 2, image=photo, anchor="center")
            self._anim_idx += 1
            self._anim_job = self.after(self._anim_interval, self._animate_step)

        def _stop_animation(self):
            self._anim_running = False
            if self._anim_job:
                try:
                    self.after_cancel(self._anim_job)
                except Exception:
                    pass
                self._anim_job = None
            self.anim_btn.configure(text=f"Play {self.ANIM_SECONDS}s animated preview")
            self._schedule_preview()

        # ---------------- export ----------------

        def _on_export(self):
            if not self.video_path:
                messagebox.showinfo("No video", "Load a video first.")
                return
            try:
                params = self._collect_params(for_export=True)
            except Exception as e:
                messagebox.showerror("Invalid parameters", str(e))
                return

            self.export_btn.configure(state="disabled", text="Exporting...")
            self.progress.configure(mode="determinate", maximum=100, value=0)
            self._log(f"Starting export -> {params.output} (format={params.format})")

            def progress_cb(done, total):
                self._export_queue.put(("progress", done, total))

            def log_cb(msg):
                self._export_queue.put(("log", msg))

            def worker():
                try:
                    run_export(params, log=log_cb, progress=progress_cb)
                    self._export_queue.put(("done", None, None))
                except Exception as e:
                    self._export_queue.put(("error", str(e), None))

            self._export_thread = threading.Thread(target=worker, daemon=True)
            self._export_thread.start()
            self.after(100, self._poll_export_queue)

        def _poll_export_queue(self):
            drained_any = False
            try:
                while True:
                    kind, a, b = self._export_queue.get_nowait()
                    drained_any = True
                    if kind == "progress":
                        done, total = a, b
                        if total:
                            self.progress.configure(mode="determinate", maximum=total, value=done)
                        else:
                            self.progress.configure(mode="indeterminate")
                    elif kind == "log":
                        self._log(a)
                    elif kind == "done":
                        self._export_finished(success=True)
                        return
                    elif kind == "error":
                        self._export_finished(success=False, error=a)
                        return
            except queue.Empty:
                pass
            self.after(100, self._poll_export_queue)

        def _export_finished(self, success, error=None):
            self.export_btn.configure(state="normal", text="Export")
            self.progress.configure(value=0)
            if success:
                self.var_status.set("Export complete.")
                self._log("Export complete.")
                messagebox.showinfo("Done", "Export finished successfully.")
            else:
                self.var_status.set("Export failed.")
                self._log(f"ERROR: {error}")
                messagebox.showerror("Export failed", error or "Unknown error")


def launch_gui(initial_input=None):
    if not _TK_AVAILABLE:
        sys.exit("Tkinter is not available in this Python environment; cannot launch the GUI. "
                 "Use the CLI flags instead (run with -h for help).")
    app = VidBWStudioApp(initial_input=initial_input)
    app.mainloop()


# =====================================================================
# Entry point
# =====================================================================

def main():
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.gui or args.input is None:
        launch_gui(initial_input=args.input)
    else:
        run_cli(args)


if __name__ == "__main__":
    main()
