#include <arduinoFFT.h>

const int MIC_PIN = A2;
const int SAMPLE_RATE = 8000;   // 8 kHz sampling
const int SAMPLES = 256;        // FFT size

// Correct template-based FFT object
ArduinoFFT<double> FFT = ArduinoFFT<double>();

double vReal[SAMPLES];
double vImag[SAMPLES];

const float MIC_ZERO = 2048.0;  // ADC midpoint for RP2040 (12-bit)

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);
  delay(1000);
  Serial.println("8 kHz dB(A) + Relative dB meter started...");
}

void loop() {

  // ---- 1. Collect samples at 8 kHz ----
  for (int i = 0; i < SAMPLES; i++) {
    vReal[i] = analogRead(MIC_PIN) - MIC_ZERO;
    vImag[i] = 0;
    delayMicroseconds(1000000 / SAMPLE_RATE);
  }

  // ---- 2. Compute Relative dB (simple RMS) ----
  double sumSquaresRaw = 0;
  for (int i = 0; i < SAMPLES; i++) {
    sumSquaresRaw += vReal[i] * vReal[i];
  }
  double rmsRaw = sqrt(sumSquaresRaw / SAMPLES);
  double relativeDB = 20.0 * log10(rmsRaw + 1);  // +1 avoids log(0)

  // ---- 3. FFT for A-weighting ----
  FFT.windowing(vReal, SAMPLES, FFT_WIN_TYP_HAMMING, FFT_FORWARD);
  FFT.compute(vReal, vImag, SAMPLES, FFT_FORWARD);
  FFT.complexToMagnitude(vReal, vImag, SAMPLES);

  // ---- 4. Apply A-weighting ----
  double sumSquaresA = 0;

  for (int i = 1; i < SAMPLES / 2; i++) {
    double f = (i * SAMPLE_RATE) / SAMPLES;

    // A-weighting formula
    double ra = pow(12200.0, 2) * pow(f, 4);
    double rb = (f * f + 20.6 * 20.6);
    double rc = sqrt((f * f + 107.7 * 107.7) * (f * f + 737.9 * 737.9));
    double rd = (f * f + 12200.0 * 12200.0);

    double A = ra / (rb * rc * rd);
    double A_weight = 20.0 * log10(A) + 2.0;  // +2 dB normalization

    double weighted = vReal[i] * pow(10, A_weight / 20.0);
    sumSquaresA += weighted * weighted;
  }

  // ---- 5. Convert to dB(A) ----
  double rmsA = sqrt(sumSquaresA / (SAMPLES / 2));
  double dBA = 20.0 * log10(rmsA + 1);

  // ---- 6. Output both ----
  Serial.print("Relative dB: ");
  Serial.print(relativeDB);
  Serial.print("    |    dB(A): ");
  Serial.println(dBA);

  delay(5000); // print every 5 seconds
}