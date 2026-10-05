import React, {CSSProperties} from 'react';
import {AbsoluteFill, Audio, Img, interpolate, interpolateColors, spring, staticFile, useCurrentFrame} from 'remotion';

const C = {paper: '#F5F3EA', ink: '#203E35', muted: '#768679', line: '#D6DED1', mint: '#C6E5D2', dark: '#14251F'};
const font = '-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif';
const clamp = (v: number, a = 0, b = 1) => Math.max(a, Math.min(b, v));
const smooth = (f: number, start: number, end: number) => {
  const p = clamp((f - start) / (end - start)); return p * p * (3 - 2 * p);
};
const mix = (a: number, b: number, p: number) => a + (b - a) * p;
const ease = (f: number, start: number, duration = 46) => spring({frame: f - start, fps: 60,
  config: {damping: 22, stiffness: 115, mass: .85}, durationInFrames: duration});
const abs = (style: CSSProperties): CSSProperties => ({position: 'absolute', ...style});

function Reveal({children, frame, delay = 0, style}: {children: React.ReactNode; frame: number; delay?: number; style?: CSSProperties}) {
  const p = ease(frame, delay);
  return <div style={{overflow: 'hidden', ...style}}><div style={{transform: `translateY(${(1 - p) * 110}%)`, opacity: smooth(frame, delay, delay + 18)}}>{children}</div></div>;
}

function Sprig({frame, kind = 'idle', style}: {frame: number; kind?: 'idle' | 'performance'; style?: CSSProperties}) {
  const cycle = ((Math.max(0, Math.floor(frame)) % 238) + 238) % 238;
  const index = kind === 'performance' ? Math.round(clamp(frame, 0, 299)) : cycle <= 119 ? cycle : 238 - cycle;
  return <Img src={staticFile(`sprig/${kind}/frame-${String(index).padStart(4, '0')}.png`)}
    style={{display: 'block', width: '100%', height: '100%', objectFit: 'contain', ...style}} />;
}

function Scene({frame, from, to, children, style}: {frame: number; from: number; to: number; children: React.ReactNode; style?: CSSProperties}) {
  if (frame < from || frame >= to + 22) return null;
  const opacity = from === 0 ? 1 : smooth(frame, from, from + 22);
  return <AbsoluteFill style={{opacity, background: C.paper, ...style}}>{children}</AbsoluteFill>;
}

function Header({n, label, frame, color = C.ink}: {n: string; label: string; frame: number; color?: string}) {
  return <div style={abs({left: 70, right: 70, top: 64, display: 'flex', justifyContent: 'space-between', alignItems: 'center', color})}>
    <div style={{display: 'flex', alignItems: 'center', gap: 14, fontSize: 23, letterSpacing: '-.025em', fontWeight: 650}}>
      <svg width="30" height="30" viewBox="0 0 30 30"><path d="M6 21V13a9 9 0 0 1 18 0v8M9 8 7 3M21 8l2-5" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round"/><circle cx="11" cy="16" r="1.7" fill="currentColor"/><circle cx="19" cy="16" r="1.7" fill="currentColor"/><path d="M12 21q3 3 6 0" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/></svg>
      codex-usage-bar
    </div>
    <div style={{display: 'flex', gap: 15, alignItems: 'center', fontSize: 18, letterSpacing: '.1em', opacity: .65}}><span>{label}</span><span style={{height: 16, borderLeft: '1px solid currentColor', opacity: .35}}/><span>{n}</span></div>
    <div style={abs({left: 0, right: 0, top: 57, height: 1, background: color, opacity: .13, transformOrigin: 'left', transform: `scaleX(${smooth(frame, 0, 36)})`})}/>
  </div>;
}

function Title({frame, title, english, color = C.ink}: {frame: number; title: string; english: string; color?: string}) {
  return <div style={abs({top: 190, left: 74, right: 55, color})}>
    <Reveal frame={frame} delay={7} style={{fontSize: 79, fontWeight: 560, lineHeight: 1.3, letterSpacing: '-.07em'}}>{title}</Reveal>
    <Reveal frame={frame} delay={20} style={{marginTop: 22, fontSize: 28, fontWeight: 400, letterSpacing: '-.015em', opacity: .68}}>{english}</Reveal>
  </div>;
}

function FilmFooter({dark = false}: {dark?: boolean}) {
  return <div style={abs({bottom: 43, left: 72, right: 72, display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 28, color: dark ? '#A4BCAF' : '#768476', letterSpacing: '.015em'})}>
    <span>产品演示 · 额度为示例数据</span><span style={{fontSize: 20}}>MACOS · v0.9.0</span>
  </div>;
}

