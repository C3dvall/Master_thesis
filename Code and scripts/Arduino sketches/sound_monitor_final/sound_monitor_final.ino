/*******************************************************************************
 * Combined Sketch: Edge Impulse Audio Inference + TTN LoRaWAN Uplink
 *
 * Flow:
 *   1. setup() initialises Serial, ADC/DMA, and LMIC – then waits for
 *      EV_JOINED before doing anything else (join-first design).
 *   2. Once joined, loop() checks the ambient dB(A) level once every
 *      DB_MONITOR_INTERVAL_MS (1 s) using a short ~50 ms capture.
 *   3. If that level is >= DB_THRESHOLD (a global variable you can tune),
 *      a full EI_CLASSIFIER 5 s inference window is recorded and classified.
 *   4. The result is packed into a 14-byte payload and transmitted
 *      immediately, provided the radio stack is idle.
 *   5. If a TX is already in flight when inference finishes, the new
 *      result overwrites the pending buffer so the NEXT window sends
 *      the freshest data without queuing stale packets.
 *
 * Payload layout (14 bytes):
 *   [0]     dB level (measured over the 5 s inference window)
 *   [1]     hour   (RTC / compile-time fallback)
 *   [2]     minute
 *   [3]     second
 *   [4]     sequence counter
 *   [5-8]   latitude  × 1 000 000 (int32, big-endian)
 *   [9-12]  longitude × 1 000 000 (int32, big-endian)
 *   [13]    predicted category index (0-4)
 *              0 = ambient_weather
 *              1 = traffic_transport
 *              2 = animals
 *              3 = human_activity
 *              4 = construction
 *******************************************************************************/

// ── Edge Impulse ──────────────────────────────────────────────────────────────
#include <MASTER_V4_inferencing.h>
#include "hardware/adc.h"
#include "hardware/dma.h"
#include "hardware/irq.h"

// ── LoRaWAN / LMIC ────────────────────────────────────────────────────────────
#include <lmic.h>
#include <hal/hal.h>
#include <SPI.h>

// ── RTC (optional – uncomment if an RTC chip is wired up) ────────────────────
// #include <RTClib.h>
// RTC_DS3231 rtc;

// =============================================================================
// CONFIGURATION
// =============================================================================
#define DB_MONITOR_INTERVAL_MS 1000UL    // check the noise level once a second

// Global dB(A) threshold that triggers a full 5 s inference + LoRaWAN uplink.
// Tune this in the field – e.g. raise it to ignore background noise, lower it
// to make the sensor more sensitive.
float DB_THRESHOLD = 70.0f;

// Length of the short "monitoring" capture used for the once-a-second dB
// check (~50 ms of audio). This is intentionally much smaller than the
// model's 5 s inference window so the check is fast and cheap.
#define DB_MONITOR_SAMPLE_MS   50UL

// Forward declaration – computeDBA() is defined later in this file but is
// used earlier by monitor_db_level().
float computeDBA(int16_t *buffer, uint32_t len);

// =============================================================================
// dB(A) CONFIG + CALIBRATION
// =============================================================================
#define USE_CALIBRATION true

float CAL_REF_RMS   = 1.0f;
float CAL_SLOPE     = 1.0f;
float CAL_OFFSET_DB = 35.03f;

#define DB_MIN 0.0f
#define DB_MAX 120.0f

// =============================================================================
// CATEGORY LABELS  (indices 0-4 match EI classifier output order)
// =============================================================================
static const char* CATEGORY_LABELS[4] = {
    "alarm", "construction", "human_activity", "traffic"
};

// =============================================================================
// A-WEIGHTING FILTER (BIQUAD CASCADE)
// =============================================================================
typedef struct {
  float b0, b1, b2;
  float a1, a2;
  float z1, z2;
} Biquad;

// Approximation for 16kHz sampling
Biquad aweighting[] = {
  {0.2557411, -0.5114822, 0.2557411, -0.5772405, 0.4217870, 0, 0},
  {1.0, -2.0, 1.0, -1.9900475, 0.9900723, 0, 0}
};

#define NUM_BIQUADS (sizeof(aweighting)/sizeof(Biquad))

float processAWeighting(float x) {
  for (int i = 0; i < NUM_BIQUADS; i++) {
    Biquad *f = &aweighting[i];

    float y = f->b0 * x + f->z1;
    f->z1 = f->b1 * x - f->a1 * y + f->z2;
    f->z2 = f->b2 * x - f->a2 * y;

    x = y;
  }
  return x;
}

