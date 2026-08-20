#include <Arduino.h>
#include "telegrama.h"
#include "pioneerevladi_80x16bw.h"   // packed 80x16 video: video_get_frame(), VIDEO_* constants

// ESP32-C3 Super Mini pinout
// Data bus is GPIO0-7 (contiguous), so writing a byte is a single register write.
#define LCD_RS  10
#define LCD_E   9
#define BACKLIGHT 8
// GPIO0-7 -> bits 0-7 in the GPIO register, so the mask is just 0xFF
#define LCD_DATA_MASK 0xFF

inline void lcdRS(bool state) {
  if (state)
    GPIO.out_w1ts.val = (1UL << LCD_RS);
  else
    GPIO.out_w1tc.val = (1UL << LCD_RS);
}

inline void lcdPulseEnable() {
  GPIO.out_w1ts.val = (1UL << LCD_E);
  delayMicroseconds(20);
  GPIO.out_w1tc.val = (1UL << LCD_E);
  delayMicroseconds(20);
}

inline void lcdWrite8(uint8_t value) {
  // clear data bus (GPIO0-7), then set exactly the bits we want.
  // Since LCD_D pins map 1:1 to bits 0-7, "value" IS the output mask.
  GPIO.out_w1tc.val = LCD_DATA_MASK;
  GPIO.out_w1ts.val = value;
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
  pinMode(BACKLIGHT,OUTPUT);
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

// single buffer, no swapping needed on single core

typedef struct {
  uint8_t A[8][8];
  uint8_t B[8][8];
  uint8_t C[8][8];
  uint8_t D[8][8];
} CharsetBank;

CharsetBank bank;

#define FB_WIDTH  80
#define FB_HEIGHT 16

uint8_t framebuffer[FB_HEIGHT][FB_WIDTH];

inline void clearFramebuffer() {
  memset(framebuffer, 0, sizeof(framebuffer));
}

inline void drawPixel(int x, int y) {
  if (x < 0 || x >= FB_WIDTH) return;
  if (y < 0 || y >= FB_HEIGHT) return;
  framebuffer[y][x] = 1;
}

// line draw
void drawLine(int x0, int y0, int x1, int y1) {
  int dx = abs(x1 - x0);
  int sx = x0 < x1 ? 1 : -1;
  int dy = -abs(y1 - y0);
  int sy = y0 < y1 ? 1 : -1;
  int err = dx + dy;

  while (true) {
    drawPixel(x0, y0);
    if (x0 == x1 && y0 == y1) break;
    int e2 = 2 * err;
    if (e2 >= dy) { err += dy; x0 += sx; }
    if (e2 <= dx) { err += dx; y0 += sy; }
  }
}

// circle draw using midpoint circle algorithm
void drawCircle(int centerX, int centerY, int radius) {
  int x = radius;
  int y = 0;
  int decisionParameter = 3 - 2 * radius;

  while (x >= y) {
    drawPixel(centerX + x, centerY + y);
    drawPixel(centerX - x, centerY + y);
    drawPixel(centerX + x, centerY - y);
    drawPixel(centerX - x, centerY - y);
    drawPixel(centerX + y, centerY + x);
    drawPixel(centerX - y, centerY + x);
    drawPixel(centerX + y, centerY - x);
    drawPixel(centerX - y, centerY - x);

    if (decisionParameter < 0) {
      decisionParameter = decisionParameter + 4 * y + 6;
    } else {
      decisionParameter = decisionParameter + 4 * (y - x) + 10;
      x--;
    }
    y++;
  }
}

// filled circle draw
void drawFilledCircle(int centerX, int centerY, int radius) {
  for (int y = -radius; y <= radius; y++) {
    for (int x = -radius; x <= radius; x++) {
      if (x * x + y * y <= radius * radius) {
        drawPixel(centerX + x, centerY + y);
      }
    }
  }
}

// box draw (rectangle with outline)
void drawBox(int x1, int y1, int x2, int y2) {
  drawLine(x1, y1, x2, y1);  // top line
  drawLine(x2, y1, x2, y2);  // right line
  drawLine(x2, y2, x1, y2);  // bottom line
  drawLine(x1, y2, x1, y1);  // left line
}

// filled box draw
void drawFilledBox(int x1, int y1, int x2, int y2) {
  int minX = x1 < x2 ? x1 : x2;
  int maxX = x1 > x2 ? x1 : x2;
  int minY = y1 < y2 ? y1 : y2;
  int maxY = y1 > y2 ? y1 : y2;

  for (int y = minY; y <= maxY; y++) {
    for (int x = minX; x <= maxX; x++) {
      drawPixel(x, y);
    }
  }
}

// square draw (special case of box with equal sides)
void drawSquare(int x, int y, int size) {
  drawBox(x, y, x + size, y + size);
}

// filled square draw
void drawFilledSquare(int x, int y, int size) {
  drawFilledBox(x, y, x + size, y + size);
}

// ---- font rendering ----

void drawGlyph(int x, int y, char c) {
  if (c < FONT_FIRST || c > FONT_LAST) return;

  const uint8_t* glyph = font10x16 + (c - FONT_FIRST) * 32;

  for (int row = 0; row < FONT_HEIGHT; row++) {
    uint8_t hi = pgm_read_byte(glyph + row * 2);
    uint8_t lo = pgm_read_byte(glyph + row * 2 + 1);

    // Reconstruct 10-bit row: hi=bits[9:2], lo top 2 bits=bits[1:0]
    uint16_t bits = ((uint16_t)hi << 2) | (lo >> 6);

    for (int col = 0; col < FONT_WIDTH; col++) {
      if (bits & (1 << (FONT_WIDTH - 1 - col))) {
        drawPixel(x + col, y + row);
      }
    }
  }
}

void drawBigText(const char* text, int x, int y) {
  while (*text) {
    drawGlyph(x, y, *text);
    x += FONT_WIDTH;
    text++;
    if (x >= FB_WIDTH) break;
  }
}

// ---- tile extraction ----

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

// ---- video playback ----
//
// pioneer.h stores each frame as 160 bytes: 16 rows x 10 bytes/row,
// 1 bit per pixel, MSB-first, matching FB_WIDTH/FB_HEIGHT exactly.
// VIDEO_FRAME_BYTES (160) == sizeof(framebuffer) worth of bits, so
// unpacking is a straight bit-to-byte expansion, no partial bytes.

static_assert(VIDEO_WIDTH == FB_WIDTH, "video width must match framebuffer");
static_assert(VIDEO_HEIGHT == FB_HEIGHT, "video height must match framebuffer");
static_assert(VIDEO_FRAME_BYTES == (FB_WIDTH / 8) * FB_HEIGHT, "unexpected frame packing size");

uint8_t videoFrameBuf[VIDEO_FRAME_BYTES];
uint16_t videoFrameIndex = 0;
unsigned long lastFrameMillis = 0;
const unsigned long frameIntervalMs = (unsigned long)((1000.0 / VIDEO_FPS)-14);

void unpackFrameToFramebuffer(const uint8_t* packed) {
  const int bytesPerRow = FB_WIDTH / 8; // 10
  for (int row = 0; row < FB_HEIGHT; row++) {
    const uint8_t* rowBytes = packed + row * bytesPerRow;
    for (int byteIdx = 0; byteIdx < bytesPerRow; byteIdx++) {
      uint8_t b = rowBytes[byteIdx];
      for (int bit = 0; bit < 8; bit++) {
        framebuffer[row][byteIdx * 8 + bit] = (b >> (7 - bit)) & 1;
      }
    }
  }
}

// rebuild the CGRAM charset bank from whatever is currently in framebuffer

void buildCharsetBankFromFramebuffer() {
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

// pulls in the next video frame (looping) once every 1000/VIDEO_FPS ms
// and rebuilds the charset bank from it. Cheap enough to call every
// loop() iteration - it no-ops until it's actually time for a new frame.

void advanceVideoFrameIfDue() {
  unsigned long now = millis();
  if (now - lastFrameMillis < frameIntervalMs) return;
  lastFrameMillis = now;

  video_get_frame(videoFrameIndex, videoFrameBuf);
  unpackFrameToFramebuffer(videoFrameBuf);
  buildCharsetBankFromFramebuffer();

  videoFrameIndex++;
  if (videoFrameIndex >= VIDEO_FRAME_COUNT) videoFrameIndex = 0;
}

// write the currently-built charset bank to the LCD ONCE
void drawStaticFrame() {

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

/* ================= SETUP ================= */

void setup() {

  lcdInit();

  // Load frame 0 and build the first charset bank before the refresh
  // loop starts, so the very first paint isn't blank.
  video_get_frame(0, videoFrameBuf);
  unpackFrameToFramebuffer(videoFrameBuf);
  buildCharsetBankFromFramebuffer();
  videoFrameIndex = 1;
  lastFrameMillis = millis();
}

/* ================= LOOP =================
   This must keep running: the LCD only has 8 CGRAM slots, but we're
   displaying 4 banks (32 custom chars) worth of tiles. Each pass here
   loads one bank into CGRAM and paints its quadrant; cycling through
   all 4 banks fast enough relies on persistence of vision to look like
   a stable full picture. On top of that refresh requirement, we now
   also advance the video playback at VIDEO_FPS, rebuilding the
   charset bank in place whenever a new frame is due. */

void loop() {
  advanceVideoFrameIfDue();
  drawStaticFrame();
}
