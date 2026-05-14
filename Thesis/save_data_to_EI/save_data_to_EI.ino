#include <Arduino.h>
#include "hardware/adc.h"
#include "hardware/dma.h"
#include "hardware/irq.h"

// ===== USER CONFIG =====
#define MIC_PIN        A0
#define SAMPLE_RATE    16000
#define RECORD_SECONDS 5
#define TOTAL_SAMPLES  (SAMPLE_RATE * RECORD_SECONDS)

// ===== GLOBALS =====
int16_t *audioBuffer;
int dma_chan;
volatile bool dma_done = false;

// ===== DMA IRQ HANDLER =====
void dma_handler() {
    dma_hw->ints0 = 1u << dma_chan;
    dma_done = true;
}

// ===== MICROPHONE SELF TEST =====
bool testMic() {
    Serial.println("[TEST] Checking microphone...");

    long sum = 0;

    for (int i = 0; i < 200; i++) {
        int v = analogRead(MIC_PIN);
        sum += v;
        delay(2);
    }

    int avg = sum / 200;

    Serial.printf("[TEST] Mic average level: %d\n", avg);

    if (avg < 200 || avg > 4000) {
        Serial.println("[ERROR] Mic reading out of expected range");
        return false;
    }

    Serial.println("[TEST] Microphone OK");
    return true;
}

// ===== SEND EDGE IMPULSE HEADER =====
void sendEIHeader() {

    Serial.println(
        "{\"protected\":{\"ver\":\"v1\"},"
        "\"payload\":{"
        "\"device_name\":\"rp2040-mic\","
        "\"device_type\":\"custom\","
        "\"interval_ms\":0.0625,"
        "\"sensors\":[{\"name\":\"audio\",\"units\":\"raw\"}]"
        "}}"
    );
}

// ===== SETUP ADC + DMA =====
void setupADC_DMA() {

    adc_init();
    adc_gpio_init(MIC_PIN);
    adc_select_input(0);

    dma_chan = dma_claim_unused_channel(true);

    dma_channel_config cfg = dma_channel_get_default_config(dma_chan);

    channel_config_set_transfer_data_size(&cfg, DMA_SIZE_16);
    channel_config_set_read_increment(&cfg, false);
    channel_config_set_write_increment(&cfg, true);
    channel_config_set_dreq(&cfg, DREQ_ADC);

    dma_channel_configure(
        dma_chan,
        &cfg,
        audioBuffer,
        &adc_hw->fifo,
        TOTAL_SAMPLES,
        false
    );

    adc_fifo_setup(
        true,
        true,
        1,
        false,
        false
    );

    adc_set_clkdiv(48000000.0 / SAMPLE_RATE);

    dma_channel_set_irq0_enabled(dma_chan, true);

    irq_set_exclusive_handler(DMA_IRQ_0, dma_handler);
    irq_set_enabled(DMA_IRQ_0, true);
}

// ===== RECORD AUDIO =====
void recordAudio() {

    dma_done = false;

    dma_channel_set_write_addr(
        dma_chan,
        audioBuffer,
        false
    );

    dma_channel_set_trans_count(
        dma_chan,
        TOTAL_SAMPLES,
        false
    );

    adc_run(true);

    dma_channel_start(dma_chan);

    while (!dma_done) {
    }

    adc_run(false);

    adc_fifo_drain();
}

// ===== STREAM TO EDGE IMPULSE =====
void streamToEI() {

    for (int i = 0; i < TOTAL_SAMPLES; i++) {

        Serial.println(audioBuffer[i]);
    }
}

// ===== SETUP =====
void setup() {

    Serial.begin(115200);

    delay(2000);

    Serial.println("\n=== Continuous RP2040 Audio Stream ===");

    audioBuffer = (int16_t*) malloc(
        TOTAL_SAMPLES * sizeof(int16_t)
    );

    if (!audioBuffer) {

        Serial.println("[FATAL] Failed to allocate buffer");

        while (true) {
            delay(1000);
        }
    }

    if (!testMic()) {

        while (true) {
            delay(1000);
        }
    }

    setupADC_DMA();

    sendEIHeader();

    Serial.println("[INFO] Continuous streaming started");
}

// ===== MAIN LOOP =====
void loop() {

    recordAudio();

    streamToEI();
}