#include <Arduino.h>
#include <Wire.h>
#include "soc/gpio_reg.h"  // REG_WRITE / GPIO_OUT_W1TS_REG / GPIO_OUT_W1TC_REG - stable across arduino-esp32 core versions
#include "font5x8.h"       // 5x8-style bitmap font (font10x16 array, FONT_FIRST/FONT_LAST)

// ESP32-C3 Super Mini pinout
// Data bus is GPIO0-7 (contiguous), so writing a byte is a single register write.
#define LCD_RS  10
#define LCD_E   9
#define BACKLIGHT 8
#define FB_WIDTH  80
#define FB_HEIGHT 16

// GPIO0-7 -> bits 0-7 in the GPIO register, so the mask is just 0xFF
#define LCD_DATA_MASK 0xFF

// ---- I2C slave config -------------------------------------------------
// NOTE: on some ESP32-C3 Super Mini variants, GPIO20/21 are also the
// hardware UART0 pins. If your board doesn't use native USB-CDC for
// Serial (check "USB CDC On Boot" in board settings), Serial and I2C
// will collide on the same wires. Verify before wiring this up.
#define I2C_SDA        21
#define I2C_SCL        20
#define I2C_SLAVE_ADDR 0x08
#define I2C_FREQ       100000

// ---- Graphics command types --------------------------------------------
// Defined here, immediately after the includes/defines and before any
// function bodies, on purpose: the Arduino IDE auto-generates forward
// declarations for every function in the sketch and inserts them near
// the top of the file. If a struct like GfxCmd is only defined further
// down (e.g. next to the functions that use it), the auto-inserted
// prototype for gfxQueuePush(const GfxCmd&) ends up referencing GfxCmd
// before it's been declared, and the build fails with
// "'GfxCmd' does not name a type". Keeping custom types up here avoids
// that entirely.

enum GraphyteOp : uint8_t {
  OP_CLEAR                 = 0x00,
  OP_DRAW_LINE              = 0x01,
  OP_INVERT_LINE            = 0x02,
  OP_DRAW_RECT              = 0x03,
  OP_INVERT_RECT            = 0x04,
  OP_DRAW_RECT_FILLED       = 0x05,
  OP_INVERT_RECT_FILLED     = 0x06,
  OP_DRAW_CIRCLE            = 0x07,
  OP_INVERT_CIRCLE          = 0x08,
  OP_DRAW_CIRCLE_FILLED     = 0x09,
  OP_INVERT_CIRCLE_FILLED   = 0x0A,
  OP_DRAW_TEXT              = 0x0B,
};

#define GFX_QUEUE_LEN   8
#define GFX_TEXT_MAXLEN 16
#define GFX_RX_MAXLEN   64

typedef struct {
  uint8_t op;
  int16_t p[4];
  char text[GFX_TEXT_MAXLEN + 1];
} GfxCmd;

inline void lcdRS(bool state) {
  if (state)
    REG_WRITE(GPIO_OUT_W1TS_REG, (1UL << LCD_RS));
  else
    REG_WRITE(GPIO_OUT_W1TC_REG, (1UL << LCD_RS));
}

inline void lcdPulseEnable() {
  REG_WRITE(GPIO_OUT_W1TS_REG, (1UL << LCD_E));
  delayMicroseconds(19);
  REG_WRITE(GPIO_OUT_W1TC_REG, (1UL << LCD_E));
  delayMicroseconds(19);
}

inline void lcdWrite8(uint8_t value) {
  // clear data bus (GPIO0-7), then set exactly the bits we want.
  // Since LCD_D pins map 1:1 to bits 0-7, "value" IS the output mask.
  REG_WRITE(GPIO_OUT_W1TC_REG, LCD_DATA_MASK);
  REG_WRITE(GPIO_OUT_W1TS_REG, (uint32_t)value);
  lcdPulseEnable();
}

