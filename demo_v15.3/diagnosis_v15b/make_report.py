"""Stage-one report only: no vehicle/controller implementation or parameter changes."""
import os
import sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
os.environ['MPLCONFIGDIR']=str(HERE/'private'/'mpl')
import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

LABEL='합성·직선·시뮬레이션'
summary=json.loads((HERE/'private/v15c/summary.json').read_text())
old=json.loads((HERE/'private/summary.json').read_text())
nm=[r for r in summary['results'] if r['backend']=='nmpc']
rule=[r for r in summary['results'] if r['backend']=='rule']
f=font_manager.FontProperties(fname='/System/Library/Fonts/AppleSDGothicNeo.ttc')
font_manager.fontManager.addfont(f.get_file())
plt.rcParams.update({'font.family':f.get_name(),'axes.unicode_minus':False,'font.size':10})
fig,axs=plt.subplots(3,2,figsize=(12,9))
for case in ['brake40','stuck15']:
    rows=json.loads((HERE/f'private/v15c/nmpc_{case}.json').read_text())['rows']
    t=np.array([r['t'] for r in rows]); m=(t>=19)&(t<=36)
    for ax,key,title in zip(axs.ravel(),['target_y','delta_applied','ego_v','a_applied','a_lat','jerk'],
                             ['Target lateral position [m]','Applied steering [rad]','Ego speed [m/s]','Applied acceleration [m/s²]','Lateral acceleration [m/s²]','Jerk [m/s³]']):
        ax.plot(t[m],[r[key] for r in rows if 19<=r['t']<=36],label=case)
        ax.set_title(title); ax.set_xlabel('Time [s]'); ax.grid(alpha=.2); ax.legend()
axs[2,0].axhline(8,ls='--',color='gray'); axs[2,0].axhline(-8,ls='--',color='gray')
fig.suptitle('현재 demo_v15c 진단 — '+LABEL)
fig.tight_layout(rect=[0,0,1,.96]); fig.savefig(HERE/'diagnostic_timeseries.png',dpi=160); plt.close(fig)

lines=[]
def p(s=''): lines.append(s if s.startswith('|') else s+'\n')
def n(v,d=3):return 'N/A' if v is None else f'{v:.{d}f}'
def table(headers,rows):
    esc=lambda x:str(x).replace('|','\\|')
    p('**'+LABEL+'**');p('| '+' | '.join(map(esc,headers))+' |');p('| '+' | '.join(['---']*len(headers))+' |')
    for row in rows:p('| '+' | '.join(map(esc,row))+' |')
    p()

