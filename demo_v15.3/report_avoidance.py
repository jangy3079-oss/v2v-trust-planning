"""Aggregate frozen runs and four figures; does not execute or tune controllers."""
import os
os.environ.setdefault('MPLCONFIGDIR','/tmp/demo_v15c_mpl')
import json,sys,hashlib,csv
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
HERE=Path(__file__).resolve().parent;ROOT=HERE.parent
PRIVATE=HERE/'private/avoidance';OUT=HERE/'results/avoidance';FIG=HERE/'figures'
MODES=['rule-combined','nmpc-combined','v15b-nmpc']   # off/lateral comparison modes removed (2026-10-29)
CASES=['brake15','brake25','brake40','drift','stuck','rear_accel','normal_lc_on','normal_lc_off']
COLORS=['#086c66','#7540a2','#d87835']
BANNER='합성·직선·시뮬레이션'
fp='/System/Library/Fonts/AppleSDGothicNeo.ttc'
if Path(fp).exists():font_manager.fontManager.addfont(fp);plt.rcParams['font.family']=font_manager.FontProperties(fname=fp).get_name()
plt.rcParams.update({'axes.unicode_minus':False,'axes.grid':True,'grid.alpha':.2,'font.size':10,'savefig.dpi':160})

def load(mode,case='brake25',seed=0):
    z=np.load(PRIVATE/'traces'/f'{mode}_{case}_{seed:02d}.npz')
    return dict(zip(z['columns'],z['data'].T))
def f(v,d=2):return 'N/A' if v is None else f'{v:.{d}f}'
def median(vals):
    a=[v for v in vals if v is not None]
    return float(np.median(a)) if a else None
def table(lines,headers,rows):
    lines+=['','| '+' | '.join(str(v).replace('|',r'\|') for v in headers)+' |','|'+'|'.join(['---']*len(headers))+'|']
    lines+=['| '+' | '.join(str(v).replace('|',r'\|') for v in row)+' |' for row in rows]
def verdict(summary):
    new=[m for m in MODES if m!='v15b-nmpc']
    exits=sum(summary[m]['road_center_exit'] for m in new);ymax=max(summary[m]['max_abs_y'] for m in new)
    stops=sum(summary[m]['stopped_at_end'] for m in new)
    rc,nc=summary['rule-combined'],summary['nmpc-combined']
    return (f"**판정(v15c-fix, 계산값):** 신규 2조합(rule·nmpc 종+횡) 320회에서 자차 중심 도로 이탈 {exits}회, 최대 |Y| {ymax:.2f} m, 종료 시 정지 {stops}회. "
            f"rule-combined 충돌 {rc['collisions']}/160·복귀 완료 {rc['returned']}/160, nmpc-combined 충돌 {nc['collisions']}/160·복귀 완료 {nc['returned']}/160. "
            "충돌이 남은 시나리오(아래 표)는 막은 것으로 세지 않는다.")
def save(fig,name):
    fig.savefig(FIG/name,bbox_inches='tight');plt.close(fig)

