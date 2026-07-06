# IoT Urban Noise Monitoring and Classification

A low cost IoT based urban noise monitoring system developed as part of a Master's Thesis in Computer Science (Internet of Things) at Malmö University.

The project combines embedded machine learning, environmental sensing, and LoRaWAN communication to continuously monitor urban noise levels while classifying the dominant sound source directly on the device.

Instead of transmitting raw audio, the device performs on edge inference and only sends compact metadata consisting of sound level, sound classification, timestamp, and GPS coordinates. This significantly reduces bandwidth usage, improves privacy, and lowers power consumption.

---

## Project Overview

Environmental noise pollution is a growing public health concern. Traditional monitoring stations are expensive and sparsely deployed, making it difficult to understand how noise varies throughout a city.

This project demonstrates a scalable and affordable alternative using low cost hardware and embedded AI.

The system:

- Measures environmental sound pressure levels (dB(A))
- Performs on device sound classification
- Sends results over LoRaWAN
- Stores data for visualization and analysis
- Operates inside a custom weather resistant 3D printed enclosure

The prototype was designed and evaluated for deployment in Malmö, Sweden.

---

## Features

- Real time sound level monitoring
- Embedded machine learning using Edge Impulse
- On device audio classification
- LoRaWAN communication
- GPS location reporting
- Low power operation
- Weather resistant enclosure
- Compact binary payload transmission
- Privacy preserving design by avoiding raw audio transmission

---

## Hardware

The prototype consists of:

| Component | Description |
|-----------|-------------|
| Adafruit Feather RP2040 RFM | Main microcontroller with integrated LoRa radio |
| SPH8878LR5H-1 MEMS Microphone | Environmental sound acquisition |
| 868 MHz LoRaWAN Antenna | Extended communication range |
| 3.7V LiPo Battery | Portable power supply |
| Custom 3D Printed Enclosure | Outdoor protection |

---

## System Architecture

The system is divided into three layers:

```
MEMS Microphone
        │
        ▼
Adafruit Feather RP2040
        │
        ├── Measure dB(A)
        ├── Edge Impulse Classification
        └── Package Metadata
                │
                ▼
          LoRaWAN Network
                │
                ▼
      The Things Network (TTN)
                │
                ▼
         MQTT Subscriber
                │
                ▼
        Data Storage / Analysis
```

---

## Machine Learning

The sound classifier was built using Edge Impulse.

### Feature Extraction

- Mel Frequency Energy (MFE)
- 16 kHz sampling
- 5 second inference window
- Embedded neural network

### Training Data

The model was trained using a combination of:

- ESC-50
- UrbanSound8K
- Human Screaming Dataset
- Custom field recordings collected in Malmö

The final model classifies urban sounds such as:

- Traffic
- Construction
- Weather
- Human activity
- Animals
- Emergency sirens

---

## Data Pipeline

1. Capture environmental audio
2. Calculate A weighted sound pressure level
3. Extract MFE features
4. Perform embedded inference
5. Generate classification label
6. Package metadata into binary payload
7. Transmit via LoRaWAN
8. Decode payload on server
9. Store results for later analysis

Only metadata is transmitted.

No raw audio is stored or uploaded.

---

## Payload Structure

| Byte(s) | Content |
|----------|----------|
| 0 | dB(A) |
| 1 | Hour |
| 2 | Minute |
| 3 | Second |
| 4 | Packet Sequence Number |
| 5–8 | Latitude |
| 9–12 | Longitude |
| 13 | Classification Label |

---

## Repository Structure

```
.
├── firmware/
│   ├── Arduino source
│   ├── Edge Impulse model
│   └── LoRaWAN implementation
│
├── server/
│   ├── MQTT subscriber
│   ├── Payload decoder
│   └── Data storage
│
├── enclosure/
│   ├── STL files
│   └── CAD models
│
├── datasets/
│
├── images/
│
└── README.md
```

---

## Technologies Used

- Arduino
- C++
- Python
- Edge Impulse
- LoRaWAN
- The Things Network (TTN)
- MQTT
- SolidWorks
- 3D Printing

---

## Evaluation

The prototype was evaluated in several areas:

- MEMS microphone calibration
- dB(A) measurement accuracy
- Acoustic impact of enclosure
- Embedded classification performance
- LoRaWAN transmission reliability
- Battery operation
- Outdoor deployment feasibility

---

## Privacy

To improve privacy and reduce network traffic:

- Audio is processed locally.
- Raw recordings are discarded after inference.
- Only classification labels, sound levels, timestamps, and coordinates are transmitted.

---

## Future Improvements

Possible future work includes:

- Solar powered deployment
- Larger scale sensor networks
- Improved battery life
- More robust sound classification
- Additional environmental sensors
- Noise heat map visualization
- Cloud dashboard
- Over the air firmware updates

---

## Thesis

This repository accompanies the Master's Thesis:

**A Low-Cost IoT-Based System for Continuous Urban Noise Classification & Monitoring**

Computer Science – Internet of Things

Malmö University

2026

Authors:

- Christian Edvall
- Peter Ha Zu

Supervisor:

- Ali Soleimani

---

## License

This project is intended for research and educational purposes.
