#!/usr/bin/python3
import serial
import re
from time import sleep
import argparse
from datetime import datetime

import pyaudio
import wave
import threading
from threading import Timer
import time

import signal
import sys

# ✅ FIXED: Proper Ctrl+C handling
def handle_sigint(sig, frame):
    global run
    print("\nStopping data collection...")
    run = False

signal.signal(signal.SIGINT, handle_sigint)

PORT='COM20'
BAUDRATE=2400
TIMEOUT=0.1
STRFTIME='%Y-%m-%dT%H:%M:%S.%f'

FILE_SAVE_DIRECTORY='data/'

buffer = []

run = False
savingFile = False
savingTimer = False

SAVE_AUDIO = True
LEVEL_THRESHOLD = 80
sample_format = pyaudio.paInt16
channels = 1
fs = 44100
chunk = 1024
audioDuration = 3
AUDIO_HW_ID = -1


##########################################
def subbits(byte,mask,r_shift):
    return (byte & mask) >> r_shift

def is_maxhold(ctrl):
    maxhold_bits = subbits(ctrl,0b00110000,4)
    if maxhold_bits == 0b10:
        return True
    elif maxhold_bits == 0b01:
        return False
    return None

def modetxt(ctrl):
    if subbits(ctrl,0b00001100,2) == 0b10:
        slowmode = subbits(ctrl,0b00000010,1) == 0b1
        basedon_minutes = subbits(ctrl,0b00000001,0) == 0b1
        Leq_mode = True
    else:
        slowmode = subbits(ctrl,0b00000001,0) == 0b1
        basedon_minutes = None
        Leq_mode = False

    non_Leq_modes={
            0b000: 'Lp_(dB),Weighting_A',
            0b001: 'Lp_(dB),Weighting_C',
            0b010: 'Lp_(dB),Flat',
            0b011: 'Ln_(%),Weighting_A',
            0b101: 'Unknown',
            0b110: 'Cal_(dB)'
            }
    if Leq_mode:
        txt = 'Leq_(dB),Weighting_A'
        if basedon_minutes:
            txt+=',based_on_minutes'
        else:
            txt+=',based_on_10s'
    else:
        txt = non_Leq_modes[subbits(ctrl,0b00001110,1)]

    if slowmode:
        txt+=',Slow'
    else:
        txt+=',Fast'

    if is_maxhold(ctrl):
        txt+=',MaxHold'
    return txt

def chkchksum(msg):
    if len(msg)<=2: return False
    return int(msg[-1]) == (sum(x for x in msg[:-1]) % 256)

def decode_msg(msg):
    m = re.match(b'^\x08\x04(?P<ctrl>.)\x0a\x0a(?P<value>...)\x01$', msg[:-1])
    if not m:
        return None

    d=m.groupdict()
    try:
        val="%0.1f" % (d['value'][0]*10+d['value'][1]+d['value'][2]/10)
    except:
        val=None

    return (val,modetxt(ord(d['ctrl'])))

def trySerialOpen(port, maxTries):
    if (maxTries <=0):
        print('Port cannot be opened, giving up')
        return -1
    try:
        port.open()
    except:
        print('Could not open the serial port, retrying in 5 seconds...')
        sleep(5)
        trySerialOpen(port, maxTries-1)

def sensorThread():
    global run, savingFile, savingTimer

    logFilename = 'log_' + datetime.now().strftime('%Y-%m-%dT%H-%M-%S') + '.txt'
    csvFile = open(FILE_SAVE_DIRECTORY + logFilename, "a")

    ser = serial.Serial()
    ser.baudrate = BAUDRATE
    ser.port = PORT
    ser.timeout = TIMEOUT

    trySerialOpen(ser, 100)
    print("Serial port opened " + str(PORT))

    while run:
        char = ser.read()

        if char==b'\x10':
            ser.write(b'\x20')
        elif char==b'':
            continue
        else:
            sleep(1)
            continue

        msg=bytes()
        while True:
            char = ser.read()
            if char == b'':
                break
            msg += char

        if len(msg)<1:
            continue

        if not chkchksum(msg):
            print("# chksum error, msg: "+str(msg))
            continue

        dt=datetime.now().strftime(STRFTIME)
        try:
            val,msg = decode_msg(msg)
            csvLine = dt + ',' + val + ',' + msg
            print(csvLine)
            csvFile.write(csvLine + '\n')
            csvFile.flush()
        except:
            print("Decode error")

        if SAVE_AUDIO and val and float(val) > LEVEL_THRESHOLD:
            print("LOUD SOUND!")
            if savingTimer or savingFile:
                savingTimer.cancel()
            savingTimer = Timer(audioDuration, audioFileSaveThread)
            savingTimer.start()

    csvFile.close()

def audioRecordThread():
    global run, buffer

    while run:
        audiodata = stream.read(chunk, exception_on_overflow=False)
        buffer.append(audiodata)

        totalSamples = (len(buffer) * chunk)
        if totalSamples > (audioDuration * fs) and not savingFile and not savingTimer:
            oldChunks = len(buffer) - round((audioDuration * fs) / chunk)
            del buffer[:oldChunks]

def audioFileSaveThread():
    global savingFile, savingTimer, buffer

    if not savingFile:
        savingFile = True
        filename = 'audio_' + datetime.now().strftime('%Y-%m-%dT%H-%M-%S') + '.wav'
        fileBuffer = buffer.copy()

        wf = wave.open(FILE_SAVE_DIRECTORY + filename, 'wb')
        wf.setnchannels(channels)
        wf.setsampwidth(portAudio.get_sample_size(sample_format))
        wf.setframerate(fs)
        wf.writeframes(b''.join(fileBuffer))
        wf.close()

        savingFile = False
        savingTimer = False
        print("Saved:", filename)

##########################################

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Sauter SU logger')
    parser.add_argument('-p', '--serialport', default=PORT)
    parser.add_argument('-f', '--datafolder', default=FILE_SAVE_DIRECTORY)
    parser.add_argument('-s', '--saveaudio', action="store_true", default=SAVE_AUDIO)
    parser.add_argument('-l', '--levelthreshold', type=int, default=LEVEL_THRESHOLD)
    parser.add_argument('-i', '--audiohwid', type=int, default=AUDIO_HW_ID)

    args=parser.parse_args()

    PORT = args.serialport
    FILE_SAVE_DIRECTORY = args.datafolder
    SAVE_AUDIO = args.saveaudio
    LEVEL_THRESHOLD = args.levelthreshold
    AUDIO_HW_ID = args.audiohwid

    print('Starting...')

    if SAVE_AUDIO:
        portAudio = pyaudio.PyAudio()
        stream = portAudio.open(format=sample_format,
                                channels=channels,
                                rate=fs,
                                frames_per_buffer=chunk,
                                input=True)

    run = True

    if SAVE_AUDIO:
        rt = threading.Thread(target=audioRecordThread, daemon=True)
        rt.start()

    st = threading.Thread(target=sensorThread, daemon=True)
    st.start()

    print("Running... Press Ctrl+C to stop")

    while run:
        time.sleep(0.5)

    print("Stopping...")

    if SAVE_AUDIO:
        stream.stop_stream()
        stream.close()
        portAudio.terminate()

    print("Done")