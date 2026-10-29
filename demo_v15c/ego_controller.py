"""Single maneuver mode (combined lateral + longitudinal avoidance), two engines. 합성·직선·시뮬레이션."""
import sys,time
from pathlib import Path
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parent))
import numpy as np
from ego_controller_preavoid import Config,gaps,ttc,closing_speed,CAR_L,CAR_W,TAU_ALARM
from planning_demo import DemoStateMachine,DemoNMPC,snap_lateral,ACC_DECEL
from vehicle import L,EGO_RAMP as RAMP,LAG
import math
TRACK_W,TRACK_Z=.4,1.   # tracker bandwidth [rad/s] and damping (lateral-error closed loop)
REAR_ESCAPE_A=3.           # rear-only threat at TTC<=4: accelerate at the plant limit while changing lanes
NMPC_HYST=.25           # NMPC lateral goal change needed to re-plan the reference [m]
NMPC_Y_MAX=3.5+.5       # adjacent lane centre + in-lane nudge; body stays on the road (|Y|+0.9 < 5.25)
BACKENDS=('combined','rule','nmpc')   # 'rule'/'nmpc' = combined with that engine (CLI compatibility)
MODES=('combined',)
SOLVERS=('rule','nmpc')
_CACHE={}

class EgoController:
    def __init__(self,backend='combined',v_set=25.,cfg=None,solver='rule'):
        self.cfg=cfg or Config();self.v_set=v_set;self.solver=solver
        self.set_backend(backend)
    def set_backend(self,backend,ego=None):
        if backend in SOLVERS:self.solver=backend;backend='combined'
        assert backend in MODES
        self.backend=backend;self.policy=DemoStateMachine(self.v_set)
        self.impl=self;self.x_pred=None;self.nmpc=None
        if self.solver=='nmpc':
            if 'nmpc' not in _CACHE:_CACHE['nmpc']=DemoNMPC(self.cfg,max_obstacles=1)
            self.nmpc=_CACHE['nmpc'];self.nmpc.reset()
        self.info=dict(state='NORMAL',stage=0,solve_ms=0.,fallback=0,fallback_now=False,emergency=False,command_clips=0)
        self.engaged=False;self.r_cmd=0.;self.delta_applied=0.
    def reset(self,ego=None):self.set_backend(self.backend,ego)
    def next_solver(self,ego=None):
        self.solver=SOLVERS[(SOLVERS.index(self.solver)+1)%2];self.set_backend(self.backend,ego)
    def update_applied(self,a,delta):
        self.delta_applied=float(delta)
        if self.nmpc is not None:self.nmpc.last_u=np.array([a,delta])
    def track(self,ego):
        """Path tracker on the lane-anchored reference y(x), sized to the shared plant limits.
        Feedforward = reference curvature previewed by the actuator lag; feedback = 2nd-order lateral error dynamics
        (ω_n = TRACK_W, ζ = TRACK_Z); the yaw-rate command is rate-limited to the plant's RAMP so it never winds up
        against the plant's steering-rate limit."""
        x,y,th,v=map(float,ego);v=max(v,1.)
        yr,dy,_=self.policy.ref_at(x)
        _,dyp,ddy=self.policy.ref_at(x+v*LAG)
        r_ff=v*ddy/(1+dyp*dyp)**1.5
        ey=y-yr;eth=math.atan2(math.sin(th-math.atan(dy)),math.cos(th-math.atan(dy)))
        r=r_ff-TRACK_W**2/v*ey-2*TRACK_Z*TRACK_W*eth
        step=RAMP*.1
        r=float(np.clip(r,self.r_cmd-step,self.r_cmd+step));self.r_cmd=r
        return float(np.arctan(L*r/v))
    def nmpc_goal(self,ego):
        """NMPC as planner: its end-of-horizon lateral position becomes the goal of the same feasible reference
        generator (hysteresis NMPC_HYST, road-clipped), so the plant only ever sees executable references."""
        if self.x_pred is None:return
        pol=self.policy;kappa=np.tan(self.delta_applied)/L
        dev=float(self.x_pred[1,-1]-pol.ref_at(self.x_pred[0,-1],base=True)[0])   # NMPC's departure from the policy path
        if abs(dev)>NMPC_HYST:
            goal=float(np.clip(pol.target_y+dev,-NMPC_Y_MAX,NMPC_Y_MAX))
            if abs(snap_lateral(goal)-pol.active_target)>1e-9:pol.set_target(goal,ego,kappa,ego[3],override=True)
        elif pol.ov is not None:
            pol.set_target(pol.target_y,ego,kappa,ego[3],force=True)   # NMPC agrees again → back to the policy target
    def step(self,ego,nb,tau,alarm):
        ego=np.asarray(ego,float)
        inf=self.policy.update(ego,nb,tau,curvature=np.tan(self.delta_applied)/L)
        self.engaged=inf['engaged']
        vref=self.policy.v_cmd_out
        steer=self.track(ego)
        a=float(np.clip(.5*(vref-ego[3]),-5,1.5))
        ms=0.;fb=False;margin=5.
        if self.nmpc is not None:
            before=self.nmpc.fallback_count;t=time.perf_counter()
            u,self.x_pred,margin=self.nmpc.solve_demo(ego,dict(nb,tau=tau),self.policy)
            ms=(time.perf_counter()-t)*1000;fb=self.nmpc.fallback_count>before
            a=float(u[0])
            if not fb:self.nmpc_goal(ego)
            steer=self.track(ego)
        if not inf['danger']:a=max(a,ACC_DECEL)   # car following alone brakes at most ACC_DECEL
        if inf['danger']:
            if not inf['rear_only']:a=min(a,inf['brake'])
            else:a=REAR_ESCAPE_A if inf['stage']>=2 else 0. # no braking for a rear-only threat; pull away instead
        alat_limit=8. if inf['emergency'] else 4.
        lim=np.arctan(L*alat_limit/max(ego[3]**2,1.))
        d=float(np.clip(steer,-lim,lim))
        self.info=dict(inf,sm=inf['state'],solve_ms=ms,fallback=self.nmpc.fallback_count if self.nmpc else 0,
                       fallback_now=fb,d_safe=margin,raw_a=a,raw_delta=steer,
                       command_clips=int(abs(d-steer)>1e-12),target_y=self.policy.active_target)
        return float(np.clip(a,-8.,3.)),d
    def yaw_rate(self,steer,v):return v*np.tan(steer)/L
