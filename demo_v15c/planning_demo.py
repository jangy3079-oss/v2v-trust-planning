"""Demo-only policy/subclasses; upstream Config/weights/constraints untouched."""
import sys
from pathlib import Path
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'v2v_trust_planning/src'))
import math
import numpy as np
from v2v_trust_planning.planning import OvertakeStateMachine,OvertakeState,TrustAwareNMPC,ObstacleTrajectory,dynamic_safety_margin
from vehicle import L,LAG,EGO_RAMP as RAMP   # ego reference is sized to the ego plant limit

# Lane geometry (3 lanes, centre lane = home lane y = 0) and reference feasibility margin.
LANE_W=3.5;NUDGE=.5;LAT_NORMAL=4.
ETA=.6        # reference uses at most 60% of the plant yaw-accel / a_lat budget, leaving room for feedback
S_MIN=20.
ESCAPE_TTC=4.   # lane change allowed from stage 2 (TTC<=4) whenever the lane is free
CLEAR_Y=1.8+.3  # lateral centre distance at which the bodies no longer overlap (+0.3 m margin)
GAP_MIN=2.      # bumper gap kept while the lateral escape completes [m]
# Normal-driving car following (ACC-like), independent of trust. Synthetic settings (user request 2026-10-29).
ACC_HEADWAY=1.5 # desired time gap [s]
ACC_STANDSTILL=5.  # desired bumper gap at standstill [m]
ACC_CLOSE_T=2.  # time constant for closing the gap error [s]
ACC_RANGE=100.  # leader search range ahead [m]
ACC_V_TAU=.5    # leader-speed low-pass time constant [s] (v_meas is noisy)
ACC_DECEL=-3.5  # deceleration limit of car following alone; stronger braking comes only from the avoidance policy [m/s²]

def _quintic(y0,p0,q0,y1,S):
    """Coefficients of y(s) with y,y',y'' = (y0,p0,q0) at s=0 and (y1,0,0) at s=S."""
    c0,c1,c2=y0,p0,q0/2
    A=np.array([[S**3,S**4,S**5],[3*S**2,4*S**3,5*S**4],[6*S,12*S**2,20*S**3]])
    b=np.array([y1-c0-c1*S-c2*S*S,-c1-2*c2*S,-2*c2])
    return np.array([c0,c1,c2,*np.linalg.solve(A,b)])

HOME=0.   # ego starts in the centre lane and returns there

def snap_lateral(y):
    """Nearest allowed lateral target: a lane centre or a lane centre ± NUDGE, never on a lane line or off the road."""
    allowed=[c+o for c in (-LANE_W,0.,LANE_W) for o in (-NUDGE,0.,NUDGE)]
    return float(min(allowed,key=lambda a:abs(a-y)))