function Opening({frame}: {frame: number}) {
  const p = ease(frame, 15, 68);
  return <>
    <Header n="01" label="MEET SPRIG" frame={frame}/>
    <Title frame={frame} title="留一点余量。" english="A little room to keep going."/>
    <div style={abs({left: 105, top: 520, width: 870, height: 580, borderRadius: '50%', border: '1px solid #CCDCCD', transform: `scale(${mix(.86, 1, p)}) rotate(-12deg)`, opacity: .72})}/>
    <div style={abs({left: 120, top: 619, fontSize: 214, fontWeight: 750, letterSpacing: '-.075em', color: '#DDE5D8', lineHeight: 1, transform: `translateX(${mix(-40, 0, p)}px)`})}>SPRIG</div>
    <div style={abs({left: 118, top: 360, width: 844, height: 844, transform: `translateY(${mix(90, 0, p)}px) scale(${mix(.82, 1, p)})`, opacity: p})}>
      <Sprig kind="performance" frame={frame}/>
    </div>
    <div style={abs({left: 76, top: 1122, display: 'flex', gap: 15, alignItems: 'center', opacity: smooth(frame, 70, 110)})}>
      <span style={{width: 7, height: 7, borderRadius: '50%', background: '#6EAC89'}}/>
      <span style={{fontSize: 23, fontWeight: 500}}>Sprig · 芽团</span><span style={{fontSize: 20, color: C.muted}}>你的 3D 小伙伴</span>
    </div>
    <FilmFooter/>
  </>;
}

function Cursor({x, y, opacity = 1, click = 0}: {x: number; y: number; opacity?: number; click?: number}) {
  return <div style={abs({left: x, top: y, width: 42, height: 54, opacity, transform: `scale(${1 - click * .13})`, transformOrigin: '8px 8px', filter: 'drop-shadow(0 4px 4px #10251c25)', zIndex: 30})}>
    <svg width="42" height="54" viewBox="0 0 42 54"><path d="M7 4v37l10-10 7 16 7-3-7-16h14Z" fill="#FDFDF8" stroke="#264137" strokeWidth="2.8" strokeLinejoin="round"/></svg>
  </div>;
}

function ProductWindow({frame, x = 60, y = 620, width = 960, collapse = 0, locale = 'zh', dark = 0}: {
  frame: number; x?: number; y?: number; width?: number; collapse?: number; locale?: 'zh' | 'en'; dark?: number;
}) {
  const barWidth = width - 60, scale = barWidth / 768;
  const fullHeight = 94 * scale, smallHeight = 44 * scale;
  const height = mix(fullHeight, smallHeight, collapse);
  const barY = 88, bottom = barY + height + 16;
  const darkOn = dark >= .5;
  const surface = interpolateColors(dark, [0, 1], ['#FFFFFF', '#1C2925']);
  const shell = interpolateColors(dark, [0, 1], ['#EAF0E8', '#253C32']);
  const ink = interpolateColors(dark, [0, 1], ['#738479', '#A2B6A7']);
  return <div style={abs({left: x, top: y, width, height: bottom + 160, borderRadius: 34, border: `1px solid ${darkOn ? '#496052' : '#D2DDD0'}`, background: surface,
    boxShadow: `0 36px 75px ${darkOn ? '#04100c30' : '#38563c10'}, 0 4px 14px #19382905`, overflow: 'hidden'})}>
    <div style={abs({top: 26, left: 28, display: 'flex', gap: 8})}>{['#D3DCD0', '#DDE4D8', '#C9D9C9'].map(c => <span key={c} style={{height: 9, width: 9, borderRadius: 10, background: c}}/>)}</div>
    <span style={abs({top: 20, left: 0, width: '100%', textAlign: 'center', color: ink, fontSize: 18, fontWeight: 500})}>Codex</span>
    <div style={abs({left: 30, top: barY, width: barWidth, height})}>
      {(['light', 'dark'] as const).map(theme => <React.Fragment key={theme}>
        <Img src={staticFile(`ui/bar-${locale}-${theme}-expanded.png`)} style={abs({left: 0, top: 0, width: barWidth, height: fullHeight, opacity: (1 - collapse) * (theme === 'dark' ? dark : 1 - dark)})}/>
        <Img src={staticFile(`ui/bar-${locale}-${theme}-compact.png`)} style={abs({left: 0, top: 0, width: barWidth, height: smallHeight, opacity: collapse * (theme === 'dark' ? dark : 1 - dark)})}/>
      </React.Fragment>)}
      <div style={abs({left: 17 * scale, top: 19 * scale, width: 56 * scale, height: 56 * scale, opacity: 1 - collapse})}><Sprig frame={frame}/></div>
    </div>
    <div style={abs({left: 30, right: 30, top: bottom, height: 112, borderRadius: 23, border: `1px solid ${darkOn ? '#435C4B' : '#D4DFD1'}`, background: surface, color: ink})}>
      <span style={abs({left: 23, top: 20, fontSize: 22, opacity: .8})}>{locale === 'zh' ? '继续你的想法…' : 'Keep your ideas going…'}</span>
      <span style={abs({left: 23, bottom: 15, fontSize: 25, fontWeight: 300})}>＋</span>
      <span style={abs({right: 19, bottom: 16, width: 26, height: 26, borderRadius: '50%', background: shell, textAlign: 'center', fontSize: 19})}>↑</span>
    </div>
    <span style={abs({left: 32, top: bottom + 127, fontSize: 14, color: ink, letterSpacing: '.04em'})}>INTERFACE DEMONSTRATION</span>
  </div>;
}