inline void lcdCommand(uint8_t cmd) {
  lcdRS(0);
  lcdWrite8(cmd);
}

inline void lcdData(uint8_t data) {
  lcdRS(1);
  lcdWrite8(data);
}

void lcdSetCursor(uint8_t col, uint8_t row) {
  lcdCommand(0x80 | (row ? (0x40 + col) : col));
}

void lcdInit() {
  pinMode(BACKLIGHT, OUTPUT);
  pinMode(LCD_RS, OUTPUT);
  pinMode(LCD_E, OUTPUT);
  digitalWrite(BACKLIGHT, HIGH);

  for (int i = 0; i < 8; i++) {
    pinMode(i, OUTPUT); // GPIO0-7
  }

  delay(50);

  lcdCommand(0x38);
  lcdCommand(0x0C);
  lcdCommand(0x01);
  delay(5);
  lcdCommand(0x06);
}

// load ze memory

void lcdLoadSet(uint8_t set[8][8]) {
  lcdCommand(0x40);

  for (int c = 0; c < 8; c++)
    for (int r = 0; r < 8; r++)
      lcdData(set[c][r]);

  lcdCommand(0x80);
}

// single buffer - no dual bank / mutex needed on a single core,
// since nothing else can touch it mid-frame.

typedef struct {
  uint8_t A[8][8];
  uint8_t B[8][8];
  uint8_t C[8][8];
  uint8_t D[8][8];
} CharsetBank;

CharsetBank bank;

// framebuffer

uint8_t framebuffer[FB_HEIGHT][FB_WIDTH];

inline void clearFramebuffer() {
  memset(framebuffer, 0, sizeof(framebuffer));
}

// tile extraction

void extractTileToBank(int tileX, int tileY, uint8_t dest[8]) {

  int startX = tileX * 5;
  int startY = tileY * 8;

  for (int row = 0; row < 8; row++) {

    uint8_t packed = 0;

    for (int col = 0; col < 5; col++) {
      if (framebuffer[startY + row][startX + col]) {
        packed |= (1 << (4 - col));
      }
    }

    dest[row] = packed;
  }
}

// =====================================================================
// GRAPHYTE DRAWING PRIMITIVES
// Direct port of the LCDSimulator draw_line / draw_rect / draw_circle
// methods from the Python designer tool, so a .txt command export from
// the simulator and a live I2C command stream produce identical pixels.
// =====================================================================

inline void gfxSetPixel(int x, int y, bool invert) {
  if (x < 0 || x >= FB_WIDTH || y < 0 || y >= FB_HEIGHT) return;
  framebuffer[y][x] = invert ? (framebuffer[y][x] ^ 1) : 1;
}

// matches graphyte.draw_line(x0,y0,x1,y1) / graphyte.invert_line(...)
void graphyteLine(int x0, int y0, int x1, int y1, bool invert) {
  int dx = abs(x1 - x0);
  int sx = x0 < x1 ? 1 : -1;
  int dy = -abs(y1 - y0);
  int sy = y0 < y1 ? 1 : -1;
  int err = dx + dy;

  while (true) {
    gfxSetPixel(x0, y0, invert);
    if (x0 == x1 && y0 == y1) break;
    int e2 = 2 * err;
    if (e2 >= dy) { err += dy; x0 += sx; }
    if (e2 <= dx) { err += dx; y0 += sy; }
  }
}

// matches graphyte.draw_rect(x,y,w,h) / draw_rect_filled / invert_rect(_filled)
void graphyteRect(int x, int y, int w, int h, bool filled, bool invert) {
  int x0 = x, y0 = y, x1 = x + w - 1, y1 = y + h - 1;

  if (filled) {
    for (int yy = y0; yy <= y1; yy++)
      for (int xx = x0; xx <= x1; xx++)
        gfxSetPixel(xx, yy, invert);
  } else {
    for (int xx = x0; xx <= x1; xx++) {
      gfxSetPixel(xx, y0, invert);
      gfxSetPixel(xx, y1, invert);
    }
    for (int yy = y0; yy <= y1; yy++) {
      gfxSetPixel(x0, yy, invert);
      gfxSetPixel(x1, yy, invert);
    }
  }
}

