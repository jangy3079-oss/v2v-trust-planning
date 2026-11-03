"""Frozen 800 + 160 runs; 합성·직선·시뮬레이션. No calibration or tuning."""
import os
for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS'):
    os.environ[key]='1'
import sys,json,math,time,hashlib,argparse,multiprocessing as mp
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
import numpy as np
sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent
DEST=HERE/'private/avoidance'
CASES={'brake15':(15,1),'brake25':(25,1),'brake40':(40,1),'drift':(5,2),
       'stuck':(8,2),'rear_accel':(-15,1),'normal_lc_on':(15,2),'normal_lc_off':(15,2)}
MODES=('rule-combined','nmpc-combined')   # off / lateral comparison modes removed (user decision 2026-10-29)

def initialize(legacy):
    global APP,GEOM
    source=HERE.parent/'demo_v15b' if legacy else HERE
    sys.path.insert(0,str(source))
    import app
    APP=app
    import torch
    torch.set_num_threads(1)
    # Import only new geometry, without replacing legacy sim/controller modules.
    import importlib.util
    spec=importlib.util.spec_from_file_location('geometry_only',HERE/'vehicle.py')
    GEOM=importlib.util.module_from_spec(spec);spec.loader.exec_module(GEOM)

def wrap(v):return math.atan2(math.sin(v),math.cos(v))

