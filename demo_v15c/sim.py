"""Common plant for ego/manual neighbor; sensor path unchanged. 합성·직선·시뮬레이션."""
import sys
from pathlib import Path
sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import numpy as np
from scipy.signal import sosfilt,sosfilt_zi
import sim_preavoid as OLD
from vehicle import Vehicle,L,body_gap,EGO_RAMP
G=OLD.G
DT,W,HALF,N_BG=OLD.DT,OLD.W,OLD.HALF,OLD.N_BG
A_MAX,B_MAX,A_RAMP_T=OLD.A_MAX,OLD.B_MAX,OLD.A_RAMP_T
R_RAMP,R_MAX,R_REL,T_PSI=OLD.R_RAMP,OLD.R_MAX,OLD.R_REL,OLD.T_PSI
DRIFT_LEVELS,STUCK_LEVELS,WEAVE_LEVELS=OLD.DRIFT_LEVELS,OLD.STUCK_LEVELS,OLD.WEAVE_LEVELS
ACCEL_A,ACCEL_R,ACCEL_HOLD=OLD.ACCEL_A,OLD.ACCEL_R,OLD.ACCEL_HOLD
BRAKE_A,BRAKE_T,DRIFT_R,WEAVE_R=OLD.BRAKE_A,OLD.BRAKE_T,OLD.DRIFT_R,OLD.WEAVE_R

class KeyboardController:
    def __init__(self):
        self.a=self.target=self.start=self.r_cmd=0.;self.k=0
    def command(self,keys,heading,lat_on=False,heading_ref=0.,yaw_ff=0.):
        target=A_MAX if 'W' in keys and 'S' not in keys else (-B_MAX if 'S' in keys else 0.)
        if target!=self.target:self.target,self.start,self.k=target,self.a,0
        self.k+=1;self.a=self.start+(self.target-self.start)*min(1.,self.k*DT/A_RAMP_T)
        turn=0 if lat_on else int('A' in keys)-int('D' in keys)
        if turn:self.r_cmd=float(np.clip(self.r_cmd+turn*R_RAMP*DT,-R_MAX,R_MAX))
        else:self.r_cmd*=np.exp(-DT/R_REL)
        return self.a,self.r_cmd-(heading-heading_ref)/T_PSI+yaw_ff

