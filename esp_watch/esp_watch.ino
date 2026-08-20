#include <Arduino.h>
#include <math.h>
#include <Preferences.h>   // NVS key/value store -> lives in the same SPI flash chip
#include "telegrama.h"

// ESP32-C3 Super Mini pinout
// Data bus is GPIO0-7 (contiguous), so writing a byte is a single register write.
#define LCD_RS  10
#define LCD_E   9
#define BACKLIGHT 8
#define FB_WIDTH  80
#define FB_HEIGHT 16

// GPIO0-7 -> bits 0-7 in the GPIO register, so the mask is just 0xFF
#define LCD_DATA_MASK 0xFF

inline void lcdRS(bool state) {
  if (state)
    GPIO.out_w1ts.val = (1UL << LCD_RS);
  else
    GPIO.out_w1tc.val = (1UL << LCD_RS);
}

void drawGlyph(int x, int y, char c) {
  if (c < FONT_FIRST || c > FONT_LAST) return;

  const uint8_t* glyph = font10x16 + (c - FONT_FIRST) * 32;

  for (int row = 0; row < FONT_HEIGHT; row++) {
    uint8_t hi = pgm_read_byte(glyph + row * 2);
    uint8_t lo = pgm_read_byte(glyph + row * 2 + 1);

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

inline void lcdPulseEnable() {
  GPIO.out_w1ts.val = (1UL << LCD_E);
  delayMicroseconds(19);
  GPIO.out_w1tc.val = (1UL << LCD_E);
  delayMicroseconds(19);
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

// ===== 3D CUBE DATA =====

typedef struct {
  float x;
  float y;
  float z;
} Vec3;

float cubeAngle = 0;

Vec3 cubeVerts[8] = {
  {-1,-1,-1},
  { 1,-1,-1},
  { 1, 1,-1},
  {-1, 1,-1},
  {-1,-1, 1},
  { 1,-1, 1},
  { 1, 1, 1},
  {-1, 1, 1}
};

int cubeEdges[12][2] = {
  {0,1},{1,2},{2,3},{3,0},
  {4,5},{5,6},{6,7},{7,4},
  {0,4},{1,5},{2,6},{3,7}
};

// cube renderer

void drawCube(float angle, int cx, int cy)
{
  Vec3 rotated[8];

  float s = sin(angle);
  float c = cos(angle);

  float s2 = sin(angle*0.7);
  float c2 = cos(angle*0.7);

  for(int i=0;i<8;i++)
  {
    float x = cubeVerts[i].x;
    float y = cubeVerts[i].y;
    float z = cubeVerts[i].z;

    float x1 = x*c - z*s;
    float z1 = x*s + z*c;

    float y1 = y*c2 - z1*s2;
    float z2 = y*s2 + z1*c2;

    rotated[i] = {x1,y1,z2};
  }

  int px[8];
  int py[8];

  float scale = 2;

  for(int i=0;i<8;i++)
  {
    float z = rotated[i].z + 4;

    px[i] = cx + rotated[i].x * scale / z * 10;
    py[i] = cy + rotated[i].y * scale / z * 10;
  }

  for(int i=0;i<12;i++)
  {
    int a = cubeEdges[i][0];
    int b = cubeEdges[i][1];

    drawLine(px[a],py[a],px[b],py[b]);
  }
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
// TIME KEEPING + SERIAL SYNC + NVS (SPI FLASH) PERSISTENCE
// =====================================================================
//
// We only ever need to *display* HH:MM, so instead of a full date/epoch
// we just track "seconds since midnight" and roll it over at 86400.
// This avoids pulling in date math entirely.
//
// Persistence note: the ESP32-C3 has no battery-backed hardware RTC.
// "Saving to flash" here means we remember the last synced wall-clock
// time in NVS (a reserved area of the same SPI flash the sketch lives
// in) and resume counting from there on boot using millis(). While the
// board is fully powered off, time does NOT advance - on the next boot
// the clock will read whatever it was at the last sync/save, then start
// ticking again from there. If you need the clock to stay correct across
// long power-off periods, that needs an external battery-backed RTC
// (e.g. DS3231) - this is a software-only workaround, not a substitute
// for one.

Preferences prefs;

volatile uint32_t secondsSinceMidnight = 0;   // 0..86399
uint32_t lastMillisTick = 0;

static const char* NVS_NAMESPACE = "clock";
static const char* NVS_KEY       = "secs_mid";

void saveTimeToFlash(uint32_t secs) {
  prefs.begin(NVS_NAMESPACE, false); // read/write
  prefs.putUInt(NVS_KEY, secs);
  prefs.end();
}

uint32_t loadTimeFromFlash() {
  prefs.begin(NVS_NAMESPACE, true);  // read-only
  uint32_t secs = prefs.getUInt(NVS_KEY, 0); // default 00:00:00 if never synced
  prefs.end();
  return secs;
}

void setClock(uint8_t hh, uint8_t mm, uint8_t ss) {
  hh %= 24; mm %= 60; ss %= 60;
  secondsSinceMidnight = (uint32_t)hh * 3600UL + (uint32_t)mm * 60UL + ss;
  lastMillisTick = millis();
  saveTimeToFlash(secondsSinceMidnight);
}

// advance the clock based on elapsed millis(); call once per loop
void tickClock() {
  uint32_t now = millis();
  uint32_t elapsedMs = now - lastMillisTick; // handles millis() rollover fine (unsigned math)

  if (elapsedMs >= 1000) {
    uint32_t elapsedSec = elapsedMs / 1000;
    lastMillisTick += elapsedSec * 1000;
    secondsSinceMidnight = (secondsSinceMidnight + elapsedSec) % 86400UL;
  }
}

void getClockHHMM(char* out /* needs >= 6 bytes, "HH:MM\0" */) {
  uint32_t s = secondsSinceMidnight;
  uint8_t hh = s / 3600;
  uint8_t mm = (s % 3600) / 60;
  sprintf(out, "%02d:%02d", hh, mm);
}

// Parses "HH:MM" or "HH:MM:SS". Returns true on success.
bool parseTimeString(const char* str, uint8_t* hh, uint8_t* mm, uint8_t* ss) {
  int h = 0, m = 0, s = 0;
  int matched = sscanf(str, "%d:%d:%d", &h, &m, &s);
  if (matched < 2) return false;
  if (matched == 2) s = 0;
  if (h < 0 || h > 23 || m < 0 || m > 59 || s < 0 || s > 59) return false;
  *hh = (uint8_t)h; *mm = (uint8_t)m; *ss = (uint8_t)s;
  return true;
}

// Non-blocking serial line reader.
// Accepts either:
//   SETTIME HH:MM
//   SETTIME HH:MM:SS
// or, for quick manual testing in the Serial Monitor, a bare:
//   HH:MM
//   HH:MM:SS
void handleSerialSync() {
  static char lineBuf[32];
  static uint8_t lineLen = 0;

  while (Serial.available() > 0) {
    char c = (char)Serial.read();

    if (c == '\r') continue; // ignore CR, wait for LF

    if (c == '\n') {
      lineBuf[lineLen] = '\0';

      const char* payload = lineBuf;
      if (strncmp(lineBuf, "SETTIME ", 8) == 0) {
        payload = lineBuf + 8;
      }

      uint8_t hh, mm, ss;
      if (parseTimeString(payload, &hh, &mm, &ss)) {
        setClock(hh, mm, ss);
        Serial.print("OK ");
        char buf[9];
        sprintf(buf, "%02d:%02d:%02d", hh, mm, ss);
        Serial.println(buf);
      } else if (lineLen > 0) {
        Serial.print("ERR bad time string: ");
        Serial.println(lineBuf);
      }

      lineLen = 0;
      continue;
    }

    if (lineLen < sizeof(lineBuf) - 1) {
      lineBuf[lineLen++] = c;
    } else {
      // line too long, drop it to avoid overflow
      lineLen = 0;
    }
  }
}

// build one frame of the cube into the framebuffer + tile bank

void renderFrame() {

  clearFramebuffer();

  cubeAngle += 0.08;

  drawCube(cubeAngle, 65, 8);

  char timeStr[6]; // "HH:MM\0"
  getClockHHMM(timeStr);
  drawBigText(timeStr, 2, 2);

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

  // restore last known time from SPI flash (NVS) and start ticking from there
  secondsSinceMidnight = loadTimeFromFlash();
  lastMillisTick = millis();

  Serial.println("Ready. Send 'SETTIME HH:MM' or 'SETTIME HH:MM:SS' to sync.");
}

void loop() {
  handleSerialSync();
  tickClock();

  // single core: build the next cube frame, then immediately push it
  renderFrame();
  drawFrame();
}
