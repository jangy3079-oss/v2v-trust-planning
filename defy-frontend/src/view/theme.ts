/*
 * 화면 색·글꼴 (캔버스·SVG 용). CSS 쪽 값은 styles.css 의 :root 와 맞춘다.
 * 명세 3.1·4.1: tau_det = 하늘색, tau_eff = 주황색, tau_a2 = 회색.
 * 밝은 기록지(패널·그래프) 위에서는 같은 색상을 한 단계 진하게 써서 대비를 맞춘다.
 */
export const FONT_UI = '"IBM Plex Sans KR", system-ui, sans-serif';
export const FONT_NUM = '"Barlow Semi Condensed", "IBM Plex Sans KR", system-ui, sans-serif';

// 패널·그래프 (밝은 기록지 위)
export const PAPER = {
  sheet: '#F5F7F6',
  ink: '#1A2632',
  ink2: '#4E5B66',
  ink3: '#7A858D',
  grid: '#CBD3D7',
  track: '#D5DCDF',
  strip: '#DCE2E4',
  prep: 'rgba(242,194,48,.12)',
  det: '#1E9BD0',
  eff: '#E8711F',
  a2: '#8C949C',
  alarmRef: '#C8322B',
  recoveryRef: '#2E8B57',
} as const;