class Sim(OLD.Sim):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.ego_vehicle=Vehicle(self.ego,ramp=EGO_RAMP)
        self.neighbor_vehicle=Vehicle([self.x,self.yk+self.bg_y[0],0.,self.v])
        self.keyboard=KeyboardController()
        self.bg_heading_prev=self.inj_heading_prev=0.
        self.body_min=float('inf');self.body_events=0;self._hit=False
        self.frame_gap=float('inf');self.frame_hit=False;self.frame_events=0
    def y_true(self):
        if hasattr(self,'neighbor_vehicle'):return float(self.neighbor_vehicle.state[1])
        return super().y_true()
    def clear_injection(self):
        if self.inj is None:return
        self.inj=None;self.v_set=self.v;self.y_inj=self.vy_inj=0.;self.inj_heading_prev=0.
    def step_ego(self,ego_u):
        self.ego=self.ego_vehicle.step(*(ego_u or (0.,0.)),DT)
    def step(self,keys=(),ego_u=None):
        self.k+=1;k=self.k;kb=k%N_BG
        nb=self.neighbor_vehicle
        self.x,self.yk,self.psi,self.v=map(float,nb.state)
        lat_on=self.inj is not None and self.inj['kind'] not in ('brake','accel')
        bg_vy=(self.bg_y[kb]-self.bg_y[max(0,kb-1)])/DT
        bg_heading=float(np.arctan2(bg_vy,max(self.v,1.)))
        bg_r=(bg_heading-self.bg_heading_prev)/DT;self.bg_heading_prev=bg_heading
        tt=(k-self.inj['k0'])*DT if self.inj else 0.
        inj_h=0.;a_inj=0.
        if self.inj:
            kind=self.inj['kind'];sg=self.inj['sign'];level=self.inj['level']
            if kind=='brake':
                if tt<BRAKE_T:a_inj=BRAKE_A
                else:self.clear_injection()
            elif kind=='accel':
                amp,ramp=level;j=k-self.inj['k0'];nn=int(round(ramp/DT))
                if j<nn:a_inj=amp*(j+1)/nn
                elif j<nn+ACCEL_HOLD:a_inj=amp
                else:self.clear_injection()
            elif kind=='drift':inj_h=float(np.arctan2(sg*level*G.smoothstep(tt/DRIFT_R),max(self.v,1.)))
            elif kind=='stuck':inj_h=sg*level*tt
            elif kind=='weave':
                amp,period=level
                def target(t):return G.smoothstep(max(t,0)/WEAVE_R)*amp*np.sin(2*np.pi*t/period)
                vy=(target(tt)-target(tt-DT))/DT
                inj_h=float(np.arctan2(vy,max(self.v,1.)))
        inj_r=(inj_h-self.inj_heading_prev)/DT;self.inj_heading_prev=inj_h
        a_user,r_cmd=self.keyboard.command(keys,self.psi,lat_on,bg_heading+inj_h,bg_r+inj_r)
        self.a_user,self.r_cmd=a_user,self.keyboard.r_cmd
        if abs(a_user)>.05 or a_inj!=0:self.v_set=self.v
        ac=float(self.bg_a[kb]-G.K_V*(self.v-self.v_set)+a_user+a_inj)
        delta=float(np.arctan(L*r_cmd/max(self.v,1.)))
        nb.step(ac,delta,DT)
        self.step_ego(ego_u)
        self.x,self.yk,self.psi,self.v=map(float,nb.state)
        self.a=nb.a;y=self.yk;r=nb.r+self.bg_rx[kb]
        if self.inj and lat_on and self.inj['cross_k'] is None and abs(y-self.inj['lane_c'])>=HALF:self.inj['cross_k']=k
        v_meas=self.v+G.SIG_V_MEAS*self.rng[0].normal()
        if self.zi is None:
            z0=sosfilt_zi(G.A2_SOS)*v_meas;self.zi=[z0.copy(),z0.copy()]
        x1,self.zi[0]=sosfilt(G.A2_SOS,[v_meas],zi=self.zi[0]);x2,self.zi[1]=sosfilt(G.A2_SOS,x1,zi=self.zi[1])
        a_obs=0. if self.x2_prev is None else (x2[0]-self.x2_prev)/DT;self.x2_prev=x2[0]
        r_obs=r+G.SIG_N_YAW*self.rng[1].normal();yo=y+self.sigma_meas*self.rng[2].normal()
        self.frame_gap=float('inf');self.frame_hit=False;self.frame_events=0
        for e,b in zip(self.ego_vehicle.trace,nb.trace):
            gap,hit=body_gap(e['state'],b['state'])
            self.frame_gap=min(self.frame_gap,gap);self.frame_hit|=hit
            if hit and not self._hit:self.body_events+=1;self.frame_events+=1
            self._hit=hit
        self.body_min=min(self.body_min,self.frame_gap)
        return dict(k=k,t=k*DT,x=self.x,y=y,d_true=y-W*np.round(y/W),psi=self.psi,v=self.v,a=self.a,r=r,
                    a_obs=float(a_obs),r_obs=float(r_obs),d_obs=float(yo-W*np.round(yo/W)),v_meas=float(v_meas),y_obs=float(yo),
                    turn_signal=self.turn_signal,x_ego=self.ego[0],ego=self.ego.copy(),inj=None if self.inj is None else dict(self.inj),
                    ego_applied=dict(self.ego_vehicle.last),body_gap=self.frame_gap,body_hit=self.frame_hit,
                    body_events=self.body_events,nb_command=(ac,delta))
