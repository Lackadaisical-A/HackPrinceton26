"""SO-101 direction retargeting in forward/left/up coordinates.

Read the actual URDF transforms to establish its straight-forward pose. Scale
human segment directions to robot lengths and solve the resulting wrist reach.
This five-axis robot cannot reproduce every human shoulder and wrist pose.
"""
import math
import xml.etree.ElementTree as ET

from so101_sim.mapper import multiply
from wearable_test.model import add, scale, dot, cross, unit, rotate


def axis_q(axis, angle):
    return (math.cos(angle/2), *scale(unit(axis),math.sin(angle/2)))


def rpy_q(rpy):
    r,p,y = rpy
    return multiply(multiply(axis_q((0,0,1),y),axis_q((0,1,0),p)),axis_q((1,0,0),r))


def nearest_angle(angle, previous):
    return previous+(angle-previous+math.pi)%(2*math.pi)-math.pi


class Kinematics:
    def __init__(self, urdf):
        tree = ET.parse(urdf)
        self.joints = []
        self.limits = {}
        for element in tree.findall("joint"):
            origin = element.find("origin")
            xyz = tuple(map(float,origin.get("xyz","0 0 0").split())) if origin is not None else (0,0,0)
            rpy = tuple(map(float,origin.get("rpy","0 0 0").split())) if origin is not None else (0,0,0)
            axis = element.find("axis")
            joint = dict(name=element.get("name"),parent=element.find("parent").get("link"),
                         child=element.find("child").get("link"),type=element.get("type"),xyz=xyz,q=rpy_q(rpy),
                         axis=tuple(map(float,axis.get("xyz").split())) if axis is not None else (0,0,1))
            self.joints.append(joint)
            if joint["type"] == "revolute":
                limit = element.find("limit")
                self.limits[joint["name"]] = (float(limit.get("lower")),float(limit.get("upper")))
        self.root = (set(j["parent"] for j in self.joints)-set(j["child"] for j in self.joints)).pop()

    def frames(self, values):
        links = {self.root:((0,0,0),(1,0,0,0))}
        result, pending = {}, list(self.joints)
        while pending:
            before = len(pending)
            for joint in pending[:]:
                if joint["parent"] not in links: continue
                parent_pos,parent_q = links[joint["parent"]]
                pos = add(parent_pos,rotate(parent_q,joint["xyz"]))
                q = multiply(parent_q,joint["q"])
                if joint["type"] != "fixed":
                    q = multiply(q,axis_q(joint["axis"],values.get(joint["name"],0)))
                links[joint["child"]] = result[joint["name"]] = (pos,q)
                pending.remove(joint)
            if len(pending) == before: raise ValueError("Invalid URDF joint tree")
        return result

    def directions(self, values):
        frame = self.frames(values)
        upper = unit(add(frame["elbow_flex"][0],scale(frame["shoulder_lift"][0],-1)))
        forearm = unit(add(frame["wrist_flex"][0],scale(frame["elbow_flex"][0],-1)))
        wrist_q = frame["wrist_roll"][1]
        return {"upper":upper,"forearm":forearm,"hand":rotate(wrist_q,(0,0,-1)),"palm_up":rotate(wrist_q,(1,0,0))}

    def forward_pose(self):
        values = dict.fromkeys(self.limits,0.)
        # In this URDF pan's world axis is -Z, the three flex axes are +Y,
        # and wrist roll's axis is opposite the tool's forward direction.
        for joint,segment in (("shoulder_lift","upper"),("elbow_flex","forearm"),("wrist_flex","hand")):
            direction = self.directions(values)[segment]
            values[joint] += math.atan2(direction[2],direction[0])
        up = self.directions(values)["palm_up"]
        values["wrist_roll"] = math.atan2(-up[1],up[2])
        for name,value in values.items():
            low,high = self.limits[name]
            if not low <= value <= high: raise ValueError(f"Forward pose exceeds {name} URDF limits")
        return values


