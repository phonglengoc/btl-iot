#include "LiquidCrystal_I2C.h"
#include <Arduino.h>
#include <Wire.h>
#include <inttypes.h>

LiquidCrystal_I2C::LiquidCrystal_I2C(uint8_t lcd_addr, uint8_t lcd_cols, uint8_t lcd_rows, uint8_t charsize) {
  _addr = lcd_addr;
  _cols = lcd_cols;
  _rows = lcd_rows;
  _charsize = charsize;
  _backlightval = LCD_BACKLIGHT;
  _i2cErrors = 0;
}

void LiquidCrystal_I2C::begin() {
  // Keep Wire pins configured by caller (GPIO11 SDA / GPIO12 SCL on OhStem YoloUNO)
  _displayfunction = LCD_4BITMODE | LCD_1LINE | LCD_5x8DOTS;
  if (_rows > 1) {
    _displayfunction |= LCD_2LINE;
  }
  if ((_charsize != 0) && (_rows == 1)) {
    _displayfunction |= LCD_5x10DOTS;
  }

  delay(50);
  expanderWrite(_backlightval);
  delay(500);

  // HD44780 Figure 24: guaranteed reset from 8-bit OR desynced 4-bit into 4-bit mode
  write4bits(0x03 << 4);
  delayMicroseconds(4500);
  write4bits(0x03 << 4);
  delayMicroseconds(4500);
  write4bits(0x03 << 4);
  delayMicroseconds(150);
  write4bits(0x02 << 4);
  delayMicroseconds(150);

  command(LCD_FUNCTIONSET | _displayfunction);
  _displaycontrol = LCD_DISPLAYON | LCD_CURSOROFF | LCD_BLINKOFF;
  display();
  clear();

  _displaymode = LCD_ENTRYLEFT | LCD_ENTRYSHIFTDECREMENT;
  command(LCD_ENTRYMODESET | _displaymode);
  home();
}

void LiquidCrystal_I2C::clear() {
  command(LCD_CLEARDISPLAY);
  delayMicroseconds(2500);
}

void LiquidCrystal_I2C::home() {
  command(LCD_RETURNHOME);
  delayMicroseconds(2500);
}

void LiquidCrystal_I2C::setCursor(uint8_t col, uint8_t row) {
  int row_offsets[] = {0x00, 0x40, 0x14, 0x54};
  if (row >= _rows) {
    row = _rows - 1;
  }
  command(LCD_SETDDRAMADDR | (col + row_offsets[row]));
}

void LiquidCrystal_I2C::noDisplay() {
  _displaycontrol &= ~LCD_DISPLAYON;
  command(LCD_DISPLAYCONTROL | _displaycontrol);
}

void LiquidCrystal_I2C::display() {
  _displaycontrol |= LCD_DISPLAYON;
  command(LCD_DISPLAYCONTROL | _displaycontrol);
}

void LiquidCrystal_I2C::noCursor() {
  _displaycontrol &= ~LCD_CURSORON;
  command(LCD_DISPLAYCONTROL | _displaycontrol);
}

void LiquidCrystal_I2C::cursor() {
  _displaycontrol |= LCD_CURSORON;
  command(LCD_DISPLAYCONTROL | _displaycontrol);
}

void LiquidCrystal_I2C::noBlink() {
  _displaycontrol &= ~LCD_BLINKON;
  command(LCD_DISPLAYCONTROL | _displaycontrol);
}

void LiquidCrystal_I2C::blink() {
  _displaycontrol |= LCD_BLINKON;
  command(LCD_DISPLAYCONTROL | _displaycontrol);
}

void LiquidCrystal_I2C::noBacklight() {
  _backlightval = LCD_NOBACKLIGHT;
  expanderWrite(0);
}

void LiquidCrystal_I2C::backlight() {
  _backlightval = LCD_BACKLIGHT;
  expanderWrite(0);
}

bool LiquidCrystal_I2C::getBacklight() {
  return _backlightval == LCD_BACKLIGHT;
}

inline void LiquidCrystal_I2C::command(uint8_t value) {
  send(value, 0);
}

inline size_t LiquidCrystal_I2C::write(uint8_t value) {
  send(value, Rs);
  return 1;
}

void LiquidCrystal_I2C::send(uint8_t value, uint8_t mode) {
  uint8_t highnib = value & 0xF0;
  uint8_t lownib = (value << 4) & 0xF0;
  write4bits(highnib | mode);
  write4bits(lownib | mode);
}

void LiquidCrystal_I2C::write4bits(uint8_t value) {
  expanderWrite(value);
  pulseEnable(value);
}

void LiquidCrystal_I2C::expanderWrite(uint8_t _data) {
  if (_addr == 0)
    return;
  Wire.beginTransmission(_addr);
  Wire.write((int)(_data) | _backlightval);
  if (Wire.endTransmission() != 0) {
    ++_i2cErrors;
  }
}

void LiquidCrystal_I2C::pulseEnable(uint8_t _data) {
  expanderWrite(_data | En);
  delayMicroseconds(2);
  expanderWrite(_data & ~En);
  delayMicroseconds(60);
}