// =============================================================================
// TTN / LMIC CREDENTIALS  –  replace 0x00 values with your own
// =============================================================================
// Little-endian (LSB first) – copy from TTN console, reversed
static const u1_t PROGMEM APPEUI[8]  = { 0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00 };
void os_getArtEui(u1_t* buf)         { memcpy_P(buf, APPEUI, 8); }

static const u1_t PROGMEM DEVEUI[8]  = { 0x00,0x6D,0x07,0xD0,0x7E,0xD5,0xB3,0x70 };
void os_getDevEui(u1_t* buf)         { memcpy_P(buf, DEVEUI, 8); }

// Big-endian – copy from TTN console as-is
static const u1_t PROGMEM APPKEY[16] = {
    0xE0,0x30,0xF0,0xDB,0xE9,0x5B,0x0C,0x95,
    0xA4,0x8B,0xCC,0x05,0x04,0xBD,0xD9,0xA1
};
void os_getDevKey(u1_t* buf)         { memcpy_P(buf, APPKEY, 16); }

// =============================================================================
// LoRaWAN PIN MAP  (Adafruit Feather M4 LoRa / RP2040 LoRa)
// =============================================================================
const lmic_pinmap lmic_pins = {
    .nss            = 16,
    .rxtx           = LMIC_UNUSED_PIN,
    .rst            = 17,
    .dio            = {21, 22, LMIC_UNUSED_PIN},
    .rxtx_rx_active = 0,
    .rssi_cal       = 8,
    .spi_freq       = 8000000,
};

// =============================================================================
// GLOBAL STATE
// =============================================================================
static uint8_t  g_payload[14];          // latest packed result
static osjob_t  sendjob;
static uint8_t  seq_counter   = 0;
static bool     lora_joined   = false;  // set true in onEvent(EV_JOINED)
static bool     tx_pending    = false;  // true while LMIC has a frame in flight

static uint32_t last_db_check_ms  = 0;  // millis() timestamp of last dB monitor check

// =============================================================================
// ADC / DMA  (RP2040)
// =============================================================================
#define MIC_PIN   A0
#define ADC_INPUT 0   // A0 = ADC0

static int             dma_chan;
static int16_t        *audio_buffer = nullptr;
static volatile bool   buffer_ready  = false;

void dma_handler() {
    dma_hw->ints0 = 1u << dma_chan;
    buffer_ready  = true;
}

// ── One-time ADC + DMA initialisation ────────────────────────────────────────
void setup_adc_dma(uint32_t sample_rate_hz, uint32_t n_samples) {
    adc_init();
    adc_gpio_init(MIC_PIN);
    adc_select_input(ADC_INPUT);

    adc_fifo_setup(true, true, 1, false, false);
    adc_set_clkdiv(48000000.0f / sample_rate_hz);

    audio_buffer = (int16_t*)malloc(n_samples * sizeof(int16_t));

    dma_chan = dma_claim_unused_channel(true);
    dma_channel_config cfg = dma_channel_get_default_config(dma_chan);
    channel_config_set_transfer_data_size(&cfg, DMA_SIZE_16);
    channel_config_set_read_increment  (&cfg, false);
    channel_config_set_write_increment (&cfg, true);
    channel_config_set_dreq            (&cfg, DREQ_ADC);

    dma_channel_configure(dma_chan, &cfg,
        audio_buffer, &adc_hw->fifo, n_samples, false);

    dma_channel_set_irq0_enabled(dma_chan, true);
    irq_set_exclusive_handler(DMA_IRQ_0, dma_handler);
    irq_set_enabled(DMA_IRQ_0, true);
}

// ── Reset ADC + DMA before each recording ────────────────────────────────────
void reset_adc_dma(uint32_t sample_rate_hz, uint32_t n_samples) {
    adc_run(false);
    while (adc_fifo_get_level() > 0) adc_fifo_get();
    adc_hw->fcs = 0;

    dma_channel_abort(dma_chan);
    dma_hw->ints0 = 1u << dma_chan;

    adc_init();
    adc_gpio_init(MIC_PIN);
    adc_select_input(ADC_INPUT);
    adc_fifo_setup(true, true, 1, false, false);
    adc_set_clkdiv(48000000.0f / sample_rate_hz);

    dma_channel_config cfg = dma_channel_get_default_config(dma_chan);
    channel_config_set_transfer_data_size(&cfg, DMA_SIZE_16);
    channel_config_set_read_increment  (&cfg, false);
    channel_config_set_write_increment (&cfg, true);
    channel_config_set_dreq            (&cfg, DREQ_ADC);

    dma_channel_configure(dma_chan, &cfg,
        audio_buffer, &adc_hw->fifo, n_samples, false);
}