function Metrics({frame}: {frame: number}) {
  return <>
    <Header n="02" label="AT A GLANCE" frame={frame}/>
    <Title frame={frame} title="用量，一眼就懂。" english="Your limits. Right where you work."/>
    <div style={abs({left: 76, right: 76, top: 395, display: 'grid', gridTemplateColumns: '1fr 1fr 1fr'})}>
      {[['每周剩余', '72', '%'], ['5 小时已用', '18', '%'], ['缓存命中', '—', '']].map(([label, value, suffix], i) => {
        const p = ease(frame, 23 + i * 9);
        return <div key={label} style={{paddingLeft: i ? 29 : 0, borderLeft: i ? '1px solid #D0DDCF' : undefined, transform: `translateY(${(1 - p) * 38}px)`, opacity: p}}>
          <div style={{fontSize: 23, color: C.muted}}>{label}</div>
          <div style={{marginTop: 7, fontSize: 82, lineHeight: 1.18, letterSpacing: '-.065em', fontWeight: 560}}>{value}<span style={{fontSize: 38, marginLeft: 5, fontWeight: 420}}>{suffix}</span></div>
          <div style={{fontSize: 17, marginTop: 7, color: C.muted}}>{i === 0 ? 'Weekly remaining' : i === 1 ? '5-hour usage' : '无统计时显示 —'}</div>
        </div>;
      })}
    </div>
    <div style={{transform: `translateY(${(1 - ease(frame, 46)) * 55}px)`, opacity: smooth(frame, 40, 67)}}>
      <ProductWindow frame={frame} y={670}/>
    </div>
    <div style={abs({left: 80, top: 1160, color: C.muted, fontSize: 22, opacity: smooth(frame, 100, 140)})}>就在原生输入框上方。</div>
    <FilmFooter/>
  </>;
}

function Fold({frame}: {frame: number}) {
  const p = smooth(frame, 108, 143);
  const travel = smooth(frame, 35, 100);
  const cx = mix(1010, 954, travel), cy = mix(860, 604, travel);
  const cursorOpacity = smooth(frame, 23, 40) * (1 - smooth(frame, 163, 182));
  const ring = smooth(frame, 108, 142);
  return <>
    <Header n="03" label="LESS, BUT ENOUGH" frame={frame}/>
    <Title frame={frame} title="收起来，也看得见。" english="Less interface. Same useful numbers."/>
    <ProductWindow frame={frame} y={440} collapse={p}/>
    {frame >= 108 && frame < 143 && <div style={abs({left: 953 - 45 * ring, top: 604 - 45 * ring, width: 90 * ring, height: 90 * ring, border: '2px solid #6FA184', borderRadius: '50%', opacity: 1 - ring})}/>}
    <Cursor x={cx} y={cy} opacity={cursorOpacity} click={Math.sin(clamp((frame - 105) / 12) * Math.PI)}/>
    <div style={abs({left: 77, top: 922, color: C.ink})}>
      <Reveal frame={frame} delay={150} style={{fontSize: 54, letterSpacing: '-.055em', fontWeight: 520}}>额度还在，专注继续。</Reveal>
      <Reveal frame={frame} delay={165} style={{fontSize: 24, marginTop: 22, color: C.muted}}>折叠时，Sprig 也休息一下。</Reveal>
    </div>
    <FilmFooter/>
  </>;
}

