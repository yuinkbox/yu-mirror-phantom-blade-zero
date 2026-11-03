"""Short-horizon trajectory prediction; all coordinates are Unreal centimetres."""
import math


def _sphere(a,b,center,radius):
    d=tuple(b[i]-a[i] for i in range(3))
    p=tuple(a[i]-center[i] for i in range(3))
    c=sum(x*x for x in p)-radius*radius
    if c<=0:return 0.
    aa=sum(x*x for x in d)
    bb=sum(p[i]*d[i] for i in range(3))
    disc=bb*bb-aa*c
    if aa<1e-12 or disc<0:return None
    t=(-bb-math.sqrt(disc))/aa
    return t if 0<=t<=1 else None


def _cylinder(a,b,radius,low,high):
    dx,dy,dz=(b[i]-a[i] for i in range(3))
    aa=dx*dx+dy*dy;bb=a[0]*dx+a[1]*dy;c=a[0]*a[0]+a[1]*a[1]-radius*radius
    if aa<1e-12:
        if c>0:return None
        enter,leave=0.,1.
    else:
        disc=bb*bb-aa*c
        if disc<0:return None
        root=math.sqrt(disc)
        enter,leave=max(0,(-bb-root)/aa),min(1,(-bb+root)/aa)
    if abs(dz)<1e-12:
        if not low<=a[2]<=high:return None
    else:
        z0,z1=sorted(((low-a[2])/dz,(high-a[2])/dz))
        enter,leave=max(enter,z0),min(leave,z1)
    return enter if enter<=leave else None


def incoming_time(position, velocity, player, player_velocity=(0,0,0), gravity=0,
                  radius=75, half_height=95, explosive=False, horizon=.9):
    values=(*position,*velocity,*player,*player_velocity,gravity,radius,half_height)
    if not all(math.isfinite(v) for v in values) or radius<=0 or half_height<radius:
        return None
    if not 30 <= math.sqrt(sum(v*v for v in velocity)) <= 100000:
        return None
    r=tuple(position[i]-player[i] for i in range(3))
    v=tuple(velocity[i]-player_velocity[i] for i in range(3))
    if sum(r[i]*v[i] for i in range(3))>=0 and not explosive:
        return None
    previous=0.;a=r
    for step in range(1,int(horizon/.01)+1):
        t=step*.01
        x,y,z=(r[i]+v[i]*t+(gravity*t*t*.5 if i==2 else 0) for i in range(3))
        b=(x,y,z);h=half_height-radius
        hits=[_sphere(a,b,(0,0,side*h),radius) for side in (-1,1)]
        hits.append(_cylinder(a,b,radius,-h,h))
        # Molotov warning envelope around the player's feet, not a claimed blast radius.
        if explosive and velocity[2]+gravity*t<0:
            hits.append(_cylinder(a,b,220,-half_height-90,-half_height+90))
        hits=[hit for hit in hits if hit is not None]
        if hits:return previous+.01*min(hits)
        a=b
        previous=t
    return None
