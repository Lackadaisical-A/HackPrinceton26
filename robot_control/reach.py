"""Small Cartesian steps of the gripper tip from forward kinematics alone. No hardware access."""
import math

DIRECTIONS = ('left','right','forward','back','up','down')


def direction_vector(tip,direction,distance):
    """A step in the arm's own terms: forward is away from the pan axis, left is counter-clockwise seen from above."""
    if direction not in DIRECTIONS: raise ValueError('Unknown nudge direction')
    reach = math.hypot(tip[0],tip[1])
    rx,ry = (tip[0]/reach,tip[1]/reach) if reach>1e-3 else (1.,0.)
    return {'forward':(rx*distance,ry*distance,0.),'back':(-rx*distance,-ry*distance,0.),
            'left':(-ry*distance,rx*distance,0.),'right':(ry*distance,-rx*distance,0.),
            'up':(0.,0.,distance),'down':(0.,0.,-distance)}[direction]


def solve3(a,b):
    det = (a[0][0]*(a[1][1]*a[2][2]-a[1][2]*a[2][1])-a[0][1]*(a[1][0]*a[2][2]-a[1][2]*a[2][0])
           +a[0][2]*(a[1][0]*a[2][1]-a[1][1]*a[2][0]))
    if abs(det)<1e-18: raise ValueError('Nudge is not possible from this pose')
    result = []
    for column in range(3):
        m = [[b[r] if c==column else a[r][c] for c in range(3)] for r in range(3)]
        result.append((m[0][0]*(m[1][1]*m[2][2]-m[1][2]*m[2][1])-m[0][1]*(m[1][0]*m[2][2]-m[1][2]*m[2][0])
                       +m[0][2]*(m[1][0]*m[2][1]-m[1][1]*m[2][0]))/det)
    return result


def step_joints(fk,angles,delta,names,limits=None,max_joint_step=math.radians(15),tolerance=.005):
    """Joint angles (radians) that move fk(angles) by `delta` metres, changing only `names`.

    Damped least squares on a numerical Jacobian, so it needs nothing but forward kinematics.
    Raises ValueError when the step is out of reach or would need a large joint move.
    """
    start = fk(angles)
    goal = [start[i]+delta[i] for i in range(3)]
    q = dict(angles)
    for _ in range(8):
        here = fk(q)
        error = [goal[i]-here[i] for i in range(3)]
        if math.sqrt(sum(e*e for e in error))<tolerance/5: break
        columns = []
        for name in names:
            moved = fk({**q,name:q[name]+1e-4})
            columns.append([(moved[i]-here[i])/1e-4 for i in range(3)])
        a = [[sum(c[r]*c[k] for c in columns)+(1e-6 if r==k else 0.) for k in range(3)] for r in range(3)]
        y = solve3(a,error)
        for name,column in zip(names,columns):
            q[name] += sum(column[i]*y[i] for i in range(3))
            if limits is not None: q[name] = max(limits[name][0],min(limits[name][1],q[name]))
    here = fk(q)
    if math.sqrt(sum((goal[i]-here[i])**2 for i in range(3)))>tolerance:
        raise ValueError('Nudge is out of reach from this pose')
    if any(abs(q[name]-angles[name])>max_joint_step for name in names):
        raise ValueError('Nudge would need too large a joint move')
    return q
