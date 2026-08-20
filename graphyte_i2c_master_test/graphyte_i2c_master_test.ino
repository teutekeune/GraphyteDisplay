#include <Arduino.h>
#include <Wire.h>

// ---- I2C config - must match the slave sketch -------------------------
#define I2C_SDA        21
#define I2C_SCL        20
#define I2C_SLAVE_ADDR 0x08
#define I2C_FREQ       100000

// How long each drawn frame stays on screen before moving to the next step
#define STEP_DELAY_MS  180

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

void logResult(uint8_t op, uint8_t err) {
  Serial.print("  op 0x");
  if (op < 0x10) Serial.print('0');
  Serial.print(op, HEX);
  Serial.print(" -> ");
  switch (err) {
    case 0: Serial.println("OK"); break;
    case 1: Serial.println("ERR data too long for TX buffer"); break;
    case 2: Serial.println("ERR NACK on address (slave not responding - check wiring/power)"); break;
    case 3: Serial.println("ERR NACK on data"); break;
    case 5: Serial.println("ERR timeout"); break;
    default: Serial.println("ERR unknown"); break;
  }
}

void sendCmd(uint8_t op) {
  Wire.beginTransmission(I2C_SLAVE_ADDR);
  Wire.write(op);
  logResult(op, Wire.endTransmission());
}

void sendCmd3(uint8_t op, uint8_t a, uint8_t b, uint8_t c) {
  Wire.beginTransmission(I2C_SLAVE_ADDR);
  Wire.write(op);
  Wire.write(a); Wire.write(b); Wire.write(c);
  logResult(op, Wire.endTransmission());
}

void sendCmd4(uint8_t op, uint8_t a, uint8_t b, uint8_t c, uint8_t d) {
  Wire.beginTransmission(I2C_SLAVE_ADDR);
  Wire.write(op);
  Wire.write(a); Wire.write(b); Wire.write(c); Wire.write(d);
  logResult(op, Wire.endTransmission());
}

void sendText(uint8_t x, uint8_t y, const char* text) {
  Wire.beginTransmission(I2C_SLAVE_ADDR);
  Wire.write(OP_DRAW_TEXT);
  Wire.write(x); Wire.write(y);
  for (const char* p = text; *p; p++) Wire.write((uint8_t)*p);
  logResult(OP_DRAW_TEXT, Wire.endTransmission());
}

void step(const char* label) {
  Serial.println(label);
}

void setup() {
  Serial.begin(115200);
  delay(300);

  Wire.begin(I2C_SDA, I2C_SCL, I2C_FREQ); // master mode

  Serial.println("Graphyte I2C test master ready.");
  Serial.print("Probing slave at 0x");
  Serial.println(I2C_SLAVE_ADDR, HEX);
  Wire.beginTransmission(I2C_SLAVE_ADDR);
  uint8_t err = Wire.endTransmission();
  if (err == 0) {
    Serial.println("Slave found, starting test loop.");
  } else {
    Serial.println("WARNING: slave did not ACK. Check SDA/SCL wiring, "
                    "shared ground, pull-up resistors, and that the "
                    "slave sketch is running.");
  }
}

void loop() {
  step("CLEAR");
  sendCmd(OP_CLEAR);
  delay(STEP_DELAY_MS);

  step("DRAW_LINE - X across the whole screen");
  sendCmd4(OP_DRAW_LINE, 0, 0, 79, 15);
  sendCmd4(OP_DRAW_LINE, 79, 0, 0, 15);
  delay(STEP_DELAY_MS);

  step("CLEAR");
  sendCmd(OP_CLEAR);
  delay(400);

  step("DRAW_RECT - outline");
  sendCmd4(OP_DRAW_RECT, 5, 2, 25, 10);
  delay(STEP_DELAY_MS);

  step("INVERT_RECT - same outline, toggles it off");
  sendCmd4(OP_INVERT_RECT, 5, 2, 25, 10);
  delay(STEP_DELAY_MS);

  step("CLEAR");
  sendCmd(OP_CLEAR);
  delay(400);

  step("DRAW_RECT_FILLED");
  sendCmd4(OP_DRAW_RECT_FILLED, 5, 2, 30, 12);
  delay(STEP_DELAY_MS);

  step("INVERT_RECT_FILLED - punches a hole in it");
  sendCmd4(OP_INVERT_RECT_FILLED, 15, 5, 12, 6);
  delay(STEP_DELAY_MS);

  step("CLEAR");
  sendCmd(OP_CLEAR);
  delay(400);

  step("DRAW_CIRCLE - outline");
  sendCmd3(OP_DRAW_CIRCLE, 40, 8, 7);
  delay(STEP_DELAY_MS);

  step("INVERT_CIRCLE - same circle, toggles it off");
  sendCmd3(OP_INVERT_CIRCLE, 40, 8, 7);
  delay(STEP_DELAY_MS);

  step("CLEAR");
  sendCmd(OP_CLEAR);
  delay(400);

  step("DRAW_CIRCLE_FILLED");
  sendCmd3(OP_DRAW_CIRCLE_FILLED, 40, 8, 7);
  delay(STEP_DELAY_MS);

  step("INVERT_CIRCLE_FILLED - punches a hole in the center");
  sendCmd3(OP_INVERT_CIRCLE_FILLED, 40, 8, 3);
  delay(STEP_DELAY_MS);

  step("CLEAR");
  sendCmd(OP_CLEAR);
  delay(400);

  step("DRAW_TEXT");
  sendText(2, 4, "HELLO I2C");
  delay(STEP_DELAY_MS * 2);

  step("CLEAR - restarting cycle");
  sendCmd(OP_CLEAR);
  delay(1000);
}