function Preferences({frame}: {frame: number}) {
  const language = smooth(frame, 120, 145), dark = smooth(frame, 204, 240);
  const ink = interpolateColors(dark, [0, 1], [C.ink, '#E5F1E7']);
  const muted = interpolateColors(dark, [0, 1], [C.muted, '#A4B8A9']);
  const locale = frame >= 132 ? 'zh' : 'en';
  return <>
    <div style={abs({width: 3200, height: 3200, borderRadius: '50%', left: -600, top: -1220, background: C.dark, transform: `scale(${dark})`, transformOrigin: '50% 50%'})}/>
    <Header n="04" label="FEELS LIKE YOU" frame={frame} color={ink}/>
    <Title frame={frame} title="你的语言。你的主题。" english="English or 中文. Light or dark." color={ink}/>
    <div style={abs({left: 78, top: 406, display: 'flex', gap: 24, alignItems: 'center', color: muted})}>
      <span style={{fontSize: 23}}>Codex 语言</span>
      <div style={{position: 'relative', border: `1px solid ${dark > .5 ? '#46604F' : '#C8D7C7'}`, width: 378, height: 62, borderRadius: 40, display: 'flex', alignItems: 'center', padding: 5}}>
        <div style={abs({left: 5 + 184 * language, top: 5, height: 50, width: 182, background: '#C7E4CE', borderRadius: 30})}/>
        {['English', '简体中文'].map((word, i) => <span key={word} style={{zIndex: 1, width: 184, textAlign: 'center', fontSize: 23, color: (i === 0 ? language < .5 : language > .5) ? C.ink : muted}}>{word}</span>)}
      </div>
      <div style={{marginLeft: 4, width: 96, height: 56, borderRadius: 36, background: dark > .5 ? '#345440' : '#DFE8DB', position: 'relative'}}>
        <span style={abs({left: 5 + dark * 40, top: 5, width: 46, height: 46, borderRadius: '50%', background: '#F7F8EC', textAlign: 'center', lineHeight: '46px', fontSize: 27, color: C.ink})}>{dark > .5 ? '☾' : '☀'}</span>
      </div>
    </div>
    <ProductWindow frame={frame} y={565} locale={locale} dark={dark}/>
    <div style={abs({left: 79, top: 1044, fontSize: 35, color: ink, lineHeight: 1.65, letterSpacing: '-.03em'})}>
      <Reveal frame={frame} delay={50}>跟随 App 设置，自然融入。</Reveal>
      <Reveal frame={frame} delay={65} style={{fontSize: 23, color: muted}}>切换语言，保留当前额度与状态。</Reveal>
    </div>
    <FilmFooter dark={dark > .5}/>
  </>;
}

function Closing({frame}: {frame: number}) {
  const p = ease(frame, 15, 68);
  return <>
    <Header n="05" label="MAKE ROOM" frame={frame}/>
    <div style={abs({left: 74, top: 183, color: C.ink})}>
      <Reveal frame={frame} delay={12} style={{fontSize: 79, fontWeight: 560, lineHeight: 1.25, letterSpacing: '-.065em'}}>保持专注。</Reveal>
      <Reveal frame={frame} delay={29} style={{fontSize: 79, fontWeight: 560, lineHeight: 1.25, letterSpacing: '-.065em'}}>余量有数。</Reveal>
    </div>
    <div style={abs({left: 409, top: 324, width: 577, height: 577, borderRadius: '50%', background: '#D7EBD9', opacity: .76, transform: `scale(${mix(.4, 1, p)})`})}/>
    <div style={abs({left: 183, top: 361, width: 778, height: 778, transform: `translateY(${mix(35, 0, p)}px)`, opacity: p})}><Sprig frame={frame} kind="performance"/></div>
    <div style={abs({left: 77, top: 1097})}>
      <Reveal frame={frame} delay={61} style={{fontSize: 44, fontWeight: 600, letterSpacing: '-.04em'}}>codex-usage-bar</Reveal>
      <Reveal frame={frame} delay={72} style={{fontSize: 22, marginTop: 16, color: C.muted}}>Meet Sprig. Keep your flow.</Reveal>
    </div>
    <FilmFooter/>
  </>;
}

export const SprigFilm = () => {
  const frame = useCurrentFrame();
  return <AbsoluteFill style={{background: C.paper, color: C.ink, fontFamily: font, WebkitFontSmoothing: 'antialiased', overflow: 'hidden'}}>
    <Audio src={staticFile('audio/sprig-score.wav')}/>
    <Scene frame={frame} from={0} to={240}><Opening frame={frame}/></Scene>
    <Scene frame={frame} from={240} to={570}><Metrics frame={frame - 240}/></Scene>
    <Scene frame={frame} from={570} to={840}><Fold frame={frame - 570}/></Scene>
    <Scene frame={frame} from={840} to={1140}><Preferences frame={frame - 840}/></Scene>
    <Scene frame={frame} from={1140} to={1440} style={{background: '#EEF2E5'}}><Closing frame={frame - 1140}/></Scene>
  </AbsoluteFill>;
};
