"""Infer closing contact and regulate a small position bias, without changing servo protection."""
import math


class GraspHold:
    def __init__(self,contact_load=180,hold_load=120):
        self.contact_load,self.hold_load = contact_load,hold_load
        self.reset()

    def reset(self):
        self.holding = False
        self.closed_endpoint = False
        self.since = self.anchor = self.target = self.request = self.unloaded = None
        self.load = 0.

    @property
    def contacted(self):
        return self.holding or self.closed_endpoint

    def regulate(self,desired,measured,low,high,dt):
        # Regulate only after a loaded stall has been observed. Limiting force
        # during normal travel can create the very stall we are trying to detect.
        adjustment = 100*dt if self.load>self.hold_load+25 else -30*dt if self.load<self.hold_load-25 else 0
        self.target = min(high,max(low,desired,measured-12,min(measured+4,self.target+adjustment)))
        return self.target

    def update(self,desired,target,previous,measured,load,limits,dt,now):
        low,high = limits
        if not all(math.isfinite(v) for v in (desired,target,previous,measured,load,dt,now,low,high)):
            raise ValueError('Invalid grasp telemetry')
        self.load = abs(load)
        # Physical closing is decreasing raw position; reverse only changes input mapping.
        if self.request is not None and (desired>self.request+8 or desired>measured+8):
            self.reset()
            # Discard the old closing lead immediately on an opening request.
            return min(high,max(low,measured+max(0,target-measured)))
        if self.contacted:
            self.request = min(self.request,desired)
            if self.load<self.hold_load*.25:
                if self.unloaded is None: self.unloaded = now
                if now-self.unloaded>=.4:
                    self.reset()
                    return measured
            else: self.unloaded = None
            return self.regulate(desired,measured,low,high,dt)
        # Include the closed endpoint: a slightly inaccurate endpoint must not
        # leave the servo pushing forever, even with only a few ticks of error.
        # Elsewhere, normal settling within 12 ticks of the requested aperture
        # must not latch a grip and block the user's next closing request.
        closing = (desired<measured-12 or desired<=low+2) and target<=measured+2
        if not closing or self.load<self.contact_load:
            self.since = self.anchor = self.target = self.request = self.unloaded = None
            return target
        # Keep the full travel command while the encoder is making progress.
        # Normal acceleration and gearbox friction can exceed contact_load.
        # Observe 120 ms of loaded, nearly stationary jaws BEFORE changing the
        # target; otherwise pressure relief can produce a false self-latching grip.
        if self.since is None or abs(measured-self.anchor)>12:
            self.since,self.anchor = now,measured
        elif now-self.since>=.12:
            self.closed_endpoint = measured<=low+20
            self.holding = not self.closed_endpoint
            self.request = desired
            self.target = max(low,desired,measured-4)
            return self.target
        return target

    def snapshot(self):
        return dict(holding=self.holding,load=self.load,
                    detail='Holding object (inferred from closing load and stopped motion).' if self.holding
                    else 'Closed endpoint reached; relieving closing pressure.' if self.closed_endpoint
                    else 'Checking jaw movement before confirming contact.' if self.since is not None
                    else 'Contact detection ready; opening releases the grasp.')