def run_one(job):
    case,seed,mode=job;legacy=mode=='v15b-nmpc'
    gap,lane=CASES[case]
    args=dict(seed=seed,bg=False,gap=gap,lane=lane,v0=25.,v_ego=25.,k_drain=1.3)
    if legacy:args['backend']='nmpc'
    else:
        engine,maneuver=mode.split('-');args.update(backend=maneuver,solver=engine)
    ses=APP.Session(**args)
    rows=[];submetrics=[];hit_prev=False;events=0;first_hit=None;first_alarm=None;first_avoid=None
    first_complete=first_return=return_complete=None;done_s=home_s=0.
    previous_a=previous_delta=0.;clips={};cmd_clips=0;fb_active=0
    previous_ego=ses.sim.ego.copy()
    previous_nb=np.array([ses.sim.x,ses.sim.y_true(),0.,ses.sim.v])
    any_det=any_eff=False;pre_motion=False;any_avoid=False;post_motion=False
    start=time.perf_counter()
    for k in range(600):
        t=k*.1
        if k==180 and case=='normal_lc_on':ses.key('E')
        if k==200:
            if case.startswith('brake'):ses.key('4')
            elif case=='drift':ses.key('1')
            elif case=='stuck':ses.key('2')
            elif case=='rear_accel':ses.key('5')
        if (k==300 and case=='drift') or (k==260 and case=='stuck'):ses.key('0')
        if k==260 and case=='normal_lc_on':ses.key('E')
        held={'D'} if case.startswith('normal_lc') and 200<=k<230 else set()
        applied_info=dict(ses.ctl.info)
        old_command=ses.u or (0.,0.)
        o,out=ses.step(held)
        e=o['ego'];n=np.array([o['x'],o['y'],o['psi'],o['v']])
        info=ses.ctl.info
        st=info.get('sm',info.get('state','NORMAL'))
        state={'NORMAL':0,'AVOIDING':1,'RETURNING':2}.get(st,0)
        if legacy:
            a=(e[3]-previous_ego[3])/.1;r=(e[2]-previous_ego[2])/.1
            delta=math.atan(2.7*r/max(previous_ego[3],1.))
            alat=previous_ego[3]*r;jerk=(a-previous_a)/.1;dr=(delta-previous_delta)/.1
            motion=e[:2]-previous_ego[:2]
            slip=abs(wrap(math.atan2(motion[1],motion[0])-previous_ego[2]))*180/math.pi if np.linalg.norm(motion)>1e-8 else 0.
            metrics=[dict(a=a,a_lat=alat,jerk=jerk,delta_rate=dr,rate_bound=2.7*.05/max(previous_ego[3],1.),r=r,slip=slip*math.pi/180)]
            fg=float('inf');fh=False
            for j in range(1,11):
                f=j/10;g,h=GEOM.body_gap(previous_ego*(1-f)+e*f,previous_nb*(1-f)+n*f)
                fg=min(fg,g);fh|=h
                if h and not hit_prev:events+=1
                hit_prev=h
            last=dict(a=a,delta=delta,r=r,a_lat=alat,jerk_max=abs(jerk),delta_rate_max=abs(dr),slip_max_deg=slip)
        else:
            plant=ses.sim.ego_vehicle;last=plant.last;metrics=plant.trace
            fg,fh=o['body_gap'],o['body_hit'];events=ses.sim.body_events
            for name,count in last['clips'].items():clips[name]=clips.get(name,0)+count
            a,delta=plant.a,plant.delta
        if fh and first_hit is None:first_hit=t
        if out['alarm_eff'] and first_alarm is None:first_alarm=t
        emergency=bool(applied_info.get('emergency',False))
        for m in metrics:
            submetrics.append([m['a'],abs(m['a_lat']),abs(m['jerk']),abs(m['delta_rate']),
                               abs(m['r']),abs(m['slip'])*180/math.pi,abs(m['delta_rate'])-m['rate_bound'],int(emergency)])
        cmd_clips+=info.get('command_clips',0)
        fb_active+=int(info.get('fallback_now',False) and state!=0)
        motion=e[:2]-previous_ego[:2]
        end_slip=abs(wrap(math.atan2(motion[1],motion[0])-e[2]))*180/math.pi if np.linalg.norm(motion)>1e-8 else 0.
        actual_motion=abs(e[1])>.1 or last['a']<-1.
        if k<200:pre_motion|=actual_motion
        else:
            any_det|=bool(out['alarm_det']);any_eff|=bool(out['alarm_eff'])
            any_avoid|=state==1;post_motion|=actual_motion
        if state==1 and first_avoid is None:first_avoid=t
        if legacy:
            target=info.get('target_y',0.)
            done_s=done_s+.1 if state==1 and abs(e[1]-target)<=.1 and abs(e[2])<=math.radians(5) else 0.
            home_s=home_s+.1 if state==2 and abs(e[1])<=.1 and abs(e[2])<=math.radians(5) and abs(e[3]-25)<=.5 else 0.
            if done_s>=1-1e-9 and first_complete is None:first_complete=t
            if state==2 and first_return is None:first_return=t
            if home_s>=1-1e-9 and return_complete is None:return_complete=t
        rows.append([t,*e,*n,last['a'],last['delta'],last['r'],last['a_lat'],last['jerk_max'],
                     last['delta_rate_max'],last['slip_max_deg'],end_slip,fg,int(fh),
                     out['tau_eff'],int(out['alarm_det']),int(out['alarm_eff']),state,
                     info.get('stage',0),ses.ego_rec['a'],ses.ego_rec['steer'],info.get('solve_ms',0.),
                     int(info.get('fallback_now',False)),int(emergency),int(info.get('command_clips',0)),
                     int(APP.EC.gaps(e,o['x'],o['y'])[-1])])
        previous_ego=e.copy();previous_nb=n.copy();previous_a=a;previous_delta=delta
    columns=['t','ex','ey','heading','v','nx','ny','nheading','nv','a','delta','r','alat','jerk',
             'delta_rate','slip','end_slip','gap','hit','tau_eff','alarm_det','alarm_eff','state','stage',
             'a_cmd','delta_cmd','solve_ms','fallback','applied_emergency','command_clips','axis_hit']
    data=np.array(rows,float);m=np.array(submetrics,float)
    if not legacy:
        p=ses.ctl.policy
        # Policy clock starts at .1 while observation timestamps start at 0.
        def stamp(value):return None if value is None else max(0.,value-.1)
        first_avoid,first_complete,first_return,return_complete=map(stamp,(p.first_start,p.first_complete,p.first_return,p.return_complete))
    normal=m[:,7]==0;emer=~normal
    result=dict(case=case,seed=seed,mode=mode,collision=bool(first_hit is not None),events=events,
        min_gap=float(data[:,17].min()),first_hit=first_hit,first_alarm=first_alarm,
        first_avoid=first_avoid,complete=first_complete,return_start=first_return,return_complete=return_complete,
        min_a=float(m[:,0].min()),max_a=float(m[:,0].max()),max_alat=float(m[:,1].max()),
        normal_alat=float(m[normal,1].max()) if normal.any() else 0.,
        emergency_alat=float(m[emer,1].max()) if emer.any() else 0.,
        max_jerk=float(m[:,2].max()),max_delta_rate=float(m[:,3].max()),max_yaw=float(m[:,4].max()),
        max_slip=float(m[:,5].max()),max_end_slip=float(data[:,16].max()),
        rate_excess=float(max(0.,m[:,6].max())),normal_fail=bool(np.any(m[normal,1]>4+1e-8)),
        emergency_fail=bool(np.any(m[emer,1]>8+1e-8)),accel_fail=bool(np.any((m[:,0]<-8-1e-8)|(m[:,0]>3+1e-8))),
        rate_fail=bool(np.any(m[:,6]>1e-8)),slip_fail=bool(np.any(m[:,5]>5+1e-8)),
        alarm_det=any_det,alarm_eff=any_eff,avoid=any_avoid,post_motion=post_motion,pre_motion=pre_motion,
        fallback=int(data[:,27].sum()),fallback_active=fb_active,clips=clips,command_clips=cmd_clips,
        solve_ms=dict(zip(('p50','p90','p99','max'),map(float,np.percentile(data[:,26],[50,90,99,100])))),
        wall_s=time.perf_counter()-start)
    name=f'{mode}_{case}_{seed:02d}'
    np.savez_compressed(DEST/'traces'/f'{name}.npz',data=data,columns=np.array(columns))
    (DEST/'runs'/f'{name}.json').write_text(json.dumps(result,indent=2,default=lambda value:value.item()))
    return result

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--workers',type=int,default=6)
    ap.add_argument('--part',choices=['all','new','old'],default='all');ap.add_argument('--resume',action='store_true')
    args=ap.parse_args()
    for d in ('traces','runs'):(DEST/d).mkdir(parents=True,exist_ok=True)
    manifest=DEST/'evaluation_manifest.json'
    paths=[HERE/name for name in ('README.md','vehicle.py','sim.py','ego_controller.py','planning_demo.py','app.py','detector.py','accum.py','validate_avoidance.py')]
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    if manifest.exists() and args.resume:
        old=json.loads(manifest.read_text());assert old['source_sha256']==hashes,'Frozen evaluation source changed'
    else:manifest.write_text(json.dumps(dict(source_sha256=hashes,cases=CASES,modes=MODES,seeds=list(range(20)),duration=60,onset=20,workers=args.workers),indent=2))
    for legacy in ([False,True] if args.part=='all' else [args.part=='old']):
        jobs=[(case,seed,mode) for mode in (('v15b-nmpc',) if legacy else MODES) for case in CASES for seed in range(20)]
        if args.resume:jobs=[j for j in jobs if not (DEST/'runs'/f'{j[2]}_{j[0]}_{j[1]:02d}.json').exists()]
        print('START', 'legacy' if legacy else 'new',len(jobs),'runs',flush=True)
        with ProcessPoolExecutor(max_workers=args.workers,mp_context=mp.get_context('spawn'),initializer=initialize,initargs=(legacy,)) as pool:
            futures={pool.submit(run_one,j):j for j in jobs}
            for i,f in enumerate(as_completed(futures),1):
                r=f.result()
                if i%10==0 or i==len(jobs):print('DONE',i,'/',len(jobs),r['mode'],r['case'],r['seed'],'wall/run',round(r['wall_s'],1),flush=True)
    print('FINISHED',flush=True)

if __name__=='__main__':main()