def main():
    OUT.mkdir(exist_ok=True,parents=True);FIG.mkdir(exist_ok=True)
    runs=[json.loads(p.read_text()) for p in sorted((PRIVATE/'runs').glob('*.json'))]
    runs=[r for r in runs if r['mode'] in MODES]   # older off/lateral run files, if present, are ignored
    n=160*len(MODES)
    assert len(runs)==n,len(runs)
    assert len({(r['mode'],r['case'],r['seed']) for r in runs})==n
    summary={};scenarios={};pooled={}
    for mode in MODES:
        rs=[r for r in runs if r['mode']==mode];assert len(rs)==160
        series=[load(mode,r['case'],r['seed']) for r in rs]
        solve=np.concatenate([d['solve_ms'] for d in series])
        pooled[mode]=solve
        summary[mode]=dict(runs=160,collisions=sum(r['collision'] for r in rs),events=sum(r['events'] for r in rs),
            min_gap=min(r['min_gap'] for r in rs),median_min_gap=median([r['min_gap'] for r in rs]),
            road_center_exit=sum(bool(np.any(np.abs(d['ey'])>5.25)) for d in series),
            max_abs_y=max(float(np.max(np.abs(d['ey']))) for d in series),
            max_abs_delta=max(float(np.max(np.abs(d['delta']))) for d in series),
            stopped_at_end=sum(bool(d['v'][-1]==0) for d in series),
            min_a=min(r['min_a'] for r in rs),max_a=max(r['max_a'] for r in rs),
            **{key:max(r[key] for r in rs) for key in ('max_alat','normal_alat','emergency_alat','max_jerk','max_delta_rate','max_yaw','max_slip','max_end_slip','rate_excess')},
            **{key:sum(r[key] for r in rs) for key in ('normal_fail','emergency_fail','accel_fail','rate_fail','slip_fail','fallback','fallback_active','command_clips','pre_motion')},
            started=sum(r['first_avoid'] is not None for r in rs),completed=sum(r['complete'] is not None for r in rs),
            returned=sum(r['return_complete'] is not None for r in rs),return_started=sum(r['return_start'] is not None for r in rs),
            clips={key:sum(r['clips'].get(key,0) for r in rs) for key in ('accel_command','steer_command','steer_rate','speed_lat')},
            solve_ms=dict(zip(('p50','p90','p99','max'),map(float,np.percentile(solve,[50,90,99,100])))))
        for case in CASES:
            ss=[r for r in rs if r['case']==case];assert len(ss)==20
            scenarios[f'{mode}/{case}']=dict(n=20,collisions=sum(r['collision'] for r in ss),
                min_gap=min(r['min_gap'] for r in ss),median_gap=median([r['min_gap'] for r in ss]),
                **{k:sum(r[k] for r in ss) for k in ('alarm_det','alarm_eff','avoid','post_motion','pre_motion')},
                **{k:median([r[k] for r in ss]) for k in ('first_alarm','first_avoid','complete','return_start','return_complete')},
                started=sum(r['first_avoid'] is not None for r in ss),completed=sum(r['complete'] is not None for r in ss),
                returned=sum(r['return_complete'] is not None for r in ss))
    protected=json.loads((HERE/'diagnosis_v15b/private/v15c/source_hashes_before.json').read_text())
    immutable={p:h for p,h in protected.items() if not p.startswith('demo_v15c/') or Path(p).name in ('detector.py','accum.py')}
    checks={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in immutable.items()}
    assert all(checks.values()),checks
    unit=json.loads((PRIVATE/'unit_tests.json').read_text())
    payload=dict(condition=BANNER,summary=summary,scenarios=scenarios,unit_tests=unit,protected_sha256_checks=checks)
    (OUT/'summary.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2))
    with (OUT/'scenario_summary.csv').open('w',newline='') as file:
        writer=csv.DictWriter(file,fieldnames=['mode_case',*next(iter(scenarios.values())).keys()]);writer.writeheader()
        writer.writerows(dict(mode_case=k,**v) for k,v in scenarios.items())
    traces={mode:load(mode) for mode in MODES}
    replay=json.loads((PRIVATE/'manual_replay.json').read_text())
    # Figure 1: fixed representative and actual independently computed identical-command outputs.
    fig,ax=plt.subplots(2,2,figsize=(13,8),layout='constrained')
    for mode,color in zip(MODES[:-1],COLORS):
        d=traces[mode];mask=(d['t']>=20)&(d['t']<=40)
        ax[0,0].plot(d['ex'][mask]-d['ex'][199],d['ey'][mask],label=mode,color=color)
    d=traces[MODES[0]];mask=(d['t']>=20)&(d['t']<=40)   # neighbour path (same in every mode)
    ax[0,0].plot(d['nx'][mask]-d['ex'][199],d['ny'][mask],':',color='black',label='neighbor')
    ax[0,0].set(xlabel='x since onset [m]',ylabel='y [m]',title='brake25 / seed0 / 20–40 s');ax[0,0].legend(fontsize=8)
    for role,ls in [('manual','-'),('ego','--')]:
        path=np.array([r[role] for r in replay])
        ax[0,1].plot(path[:,0],path[:,1],ls,label=role,lw=2)
    ax[0,1].set(xlabel='x [m]',ylabel='y [m]',title='Same keyboard lane-change + brake commands');ax[0,1].legend()
    for pane,key,title in [(ax[1,0],'alat','Lateral acceleration ECDF'),(ax[1,1],'jerk','Absolute jerk per 0.1 s peak ECDF')]:
        for prefix,ls,label in [('', '-', 'manual'),('ego_','--','ego')]:
            vals=np.sort([r[prefix+key] for r in replay]);pane.plot(vals,np.arange(1,len(vals)+1)/len(vals),ls,label=label,lw=2)
        pane.set(title=title,xlabel='m/s²' if key=='alat' else 'm/s³',ylabel='CDF');pane.legend()
    fig.suptitle(BANNER+' | 1. 궤적·동일명령 비교 (축 척도는 패널별 상이)')
    save(fig,'avoidance_1_trajectories.png')
    # Figure 2: full 60 s, no removal of failure/recovery intervals.
    fig,axes=plt.subplots(3,2,figsize=(14,10),sharex=True,layout='constrained')
    for ax,key,label in zip(axes.flat,['v','a','alat','r','delta','jerk'],['Speed [m/s]','Acceleration [m/s²]','Lateral acceleration [m/s²]','Yaw rate [rad/s]','Steering [rad]','Absolute jerk peak [m/s³]']):
        for mode,color in zip(MODES[:-1],COLORS):ax.plot(traces[mode]['t'],traces[mode][key],label=mode,color=color,lw=1)
        ax.axvline(20,color='black',ls=':',lw=1);ax.set_ylabel(label);ax.set_xlabel('t [s]')
    axes[0,0].legend(fontsize=8);axes[1,0].axhline(4,color='gray',ls=':');axes[1,0].axhline(-4,color='gray',ls=':')
    fig.suptitle(BANNER+' | 2. brake25 seed0 · 실제 적용 상태 (전체 60 s)')
    save(fig,'avoidance_2_timeseries.png')
    # Figure 3: all cases and all seeds, groupwise medians and worst values.
    fig,axes=plt.subplots(2,1,figsize=(15,9),sharex=True,layout='constrained')
    xx=np.arange(len(CASES));w=.25
    for i,(mode,color) in enumerate(zip(MODES,COLORS)):
        s=[scenarios[f'{mode}/{case}'] for case in CASES];x=xx+(i-1)*w
        axes[0].bar(x,[r['collisions'] for r in s],w,color=color,label=mode)
        axes[1].bar(x,[r['median_gap'] for r in s],w,color=color)
        axes[1].scatter(x,[r['min_gap'] for r in s],s=10,color='black',zorder=4)
    axes[0].set(ylabel='Collision runs / 20',ylim=(0,23));axes[0].legend(ncol=3)
    axes[1].set(ylabel='Minimum body gap [m], median; dots = worst',xticks=xx,xticklabels=CASES)
    fig.suptitle(BANNER+' | 3. 시나리오별 20회 · 회전 차체 간격')
    save(fig,'avoidance_3_outcomes.png')
    # Figure 4: original v15b (different accumulator/neighbor dynamics), explicitly a bundled comparison.
    fig,axes=plt.subplots(2,2,figsize=(13,8),layout='constrained')
    for mode,color in [('v15b-nmpc',COLORS[-1]),('nmpc-combined',COLORS[1])]:
        d=traces[mode];mask=(d['t']>=20)&(d['t']<=40)
        axes[0,0].plot(d['ex'][mask]-d['ex'][199],d['ey'][mask],color=color,label=mode)
        for ax,key in [(axes[0,1],'alat'),(axes[1,0],'delta'),(axes[1,1],'jerk')]:ax.plot(d['t'],d[key],color=color,label=mode,lw=1)
    axes[0,0].set(xlabel='x since onset [m]',ylabel='y [m]',title='20–40 s');axes[0,0].legend()
    for ax,label in [(axes[0,1],'Lateral acceleration [m/s²]'),(axes[1,0],'Steering [rad]'),(axes[1,1],'Absolute jerk peak [m/s³]')]:ax.set(xlabel='t [s]',ylabel=label)
    fig.suptitle(BANNER+' | 4. v15b 전후 묶음 비교 · brake25 seed0\n탐지 누적기·이웃 플랜트도 다름: 회피 정책만의 인과 효과가 아님')
    save(fig,'avoidance_4_before_after.png')
    lines=['# 회피기동 검증 결과','',f'**{BANNER}. 실제 도로 성능 주장이 아니다.**',
           '', '기존 demo_v15c의 탐지·누적·보정·홀드를 유지한 채 공통 Vehicle, 횡·종 회피, 복귀 참조, 화면 보간을 구현했다. [구현 전 고정 설계](DESIGN_FROZEN.md), [1단계 진단](DIAGNOSIS_PHASE1.md).',
           '', '신규 320회(rule·nmpc 종+횡) + 160회 v15b 비교. 비교용 off(직진)·lateral(횡만) 모드는 2026-10-29 사용자 결정으로 삭제했다(이전 결과는 results/avoidance_fix1_ego_ramp005/ 등에 보존). 8시나리오×시드0–19, 각60초, 주입20초, 배경OFF. 20회 차이는 센서 잡음이며 다양한 운전자/도로에 대한 반복이 아니다. 저장 오류 1건만 수정했으며 결과를 보고 제어·탐지 임계값을 변경하지 않았다.',
           '', verdict(summary), '', '## 충돌과 최소 간격', '', '각 칸은 충돌 run/20; 최솟값0은 차체 접촉/겹침이다. 충돌 후에도 로그를 이어가므로 이후 회피·복귀가 이전 충돌을 취소하지 않는다.']
    table(lines,['시나리오',*MODES],[[case,*[f"{scenarios[f'{mode}/{case}']['collisions']}/20" for mode in MODES]] for case in CASES])
    table(lines,['조합','충돌 run/160','충돌 사건','최소 간격 worst / median m','시작 / 횡목표 완료 / 복귀 완료'],
          [[mode,f"{s['collisions']}/160",s['events'],f"{s['min_gap']:.3f} / {s['median_min_gap']:.3f}",f"{s['started']} / {s['completed']} / {s['returned']}"] for mode,s in summary.items()])
    lines+=['','## 차선·도로 유지: 충돌 수만으로 성공 판정 불가','','도로는 중심0, 3차로×3.5m이므로 |Y|>5.25m는 자차 중심 자체가 도로 밖인 경우다. 차체 일부 이탈보다 보수적인 집계이며 안전 통과 기준을 새로 만든 것이 아니다. 충돌 후 물리 충격은 모델링하지 않고 궤적을 계속 적분한다.']
    table(lines,['조합','중심 도로 이탈 run/160','최대 |Y| m','60초 시점 완전 정지 run'],[[mode,s['road_center_exit'],f(s['max_abs_y']),s['stopped_at_end']] for mode,s in summary.items()])
    lines+=['','v15c-fix 이전(Astra 구현)에는 기존 이득(ω=1)·4초 참조가 플랜트 조향 변화율 상한(25 m/s에서 0.0054 rad/s)을 크게 넘는 명령을 내 변화율 포화 진동으로 발산했다(최대 |Y| 241 m). v15c-fix는 플랜트는 그대로 두고, 참조를 그 한계 안으로 설계하고(거리 기반 5차, 요가속 ≤ 0.6·0.05 rad/s²) 추종 이득을 ω=0.4로 낮추며 횡목표를 차선 중심/중심±0.5 m로 제한했다. 이 변경은 실패를 본 뒤 사용자 지시로 한 수정이며 사전 고정 설계가 아니다.']
    lines+=['','v15c-fix2(2026-10-29, 사용자 결정): 이웃 차량 한계(요가속 0.05rad/s²)는 그대로 두고 **자차 Vehicle만 0.3rad/s²(합성 설정값)**로 올렸다. 정책은 종·횡 연동으로 바꿨다: TTC≤4이고 옆 차로가 비면 바로 차선변경, 제동은 횡 이동으로 이웃 차체를 벗어날 때까지 범퍼 간격 2m를 지키는 최소 감속(이웃 제동 관측 a_obs 반영, 단계 상한 0/−5/−8), 횡으로 벗어나면 제동 해제·속도 회복. 뒤차 단독 위협은 TTC≤4에서 +3m/s² 가속.']
    lines+=['','앞차 따라가기(2026-10-29, 사용자 지적 "평소 주행하다 급정거하면 못 피한다"): 배경 흔들림이 켜진 UI에서는 이웃이 약간 느리게 달려, 자차가 25 m/s 정속을 유지하면 30초쯤 범퍼 간격이 약 5 m(0.2초)까지 줄었다. 이 거리에서 급제동하면 물리적으로 피할 수 없다. 그래서 신뢰도와 무관하게 같은 경로의 앞차에 대해 시간 간격 1.5초 + 정지 간격 5 m를 유지하도록 속도 명령 상한을 두었다(합성 설정값). 이 때문에 아래 급제동 시나리오(초기 간격 15/25/40 m)는 급제동 시점 간격이 초기값보다 벌어진 상태에서 시작한다.']
    stages=[('Astra 구현','results/avoidance_astra_before_fix/summary.json'),('fix1 (자차 0.05)','results/avoidance_fix1_ego_ramp005/summary.json'),('fix2 (앞차 따라가기 없음)','results/avoidance_fix2_no_acc/summary.json')]
    prev=[(name,json.loads((HERE/path).read_text())['summary']) for name,path in stages if (HERE/path).exists()]
    if prev:
        lines+=['','### 이전 구현 대비 (같은 시나리오·시드 프로토콜)','',' → '.join([n for n,_ in prev]+['현재'])+' 순서로 표기.']
        def chain(m,key,fmt=str):return '→'.join(fmt(b[m][key]) for _,b in prev)+'→'+fmt(summary[m][key])
        table(lines,['조합','충돌 run/160','중심 도로 이탈','최대 |Y| m','종료 정지','복귀 완료'],
              [[m,chain(m,'collisions'),chain(m,'road_center_exit'),chain(m,'max_abs_y',lambda v:f'{v:.2f}'),chain(m,'stopped_at_end'),chain(m,'returned')] for m in MODES[:-1]])
    lines+=['','## 물리 지표와 사전 기준 판정','','모든 적용 서브스텝을 집계. normal은 비상 단계가 아닌 구간 전체(평상·회피·복귀 포함)를 보수적으로 검사했다. jerk는 100Hz 적용 가속 차분, v15b는10Hz 차분이라 대역이 다르다.']
    table(lines,['조합','a min/max','|a_lat| max (normal/emergency)','|jerk| max','|δ̇| max','|r| max','|δ| max rad','slip max °'],
          [[mode,f"{s['min_a']:.3f}/{s['max_a']:.3f}",f"{s['normal_alat']:.3f}/{s['emergency_alat']:.3f}",f(s['max_jerk']),f(s['max_delta_rate'],5),f(s['max_yaw'],4),f(s['max_abs_delta'],3),f"{s['max_slip']:.2e}"] for mode,s in summary.items()])
    table(lines,['조합','normal >4 run','emergency >8 run','a 한계 위반','δ̇ 한계 위반','slip >5°','10Hz 끝 heading 차이 max °'],
          [[mode,s['normal_fail'],s['emergency_fail'],s['accel_fail'],s['rate_fail'],s['slip_fail'],f(s['max_end_slip'])] for mode,s in summary.items()])
    lines+=['','슬립은 모델의 속도 방향과 동일 시각 heading의 일치 검사다. 기구학 모델의 구조적 성질이며 실제 타이어 슬립 검증이 아니다. v15b의 전진 Euler 시작 heading, 새 모델의 중점 heading에 대응한다. 10Hz 끝 heading 차이는 별도 열로 공개했다. v15b에는 비상 단계가 없어 emergency=0이다.']
    lines+=['','## 풀이와 제한','','풀이 시간은 6개 작업 프로세스 병렬 CPU 부하에서 관측한 값. 평균만으로 실시간성을 주장하지 않는다. 명령클립은 10Hz 어댑터, plant steer/rate/speed 제한은100Hz 서브스텝 건수다.']
    table(lines,['조합','solve ms p50 / p90 / p99 / max','fallback 전체 / 회피·복귀','어댑터 조향클립','plant a / δ / δ̇ / 속도'],
          [[mode,' / '.join(f(v) for v in s['solve_ms'].values()),f"{s['fallback']} / {s['fallback_active']}",s['command_clips'],' / '.join(str(v) for v in s['clips'].values())] for mode,s in summary.items()])
    lines+=['','## 회피·완료·복귀 시각','','아래는 실행 시작 기준 중앙값 초. 조건을 달성한 run만의 중앙값이므로 완료/복귀 개수를 같이 읽어야 한다. N/A는 60초 안에 해당 이벤트가 없었다는 뜻이다. 시각 20을 빼면 주입 이후 시간. 횡목표 완료보다 먼저 위험 해소로 복귀를 시작할 수도 있다.']
    table(lines,['조합/시나리오','회피 진입 n','시작 s','횡목표 완료 s (n)','복귀 시작 s','복귀 완료 s (n)'],
          [[f'{mode}/{case}',s['started'],f(s['first_avoid']),f"{f(s['complete'])} ({s['completed']})",f(s['return_start']),f"{f(s['return_complete'])} ({s['returned']})"] for mode in MODES for case in CASES for s in [scenarios[f'{mode}/{case}']]])
    lines+=['','## 정상 차선변경의 경보·불필요 반응','','정상 차선변경 두 조건은 정답상 정상이다. 경보와 실제 반응을 분리했다. 실제 움직임은20초 이후 |Y|>.1m 또는 a<−1m/s²; NMPC가 경보 전 자체 제약으로 움직인 것도 포함된다.']
    table(lines,['조합/조건','탐지경보 / 반응경보 / AVOIDING (각/20)','실제 움직임 / 주입 전 움직임 (각/20)'],
          [[f'{mode}/{case}',f"{s['alarm_det']} / {s['alarm_eff']} / {s['avoid']}",f"{s['post_motion']} / {s['pre_motion']}"] for mode in MODES for case in CASES[-2:] for s in [scenarios[f'{mode}/{case}']]])
    lines+=['','## 동일 동작·보존·화면 확인','',f"- 독립 Vehicle 두 개에 같은 키보드 차선변경+제동 명령600개를 넣은 궤적 최대 차이: **{unit['identical_command_max_error']:.1e}**, a/a_lat/jerk를 포함한 적용 지표 사전도 일치(허용1e−9).",
           f"- 극한 입력의 횡가속 최대 {unit['extreme_limits']['lat']:.8f}m/s², 조향 변화율 초과 {unit['extreme_limits']['rate_violation']:.2e}rad/s(부동소수 오차).",
           f"- 보호 대상 {len(checks)}개 SHA-256 일치: v15b 코드, 원본 planning/Config 등, v15c detector.py/accum.py. 고정 관측의 탐지/누적 반복 출력 일치.",
           '- 실제 Pygame 루프를 dummy 디스플레이에서 실행해 C 모드/B 엔진 전환·보간·회전 렌더링을 확인했다. 실디스플레이의 지속60FPS 성능은 측정하지 않았다.',
           '', '## 그림', '', '대표는 사전 지정한 brake25 seed0이며 성공 사례를 골라 바꾸지 않았다. x–y 패널은20–40초, 시계열은전체60초다.']
    for name,title in [('avoidance_1_trajectories.png','1. 궤적과 같은명령의 수동/자차 중첩'),('avoidance_2_timeseries.png','2. 실제 상태 시계열'),('avoidance_3_outcomes.png','3. 전체 충돌과 최소 간격'),('avoidance_4_before_after.png','4. v15b 전후 움직임')]:lines+=['',f'![{title}](figures/{name})']
    lines+=['','## 알려진 한계','','- NMPC 내부 예측에는 새 플랜트 액추에이터 지연/변화율 상태가 없다(원본 그대로). 그래서 v15c-fix는 NMPC의 조향을 플랜트에 직접 넣지 않고, NMPC를 계획기로 쓴다: 가속은 NMPC 값, 횡은 NMPC 지평 끝 Y의 정책 경로 대비 편차(>0.25 m)를 차선 격자에 스냅한 목표로 같은 실행 가능 참조·추종기에 넘긴다. 즉 NMPC 횡 결정의 해상도는 0.5 m 격자이고, NMPC 최적해 그대로의 궤적이 아니다.',
           '- v15b 전후 비교는 기존 누적기와 이웃 플랜트도 다른 묶음 비교다. 신규 2조합(rule·nmpc)은 같은 플랜트·누적기를 쓰는 통제 비교다. 이전 v15b 충돌 검사는10Hz 상태를 보간하므로 신규100Hz 검사와 완전히 동일하지 않다.',
           '- 정상 횡가속4 기준 또는60초 내 복귀 미달은 위 표에 그대로 남겼다. 결과를 보고 임계값을 바꾸지 않았다. 미회피/미복귀/충돌 run은 성공으로 세지 않았다.',
           '- 저속/정지에서는 속도별 횡가속 한계만으로 큰 조향각이 허용된다(약1.52rad). 별도 기계적 조향각 상한은 이번 고정 설계에 없었다. 정지 시 v≥0 클립으로 가속도가0으로 전환되며 큰 저크가 생긴다. 이 역시 자연스러움의 한계다.',
           '- 실제 이웃 x/heading 및 잡음 관측 y/v/r을 쓰고 이웃 한 대만 있다. 배경OFF, 직선, 합성 입력, 신호 관측 가능 가정이다. 초기중심간격과 범퍼간격은4.5m 차이난다.',
           '- 새 물리 모델은 기존 위치 직접가산 주입을 명령으로 바꿨으므로 drift/stuck/정상 차선변경의 탐지 결과도 달라질 수 있다. 탐지기를 재보정하지 않았다.',
           '- 기존 docs/ 웹 명세 및 README_preavoid.md는 변경 전 계약/설명이며 새 모드·입력 단위·충돌 기하와 다르다. 이번 작업에서 웹 API는 구현하지 않았다.',
           '', '## 재현 자료', '', '집계: results/avoidance/summary.json, scenario_summary.csv. 개별 run 요약(현재 집계 480개)과10Hz 압축 로그: private/avoidance/runs, traces. 고정 해시/오류 수정 이력: private/avoidance/evaluation_manifest*.json. 원시 로그·모델·UAH 파생물은 커밋 제외. 실행법과 변경 파일은 README 마지막 절.']
    (HERE/'AVOIDANCE_REPORT.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
