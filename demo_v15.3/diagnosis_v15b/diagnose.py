"""Observation-only harness. Imports the original demo_v15b without modification."""
import os
import sys
from pathlib import Path
sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
SOURCE=sys.argv[1] if len(sys.argv)>1 else 'demo_v15b'
assert SOURCE in ('demo_v15b','demo_v15c')
OUT=HERE/'private' if SOURCE=='demo_v15b' else HERE/'private'/'v15c'
os.environ.setdefault('MPLCONFIGDIR',str(OUT/'mpl'))
os.environ.setdefault('OMP_NUM_THREADS','1')
sys.path.insert(0,str(ROOT/SOURCE))
import app
import numpy as np
import torch
import json
import time
import hashlib

LABEL='합성·직선·시뮬레이션'
CASES=[('brake15',0,15.,1,'4'),('brake25',1,25.,1,'4'),('brake40',2,40.,1,'4'),
       ('drift15',3,15.,2,'1'),('stuck15',4,15.,2,'2')]
DT=.1


def clean(obj):
    if isinstance(obj,dict): return {k:clean(v) for k,v in obj.items()}
    if isinstance(obj,(list,tuple,np.ndarray)): return [clean(v) for v in obj]
    if isinstance(obj,(np.bool_,bool)): return bool(obj)
    if isinstance(obj,(np.integer,int)): return int(obj)
    if isinstance(obj,(np.floating,float)): return float(obj) if np.isfinite(obj) else None
    return obj


def write(name,obj):
    (OUT/name).write_text(json.dumps(clean(obj),ensure_ascii=False,indent=1,allow_nan=False))


def hashes():
    files=list((ROOT/'demo_v15b').glob('*.py'))+list((ROOT/'demo_v15c').glob('*.*'))
    files+=list((ROOT/'v2v_trust_planning/src').rglob('*.py'))
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}


def dist(values,mask=None,absolute=True):
    a=np.asarray(values,float)
    if mask is not None: a=a[mask]
    if not len(a): return None
    v=np.abs(a) if absolute else a
    return dict(zip(('p50','p90','p99','max'),map(float,np.percentile(v,[50,90,99,100]))))


def summarize(rows):
    ar=lambda k:np.array([r[k] for r in rows])
    post=ar('t')>=20.
    avoid=ar('avoiding').astype(bool)
    fallback=ar('fallback_now').astype(bool)
    stats={}
    for name,mask in [('all',np.ones(len(rows),bool)),('post_onset',post),('avoiding',avoid),('fallback',fallback)]:
        mm={k:dist(ar(k),mask) for k in ['delta_applied','a_lat','r_applied','jerk','delta_rate','slip_aligned_deg','slip_end_deg','solve_ms']}
        mm.update(samples=int(mask.sum()),a_min=float(ar('a_applied')[mask].min()) if mask.any() else None,
                  a_max=float(ar('a_applied')[mask].max()) if mask.any() else None,
                  fraction_a_near0=float(np.mean(np.abs(ar('a_cmd')[mask])<.1)) if mask.any() else None,
                  fraction_a_negative=float(np.mean(ar('a_cmd')[mask]<-.1)) if mask.any() else None,
                  fraction_a_positive=float(np.mean(ar('a_cmd')[mask]>.1)) if mask.any() else None)
        stats[name]=mm
    changes=[]
    for i,r in enumerate(rows):
        if i and (r['state']!=rows[i-1]['state'] or abs(r['target_y']-rows[i-1]['target_y'])>1e-8):
            changes.append({k:r[k] for k in ['t','state','target_y','v_ref','dx','tau_eff','a_cmd','delta_cmd']})
    alarms=np.flatnonzero(ar('alarm_eff') & post)
    avoid_idx=np.flatnonzero(avoid)
    fb_rows=[{k:r[k] for k in ['t','ego_y','theta','a_cmd','delta_cmd','target_y','state','dx']} for r in rows if r['fallback_now']]
    recovery_without_return=avoid & (ar('tau_eff')>.7) & post
    # Reconstruct the source update independently from recorded applied inputs.
    er=max(r['integration_error'] for r in rows)
    slacks=[r['slack_max'] for r in rows if r['slack_max'] is not None]
    return dict(stats=stats,transitions=changes,fallback_samples=int(fallback.sum()),fallback_during_avoid=int((fallback&avoid).sum()),
                fallback_rows=fb_rows,collisions=int(sum(r['hit'] and (i==0 or not rows[i-1]['hit']) for i,r in enumerate(rows))),
                min_gap=float(ar('gap').min()),first_alarm_s=float(ar('t')[alarms[0]]) if len(alarms) else None,
                first_avoid_s=float(ar('t')[avoid_idx[0]]) if len(avoid_idx) else None,
                max_target_y_step=float(np.max(np.abs(np.diff(ar('target_y'))))),
                v_ref_min_max=[float(ar('v_ref').min()),float(ar('v_ref').max())],
                a_lat_over8_samples=int((np.abs(ar('a_lat'))>8).sum()),
                solve_over100ms_samples=int((ar('solve_ms')>100).sum()),
                integration_error_max=er,slack_max=max(slacks) if slacks else None,
                trust_recovered_but_avoiding_seconds=float(recovery_without_return.sum()*.1),
                final_state=rows[-1]['state'],final_ego_y=rows[-1]['ego_y'],
                final_speed=rows[-1]['ego_v'],final_dx=rows[-1]['dx'])


