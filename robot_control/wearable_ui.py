"""Operator controls; every servo operation runs in WearableRobot's worker."""
import tkinter as tk
from tkinter import ttk
import time

from robot_control.arm import JOINTS
from robot_control.tuning_ui import TuningPanel


class PhysicalPanel:
    def __init__(self,parent,worker,on_stop):
        self.worker,self.closed = worker,False
        self.on_stop,self.tuning_panel = on_stop,None
        self.refresh_after = None
        self.window = tk.Toplevel(parent)
        self.window.title(f"Physical SO-101 — {worker.port}")
        self.window.geometry("1050x610")
        self.window.configure(bg="#0b1220")
        self.window.protocol("WM_DELETE_WINDOW",self.close)
        self.window.bind("<Escape>",lambda event:on_stop())
        body = ttk.Frame(self.window,padding=18)
        body.pack(fill="both",expand=True)
        ttk.Label(body,text=f"PHYSICAL SO-101 / {worker.port}",font=("Segoe UI",16,"bold")).pack(anchor="w")
        ttk.Label(body,text="Connection is read-only until you enable control.",padding=(0,8)).pack(anchor="w")
        buttons = ttk.Frame(body)
        buttons.pack(fill="x",pady=5)
        absolute = worker.config['mapping_mode']=='absolute'
        self.arm_button = ttk.Button(buttons,text="Align & follow URDF" if absolute else "Enable arm · relative",command=lambda:worker.request("enable_arm"))
        self.arm_button.pack(side="left",padx=(0,6))
        self.claw_button = ttk.Button(buttons,text="Enable claw",command=lambda:worker.request("enable_claw"))
        self.claw_button.pack(side="left",padx=(0,6))
        ttk.Button(buttons,text="STOP / HOLD  [Esc]",command=on_stop).pack(side="left")
        ttk.Button(buttons,text="Live tuning...",command=self.open_tuning).pack(side="right")
        self.reconnect_button = ttk.Button(buttons,text='Reconnect robot',command=self.reconnect)
        self.reconnect_button.pack(side='right',padx=6)
        self.status = tk.StringVar(value="Connecting…")
        ttk.Label(body,textvariable=self.status,wraplength=750,foreground="#ffcb70",padding=(0,10)).pack(fill="x")
        self.state = tk.StringVar()
        ttk.Label(body,textvariable=self.state,wraplength=1000).pack(anchor="w")
        self.detail = tk.StringVar()
        ttk.Label(body,textvariable=self.detail,wraplength=750,foreground="#ffcb70").pack(anchor="w",pady=(5,0))
        self.alignment_detail = tk.StringVar()
        ttk.Label(body,textvariable=self.alignment_detail,wraplength=1000).pack(anchor='w')
        self.replay_detail = tk.StringVar()
        ttk.Label(body,textvariable=self.replay_detail,wraplength=1000).pack(anchor='w')
        self.table = ttk.Treeview(body,columns=("position","desired","target","gap","limit","load","torque"),show="tree headings",height=6)
        self.table.heading("#0",text="Joint")
        self.table.column("#0",width=150)
        for key,label in (("position","Actual pose"),("desired","Preview pose"),("target","Sent raw"),
                          ("gap","Request gap °"),("limit","Limiting"),("load","Load counts"),("torque","Torque")):
            self.table.heading(key,text=label)
            self.table.column(key,width=115,anchor="center")
        for name in JOINTS: self.table.insert("","end",iid=name,text=name.replace("_"," "))
        self.table.pack(fill="x",pady=10)
        ttk.Label(body,text="1. Recenter/calibrate in Wearable Lab. Verify the URDF follows you.\n"
                           f"2. Align & follow moves toward the captured preview at {worker.config['alignment_deg_s']:g}°/s.\n"
                           "3. Hold your arm still until LIVE, then make a small movement. Claw + haptics turn on with the arm (center the joystick first).\n"
                           "Preview and Actual show matching URDF joint angles (claw stays in raw counts). Sent = motor command.\n"
                           "Base = reach heading; shoulder/elbow = learned ranges or reach IK; wrist = hand orientation.\n"
                           f"Wrist roll {worker.retarget.wrist_roll_gain:g}x; trim {worker.joint_mapping.trim_deg['wrist_roll']:g}° CCW viewed from behind the wrist.\n"
                           "RATE = speed cap; LEAD = motor lags; RANGE = requested pose exceeds saved travel.\n"
                           "Stops hold position with torque on. Resets require manual re-enable.\n"
                           "Joystick: double-click starts/finishes recording; one click returns to the first pose and replays.\n"
                           "Hold joystick 0.7s to stop; during replay any press stops. PAUL has the same replay controls.\n"
                           "Keep the power switch reachable: USB loss prevents a new hold command.",
                  wraplength=1000,justify="left").pack(anchor="w")
        self.refresh()

    def refresh(self):
        if self.closed: return
        state = self.worker.snapshot()
        fresh = state["seen"] is not None and time.monotonic()-state["seen"]<.5
        self.status.set(state["message"])
        arm_mode = 'ALIGNING' if state['aligning'] else 'LIVE' if state['enabled'] else 'PAUSED' if state['message'].startswith('PAUSED') else 'OFF'
        self.state.set(f"Arm {arm_mode} · Claw {'ENABLED' if state['claw_enabled'] else 'OFF'} · "
                       f"Telemetry {'LIVE' if fresh else 'waiting'} · {state['loop_hz']:.0f} Hz · reads {state['read_ms']:.1f} ms · "
                       f"{state['tuning']['speed_deg_s']:g}°/s max · Claw {self.worker.claw.speed:g} travel/s")
        self.state.set(self.state.get()+f" · Load stops {'ON' if state['controls']['load_stops_enabled'] else 'OFF'} · Slider {'ON' if state['slider']['enabled'] else 'OFF'}")
        limits = ', '.join(n.replace('_',' ') for n in state['limited'])
        self.detail.set("Saved joint range reached: "+limits if limits else state['input_status'])
        if state['grasp']['holding']: self.detail.set(f"Holding object | load {state['grasp']['load']:g} counts | open claw to release")
        self.alignment_detail.set(state['alignment_progress'])
        replay = state['replay']
        self.replay_detail.set(f"Replay {replay['phase']} | {replay['duration_s']:.1f}s recorded | {replay['detail']}")
        ready = fresh and state["connected"] and state["can_enable"]
        self.reconnect_button.configure(state='normal' if state['can_reconnect'] else 'disabled')
        self.arm_button.configure(state="normal" if ready and not state["enabled"] else "disabled")
        self.claw_button.configure(state="normal" if ready and (not state["claw_enabled"] or state['grip_hold_active']) else "disabled")
        for name in JOINTS:
            desired = state['desired'].get(name)
            actual = state['positions'].get(name)
            match = state['matching'].get(name,{})
            self.table.item(name,values=(f"{match['actual_deg']:+.1f}°" if match else state['positions'].get(name,'—'),
                            f"{match['preview_deg']:+.1f}°" if match.get('preview_deg') is not None else '—',
                            round(state["targets"][name]) if name in state["targets"] else "—",
                            f'{(desired-actual)*360/4095:+.1f}' if desired is not None and actual is not None else '—',
                            state['flags'].get(name,''),
                            state["loads"].get(name,"—"),"ON" if state["torque"].get(name) else "off"))
        self.refresh_after = self.window.after(100,self.refresh)

    def open_tuning(self):
        if self.tuning_panel is None or self.tuning_panel.closed:
            self.tuning_panel = TuningPanel(self.window,self.worker,self.on_stop)
        self.tuning_panel.window.lift()

    def reconnect(self):
        try: self.worker.reconnect()
        except ValueError as error: self.status.set(str(error))

    def close(self):
        if self.closed: return
        self.closed = True
        self.worker.pause("physical controls closed")
        if self.refresh_after is not None: self.window.after_cancel(self.refresh_after)
        if self.tuning_panel is not None: self.tuning_panel.close()
        self.window.destroy()
