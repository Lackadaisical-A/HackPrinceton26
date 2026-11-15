"""Bounded alignment completion with settling, per-joint progress and a timeout."""
import math


class Alignment:
    TICKS_PER_DEGREE = 4095/360

    def __init__(self,target,measured,limits,now,config):
        self.target = {n:max(limits[n][0],min(limits[n][1],v)) for n,v in target.items()}
        self.started = now
        self.config = config
        self.settled_since = None
        self.last_progress = dict.fromkeys(target,now)
        self.best_error = self.errors(measured)
        self.detail = ''

    def errors(self,measured):
        errors = {n:abs(v-measured[n])/self.TICKS_PER_DEGREE for n,v in self.target.items()}
        if not all(math.isfinite(v) for v in errors.values()): raise ValueError('Invalid alignment feedback')
        return errors

    def update(self,measured,now):
        errors = self.errors(measured)
        worst = max(errors,key=errors.get)
        tolerance = self.config['alignment_tolerance_deg']
        elapsed = now-self.started
        self.detail = f'Alignment {elapsed:.1f}s · largest gap: {worst.replace("_"," ")} {errors[worst]:.1f}° (tolerance {tolerance:g}°)'
        if all(v<=tolerance for v in errors.values()):
            if self.settled_since is None: self.settled_since = now
            if now-self.settled_since>=self.config['alignment_settle_s']: return True
        else:
            self.settled_since = None
        if elapsed>=self.config['alignment_timeout_s']:
            raise ValueError(f'Alignment timed out: {worst} is {errors[worst]:.1f}° short; inspect the joint before re-enabling')
        for name,error in errors.items():
            if error<self.best_error[name]-.5 or error<=tolerance:
                self.best_error[name] = error
                self.last_progress[name] = now
            if now-self.last_progress[name]>=self.config['alignment_stall_s']:
                raise ValueError(f'Alignment stalled: {name} is {error:.1f}° short with no progress; inspect the joint before re-enabling')
        return False