def run_case(case,backend):
    name,seed,gap,lane,key=case
    ses=app.Session(seed=seed,gap=gap,lane=lane,v0=25.,v_ego=25.,backend=backend)
    rows=[]; prev_delta=prev_a=0.
    for k in range(600):
        if k==200: ses.key(key)
        if k==300 and key in ('1','2'): ses.key('0')
        before=ses.sim.ego.copy()
        a_ap,yr_ap=ses.u or (0.,0.)
        delta_ap=rows[-1]['delta_cmd'] if rows else 0.
        o,out=ses.step(())
        after=o['ego']; info=ses.ctl.info
        dv=(after[3]-before[3])/DT
        yaw=(after[2]-before[2])/DT
        vel=(after[:2]-before[:2])/DT
        angle=np.arctan2(vel[1],vel[0])
        angle_diff=lambda a,b:float(np.degrees(np.arctan2(np.sin(a-b),np.cos(a-b))))
        sm=getattr(getattr(ses.ctl.impl,'nmpc',None),'state_machine',None)
        target=float(sm.target_y if sm else getattr(ses.ctl.impl,'y_ref',0.))
        vr=float(sm.current_v_cmd if sm else ses.ctl.v_set)
        avoiding=info.get('sm')=='AVOIDING' or bool(getattr(ses.ctl.impl,'engaged',False))
        prediction=np.array([before[0]+before[3]*np.cos(before[2])*DT,before[1]+before[3]*np.sin(before[2])*DT,
                             before[2]+yr_ap*DT,max(0.,before[3]+a_ap*DT)])
        slack_max=slack_first=None
        if backend=='nmpc' and not info.get('fallback_now'):
            try:
                sl=np.asarray(ses.ctl.impl.nmpc.opti.debug.value(ses.ctl.impl.nmpc.eps[0])).reshape(-1)
                slack_max=float(sl.max()); slack_first=float(sl[0])
            except RuntimeError: pass
        row=dict(t=o['t'],ego_x=after[0],ego_y=after[1],theta=after[2],ego_v=after[3],v_before=before[3],
                 a_applied=dv,r_applied=yaw,delta_applied=delta_ap,a_lat=before[3]*yaw,
                 jerk=(dv-prev_a)/DT,delta_rate=(delta_ap-prev_delta)/DT,
                 slip_aligned_deg=angle_diff(angle,before[2]),slip_end_deg=angle_diff(angle,after[2]),
                 nb_x=o['x'],nb_y=o['y'],nb_v=o['v'],nb_psi=o['psi'],nb_a=o['a'],nb_r=o['r'],
                 a_cmd=ses.ego_rec['a'],delta_cmd=ses.ego_rec['steer'],tau_eff=out['tau_eff'],alarm_eff=out['alarm_eff'],
                 target_y=target,v_ref=vr,state=info.get('sm',info.get('state','normal')),avoiding=avoiding,
                 dx=ses.ego_rec['dx'],gap=ses.ego_rec['gap'],hit=ses.ego_rec['hit'],ttc=ses.ego_rec['ttc'],
                 fallback_now=info.get('fallback_now',False),solve_ms=info.get('solve_ms',0.),
                 slack_max=slack_max,slack_first=slack_first,integration_error=float(np.max(np.abs(after-prediction))))
        rows.append(clean(row)); prev_delta,prev_a=delta_ap,dv
    result=dict(label=LABEL,name=name,seed=seed,backend=backend,init=dict(gap=gap,lane=lane,v0=25.,v_ego=25.),
                summary=summarize(rows),rows=rows)
    write(f'{backend}_{name}.json',result)
    ss=result['summary']; st=ss['stats']['avoiding']
    print(backend,name,'avoid',st['samples'],'a_min',st['a_min'],'alatmax',st['a_lat'],
          'fallback',ss['fallback_samples'],'coll',ss['collisions'],flush=True)
    return {k:v for k,v in result.items() if k!='rows'}


def manual_probe():
    ses=app.SM.Sim(seed=0,bg=False,v_ego=25.,v0=25.)
    rows=[]; last_a=last_delta=0.
    for k in range(300):
        keys={'D'} if 50<=k<80 else ({'W'} if 120<=k<140 else ({'S'} if 200<=k<220 else set()))
        psi0,v0=ses.psi,ses.v
        o=ses.step(keys)
        r=(ses.psi-psi0)/.1
        delta=np.arctan(2.7*r/max(ses.v,1e-8))
        rows.append(dict(t=o['t'],keys=','.join(keys),v=ses.v,a=ses.a,r=r,r_cmd=ses.r_cmd,
                         a_lat=ses.v*r,delta=delta,delta_rate=(delta-last_delta)/.1,jerk=(ses.a-last_a)/.1))
        last_a,last_delta=ses.a,delta
    write('manual_probe.json',dict(label=LABEL,rows=rows))
    return {k:dist([r[k] for r in rows]) for k in ['a','r','r_cmd','a_lat','delta','delta_rate','jerk']}


if __name__=='__main__':
    OUT.mkdir(exist_ok=True,parents=True)
    torch.set_num_threads(1)
    before=hashes(); write('source_hashes_before.json',before)
    results=[]
    for backend in ('nmpc','rule'):
        for case in CASES:
            results.append(run_case(case,backend))
    manual=manual_probe()
    after=hashes(); changed=[k for k in before if before[k]!=after.get(k)]
    assert not changed, changed
    summary=dict(label=LABEL,source=SOURCE,source_files_unchanged=True,results=results,manual=manual,
                 versions=dict(python=sys.version,numpy=np.__version__,torch=torch.__version__),
                 note='Five predeclared scenarios, both unchanged backends. No tuning; source code unchanged.')
    write('summary.json',summary)
    print('DONE source unchanged',len(before),'files',flush=True)