p('# demo_v15c — 1단계 진단 보고 (구현 전)')
p('2026-10-28. **합성·직선·시뮬레이션**. 실제 도로 성능이 아니다. 2단계 설계 고정과 3단계 구현/검증은 아직 하지 않았다.')
p('## 결론')
p('주원인은 **속도와 무관한 ±0.5rad 조향 허용, 횡가속·조향 변화율 하드 제한 부재, 3.78~4.26m 목표 횡위치의 순간 변경**이다. 여기에 NMPC가 회피 때도25m/s를 유지하거나27m/s로 가속하는 정책이 결합돼 횡방향 급기동을 선택한다. 예측 위치 직접 대입이나 축 정렬 렌더링은 원인이 아니다.')
p('규칙 백엔드에는 이미 종방향 제동이 있다. 그러나 접근속도0.5m/s 미만이면 감속을 멈추므로, 작은 간격에서 천천히 재접근할 때 충돌한 사례가 있었다. 따라서 새 정책에는TTC뿐 아니라 정지/저속 접근 때의 간격 조건이 필요하다.')
p('사용자 확인에 따라 **기존 demo_v15c를 수정 대상**으로 삼는다. 기존 v15c는v14b B14b 누적기, 홀드3초, 기본k_drain1.3을 사용한다. 이를 그대로 보존한다. 요청 최초의v15b 복사 지시는 기존v15c에 기능을 넣는 방향으로 정정됐으며, 덮어쓰기/기존 파일수정은 하지 않았다.')
p('## 측정 범위와 무변경 확인')
p('현재v15c에서 사전 고정한5개 에피소드를NMPC와rule로 각각1회 실행했다(시드0~4). 같은v15b 측정10회는 비교용으로 보존했다. **이것은3단계의시나리오당20회 검증이 아니다.** 5개 중drift15에서는 두백엔드 모두 회피가 개시되지 않았다. 이를 성공한 회피로 바꾸거나 다른시드로 대체하지 않았다.')
p('각60초·600스텝, 주입20초, 초기속도25m/s, σmeas=.1m, 배경ON, 방향지시등가정ON. brake는같은차선, drift/stuck은왼쪽차선(+3.5m). 간격은초기중심간격15/25/40m이다. 배경가감속 때문에주입시점간격은초기값과다르다. drift/stuck은30초에기존키0으로해제했다.')
p('원본v15b, 기존v15c최상위파일, planning/Config를포함한src의37개파일은실행전후SHA-256이동일했다. 검사목록과해시는private의source_hashes_before.json에있다. 새진단하네스만추가했으며탐지기·누적기·보정값·홀드·제어정책을수정하지않았다.')
p('## 원인 후보별 판정과 영향 순위')
table(['가설','근거 수치·코드','판정','영향 순위'],[
['1. 상태 갱신/예측값 직접대입','sim.py:125–129는(a,r)로전진오일러. 기록6000스텝의직접재계산최대차0. x_pred는app.py:315–318에서점으로표시만. 수동은sim.py:174–180에서v/ψ를먼저갱신.','직접대입 아님. 공통Vehicle/동일step은 아님.','7'],
['2. 조향각 한계','δ최대0.500rad; 전체최대|a_lat|147.501m/s², |r|5.463rad/s. 25m/s·δ=.5이면126.459m/s².','확인: 속도별횡가속제한없음. 8m/s²의약15.8배(25m/s).','1'],
['3. 액추에이터/변화율','NMPC 최대|δdot|5.000rad/s, jerk60.0m/s³. 수동예시0.015735rad/s, jerk1.5075m/s³.','확인: 자차입력완화없음. 비용함수의Δu벌점은하드한계가아님.','3'],
['4. 제어/렌더 주기','DT=.1s, clock.tick(60), 보간없음. NMPC첫큰회전에서한스텝heading변화약29°, 전체최대31.30°. 풀이최대54.89ms, 100ms초과0/3000.','10Hz상태ZOH확인. 이번헤드리스에서제어deadline초과는미관측; GUI FPS는미측정.','6'],
['5. target_y 불연속','회피진입0→−3.782/−4.230/−4.263/−4.056m. 복귀는즉시0m. 복귀상태→정상은각0.4초.','확인: 전체1초예측지평에상수목표Y를즉시대입.','2'],
['6. fallback 개입','전체72/39/62/0/0회(합173), AVOIDING중0회. 모두급제동시나리오의복귀완료뒤NORMAL. −3↔가속으로최대jerk60.0.','발생확인. 이번회피의급조향원인은아님; 복귀후종방향급변원인.','5'],
['7. 회전/슬립','car()는sin/cos로회전된4꼭짓점. 시작heading정렬slip최대7.1e−13°. 종료heading과차분속도비교시31.30°.','축정렬아님. 실타이어슬립증거없음; 큰종료heading차는시간정렬/큰적분스텝효과.','원가설 기각'],
['8. 종방향 회피 부재','brake40의NMPC AVOIDING동안|a_cmd|<.1비율100%, 속도기준25m/s. stuck15는기준최대27m/s·양가속명령43.5%. rule은−5까지제동.','NMPC정책에서감속목표없음. rule까지감속없다는가설은기각. 20m/s하드코딩은reset에서25로교정됨.','4'],
['9. 수동복원 항','r=r_cmd−ψ/1s. r_cmd가0이면도로방향으로ψ를복원. 실제r과r_cmd가다름(수동예시최대|r|.03257).','키보드자세보조다. 공통플랜트에넣지않고키보드컨트롤러에둘것을제안.','분리 필수'],
['10. 기타','화면횡26px/m·종10px/m(2.6배왜곡), 1스텝제어전달, 장애물예측시간0.1s불일치, 축정렬충돌검사, 뒤접근TTC=∞.','모두코드확인. 통합플랜트만으로자동해결되지않음.','보조/설계 제약']])
p('행번호는현재v15c기준이며v15b의sim.py/ego_controller.py와같다. “영향순위”는소스와관측에기반한진단판단이다. 파라미터ablation/튜닝으로인과효과크기를추정한것은아니다.')
p('## 자차와 수동차량 갱신 비교')
table(['항목','현재 자차','현재 수동 이웃'],[
['입력','[a_cmd,δ]→r=v·tanδ/2.7; 다음10Hz스텝에적용','키W/S→2초a램프; A/D→r_cmd램프/해제감쇠'],
['속도/heading','v_old·cosθ_old, v_old·sinθ_old로위치갱신후v/θ업데이트','먼저v_new와ψ_new계산후그값으로x/yk업데이트'],
['위치','X+=v_old cosθ_old dt; Y+=v_old sinθ_old dt','x+=v_new cosψ_new dt; yk+=v_new sinψ_new dt'],
['heading','θ+=r dt; 자동도로방향복원없음','ψ+=(r_cmd−ψ/1s)dt'],
['공통성','같은Sim컨테이너안별도step_ego메서드','같은Sim컨테이너안step내인라인구현; 별도Vehicle클래스없음'],
['추가횡이동','없음','배경bg_y와이상y_inj를운동학위치에더함. 센서heading은후진차분으로재구성']])
p('따라서“수동차와완전히동일한플랜트”요구는현재두운동학부분을합치는것외에도배경·drift·stuck주입경로를어떻게다룰지정해야한다. 기존이웃주입은일부가위치오프셋을직접더하며공통Vehicle.step을우회할수있다. 탐지파라미터는유지하더라도입력궤적이바뀌면탐지시점은바뀔수있다.')
p('## 5개 에피소드: 실제 최댓값')
rows=[]
for r in summary['results']:
 q=r['summary']; s=q['stats']['all']
 rows.append([r['backend'],r['name'],n(q['first_avoid_s'],1),n(s['delta_applied']['max']),n(s['a_lat']['max'],2),n(s['delta_rate']['max']),n(s['r_applied']['max']),n(s['jerk']['max'],2),f"{q['fallback_samples']}/{q['fallback_during_avoid']}",q['collisions'],n(q['min_gap'])])