class DemoStateMachine(OvertakeStateMachine):
    def __init__(self,v_set=25.):
        self.v_set=v_set
        super().__init__()
        self.reset()
    def reset(self):
        super().reset()
        self.current_v_cmd=self.v_set
        self.t=0.;self.clear_s=0.;self.stage=0;self.engaged=False;self.kappa=0.;self.side=-1.;self.need_decel=0.;self.v_lead=None;self.v_follow=float('inf');self.v_cmd_out=self.v_set
        self.segment=None;self.target_y=0.;self.target_latched=False;self.ov=None
        self.first_start=self.first_complete=self.first_return=self.return_complete=None
        self.done_s=self.home_s=0.
    def set_target(self,target,ego,curvature=0.,v_design=None,override=False,force=False):
        """Distance-parameterized quintic y(x) from the current state to a lane-anchored target.
        Length grows until the plant can follow it at design speed (yaw accel ≤ ETA·RAMP, a_lat ≤ ETA·4).
        override=True writes a temporary planner (NMPC) segment on top of the policy's own segment."""
        target=snap_lateral(target)
        if not (force or override) and self.segment is not None and abs(target-self.target_y)<1e-9 and self.ov is None:return
        x0,y0,th=float(ego[0]),float(ego[1]),float(ego[2])
        p0=math.tan(th);q0=float(curvature)*(1+p0*p0)**1.5;d=target-y0
        v=max(float(ego[3]) if v_design is None else v_design,1.)
        S=max(S_MIN,(60*abs(d)*v*v/(ETA*RAMP))**(1/3))
        for _ in range(60):
            c=_quintic(y0,p0,q0,target,S)
            s=np.linspace(0,S,200)
            yaw_acc=np.abs(6*c[3]+24*c[4]*s+60*c[5]*s*s).max()*v*v   # v²·y''' ≈ dr/dt [rad/s²]
            a_lat=np.abs(2*c[2]+6*c[3]*s+12*c[4]*s*s+20*c[5]*s**3).max()*v*v
            if yaw_acc<=ETA*RAMP and a_lat<=ETA*LAT_NORMAL:break
            S*=1.1
        if override:self.ov=((x0,S,c),target)
        else:self.segment=(x0,S,c);self.target_y=target;self.ov=None
    @property
    def active_target(self):return self.target_y if self.ov is None else self.ov[1]
    def ref_at(self,x,base=False):
        """(y, dy/dx, d²y/dx²) of the tracked lateral reference at x (base=True: the policy's own, ignoring overrides)."""
        seg,tgt=(self.segment,self.target_y) if (base or self.ov is None) else self.ov
        if seg is None:return tgt,0.,0.
        x0,S,c=seg;s=min(S,max(0.,x-x0))
        if s>=S:return tgt,0.,0.
        y=sum(c[i]*s**i for i in range(6))
        dy=sum(i*c[i]*s**(i-1) for i in range(1,6))
        ddy=sum(i*(i-1)*c[i]*s**(i-2) for i in range(2,6))
        return float(y),float(dy),float(ddy)
    def _lane_free(self,y_lane,nb,dx):
        return abs(y_lane)<=LANE_W and not (abs(nb['y']-y_lane)<(LANE_W+1.8)/2 and abs(dx)<60)
    def _time_to_clear(self,ego,nb):
        """Time until the tracked lateral reference puts the ego body CLEAR_Y beside the neighbour (inf: never)."""
        x=float(ego[0]);v=max(float(ego[3]),1.)
        if abs(ego[1]-nb['y'])>=CLEAR_Y:return 0.
        for i in range(1,81):                       # 8 s look-ahead in .1 s steps along the reference
            if abs(self.ref_at(x+v*.1*i)[0]-nb['y'])>=CLEAR_Y:return .1*i+LAG
        return float('inf')
    @staticmethod
    def _required_decel(gap,closing,t_clear):
        """Smallest constant ego deceleration keeping the bumper gap >= GAP_MIN until t_clear (actuator lag included)."""
        if closing<=0:return 0.
        g=gap-closing*LAG-GAP_MIN
        if g<=0:return 8.
        stop=closing*closing/(2*g)                 # closing speed driven to 0 before contact
        if not math.isfinite(t_clear):return stop
        if t_clear<=0:return 0.
        d=2*(closing*t_clear-g)/(t_clear*t_clear)  # gap reaches GAP_MIN exactly at t_clear
        if d<=0:return 0.
        return d if closing-d*t_clear>=0 else stop
    def _other_lane(self,ego,nb):
        settled=abs(ego[1]-self.target_y)<=.3 and abs(self.target_y-HOME)>NUDGE
        return settled and abs(round(ego[1]/LANE_W)-round(nb['y']/LANE_W))>=1
    def update(self,ego,nb,tau,dt=.1,curvature=0.):
        self.t+=dt;self.kappa=curvature
        dx=nb['x']-ego[0];dy=nb['y']-ego[1]
        exv=ego[3]*np.cos(ego[2]);nxv=nb['v']*np.cos(nb['psi'])
        nvy=nb['v']*np.sin(nb['psi']);evy=ego[3]*np.sin(ego[2])
        rel_y=nvy-evy;pred_dy=dy+4*rel_y
        corridor=min(abs(dy),abs(pred_dy))<=1.8 or dy*pred_dy<=0
        front=dx>=0;closing=(exv-nxv) if front else (nxv-exv)
        gap=max(0.,abs(dx)-4.5)
        ttc=gap/closing if closing>1e-6 else float('inf')
        lat_closing=-np.sign(dy)*rel_y
        lat_ttc=max(0.,abs(dy)-1.8)/lat_closing if lat_closing>1e-6 else float('inf')
        lateral_near=abs(dx)<=9.5+max(0.,closing)*4
        if lateral_near:ttc=min(ttc,lat_ttc)
        if front and abs(dy)<=1.8 and gap<5:ttc=min(ttc,2.)
        approach=closing>1e-6 or (lateral_near and lat_closing>1e-6) or (front and gap<5 and abs(dy)<=1.8)
        risk=bool(tau<=.7 and abs(dx)<=60 and corridor and approach)
        self.stage=(3 if ttc<=2 else 2 if ttc<=4 else 1) if risk else 0
        behind_only=not front
        desired_target=None
        if risk and self.state!=OvertakeState.AVOIDING:
            self.state=OvertakeState.AVOIDING;self.engaged=True;self.target_latched=False
            self.done_s=0.
            if self.first_start is None:self.first_start=self.t
        if self.state==OvertakeState.AVOIDING:
            if not self.target_latched:
                # Away from the neighbour; nudge stays inside the home lane.
                self.side=-1. if nb['y']>=HOME else 1.
                self.set_target(HOME+self.side*NUDGE,ego,self.kappa,ego[3])
                self.target_latched=True
            # Coupled manoeuvre: as soon as TTC<=4 and an adjacent lane is free, change lanes (braking below only buys
            # the time the lateral move needs). The opposite side is tried only while ego has not moved yet.
            if risk and ttc<=ESCAPE_TTC and abs(self.target_y-HOME)<=NUDGE:
                sides=(self.side,-self.side) if abs(ego[1]-HOME)<.2 else (self.side,)
                for sg in sides:
                    if self._lane_free(HOME+sg*LANE_W,nb,dx):
                        self.side=sg;self.set_target(HOME+sg*LANE_W,ego,self.kappa,ego[3]);break
            safe=gap>=5 or max(0.,abs(dy)-1.8)>=.5
            self.clear_s=self.clear_s+dt if tau>.7 else 0.
            passed=dx<-9.5 and closing<=0
            departed=dx>60 or abs(nb['y'])>6.15
            if safe and (self.clear_s>=2-1e-9 or passed or departed):
                self.state=OvertakeState.RETURNING;self.engaged=False
                self.set_target(HOME,ego,self.kappa,max(ego[3],self.v_set));self.stage=0
                if self.first_return is None:self.first_return=self.t
        if self.state==OvertakeState.AVOIDING:
            done=abs(ego[1]-self.active_target)<=.1 and abs(ego[2])<=np.deg2rad(5)
            self.done_s=self.done_s+dt if done else 0.
            if self.done_s>=1-1e-9 and self.first_complete is None:self.first_complete=self.t
        if self.state==OvertakeState.RETURNING:
            home=abs(ego[1]-HOME)<=.1 and abs(ego[2])<=np.deg2rad(5) and abs(ego[3]-min(self.v_set,self.v_follow))<=.5   # behind a slower leader, its speed
            self.home_s=self.home_s+dt if home else 0.
            if self.home_s>=1-1e-9:
                self.state=OvertakeState.NORMAL;self.target_latched=False
                if self.return_complete is None:self.return_complete=self.t
        brake=0.
        cap=-8. if self.stage==3 else (-5. if self.stage==2 else 0.)
        clear=abs(ego[1]-nb['y'])>=CLEAR_Y   # ego body already beside the neighbour's body (+margin)
        if risk and not behind_only:
            if abs(dy)>1.8:brake=cap        # neighbour still beside ego's body (side intrusion): stage braking
            else:
                need=self._required_decel(gap,closing,self._time_to_clear(ego,nb))
                if need>0:need+=max(0.,-float(nb.get('a',0.)))   # neighbour braking adds to the closing rate
                brake=max(cap,-need) if need>0 else 0.
        self.need_decel=-brake
        if brake<0:
            goal=max(0.,ego[3]+brake*4)
            self.current_v_cmd=max(goal,min(self.current_v_cmd,ego[3])+brake*dt)
        elif risk and not clear:self.current_v_cmd=min(self.current_v_cmd,ego[3])
        elif self.state!=OvertakeState.AVOIDING or clear or self._other_lane(ego,nb):
            # Speed recovers once the threat is gone or ego is laterally clear of the neighbour.
            self.current_v_cmd+=float(np.clip(self.v_set-self.current_v_cmd,-.5*dt,.5*dt))
        # Car following: cap the speed command so ego keeps ACC_HEADWAY behind a leader in its own path.
        self.v_follow=float('inf')
        if 0<dx<=ACC_RANGE and abs(dy)<1.8:
            a_f=-math.expm1(-dt/ACC_V_TAU)
            self.v_lead=nb['v'] if self.v_lead is None else self.v_lead+a_f*(nb['v']-self.v_lead)
            desired=ACC_STANDSTILL+ACC_HEADWAY*ego[3]
            self.v_follow=max(0.,self.v_lead+(gap-desired)/ACC_CLOSE_T)
        else:self.v_lead=None
        # Output-only cap: the policy's own speed command is not lowered, so speed comes back as soon as the gap opens.
        self.v_cmd_out=min(self.current_v_cmd,self.v_follow)
        return dict(state=self.state.value,stage=self.stage,danger=risk,ttc=float(ttc),closing=float(closing),
                    rear_only=behind_only,brake=brake,target_y=self.target_y,v_ref=self.v_cmd_out,
                    emergency=self.stage==3,engaged=self.engaged,v_follow=self.v_follow if math.isfinite(self.v_follow) else None)

