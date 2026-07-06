/*******************************************************************************
 * Standalone Sketch: Edge Impulse Audio Inference (No LoRaWAN)
 *
 * This is a stripped-down version of device_firmware_v2.ino with ALL LoRaWAN /
 * LMIC / TTN code removed. It does ONLY:
 *
 *   1. setup() initialises Serial and ADC/DMA. No radio, no join step –
 *      monitoring starts immediately.
 *   2. loop() checks the ambient dB(A) level once every
 *      DB_MONITOR_INTERVAL_MS (1 s) using a short ~50 ms capture.
 *   3. If that level is >= DB_THRESHOLD (tunable global), a full
 *      EI_CLASSIFIER 5 s inference window is recorded and classified.
 *   4. The result is printed to Serial as a single CSV line:
 *
 *        millis,db_level,category,confidence
 *
 *      e.g.   482310,74.32,traffic,0.8821
 *
 * No payload packing, no radio, no GPS placeholders, no LMIC callbacks.
 *
 * HOW TO GET A .csv FILE OUT OF THIS:
 *   The board itself has no filesystem, so it cannot write a .csv file on its
 *   own. Run the companion Python script (serial_csv_logger.py) on your
 *   computer while this is plugged in via USB — it reads these CSV lines from
 *   the serial port and appends them to a real .csv file. See the README
 *   provided alongside this sketch for the alternative (SD card) approach if
 *   you want fully standalone logging with no computer attached.
 *******************************************************************************/

// ── Edge Impulse ──────────────────────────────────────────────────────────────
#include <MASTER_V4_inferencing.h>
#include "hardware/adc.h"
#include "hardware/dma.h"
#include "hardware/irq.h"

// ── RTC (optional – uncomment if an RTC chip is wired up) ────────────────────
// #include <RTClib.h>
// RTC_DS3231 rtc;

// =============================================================================
// CONFIGURATION
// =============================================================================
#define DB_MONITOR_INTERVAL_MS 1000UL    // check the noise level once a second

// Global dB(A) threshold that triggers a full 5 s inference cycle.
// Tune this in the field – e.g. raise it to ignore background noise, lower it
// to make the sensor more sensitive.
float DB_THRESHOLD = 55.0f;

// Length of the short "monitoring" capture used for the once-a-second dB
// check (~50 ms of audio). Intentionally much smaller than the model's 5 s
// inference window so the check is fast and cheap.
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
// CATEGORY LABELS  (indices match EI classifier output order)
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
// GLOBAL STATE
// =============================================================================
static uint32_t last_db_check_ms  = 0;  // millis() timestamp of last dB monitor check
static uint32_t seq_counter       = 0;  // simple incrementing record counter

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
        // No radio stack to keep alive here — just wait for the short DMA capture
    }
    stop_sampling();

    // Convert ADC values to signed centered signal (same as main capture)
    for (uint32_t i = 0; i < monitor_samples; i++) {
        audio_buffer[i] = ((int)audio_buffer[i] - 2048) * 16;
    }

    return computeDBA(audio_buffer, monitor_samples);
}