table(['백엔드','에피소드','회피시작 s','|δ| rad','|a_lat| m/s²','|δdot| rad/s','|r| rad/s','|jerk| m/s³','FB 전체/회피','충돌','최소간격 m'],rows)
p('표는전체60초의최댓값이다. 회피구간만의분포는다음표와private/summary.json에별도기록했다. NMPC충돌0은현실적차량에서안전했다는뜻이아니다. 126~148m/s² 횡가속을허용하고방향각을무시한충돌박스로측정한결과다. rule의급제동3건은각1회충돌했고숨기지않았다.')
p('### 회피 중 가속과 목표속도')
table(['NMPC 에피소드','AVOIDING 표본','적용 a 최소/최대 m/s²','|a_cmd|<.1','a_cmd<−.1','a_cmd>.1','전체 v_ref 최소/최대'],[
 [r['name'],r['summary']['stats']['avoiding']['samples'], '/'.join(n(r['summary']['stats']['avoiding'][k]) for k in ['a_min','a_max']),
  *[n(None if r['summary']['stats']['avoiding'][k] is None else 100*r['summary']['stats']['avoiding'][k],1)+'%' for k in ['fraction_a_near0','fraction_a_negative','fraction_a_positive']],
  '/'.join(n(x,2) for x in r['summary']['v_ref_min_max'])] for r in nm])