void start_sampling(uint32_t n_samples) {
    buffer_ready = false;
    dma_channel_set_trans_count(dma_chan, n_samples, false);
    dma_channel_start(dma_chan);
    adc_run(true);
}

void stop_sampling() {
    adc_run(false);
}

// =============================================================================
// LIGHTWEIGHT dB MONITORING  –  short capture reused once per second
// =============================================================================
// Reuses the same DMA channel / audio_buffer as the main inference capture,
// just with a much smaller sample count (~50 ms instead of 5 s). This keeps
// the once-a-second check cheap while the 5 s buffer stays sized for EI.
float monitor_db_level() {
    uint32_t monitor_samples = (EI_CLASSIFIER_FREQUENCY * DB_MONITOR_SAMPLE_MS) / 1000UL;
    if (monitor_samples < 1) monitor_samples = 1;
    if (monitor_samples > EI_CLASSIFIER_RAW_SAMPLE_COUNT) {
        monitor_samples = EI_CLASSIFIER_RAW_SAMPLE_COUNT;  // safety clamp
    }

    reset_adc_dma(EI_CLASSIFIER_FREQUENCY, monitor_samples);
    start_sampling(monitor_samples);
    while (!buffer_ready) {
        // Keep LMIC alive while waiting for the short DMA capture to finish
        os_runloop_once();
    }
    stop_sampling();

    // Convert ADC values to signed centered signal (same as main capture)
    for (uint32_t i = 0; i < monitor_samples; i++) {
        audio_buffer[i] = ((int)audio_buffer[i] - 2048) * 16;
    }

    return computeDBA(audio_buffer, monitor_samples);
}

// =============================================================================
// INFERENCE  –  records audio, classifies, and packs g_payload
// =============================================================================
void run_inference_and_pack_payload() {

    // ── Capture timestamp before recording ───────────────────────────────────
    uint8_t ts_hour = 0, ts_minute = 0, ts_second = 0;

    // If RTC is wired up, replace this block:
    // DateTime now = rtc.now();
    // ts_hour = now.hour(); ts_minute = now.minute(); ts_second = now.second();
    //
    // Compile-time fallback – gives the build time, not wall clock:
    {
        const char* t = __TIME__;   // "HH:MM:SS"
        ts_hour   = (t[0]-'0')*10 + (t[1]-'0');
        ts_minute = (t[3]-'0')*10 + (t[4]-'0');
        ts_second = (t[6]-'0')*10 + (t[7]-'0');
    }

    // ── Record audio ─────────────────────────────────────────────────────────
    Serial.println(F("[INF] Recording..."));
    reset_adc_dma(EI_CLASSIFIER_FREQUENCY, EI_CLASSIFIER_RAW_SAMPLE_COUNT);
    start_sampling(EI_CLASSIFIER_RAW_SAMPLE_COUNT);
    while (!buffer_ready) {
        // Keep LMIC alive while waiting for DMA to finish
        os_runloop_once();
    }
    stop_sampling();
    Serial.println(F("[INF] Recording done"));

        // Convert ADC values to signed centered signal
    for (uint32_t i = 0; i < EI_CLASSIFIER_RAW_SAMPLE_COUNT; i++) {
        audio_buffer[i] = ((int)audio_buffer[i] - 2048) * 16;
    }

    // ─────────────────────────────
    // dB(A) COMPUTATION (NEW)
    // ─────────────────────────────
    float dba = computeDBA(audio_buffer, EI_CLASSIFIER_RAW_SAMPLE_COUNT);

    Serial.print("[INF] dB(A): ");
    Serial.println(dba, 2);
    // ── Run classifier ────────────────────────────────────────────────────────
    signal_t signal;
    signal.total_length = EI_CLASSIFIER_RAW_SAMPLE_COUNT;
    signal.get_data = [](size_t offset, size_t length, float *out_ptr) -> int {
        numpy::int16_to_float(&audio_buffer[offset], out_ptr, length);
        return 0;
    };

    ei_impulse_result_t result = { 0 };
    EI_IMPULSE_ERROR err = run_classifier(&signal, &result, false);

    if (err != EI_IMPULSE_OK) {
        Serial.print(F("[INF] Classifier error: "));
        Serial.println(err);
        return;
    }

    // ── Find best-confidence category joi
    uint8_t best_index = 0;
    float   best_value = result.classification[0].value;

    Serial.println(F("[INF] Predictions:"));
    for (uint16_t i = 0; i < EI_CLASSIFIER_LABEL_COUNT && i < 5; i++) {
        float v = result.classification[i].value;
        Serial.print(F("  "));
        Serial.print(CATEGORY_LABELS[i]);
        Serial.print(F(": "));
        Serial.println(v, 5);
        if (v > best_value) { best_value = v; best_index = (uint8_t)i; }
    }
    Serial.print(F("[INF] Best: "));
    Serial.print(CATEGORY_LABELS[best_index]);
    Serial.print(F(" ("));
    Serial.print(best_value, 5);
    Serial.println(')');

    // ── Pack 14-byte payload ──────────────────────────────────────────────────
    g_payload[0] = (uint8_t) constrain((int)dba, 0, 255); 
    g_payload[1] = ts_hour;
    g_payload[2] = ts_minute;
    g_payload[3] = ts_second;
    g_payload[4] = 0;           // sequence counter – written in do_send()

    // Latitude  55.60778 → 55607780  (big-endian int32)
    int32_t lat = 55607780;
    g_payload[5]  = (lat >> 24) & 0xFF;
    g_payload[6]  = (lat >> 16) & 0xFF;
    g_payload[7]  = (lat >>  8) & 0xFF;
    g_payload[8]  =  lat        & 0xFF;

    // Longitude 12.99639 → 12996390  (big-endian int32)
    int32_t lon = 12996390;
    g_payload[9]  = (lon >> 24) & 0xFF;
    g_payload[10] = (lon >> 16) & 0xFF;
    g_payload[11] = (lon >>  8) & 0xFF;
    g_payload[12] =  lon        & 0xFF;

    g_payload[13] = best_index;
}