// matches graphyte.draw_circle(xc,yc,r) / draw_circle_filled / invert_circle(_filled)
// same midpoint algorithm as the simulator, so radii round identically
void graphyteCircle(int xc, int yc, int r, bool filled, bool invert) {
  if (filled) {
    long r2 = (long)r * r;
    for (int y = yc - r; y <= yc + r; y++)
      for (int x = xc - r; x <= xc + r; x++)
        if ((long)(x - xc) * (x - xc) + (long)(y - yc) * (y - yc) <= r2)
          gfxSetPixel(x, y, invert);
  } else {
    int x = 0, y = r, d = 1 - r;
    while (x <= y) {
      gfxSetPixel(xc + x, yc + y, invert);
      gfxSetPixel(xc - x, yc + y, invert);
      gfxSetPixel(xc + x, yc - y, invert);
      gfxSetPixel(xc - x, yc - y, invert);
      gfxSetPixel(xc + y, yc + x, invert);
      gfxSetPixel(xc - y, yc + x, invert);
      gfxSetPixel(xc + y, yc - x, invert);
      gfxSetPixel(xc - y, yc - x, invert);
      if (d < 0) {
        d += 2 * x + 3;
      } else {
        d += 2 * (x - y) + 5;
        y--;
      }
      x++;
    }
  }
}

// matches graphyte.draw_text(x,y,"text"). Reads font5x8.h's font10x16
// array: 16 bytes/glyph (2 bytes per row x 8 rows), glyph data packed
// into the top 5 bits of each row's first byte (second byte unused).
void graphyteText(int x0, int y0, const char* text) {
  for (int idx = 0; text[idx] != '\0'; idx++) {
    uint8_t code = (uint8_t)text[idx];
    if (code < FONT_FIRST || code > FONT_LAST) continue;

    const uint8_t* glyph = font10x16 + (code - FONT_FIRST) * 16;

    for (int row = 0; row < FONT_HEIGHT; row++) {
      uint8_t rowByte = pgm_read_byte(glyph + row * 2); // top 5 bits used
      for (int col = 0; col < FONT_WIDTH; col++) {
        if (rowByte & (1 << (7 - col))) {
          gfxSetPixel(x0 + idx * 6 + col, y0 + row, false);
        }
      }
    }
  }
}

// =====================================================================
// I2C SLAVE - GRAPHICS COMMAND PROTOCOL
// =====================================================================
//
// Packet layout (each I2C write from the master is one packet):
//   byte 0       : opcode
//   byte 1..N    : parameters, opcode-dependent (see below)
//
//   OP_CLEAR              0x00   (no params)
//   OP_DRAW_LINE          0x01   x0,y0,x1,y1            (4 bytes)
//   OP_INVERT_LINE        0x02   x0,y0,x1,y1            (4 bytes)
//   OP_DRAW_RECT          0x03   x,y,w,h                (4 bytes)
//   OP_INVERT_RECT        0x04   x,y,w,h                (4 bytes)
//   OP_DRAW_RECT_FILLED   0x05   x,y,w,h                (4 bytes)
//   OP_INVERT_RECT_FILLED 0x06   x,y,w,h                (4 bytes)
//   OP_DRAW_CIRCLE        0x07   xc,yc,r                (3 bytes)
//   OP_INVERT_CIRCLE      0x08   xc,yc,r                (3 bytes)
//   OP_DRAW_CIRCLE_FILLED 0x09   xc,yc,r                (3 bytes)
//   OP_INVERT_CIRCLE_FILLED 0x0A xc,yc,r                (3 bytes)
//   OP_DRAW_TEXT          0x0B   x,y,ascii-bytes...     (2 + len bytes)
//
// All coordinate bytes are uint8_t (0-255); the 80x16 canvas only uses
// 0-79 / 0-15 but nothing stops a command from drawing partly off-screen
// (gfxSetPixel bounds-checks and silently clips it, same as the sim).
//
// The framebuffer is accumulative, same as the simulator - drawing
// commands layer on top of each other until an OP_CLEAR comes in.
//
// Work is kept OUT of the I2C callback itself: onReceive just copies
// bytes into a small ring buffer. Actual pixel math happens in loop(),
// so a filled-circle command doesn't stall the bus mid-transaction.
//
// (GraphyteOp enum and GfxCmd struct are defined near the top of the
// file - see the comment there for why.)