p('brake15/25에서도회피중적용감속최댓값은약0.171/0.172m/s²수준이다. 전체60초최소가속−3은주로이후fallback이므로회피제동성능으로세지않는다. 앞차가급제동해도상태머신은관측속도가16m/s이상이면기준속도+2를요구하고, 그미만이면25m/s를유지한다.')
p('Config가중치는위치0.8, 속도1.0, 가속입력0.1, 조향입력0.2, 입력변화5, slack2000이다. 서로단위가다른Δa와Δδ를같은계수로제곱하므로, 정지명령에서−5m/s²로변경하는한단계벌점은125이고δ=.5rad변경은1.25다. 횡가속하드제한없이횡변위를빠르게만들수있어횡회피가저렴해진다는해석이다. 비용차이를분리한실험은하지않았다.')
p('NMPC예측지평은10×.1=1초다. 최대soft slack은stuck15에서1.045m(종방향스케일타원제약단위)이며나머지는약.00012~.00021m였다. slack은실제차체침범량이아니다. 높은slack가중치도충돌회피의수학적보장을제공하지않는다.')
p('### 저크·조향 변화율·풀이시간 분포')
rows=[]
for r in summary['results']:
 for segment in ['all','avoiding']:
  s=r['summary']['stats'][segment]
  cell=lambda key:'N/A' if s[key] is None else '/'.join(n(s[key][p],3) for p in ['p50','p90','p99','max'])
  rows.append([r['backend'],r['name'],segment,s['samples'],cell('jerk'),cell('delta_rate'),cell('solve_ms')])