// =============================================================================
// dB(A) COMPUTATION
// =============================================================================
float computeDBA(int16_t *buffer, uint32_t len) {

  float mean = 0.0f;
  for (uint32_t i = 0; i < len; i++) {
    mean += buffer[i];
  }
  mean /= len;

  float sum = 0.0f;

  for (uint32_t i = 0; i < len; i++) {
    float x = buffer[i] - mean;

    float weighted = processAWeighting(x);
    sum += weighted * weighted;
  }

  float rms = sqrt(sum / len);
  if (rms < 1e-12f) rms = 1e-12f;

  float db;

  if (USE_CALIBRATION) {
    db = 20.0f * log10(rms / CAL_REF_RMS);
    db = CAL_SLOPE * db + CAL_OFFSET_DB;
  } else {
    db = 20.0f * log10(rms);
  }

  if (db > DB_MAX) db = DB_MAX;
  if (db < DB_MIN) db = DB_MIN;

  return db;
}

// =============================================================================
// LMIC CALLBACKS
// =============================================================================
void onEvent(ev_t ev) {
    Serial.print(F("[TTN] "));
    switch (ev) {
        case EV_JOINING:
            Serial.println(F("Joining..."));
            break;

        case EV_JOINED:
            Serial.println(F("Joined!"));
            {
                u4_t netid = 0; devaddr_t devaddr = 0;
                u1_t nwkKey[16], artKey[16];
                LMIC_getSessionKeys(&netid, &devaddr, nwkKey, artKey);
                Serial.print(F("  devaddr: 0x"));
                Serial.println(devaddr, HEX);
            }
            LMIC_setLinkCheckMode(0);
            lora_joined = true;   // ← unblocks inference in loop()
            break;

        case EV_JOIN_FAILED:
            Serial.println(F("Join failed – retrying..."));
            // LMIC will retry automatically; lora_joined stays false
            break;

        case EV_REJOIN_FAILED:
            Serial.println(F("Rejoin failed"));
            break;

        case EV_TXSTART:
            Serial.println(F("TX started"));
            tx_pending = true;
            break;

        case EV_TXCOMPLETE:
            tx_pending = false;
            Serial.print(F("TX complete"));
            if (LMIC.txrxFlags & TXRX_ACK) Serial.print(F(" (ACK)"));
            Serial.println();
            break;

        default:
            Serial.print(F("Event: "));
            Serial.println((unsigned)ev);
            break;
    }
}