// =============================================================================
// INFERENCE  –  records audio, classifies, prints one CSV line to Serial
// =============================================================================
void run_inference_and_log() {

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
    reset_adc_dma(EI_CLASSIFIER_FREQUENCY, EI_CLASSIFIER_RAW_SAMPLE_COUNT);
    Serial.println("Starting recording...");
    start_sampling(EI_CLASSIFIER_RAW_SAMPLE_COUNT);
    while (!buffer_ready) {
        // Just wait — no radio stack to service in this build
    }
    stop_sampling();
    Serial.println("Recording done...");

    // Convert ADC values to signed centered signal
    for (uint32_t i = 0; i < EI_CLASSIFIER_RAW_SAMPLE_COUNT; i++) {
        audio_buffer[i] = ((int)audio_buffer[i] - 2048) * 16;
    }

    // ── dB(A) computation ─────────────────────────────────────────────────────
    float dba = computeDBA(audio_buffer, EI_CLASSIFIER_RAW_SAMPLE_COUNT);

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

    // ── Find best-confidence category ────────────────────────────────────────
    uint8_t best_index = 0;
    float   best_value = result.classification[0].value;

    for (uint16_t i = 0; i < EI_CLASSIFIER_LABEL_COUNT && i < 5; i++) {
        float v = result.classification[i].value;
        if (v > best_value) { best_value = v; best_index = (uint8_t)i; }
    }

    // ── Human-readable log (goes to Serial Monitor, NOT part of the CSV) ─────
    Serial.println(F("[INF] === Threshold exceeded – inference result ==="));
    Serial.print(F("[INF] dB(A): "));
    Serial.println(dba, 2);
    Serial.println(F("[INF] Predictions:"));
    for (uint16_t i = 0; i < EI_CLASSIFIER_LABEL_COUNT && i < 5; i++) {
        Serial.print(F("  "));
        Serial.print(CATEGORY_LABELS[i]);
        Serial.print(F(": "));
        Serial.println(result.classification[i].value, 5);
    }
    Serial.print(F("[INF] Best: "));
    Serial.print(CATEGORY_LABELS[best_index]);
    Serial.print(F(" ("));
    Serial.print(best_value, 5);
    Serial.println(')');

    // ── Machine-readable CSV line ──────────────────────────────────────────────
    // Format: seq,millis,hour,minute,second,db_level,category,confidence
    // A dedicated "CSV:" prefix makes it trivial for a companion script (or a
    // human skimming the Serial Monitor) to pick this exact line out from the
    // human-readable log lines above/below it.
    Serial.print(F("CSV:"));
    Serial.print(seq_counter++);
    Serial.print(',');
    Serial.print(millis());
    Serial.print(',');
    Serial.print(ts_hour);
    Serial.print(',');
    Serial.print(ts_minute);
    Serial.print(',');
    Serial.print(ts_second);
    Serial.print(',');
    Serial.print(dba, 2);
    Serial.print(',');
    Serial.print(CATEGORY_LABELS[best_index]);
    Serial.print(',');
    Serial.println(best_value, 4);
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
// setup()  –  init hardware only. No radio, no join, monitoring starts now.
// =============================================================================
void setup() {
    delay(3000);
    Serial.begin(115200);
    while (!Serial);
    Serial.println(F("=== Standalone Edge Impulse Audio Classifier (no LoRaWAN) ==="));

    // Print EI settings
    Serial.print(F("[EI] Sample rate : ")); Serial.print(EI_CLASSIFIER_FREQUENCY); Serial.println(F(" Hz"));
    Serial.print(F("[EI] Frame size  : ")); Serial.println(EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE);
    Serial.print(F("[EI] Interval    : ")); Serial.print(EI_CLASSIFIER_INTERVAL_MS); Serial.println(F(" ms"));
    Serial.print(F("[DB] Monitoring every ")); Serial.print(DB_MONITOR_INTERVAL_MS / 1000); Serial.println(F(" s"));
    Serial.print(F("[DB] Threshold   : ")); Serial.println(DB_THRESHOLD, 1);

    // Initialise ADC + DMA (allocates audio_buffer, claims DMA channel)
    setup_adc_dma(EI_CLASSIFIER_FREQUENCY, EI_CLASSIFIER_RAW_SAMPLE_COUNT);

    // Header line for the CSV — a companion script can use this to name columns
    Serial.println(F("CSV_HEADER:seq,millis,hour,minute,second,db_level,category,confidence"));

    Serial.println(F("[DB] Monitoring started."));
}

// =============================================================================
// loop()  –  checks dB level every second; runs the full 5 s inference +
//            CSV log only when the level crosses DB_THRESHOLD
// =============================================================================
void loop() {

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

    // ── Threshold exceeded: run the full 5 s inference window + log it ───────
    run_inference_and_log();
    Serial.println("Classification finished. Applying 10 Second Delay.");
    delay(10000);
}