table(['백엔드','에피소드','구간','n','|jerk| p50/p90/p99/max','|δdot| p50/p90/p99/max','풀이ms p50/p90/p99/max'],rows)
p('rule의solve_ms=0은최적화풀이가없다는기록값이며규칙계산이실제로0ms라는벤치마크가아니다. NMPC3000회중100ms초과는0회였다. 탐지·그리기·이벤트처리까지포함한GUI프레임시간은측정하지않았다.')
p('### fallback와 복귀')
p('fallback173회는모두AVOIDING종료후NORMAL에서발생했다. 첫발생시각은brake15 37.3초, brake25 31.5초, brake40 29.3초이고당시이웃은각각약193/77/70m뒤에있었다. 기존fallback은−3m/s²와δ=clip(−θ−0.5Y,±.5)다. 이샘플에서는Y≈0이므로fallback이횡회피를원래차선으로강제취소했다는증거는없다. 반면회복가속과fallback감속의교대는약60m/s³저크를만들었다. Solver의상세실패상태/잔차는수집하지않아“왜먼장애물에서실패했는가”는아직확정하지못했다.')
p('현재복귀조건은τ회복이아니라dx<−5m(완전히추월)뿐이다. stuck15에서는τeff>.7인데도AVOIDING에남은표본이합2.7초였다. 이번에는결국추월해복귀했으므로“영원히복귀하지않음”을관측했다고주장하지않는다. 구조적으로동일속도·전방간격을유지하면회복된τ만으로복귀할분기가없다.')
p('### 추가 원인: rule의 저속 접근 사각지대')
p('brake25는46.7초, brake40은45.6초에충돌했다. 그순간τeff는각.411/.329로경보가켜져있었지만a_cmd=0이었다. 이웃중심간격은4.477/4.498m, 접근속도는약.030/.038m/s여서danger의closing>.5조건에서탈락했다. 4.5m차체길이보다간격이작아지는것을TTC또는접근속도조건만으로막지못한사례다. brake15는22.1초에−5m/s²제동중충돌해여유부족사례에해당한다.')
p('### 렌더링과 헤딩')
p('차량사각형은이미헤딩sin/cos로회전한다. 60FPS그리기는마지막10Hz상태를반복표시하며보간하지않는다. 횡26px/m와종10px/m는동일길이척도가아니어서화면각도는arctan(2.6tanθ)로왜곡된다. 이는물리슬립이아니며원래의큰헤딩점프를과장한다. 예측x_pred는점열오버레이일뿐실제위치갱신에사용되지않는다.')
p('자차의차분속도방향과**스텝시작heading**은최대7.1e−13°로같다. 스텝끝heading과비교하면최대31.30°차이가난다. 전진오일러가이전heading으로이동한뒤새heading으로회전하기때문이다. 물리타이어슬립각이31°라는뜻이아니며, 앞으로의5°판정에서도동일시간정렬을고정해야한다.')
p('![진단 시계열](diagnosis_v15b/diagnostic_timeseries.png)')
p('## 수동 입력의 실제 설정과 요청 문구의 불일치')
table(['항목','현재 v15c/v15b 값','해석'],[
['키W / S','+1.5 / −3m/s²','요청의“수동S와같은−8”과다름. 공통플랜트−8과키보드기본−3은분리가능.'],
['가속램프','A_RAMP_T=2초','키목표가바뀔때현재값에서선형램프. 일반적인고정jerk하드상한은아님.'],
['요레이트램프','R_RAMP=.05rad/s²','키를누를때r_cmd변화율. 조향각δ변화율이아님.'],
['요레이트명령상한','R_MAX=.05rad/s','복원항이적용되기전명령상한. 속도별8m/s²한계와별개.'],
['해제감쇠','R_REL=.3초','키를놓았을때만exp(−dt/.3)적용. 상시액추에이터지연은아님.'],
['heading복원','T_PSI=1초','도로 방향으로 돌리는 키보드 보조. 플랜트에서 분리할 것을 제안.'],
['wheelbase','수동에는L없음; NMPC Config L=2.7m','요청에서허용한2.7m를공통모델기준으로쓸수있음.']])
p('고정수동예시(bg=False,D3초·W2초·S2초)에서|a_lat|max=.81415m/s², |r|max=.032566rad/s, 역산δmax=.003517rad, |δdot|max=.015735rad/s, |jerk|max=1.5075m/s³였다. 이는선택된입력예시의관측값이지새플랜트한계로채택한값이아니다.')
p('25m/s에서8m/s²에대응하는조향각은atan(2.7×8/625)=.03455rad, 정상4m/s²는.01728rad다. 기존R_RAMP=.05rad/s²를고정속도25에서조향변화율로바꾸면약.0054rad/s다. 속도와δ에따라변환이달라지므로근거없이상수steer_rate_max를새로정하지않았다.')
p('## 수정 방향 제안 — 아직 설계 확정/구현하지 않음')
p('- 공통Vehicle.step에기구학·입력상태·한계·적분을모으고, 키보드의도로방향복원과목표램프는입력생성계층으로분리한다.')
p('- 속도에따른횡가속한계와조향변화율하드제한을두고, NMPC출력이클립된빈도와실제로적용된입력을기록한다. Config/가중치는그대로둔다.')
p('- NMPC에는TTC단계별target_velocity를낮추는어댑터와제동명령을추가하고, 큰target_y점프는시간연속참조로완화한다. 예측모델과새플랜트의액추에이터차이는별도기록해야한다.')
p('- τ회복2초+간격조건을통한복귀와원차선/속도추종을데모서브클래스로정의한다. 원planning.py는읽기전용으로유지한다.')
p('- TTC=∞인느린접근, 뒤에서접근, 옆차선점유를정책에서분리한다. 뒤차가위험하게접근할때무조건제동하는정책은간격을더줄일수있다.')
p('- 플랜트를더작은dt로공통적분하거나렌더링보간을쓰고, 화면척도/정렬문제를함께정리한다. 3단계의충돌지표에는기존축정렬검사의한계를표기한다.')
p('## 2단계 전에 필요한 확인')
p('1. **기준버전:** 사용자확인완료. 기존demo_v15c의B14b누적기·탐지기·보정·홀드·기본k_drain을유지하고회피만추가한다.')
p('2. **공통플랜트한계와키입력:** 공통물리한계는요청대로+3/−8로두고, 현재키보드목표+1.5/−3및2초램프는유지하는안을제안한다. S키자체도−8로바꿀지는사용자결정이필요하다.')
p('3. **공통액추에이터규약:** 현재코드에는고정조향각변화율/상시1차지연의“같은값”이없다. 새상수를만들지않도록기존.05rad/s²와.3초를요레이트명령공간의공통변화율/지연으로재사용하고δ로변환하는안을제안한다. 이는기존키해제전용감쇠를상시액추에이터로일반화하는설계변경이므로확인이필요하다.')
p('4. **뒤차접근:** 제시된TTC제동단계를앞/횡충돌위험에적용하고, 뒤차단독접근에는제동을기본반응으로강제하지않는방향을제안한다. 요청문구를모든접근방향에그대로적용하면후방간격이악화될수있다.')
p('방향확인후에만README의2단계설계를고정하고구현한다. 원본문서의“1단계진단→내확인→2·3단계”요청때문에여기서멈춘다. 도구/스킬의별도승인요구때문이아니다.')
p('## 파일·실행·Git 명령 (실행하지 않음)')
p('새파일만추가: `DIAGNOSIS_PHASE1.md`, `diagnosis_v15b/DIAGNOSTIC_PLAN.md`, `diagnosis_v15b/diagnose.py`, `diagnosis_v15b/make_report.py`, 진단그림, `private/`로그·JSON. 기존README와실행코드는수정하지않았다. 폴더명의v15b는최초진단시작경위를보존하며현재주결과는private/v15c/에있다.')
p('```sh\n# Drive에서실행. 결과를다시만들때기존private로그를보존할것.\n/opt/anaconda3/bin/python -I -B demo_v15c/diagnosis_v15b/diagnose.py demo_v15c\n/opt/anaconda3/bin/python -I -B demo_v15c/diagnosis_v15b/make_report.py\n```')
p('원시로그·JSON은UAH요약값에의존하는합성궤적이므로private/에서보존하고공유/커밋하지않는다. UAH-DriveSet: Romera, Bergasa, Arroyo, ITSC2016; 비상업연구용·재배포금지. 보고서와그림은집계/사례요약이다.')
p('```sh\n# 아래는검토용명령일뿐실행하지않음\ncd "/Users/mac/Documents/Claude/Projects/논문 만들기/Drive/v2v_trust_planning"\ngit switch -c demo-v15c-avoidance-diagnosis\nmkdir -p reports/demo_v15c/diagnosis\ncp ../demo_v15c/DIAGNOSIS_PHASE1.md reports/demo_v15c/\ncp ../demo_v15c/diagnosis_v15b/DIAGNOSTIC_PLAN.md reports/demo_v15c/diagnosis/\ncp ../demo_v15c/diagnosis_v15b/diagnose.py ../demo_v15c/diagnosis_v15b/make_report.py reports/demo_v15c/diagnosis/\n# private/ cache/ *.pt UAH原本/파생물은추가하지않음\ngit add reports/demo_v15c/DIAGNOSIS_PHASE1.md reports/demo_v15c/diagnosis/*.md reports/demo_v15c/diagnosis/*.py\ngit diff --cached --name-only\ngit commit -m "Diagnose demo_v15c vehicle dynamics and avoidance before implementation"\n```')
p('복사본은보존용이다. 실행재현은원래Drive디렉터리배치와비공유로컬의존성을필요로한다.')
import re
readable=[]
in_code=False
spacing={
 '파일수정':'파일 수정','두백엔드':'두 백엔드','다른시드':'다른 시드','같은차선':'같은 차선',
 '왼쪽차선':'왼쪽 차선','초기중심간격':'초기 중심 간격','초기속도':'초기 속도','초기값과다르다':'초기값과 다르다',
 '때문에주입시점간격은':'때문에 주입 시점 간격은','배경가감속':'배경 가감속','방향지시등가정':'방향지시등 가정',
 '최상위파일':'최상위 파일','실행전후':'실행 전후','검사목록과해시는':'검사 목록과 해시는','새진단하네스만추가했으며':'새 진단 하네스만 추가했으며 ',
 '수정하지않았다':'수정하지 않았다','제어정책':'제어 정책','속도별횡가속제한없음':'속도별 횡가속 제한 없음',
 '전체최대':'전체 최대','수동예시':'수동 예시','자차입력완화없음':'자차 입력 완화 없음','하드한계가아님':'하드 한계가 아님',
 '벌점은':' 벌점은','보간없음':'보간 없음','첫큰회전에서한스텝':' 첫 큰 회전에서 한 스텝 ',
 '풀이최대':'풀이 최대','이번헤드리스에서제어':'이번 헤드리스에서 제어 ', '회피진입':'회피 진입','복귀는즉시':'복귀는 즉시',
 '복귀상태':'복귀 상태','예측지평에상수목표':' 예측 지평에 상수 목표 ', '즉시대입':'즉시 대입',
 '모두급제동시나리오의복귀완료뒤':'모두 급제동 시나리오의 복귀 완료 뒤 ',
 '발생확인':'발생 확인','이번회피의급조향원인은아님':'이번 회피의 급조향 원인은 아님',
 '복귀후종방향급변원인':'복귀 후 종방향 급변 원인','축정렬아님':'축 정렬 아님','실타이어슬립증거없음':'실제 타이어 슬립 증거 없음',
 '차분속도비교시':'차분 속도 비교 시','시간정렬':'시간 정렬','큰적분스텝효과':'큰 적분 스텝 효과',
 '양가속명령':'양의 가속 명령','감속목표없음':'감속 목표 없음','감속없다는가설은기각':'감속 없다는 가설은 기각',
 '키보드자세보조다':'키보드 자세 보조다','공통플랜트':'공통 플랜트','넣지않고':'넣지 않고 ',
 '키보드컨트롤러에둘것을제안':'키보드 컨트롤러에 둘 것을 제안','모두코드확인':'모두 코드 확인',
 '통합플랜트만으로자동해결되지않음':'통합 플랜트만으로 자동 해결되지 않음',
 '직접재계산최대차':'직접 재계산 최대 차이','공통Vehicle':'공통 Vehicle','차량모델':'차량 모델',
 '시나리오당':'시나리오당 ','한스텝':'한 스텝','한단계':'한 단계','설계변경':'설계 변경',
 '확인이필요하다':'확인이 필요하다','사용자결정이필요하다':'사용자 결정이 필요하다',
 '원래차선':'원래 차선','현재코드에는':'현재 코드에는 ','하드제한':'하드 제한','하드상한':'하드 상한',
 '제동단계':'제동 단계','뒤차단독접근에는':'뒤차 단독 접근에는 ','제동을기본반응으로강제하지않는방향을제안한다':'제동을 기본 반응으로 강제하지 않는 방향을 제안한다',
 '모든접근방향에그대로적용하면후방간격이악화될수있다':'모든 접근 방향에 그대로 적용하면 후방 간격이 악화될 수 있다',
 '방향확인후에만':'방향 확인 후에만 ','설계를고정하고구현한다':'설계를 고정하고 구현한다',
 '요청때문에여기서멈춘다':'요청 때문에 여기서 멈춘다','별도승인요구때문이아니다':'별도 승인 요구 때문이 아니다',
 '공통물리한계는요청대로':'공통 물리 한계는 요청대로 ','유지하는안을제안한다':'유지하는 안을 제안한다',
 '값이없다':'값이 없다','새상수를만들지않도록':'새 상수를 만들지 않도록 ',
 '명령공간의공통변화율':'명령 공간의 공통 변화율','재사용하고':'재사용하고 ',
 '기존키해제전용감쇠를상시액추에이터로일반화하는':'기존 키 해제 전용 감쇠를 상시 액추에이터로 일반화하는 ',
 '실도로':'실제 도로','UAH原本':'UAH 원본',
}
for line in '\n'.join(lines).splitlines():
    if line.startswith('```'): in_code=not in_code; readable.append(line); continue
    if not in_code:
        for a,b in spacing.items():line=line.replace(a,b)
        line=re.sub(r'(?<=[가-힣])(?=[A-Za-z0-9])',' ',line)
        line=re.sub(r'(?<=[A-Za-z0-9])(?=[가-힣])',' ',line)
    readable.append(line)
(ROOT/'demo_v15c/DIAGNOSIS_PHASE1.md').write_text('\n'.join(readable)+'\n')
print('wrote DIAGNOSIS_PHASE1.md and diagnostic_timeseries.png')