GfxCmd gfxQueue[GFX_QUEUE_LEN];
volatile uint8_t gfxQueueHead = 0;
volatile uint8_t gfxQueueTail = 0;

bool gfxQueuePush(const GfxCmd &cmd) {
  uint8_t next = (gfxQueueHead + 1) % GFX_QUEUE_LEN;
  if (next == gfxQueueTail) return false; // queue full, drop the command
  gfxQueue[gfxQueueHead] = cmd;
  gfxQueueHead = next;
  return true;
}

bool gfxQueuePop(GfxCmd &out) {
  if (gfxQueueTail == gfxQueueHead) return false; // empty
  out = gfxQueue[gfxQueueTail];
  gfxQueueTail = (gfxQueueTail + 1) % GFX_QUEUE_LEN;
  return true;
}

// Wire.onReceive callback - keep this fast, no drawing here.
void i2cReceiveEvent(int numBytes) {
  if (numBytes <= 0) return;

  uint8_t buf[GFX_RX_MAXLEN];
  int n = 0;
  while (Wire.available() && n < (int)sizeof(buf)) {
    buf[n++] = (uint8_t)Wire.read();
  }
  while (Wire.available()) Wire.read(); // discard anything past our buffer

  if (n < 1) return;

  GfxCmd cmd;
  memset(&cmd, 0, sizeof(cmd));
  cmd.op = buf[0];

  switch (cmd.op) {
    case OP_CLEAR:
      break;

    case OP_DRAW_LINE:
    case OP_INVERT_LINE:
    case OP_DRAW_RECT:
    case OP_INVERT_RECT:
    case OP_DRAW_RECT_FILLED:
    case OP_INVERT_RECT_FILLED:
      if (n < 5) return;
      cmd.p[0] = buf[1]; cmd.p[1] = buf[2]; cmd.p[2] = buf[3]; cmd.p[3] = buf[4];
      break;

    case OP_DRAW_CIRCLE:
    case OP_INVERT_CIRCLE:
    case OP_DRAW_CIRCLE_FILLED:
    case OP_INVERT_CIRCLE_FILLED:
      if (n < 4) return;
      cmd.p[0] = buf[1]; cmd.p[1] = buf[2]; cmd.p[2] = buf[3];
      break;

    case OP_DRAW_TEXT: {
      if (n < 3) return;
      cmd.p[0] = buf[1]; // x
      cmd.p[1] = buf[2]; // y
      int len = n - 3;
      if (len > GFX_TEXT_MAXLEN) len = GFX_TEXT_MAXLEN;
      memcpy(cmd.text, buf + 3, len);
      cmd.text[len] = '\0';
      break;
    }

    default:
      return; // unknown opcode, ignore silently
  }

  gfxQueuePush(cmd);
}