// =============================================================================
// TRANSMIT  –  stamps sequence number and fires the uplink
// =============================================================================
void do_send(osjob_t* j) {
    if (LMIC.opmode & OP_TXRXPEND) {
        Serial.println(F("[TTN] TX busy – payload will be sent next window"));
        return;
    }

    g_payload[4] = seq_counter++;

    Serial.print(F("[TTN] Sending payload: ["));
    for (uint8_t i = 0; i < sizeof(g_payload); i++) {
        if (i) Serial.print(' ');
        if (g_payload[i] < 0x10) Serial.print('0');
        Serial.print(g_payload[i], HEX);
    }
    Serial.println(']');
    Serial.print(F("[TTN] Category: "));
    Serial.println(CATEGORY_LABELS[g_payload[13]]);

    LMIC_setTxData2(1, g_payload, sizeof(g_payload), 0);
}

// =============================================================================
// setup()  –  init hardware, then start LoRaWAN join; inference waits
// =============================================================================
void setup() {
    delay(3000);
    Serial.begin(115200);
    while (!Serial);
    Serial.println(F("=== TTN + Edge Impulse Audio Classifier ==="));
    Serial.println(F("=== tt_inference_combined_2 ==="));

    // Print EI settings
    Serial.print(F("[EI] Sample rate : ")); Serial.print(EI_CLASSIFIER_FREQUENCY); Serial.println(F(" Hz"));
    Serial.print(F("[EI] Frame size  : ")); Serial.println(EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE);
    Serial.print(F("[EI] Interval    : ")); Serial.print(EI_CLASSIFIER_INTERVAL_MS); Serial.println(F(" ms"));
    Serial.print(F("[DB] Monitoring every ")); Serial.print(DB_MONITOR_INTERVAL_MS / 1000); Serial.println(F(" s"));
    Serial.print(F("[DB] Threshold   : ")); Serial.println(DB_THRESHOLD, 1);

    // Initialise ADC + DMA (allocates audio_buffer, claims DMA channel)
    setup_adc_dma(EI_CLASSIFIER_FREQUENCY, EI_CLASSIFIER_RAW_SAMPLE_COUNT);

    // ── Initialise LMIC and start OTAA join ───────────────────────────────────
    // Inference will NOT begin until EV_JOINED fires (lora_joined = true).
    os_init();
    LMIC_reset();
    LMIC_setLinkCheckMode(0);
    LMIC_setDrTxpow(DR_SF7, 14);
    LMIC_setClockError(MAX_CLOCK_ERROR * 5 / 100);

    Serial.println(F("[TTN] Starting OTAA join..."));
    LMIC_startJoining();
}

// =============================================================================
// loop()  –  drives LMIC; checks dB level every second; only runs the full
//            5 s inference + uplink when the level crosses DB_THRESHOLD
// =============================================================================
void loop() {
    // Always keep the LoRaWAN stack ticking
    os_runloop_once();

    // Do nothing until the gateway join succeeds
    if (!lora_joined) return;

    // ── Once-a-second noise level check ───────────────────────────────────────
    uint32_t now = millis();
    if (now - last_db_check_ms < DB_MONITOR_INTERVAL_MS) return;
    last_db_check_ms = now;

    float current_db = monitor_db_level();
    Serial.print(F("[DB] Level: "));
    Serial.print(current_db, 2);
    Serial.print(F(" dB  (threshold "));
    Serial.print(DB_THRESHOLD, 1);
    Serial.println(F(")"));

    if (current_db < DB_THRESHOLD) {
        return;   // below threshold – keep monitoring, no inference needed
    }

    // ── Threshold exceeded: run the full 5 s inference window ────────────────
    Serial.println(F("\n[INF] === Threshold exceeded – starting inference cycle ==="));
    run_inference_and_pack_payload();

    // ── Transmit immediately if the radio is free; otherwise the freshest
    //    payload is already in g_payload and will go out after EV_TXCOMPLETE
    //    triggers the next send opportunity (handled in onEvent above).
    if (!(LMIC.opmode & OP_TXRXPEND)) {
        do_send(&sendjob);
    } else {
        Serial.println(F("[TTN] Radio busy – updated payload queued for next TX window"));
    }
}
