# sauter_logger

A simple data logger example program
for [Sauter SU 130](https://www.sauter.eu/shop/en/measuring-instruments/occupational-safety-environment/SU/) (and maybe others).
The 2.5mm jack exposes a RS232 which can be connected to any USB to UART adapter (for example those based on the PL2303 chip).


## To start it

Clone the repo locally. Then create [a virtual environment](https://packaging.python.org/en/latest/guides/installing-using-pip-and-virtual-environments/#using-a-requirements-file)

```
python3 -m venv .venv
```

Activate the virtual environment:
```
source .venv/bin/activate
```

Install dependencies:
```
python3 -m pip install -r requirements.txt
```

Run the script:
```
python3 measure.py --serialport /dev/ttyUSB0 --datafolder /mnt/usb1 --levelthreshold 85
```
Change the parameters as you see fit.

When done, deactivate the virtual environment:
```
deactivate
```

## Protocol
The device runs at 2400baud and sends 0x10 around twice a second (which indicates that a new measurement is available). Each time you answer with 0x20, the device responds with a message containing the mode (Lp, Lq,.. fast/slow,..) and the value.

On the SU 130, the message is 0x08 0x04 MODE 0x0A 0x0A V1 V2 V3 0x01 CHECKSUM

Look into the code for further details.