// drains the queue and actually draws - called once per loop()
void processGraphicsQueue() {
  GfxCmd cmd;
  while (gfxQueuePop(cmd)) {
    switch (cmd.op) {
      case OP_CLEAR:
        clearFramebuffer();
        break;
      case OP_DRAW_LINE:
        graphyteLine(cmd.p[0], cmd.p[1], cmd.p[2], cmd.p[3], false);
        break;
      case OP_INVERT_LINE:
        graphyteLine(cmd.p[0], cmd.p[1], cmd.p[2], cmd.p[3], true);
        break;
      case OP_DRAW_RECT:
        graphyteRect(cmd.p[0], cmd.p[1], cmd.p[2], cmd.p[3], false, false);
        break;
      case OP_INVERT_RECT:
        graphyteRect(cmd.p[0], cmd.p[1], cmd.p[2], cmd.p[3], false, true);
        break;
      case OP_DRAW_RECT_FILLED:
        graphyteRect(cmd.p[0], cmd.p[1], cmd.p[2], cmd.p[3], true, false);
        break;
      case OP_INVERT_RECT_FILLED:
        graphyteRect(cmd.p[0], cmd.p[1], cmd.p[2], cmd.p[3], true, true);
        break;
      case OP_DRAW_CIRCLE:
        graphyteCircle(cmd.p[0], cmd.p[1], cmd.p[2], false, false);
        break;
      case OP_INVERT_CIRCLE:
        graphyteCircle(cmd.p[0], cmd.p[1], cmd.p[2], false, true);
        break;
      case OP_DRAW_CIRCLE_FILLED:
        graphyteCircle(cmd.p[0], cmd.p[1], cmd.p[2], true, false);
        break;
      case OP_INVERT_CIRCLE_FILLED:
        graphyteCircle(cmd.p[0], cmd.p[1], cmd.p[2], true, true);
        break;
      case OP_DRAW_TEXT:
        graphyteText(cmd.p[0], cmd.p[1], cmd.text);
        break;
    }
  }
}

// build one frame's tile bank from whatever is currently in the
// framebuffer (populated by I2C graphics commands)

void renderFrame() {
  for (int tileY = 0; tileY < 2; tileY++) {
    for (int tileX = 0; tileX < 16; tileX++) {

      uint8_t tempTile[8];
      extractTileToBank(tileX, tileY, tempTile);

      if (tileY == 0) {
        if (tileX % 2 == 0)
          memcpy(bank.A[tileX/2], tempTile, 8);
        else
          memcpy(bank.B[tileX/2], tempTile, 8);
      }
      else {
        if (tileX % 2 == 0)
          memcpy(bank.C[tileX/2], tempTile, 8);
        else
          memcpy(bank.D[tileX/2], tempTile, 8);
      }
    }
  }
}

// push the current tile bank to the LCD (cycles CGRAM through
// all 4 banks - the LCD only has 8 custom-char slots, so this
// multiplexing relies on refresh speed / persistence of vision)

void drawFrame() {

  for (int x = 0; x < 4; x++) {

    switch (x) {
      case 0:
        lcdLoadSet(bank.A);
        lcdSetCursor(0, 0);
        break;

      case 1:
        lcdLoadSet(bank.D);
        lcdSetCursor(1, 1);
        break;

      case 2:
        lcdLoadSet(bank.B);
        lcdSetCursor(1, 0);
        break;

      case 3:
        lcdLoadSet(bank.C);
        lcdSetCursor(0, 1);
        break;
    }

    for (int i = 0; i < 8; i++) {
      lcdData(i);
      lcdData(' ');
    }

    for (int i = 0; i < 80; i++) {
      lcdData(' ');
    }
  }
}

void setup() {
  Serial.begin(115200);

  lcdInit();

  Wire.begin((uint8_t)I2C_SLAVE_ADDR, I2C_SDA, I2C_SCL, I2C_FREQ);
  Wire.onReceive(i2cReceiveEvent);

  Serial.print("I2C slave listening on addr 0x");
  Serial.println(I2C_SLAVE_ADDR, HEX);
}

void loop() {
  processGraphicsQueue();

  // single core: build the next frame, then immediately push it
  renderFrame();
  drawFrame();
}
