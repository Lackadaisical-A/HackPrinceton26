# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["pyserial>=3.5", "pyzmq>=26"]
# ///
"""Identify the four vibration outputs with brief pulses; never opens the arm port.

Close Wearable Lab first. An ACK confirms firmware accepted a command, not that
a coin physically vibrated; the wearer verifies the location and sensation.
"""
import argparse
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from robot_control.haptics import MOTORS,NAMES
from wearable_test.io import WearableHub,fresh_frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wroom',default='COM7')
    parser.add_argument('--s3',default='COM8')
    args = parser.parse_args()
    hub = WearableHub(args.wroom,args.s3)
    hub.start()
    try:
        deadline = time.monotonic()+10
        while True:
            snap = hub.snapshot(); now = time.monotonic()
            try:
                fresh_frame(snap,now)
                if any(not b['connected'] or now-b['status_at']>6 or b['fields'].get('DRV2605L')!='1'
                       or b['fields'].get('PWM2')!='1' for b in snap.values()):
                    raise ValueError('Waiting for both boards and all four outputs')
                break
            except ValueError:
                if now>=deadline: raise RuntimeError('Wearable outputs are not ready; close other serial owners and check wiring')
            time.sleep(.05)
        for (board,motor),name in zip(MOTORS,NAMES):
            print(f'{name}: 35% for 0.35 seconds',flush=True)
            hub.motor(board,motor,35,.35)
            deadline = time.monotonic()+.5
            while time.monotonic()<deadline: time.sleep(.02)
            snap=hub.snapshot()
            state=snap[board]['motors'][motor]
            if not state['acks']: raise RuntimeError(f'{name}: no firmware acknowledgment')
            fresh_frame(snap,time.monotonic())
            if state['percent']: raise RuntimeError(f'{name}: pulse did not stop')
            print(f'  acknowledged {state["acks"]} times; output stopped; IMUs still fresh',flush=True)
            time.sleep(.25)
    finally:
        hub.close()


if __name__=='__main__': main()
