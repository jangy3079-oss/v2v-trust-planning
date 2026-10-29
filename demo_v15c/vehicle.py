"""Shared bounded plant. 합성·직선·시뮬레이션."""
import math
import numpy as np

L=2.7
A_MAX, A_MIN, LAT_MAX=3.,-8.,8.
RAMP, LAG, SUB_DT=.05,.3,.01
# Ego-only yaw-acceleration limit [rad/s²] (user decision 2026-10-29). Synthetic setting, not a measured vehicle value.
# The neighbour keeps RAMP (its keyboard ramp), so its motion is unchanged.
EGO_RAMP=.3

class Vehicle:
    def __init__(self,state,ramp=RAMP):
        self.ramp=float(ramp)
        self.state=np.array(state,float)
        self.a=self.delta=self.r=0.
        self.counts=dict(accel_command=0,steer_command=0,steer_rate=0,speed_lat=0)
        self.last={}
        self.trace=[]

    def step(self,a_cmd,delta_cmd,dt=.1):
        a_cmd=float(a_cmd); delta_cmd=float(delta_cmd)
        if not np.isfinite([a_cmd,delta_cmd,dt]).all() or dt<=0: raise ValueError('finite controls and positive dt required')
        n=max(1,int(math.ceil(dt/SUB_DT))); h=dt/n
        ac=float(np.clip(a_cmd,A_MIN,A_MAX))
        clips={k:0 for k in self.counts}
        clips['accel_command']=int(ac!=a_cmd)
        trace=[]; latmax=drmax=rrmax=jmax=slipmax=0.; rate_violation=0.
        for _ in range(n):
            x,y,th,v=self.state
            old_a=self.a; old_d=self.delta
            self.a += (ac-self.a)*(-math.expm1(-h/LAG))
            vn=max(0.,v+self.a*h)
            if vn==0.:self.a=(vn-v)/h
            vg=max(v,vn,1.)
            limit=math.atan(L*LAT_MAX/(vg*vg))
            dc=float(np.clip(delta_cmd,-limit,limit))
            clips['steer_command']+=int(abs(dc-delta_cmd)>1e-12)
            delayed=old_d+(dc-old_d)*(-math.expm1(-h/LAG))
            rate=L*self.ramp/max(v,1.)
            delta=old_d+float(np.clip(delayed-old_d,-rate*h,rate*h))
            clips['steer_rate']+=int(abs(delta-delayed)>1e-12)
            if abs(math.tan(delta))>1e-14:
                vmax=math.sqrt(L*LAT_MAX/abs(math.tan(delta)))
                if vn>vmax+1e-10:
                    # Previous state is feasible; only acceleration can cause this conflict.
                    vn=max(v,min(vn,vmax)); self.a=(vn-v)/h
                    clips['speed_lat']+=1
            vm=(v+vn)/2
            r=vm*math.tan(delta)/L
            mid=th+r*h/2
            nx=x+vm*math.cos(mid)*h; ny=y+vm*math.sin(mid)*h
            self.state=np.array([nx,ny,th+r*h,vn])
            self.delta=delta; self.r=r
            alat=vm*r; dr=(delta-old_d)/h; jerk=(self.a-old_a)/h
            slip=0. if vm<1e-10 else math.atan2(math.sin(math.atan2(ny-y,nx-x)-mid),math.cos(math.atan2(ny-y,nx-x)-mid))
            latmax=max(latmax,abs(alat)); drmax=max(drmax,abs(dr)); rrmax=max(rrmax,abs(r)); jmax=max(jmax,abs(jerk)); slipmax=max(slipmax,abs(slip))
            rate_violation=max(rate_violation,abs(dr)-rate)
            trace.append(dict(state=self.state.copy(),a=self.a,delta=delta,r=r,a_lat=alat,jerk=jerk,delta_rate=dr,rate_bound=rate,slip=slip))
        for k,vv in clips.items():self.counts[k]+=vv
        self.trace=trace
        self.last=dict(a=self.a,delta=self.delta,r=self.r,a_lat=trace[-1]['a_lat'],jerk_max=jmax,
                       a_lat_max=latmax,r_max=rrmax,delta_rate_max=drmax,rate_violation=rate_violation,
                       slip_max_deg=math.degrees(slipmax),clips=clips)
        return self.state.copy()


def corners(s):
    x,y,th=s[:3]; c=math.cos(th); q=math.sin(th)
    return [(x+c*dx-q*dy,y+q*dx+c*dy) for dx,dy in ((2.25,.9),(2.25,-.9),(-2.25,-.9),(-2.25,.9))]


def body_gap(a,b):
    """Oriented-box SAT and exact Euclidean polygon distance (touching counts as collision)."""
    pa,pb=corners(a),corners(b)
    separated=False
    for th in (a[2],b[2]):
        for ax,ay in ((math.cos(th),math.sin(th)),(-math.sin(th),math.cos(th))):
            aa=[x*ax+y*ay for x,y in pa]; bb=[x*ax+y*ay for x,y in pb]
            if max(aa)<min(bb) or max(bb)<min(aa):separated=True
    if not separated:return 0.,True
    best=float('inf')
    for pp,qq in ((pa,pb),(pb,pa)):
        for x,y in pp:
            for j,(x0,y0) in enumerate(qq):
                x1,y1=qq[(j+1)%4]; dx=x1-x0;dy=y1-y0
                t=max(0.,min(1.,((x-x0)*dx+(y-y0)*dy)/(dx*dx+dy*dy)))
                best=min(best,math.hypot(x-x0-t*dx,y-y0-t*dy))
    return best,False
