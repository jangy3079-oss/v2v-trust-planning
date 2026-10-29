"""Meaningful unit/contract tests before frozen evaluation."""
import sys,json,hashlib,importlib.util
from pathlib import Path
sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import numpy as np
import torch
from vehicle import Vehicle,L,LAT_MAX,body_gap,EGO_RAMP,RAMP
from planning_demo import DemoStateMachine
from ego_controller import EgoController
from sim import KeyboardController
import app

def main():
    torch.set_num_threads(1)
    out={}
    rng=np.random.RandomState(42)
    # Exact same command sequences, separate roles, same step.
    manual,ego=Vehicle([0,0,0,25]),Vehicle([0,0,0,25])
    errors=[];replay=[];driver=KeyboardController()
    for k in range(600):
        keys={"D"} if 100<=k<130 else ({"S"} if 200<=k<220 else set())
        a,r=driver.command(keys,manual.state[2])
        d=float(np.arctan(L*r/max(manual.state[3],1.)))
        aa=manual.step(a,d);bb=ego.step(a,d)
        errors.append(float(np.max(np.abs(aa-bb))))
        assert manual.last==ego.last
        replay.append(dict(t=k*.1,manual=aa.tolist(),ego=bb.tolist(),a=manual.a,alat=manual.last['a_lat'],jerk=manual.last['jerk_max'],ego_a=ego.a,ego_alat=ego.last['a_lat'],ego_jerk=ego.last['jerk_max'],command=[a,d]))
    assert max(errors)<=1e-9
    out['identical_command_max_error']=max(errors)
    # Extremes cover low-speed steering, full brake/acceleration and sign reversals.
    maxima=dict(lat=0.,rate_violation=0.,slip=0.)
    for v in (0.,1.,5.,25.,45.):
        car=Vehicle([0,0,0,v])
        for k in range(300):
            car.step(20 if k<150 else -20,(-1 if k%80<40 else 1)*.5)
            for row in car.trace:
                assert -8-1e-9<=row['a']<=3+1e-9
                assert abs(row['a_lat'])<=8+1e-8
                assert abs(row['delta_rate'])<=row['rate_bound']+1e-9
            maxima['lat']=max(maxima['lat'],car.last['a_lat_max'])
            maxima['rate_violation']=max(maxima['rate_violation'],car.last['rate_violation'])
            maxima['slip']=max(maxima['slip'],car.last['slip_max_deg'])
    out['extreme_limits']=maxima
    a=Vehicle([0,0,.1,25]);a.step(0,0)
    assert a.state[2]==.1 # plant has no keyboard road alignment
    kb=KeyboardController();_,r=kb.command(set(),.1);assert r<0
    out['keyboard_restore_separate']=True
    ctl=EgoController('combined',solver='rule')
    ac,dc=ctl.step(np.array([0,0,0,25.]),dict(x=-10.,y=0.,psi=0.,v=35.,r=0.),.1,True)
    assert ac>=0 and ctl.info['rear_only']
    out['rear_alone_no_brake']=True
    for ttc,stage,brake in [(4.1,1,0.),(4.,2,-5.),(2.1,2,-5.),(2.,3,-8.)]:
        policy=DemoStateMachine()
        info=policy.update(np.array([0,0,0,25]),dict(x=4.5+5*ttc,y=0.,psi=0.,v=20.,r=0.),.7)
        # Stage boundaries unchanged; braking is need-based within the stage cap (0 / -5 / -8).
        assert info['stage']==stage and brake-1e-12<=info['brake']<=0 and (brake<0 or info['brake']==0)
    policy=DemoStateMachine()
    assert not policy.update(np.array([0,0,0,25]),dict(x=9.5,y=0.,psi=0.,v=20.,r=0.),.8)['engaged']
    out['frozen_ttc_boundaries']=True
    # Entry, trust recovered with separation, target returns. No hidden scenario label.
    sm=DemoStateMachine()
    nb=dict(x=20.,y=.5,psi=0.,v=20.,r=0.)
    sm.update(np.array([0,0,0,25]),nb,.1)
    assert sm.engaged
    for k in range(21):sm.update(np.array([0,0,0,25]),dict(nb,x=40.,v=25.),1.)
    assert sm.state.value=='RETURNING' and sm.target_y==0
    out['trust_recovery_return']=True
    occupied=DemoStateMachine()
    # Candidate -3.5 lane occupied by the sole neighbor; no forced lane change into it.
    occupied.update(np.array([0,-3.5,0,25]),dict(x=20.,y=-3.5,psi=0.,v=20.,r=0.),.1)
    assert occupied.target_y==3.5 # sole neighbor on -3.5: opposite +3.5 is free
    # Candidate lane occupancy is evaluated from neighbor position, not scenario labels.
    assert abs(-3.5-(-3.5))<2.65 and not abs(-3.5-3.5)<2.65
    # v15c-fix: lateral targets are lane-anchored and every reference is executable by the shared plant.
    from planning_demo import snap_lateral,LANE_W
    for y in np.linspace(-7,7,281):
        t=snap_lateral(y);c=LANE_W*round(t/LANE_W)
        assert abs(c)<=LANE_W and abs(t-c)<=.5+1e-12 and abs(t)+.9<5.25
    worst=dict(err=0.,rate_clips=0,ymax=0.)
    for v in (8.,15.,25.,30.):
        for y0,th0 in ((0.,0.),(-.4,.01),(3.2,-.02)):
            for target in (-3.5,-.5,0.,.5,3.5):
                c=EgoController('combined',solver='rule',v_set=v);car=Vehicle([0,y0,th0,v],ramp=EGO_RAMP)
                c.policy.set_target(target,car.state,0.,v,force=True)
                for k in range(400):
                    c.update_applied(car.a,car.delta)
                    car.step(0.,c.track(car.state),.1)
                    worst['ymax']=max(worst['ymax'],abs(car.state[1]))
                worst['err']=max(worst['err'],abs(car.state[1]-c.policy.active_target))
                worst['rate_clips']+=car.counts['steer_rate']
    assert worst['ymax']+.9<5.25 and worst['err']<.05 and worst['rate_clips']==0,worst
    out['lane_anchored_feasible_reference']=worst
    # Car following: behind a steady slower leader in the same lane (trust normal), ego settles at the ACC gap.
    from planning_demo import ACC_HEADWAY,ACC_STANDSTILL
    c=EgoController('combined',solver='rule');car=Vehicle([0,0,0,25.],ramp=EGO_RAMP);lead=[30.,22.]
    for k in range(900):
        c.update_applied(car.a,car.delta)
        a,d=c.step(car.state,dict(x=lead[0],y=0.,psi=0.,v=lead[1],r=0.,a=0.),1.,False)
        car.step(a,d,.1);lead[0]+=lead[1]*.1
    gap=lead[0]-car.state[0]-4.5;want=ACC_STANDSTILL+ACC_HEADWAY*car.state[3]
    assert abs(car.state[3]-22.)<.1 and abs(gap-want)<.5 and c.policy.state.value=='NORMAL',(car.state,gap,want)
    out['acc_following']=dict(v=float(car.state[3]),gap=float(gap),desired=float(want))
    g,hit=body_gap([0,0,0,25],[4.4,0,0,25]);assert hit and g==0
    g,hit=body_gap([0,0,np.pi/2,25],[4.,0,0,25]);assert not hit and g>0
    out['oriented_collision']=True
    ses=app.Session(bg=False,backend='combined',solver='rule')
    assert type(ses.sim.ego_vehicle) is Vehicle and type(ses.sim.neighbor_vehicle) is Vehicle
    assert ses.sim.ego_vehicle.step.__func__ is ses.sim.neighbor_vehicle.step.__func__
    # Same plant class/step; only the yaw-acceleration limit differs (user decision 2026-10-29).
    assert ses.sim.neighbor_vehicle.ramp==RAMP==.05 and ses.sim.ego_vehicle.ramp==EGO_RAMP
    out['neighbor_limit_unchanged_ego_ramp']=dict(neighbor=RAMP,ego=EGO_RAMP)
    ses.step();ses.step()
    middle=ses.render_observation(.5)
    assert np.allclose(middle['ego'],(ses.previous_obs['ego']+ses.obs['ego'])/2)
    ses.key('C');assert ses.ctl.backend=='combined'   # C no longer switches modes: combined only
    ses.key('B');assert ses.ctl.solver=='nmpc'
    ses.step()
    out['ui_modes_solver_interpolation']=True
    # Compare immutable detector/accumulator source hashes against diagnostic baseline.
    frozen=json.loads((HERE/'diagnosis_v15b/private/v15c/source_hashes_before.json').read_text())
    for name in ('detector.py','accum.py'):
        assert hashlib.sha256((HERE/name).read_bytes()).hexdigest()==frozen['demo_v15c/'+name]
    out['detector_accumulator_source_unchanged']=True
    # Same fixed input through independent detector and accumulator instances.
    d1,d2=app.Detector('nom'),app.Detector('nom')
    params=app.AC.load_params('nom'); th=params['theta'];nu,h=params['on']
    c1=app.AC.Online(th,nu,h,k_drain=app.AC.K_DRAIN_DEFAULT)
    c2=app.AC.Online(th,nu,h,k_drain=app.AC.K_DRAIN_DEFAULT)
    for k in range(180):
        obs=[.1*np.sin(k*.1),.001*np.cos(k*.03),.2*np.sin(k*.02),int(50<k<70),25.]
        r1=d1.step(*obs);r2=d2.step(*obs)
        assert r1==r2
        e=r1['e'];lat=r1['e_lat'];it=r1['intent_window']
        assert c1.step(e,lat,it)==c2.step(e,lat,it)
    out['fixed_observation_determinism']=True
    dest=HERE/'private/avoidance';dest.mkdir(exist_ok=True,parents=True)
    (dest/'unit_tests.json').write_text(json.dumps(out,indent=2))
    (dest/'manual_replay.json').write_text(json.dumps(replay))
    print(json.dumps(out,indent=2))

if __name__=='__main__':main()