class Retargeter:
    def __init__(self, urdf, wrist_roll_gain=2.):
        if not math.isfinite(wrist_roll_gain) or not .25<=wrist_roll_gain<=4:
            raise ValueError('Wrist roll gain must be .25..4')
        self.wrist_roll_gain = wrist_roll_gain
        self.kinematics = Kinematics(urdf)
        self.limits = self.kinematics.limits
        self.neutral = self.kinematics.forward_pose()
        frames = self.kinematics.frames(self.neutral)
        length = lambda a,b: math.sqrt(dot(add(a,scale(b,-1)),add(a,scale(b,-1))))
        self.upper_length = length(frames["elbow_flex"][0],frames["shoulder_lift"][0])
        self.forearm_length = length(frames["wrist_flex"][0],frames["elbow_flex"][0])
        low,high = self.limits["elbow_flex"]
        self.bend_sign = -1 if high-self.neutral["elbow_flex"] >= self.neutral["elbow_flex"]-low else 1
        self.solution = dict(self.neutral)
        self.previous = dict(heading=0.,pan_heading=0.,hand=0.,roll=0.)
        self.previous_upper = 0.
        self.limited = []

    def clamp(self, name, value):
        low,high = self.limits[name]
        if not low <= value <= high: self.limited.append(name)
        return max(low,min(high,value))

    def targets(self, geometry, reverse):
        self.limited = []
        bones = geometry["bases"]
        reach = add(scale(bones["upper"][0],self.upper_length),scale(bones["forearm"][0],self.forearm_length))
        # A dangling bicep has little useful heading. Shoulder-to-wrist reach
        # still responds to left/right forearm movement in that common pose.
        heading = self.previous["heading"]
        if math.hypot(reach[0],reach[1]) > .01:
            heading = nearest_angle(math.atan2(reach[1],reach[0]),heading)
            self.previous["heading"] = heading
        ranges = geometry.get('motion_ranges',{})
        learned_pan = all(f'pan_{side}_deg' in ranges for side in ('left','right'))
        pan = max(self.limits['shoulder_pan'][0],min(self.limits['shoulder_pan'][1],-heading))
        # The human reach plane drives flexion/roll math. Pan amplification must
        # not project the reach into the amplified robot plane and distort those joints.
        plane = (math.cos(-pan),math.sin(-pan),0.)
        if learned_pan:
            measured_heading = geometry.get('reach_heading_deg',math.degrees(heading))
            pan_heading = self.previous['pan_heading']
            if measured_heading is not None:
                pan_heading = nearest_angle(math.radians(measured_heading),pan_heading)
                self.previous['pan_heading'] = pan_heading
            low,high = self.limits['shoulder_pan']
            endpoint = ranges['pan_left_deg'] if pan_heading>=0 else ranges['pan_right_deg']
            fraction = abs(math.degrees(pan_heading))/endpoint
            pan = self.neutral['shoulder_pan']+fraction*((low if pan_heading>=0 else high)-self.neutral['shoulder_pan'])
        else: pan = -heading
        pan = self.clamp('shoulder_pan',pan)
        elevations = {}
        for role in ("hand",):
            direction = bones[role][0]
            horizontal = dot(direction,plane)
            value = self.previous[role]
            if math.hypot(horizontal,direction[2]) > .05:
                value = nearest_angle(math.atan2(direction[2],horizontal),value)
                self.previous[role] = value
            elevations[role] = value
        n = self.neutral
        # Solve the wrist reach using this robot's link lengths. Human elbow
        # flexion can demand the mechanically impossible bend direction. Keep
        # the branch with the wider URDF elbow range; changing branches as the
        # user bends the elbow creates sudden shoulder/elbow jumps.
        radial,height = dot(reach,plane),reach[2]
        a,b = self.upper_length,self.forearm_length
        bend = math.acos(max(-1.,min(1.,(radial*radial+height*height-a*a-b*b)/(2*a*b))))
        relative = self.bend_sign*bend
        upper = math.atan2(height,radial)-math.atan2(b*math.sin(relative),a+b*math.cos(relative))
        upper = nearest_angle(upper,self.previous_upper)
        self.previous_upper = upper
        fore = upper+relative
        hand = elevations["hand"]
        learned_shoulder = 'shoulder_max_deg' in ranges
        learned_elbow = 'elbow_max_deg' in ranges
        shoulder = n['shoulder_lift']-upper
        if learned_shoulder:
            # Keep pose A's forward reference. Each side of that reference
            # independently reaches the robot's corresponding hard endpoint.
            direction = bones['upper'][0]
            elevation = math.degrees(math.atan2(direction[2],math.hypot(*direction[:2])))
            low,high = self.limits['shoulder_lift']
            endpoint = ranges['shoulder_max_deg'] if elevation>=0 else ranges.get('shoulder_min_deg',-90.)
            fraction = max(0.,min(1.,elevation/endpoint))
            shoulder = n['shoulder_lift']+fraction*((low if elevation>=0 else high)-n['shoulder_lift'])
        shoulder = self.clamp('shoulder_lift',shoulder)
        actual_upper = n["shoulder_lift"]-shoulder
        elbow_delta = actual_upper-fore
        elbow = n['elbow_flex']+elbow_delta
        if learned_elbow:
            # Bend uses the angle between calibrated limb directions, so a
            # shoulder movement alone cannot consume the elbow's travel.
            bend_degrees = math.degrees(math.acos(max(-1.,min(1.,dot(bones['upper'][0],bones['forearm'][0])))))
            low,high = self.limits['elbow_flex']
            fraction = max(0.,min(1.,bend_degrees/ranges['elbow_max_deg']))
            elbow = low+fraction*(high-low)
        elbow = self.clamp('elbow_flex',elbow)
        actual_fore = actual_upper+n["elbow_flex"]-elbow
        wrist_delta = actual_fore-hand
        wrist = self.clamp("wrist_flex",n["wrist_flex"]+wrist_delta)
        actual_hand = actual_fore+n["wrist_flex"]-wrist
        forward = (plane[0]*math.cos(actual_hand),plane[1]*math.cos(actual_hand),math.sin(actual_hand))
        side = (-plane[1],plane[0],0.)
        up = cross(forward,side)
        palm = bones["hand"][2]
        projected = add(palm,scale(forward,-dot(palm,forward)))
        roll = self.previous["roll"]
        if dot(projected,projected) > .01:
            roll = nearest_angle(math.atan2(dot(forward,cross(up,projected)),dot(up,projected)),roll)
            self.previous["roll"] = roll
        wrist_roll = self.clamp("wrist_roll",n["wrist_roll"]-self.wrist_roll_gain*roll)
        self.solution = dict(shoulder_pan=pan,shoulder_lift=shoulder,elbow_flex=elbow,wrist_flex=wrist,wrist_roll=wrist_roll)
        centers = {**n}
        for name,learned in (('shoulder_lift',learned_shoulder),('elbow_flex',learned_elbow)):
            if learned: centers[name] = sum(self.limits[name])/2
        target = {name:self.clamp(name,2*centers[name]-value) if reverse[name] else value
                  for name,value in self.solution.items()}
        self.limited = list(dict.fromkeys(self.limited))
        return target