class DemoNMPC(TrustAwareNMPC):
    """Only reference/fallback adapter. Optimizer build and Config stay upstream."""
    def solve_demo(self,ego,nb,policy):
        c=self.cfg
        ref=np.zeros((4,self.N+1))
        for k in range(self.N+1):
            v=policy.v_cmd_out;xk=ego[0]+v*k*self.dt
            yr,dy,_=policy.ref_at(xk,base=True)
            ref[:,k]=[xk,yr,math.atan(dy),v]
        x,y,psi,v,r=nb['x'],nb['y'],nb['psi'],nb['v'],nb['r']
        trajectory=np.zeros((2,self.N))
        for k in range(self.N):
            x+=v*np.cos(psi)*self.dt;y+=v*np.sin(psi)*self.dt;psi+=r*self.dt
            trajectory[:,k]=[x,y]
        closing=(ego[3]-nb['v']) if nb['x']>=ego[0] else (nb['v']-ego[3])
        margin=dynamic_safety_margin(nb['tau'],closing,c.d0_m,c.beta_base,c.beta_speed)
        obs=ObstacleTrajectory('nb',trajectory,margin,nb['tau'])
        fb=self.fallback_count
        u,xpred=super().solve(ego,ref,[obs],target_velocity=policy.v_cmd_out)
        if self.fallback_count>fb:
            u=np.array([-3.,float('nan')])   # steering left to the shared path tracker
        return u,xpred,margin
