# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["pyserial==3.5", "feetech-servo-sdk==1.0.0", "draccus==0.10.0", "deepdiff==8.6.2", "numpy>=2,<3", "tqdm>=4"]
# ///
"""Read-only COM timing and servo settings; close the controller first."""
import argparse
from pathlib import Path
import statistics
import sys
import threading
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from robot_control.arm import Arm,JOINTS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port",default="COM6")
    parser.add_argument("--samples",type=int,default=60)
    args = parser.parse_args()
    if not 5<=args.samples<=500: parser.error("samples must be 5..500")
    arm = Arm(args.port)
    try:
        arm.connect()
        def legacy():
            return {n:arm.bus.sync_read(n,list(JOINTS),normalize=False,num_retry=0)
                    for n in ("Present_Position","Present_Load","Torque_Enable")}
        waiter = threading.Event()
        for label,period,wait in (("USB reads only",0,None),
                                  ("Old loop (50 ms Event.wait)",.05,waiter.wait),
                                  ("New loop (20 ms sleep)",.02,time.sleep)):
            times = []
            for _ in range(args.samples):
                start = time.perf_counter()
                feedback = legacy()
                if wait: wait(max(0,period-(time.perf_counter()-start)))
                times.append(1000*(time.perf_counter()-start))
            ordered = sorted(times)
            print(f"{label}: mean={statistics.mean(times):.1f}ms p95={ordered[int(.95*(len(times)-1))]:.1f}ms "
                  f"max={max(times):.1f}ms capacity={1000/statistics.mean(times):.1f}Hz")
        for field in ("Torque_Enable","Present_Load","Present_Voltage","Present_Temperature","Status"):
            print(f"{field}: {arm.bus.sync_read(field,list(JOINTS),normalize=False,num_retry=0)}")
        for field in ("Goal_Velocity","Goal_Time","Acceleration","Maximum_Acceleration",
                      "CW_Dead_Zone","CCW_Dead_Zone","Torque_Limit","Return_Delay_Time",
                      "Phase","P_Coefficient","Minimum_Startup_Force"):
            print(f"{field}: {arm.bus.sync_read(field,list(JOINTS),normalize=False,num_retry=0)}")
        print("Read-only complete: no position, torque, or configuration writes.")
    finally: arm.close()


if __name__=="__main__": main()
