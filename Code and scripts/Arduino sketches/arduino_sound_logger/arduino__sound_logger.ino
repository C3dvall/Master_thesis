#include <Arduino.h>

const int MIC_PIN = A2;

// Sampling configuration
const uint32_t SAMPLE_RATE = 16000;
const uint16_t BLOCK_SIZE = 256;

// ADC config for RP2040
const uint8_t ADC_BITS = 12;

void setup() {
  Serial.begin(115200);
  analogReadResolution(ADC_BITS);

  delay(1000);

  Serial.println("# MEMS raw sample streamer");
  Serial.print("# SAMPLE_RATE=");
  Serial.println(SAMPLE_RATE);
  Serial.print("# BLOCK_SIZE=");
  Serial.println(BLOCK_SIZE);
  Serial.println("# format: block_timestamp_ms,sample_index,adc_value");
}

void loop() {
  uint32_t blockTimestamp = millis();
  uint32_t samplePeriodUs = 1000000UL / SAMPLE_RATE;
  uint32_t nextSampleMicros = micros();

  for (uint16_t i = 0; i < BLOCK_SIZE; i++) {
    while ((int32_t)(micros() - nextSampleMicros) < 0) {
      // wait for next sample time
    }
    nextSampleMicros += samplePeriodUs;

    int adc = analogRead(MIC_PIN);

    Serial.print(blockTimestamp);
    Serial.print(",");
    Serial.print(i);
    Serial.print(",");
    Serial.println(adc);
  }

  // Small pause so serial transmission can keep up
  delay(20);
}