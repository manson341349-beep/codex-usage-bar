/**
 * SPRIG — a small, grounded toy's performance score.
 *
 * All lengths use the model's local units (designed for a ~2.4-unit tall toy).
 * Angles are radians. Arms point down in local space; +L / -R Z opens the arms.
 * Pointer Y is positive upward. `update` takes elapsed seconds, never milliseconds.
 *
 * Usage:
 *   const actor = new PerformanceController();
 *   actor.setPointer(nx, ny, true); // entering also elicits one small greeting
 *   actor.setState('play');         // repeated clicks do not restart a performance
 *   const pose = actor.update(dt);  // apply to the corresponding rig pivots
 *
 * Pause calls to update while the scene is invisible; discard that hidden elapsed
 * time on resume. This class deliberately does not own requestAnimationFrame.
 * `work` and `done` are animation commands, not claims about real Codex activity.
 * V2: pointer attention is brief; a stationary pointer is not a permanent gaze
 * target. Returned pointerAttention / pointerStillSeconds expose that contract.
 */

const TAU = Math.PI * 2;
const clamp = (v, lo = 0, hi = 1) => Math.max(lo, Math.min(hi, v));
const mix = (a, b, t) => a + (b - a) * t;
const smooth = t => { t = clamp(t); return t * t * (3 - 2 * t); };
const easeOut = t => 1 - Math.pow(1 - clamp(t), 3);
const windowed = (t, start, inEnd, outStart, end) =>
  smooth((t - start) / (inEnd - start)) * (1 - smooth((t - outStart) / (end - outStart)));

const NEUTRAL = Object.freeze({
  x: 0, y: 0, sx: 1, sy: 1, sz: 1, rx: 0, ry: 0, rz: 0,
  headX: 0, headY: 0, headZ: 0,
  armL: 0.13, armR: -0.13, armLX: 0, armRX: 0,
  footL: 0, footR: 0, earL: 0, earR: 0,
  gazeX: 0, gazeY: 0, blink: 1, wink: 1, smile: 0.38,
  mouthOpen: 0, work: 0, spark: 0,
});
const CHANNELS = Object.keys(NEUTRAL);
const DURATIONS = Object.freeze({ hello: 1.8, play: 2.9, done: 3.15 });
const STATES = new Set(['idle', 'hello', 'play', 'work', 'done']);

/** Exact second-order spring integration, including underdamped secondary motion. */
class Spring {
  constructor(value, frequency, damping = 1) {
    this.value = value;
    this.velocity = 0;
    this.frequency = frequency;
    this.damping = damping;
  }
  reset(value) { this.value = value; this.velocity = 0; }
  step(target, dt) {
    const w = TAU * this.frequency;
    const a = this.value - target;
    const z = this.damping;
    if (z >= 0.999) {
      const b = this.velocity + w * a;
      const e = Math.exp(-w * dt);
      this.value = target + (a + b * dt) * e;
      this.velocity = (this.velocity - w * b * dt) * e;
    } else {
      const wd = w * Math.sqrt(1 - z * z);
      const b = (this.velocity + z * w * a) / wd;
      const e = Math.exp(-z * w * dt);
      const c = Math.cos(wd * dt), s = Math.sin(wd * dt);
      this.value = target + e * (a * c + b * s);
      this.velocity = e * ((b * wd - z * w * a) * c - (a * wd + z * w * b) * s);
    }
    return this.value;
  }
}

// A score specifies readable held poses. Springs supply continuity and follow-through.
const frame = (t, pose) => ({ t, pose: { ...NEUTRAL, ...pose } });
const PLAY = [
  frame(0, { smile: .42 }),
  frame(.20, { headZ: -.045, headX: -.025, smile: .6, armL: .19, armR: -.22 }),
  frame(.52, { sy: .85, rx: .10, rz: -.055, x: -.05, headX: .12, headZ: -.06,
    armL: .23, armR: -.19, armLX: .50, armRX: .37, gazeY: -.16, smile: .38 }),
  frame(.65, { sy: 1.095, y: .12, x: -.015, rx: -.025, rz: .045,
    armL: .65, armR: -.80, armLX: -.40, armRX: -.22, footL: .035, footR: .015,
    headX: -.08, earL: -.16, earR: .13, smile: .76, mouthOpen: .25 }),
  frame(.85, { sy: 1.035, y: .36, x: .075, rx: -.03, rz: .10, ry: -.09,
    armL: 1.30, armR: -1.08, armLX: -.27, armRX: -.45, footL: .095, footR: .025,
    headZ: -.085, headX: -.055, gazeY: .12, smile: .95, mouthOpen: .32 }),
  frame(1.04, { sy: 1.065, y: .19, x: .10, rz: .065,
    armL: .84, armR: -.73, armLX: -.13, armRX: -.15, footL: .024,
    headX: .02, earL: .11, earR: -.09, smile: .88, mouthOpen: .13 }),
  frame(1.18, { sy: .79, y: 0, x: .095, rx: .11, rz: -.035,
    armL: .59, armR: -.44, armLX: -.20, armRX: -.12,
    headX: .13, earL: .24, earR: -.20, blink: .48, smile: .65 }),
  frame(1.29, { sy: .84, x: .09, rx: .06, rz: -.035,
    armL: .47, armR: -.36, headX: .065, smile: .76 }),
  frame(1.53, { sy: 1.035, x: .055, rz: -.055, headZ: .10, headX: -.025,
    armL: .56, armR: -.28, armLX: -.12, armRX: -.20, smile: .98 }),
  frame(1.78, { x: .05, rz: -.045, headZ: .12, headX: -.025,
    armL: .59, armR: -.25, armLX: -.11, armRX: -.23, smile: .97, wink: .02 }),
  frame(1.97, { x: .05, rz: -.045, headZ: .12, headX: -.025,
    armL: .59, armR: -.25, armLX: -.11, armRX: -.23, smile: .97, wink: .02 }),
  frame(2.18, { x: .04, rz: -.035, headZ: .085,
    armL: .46, armR: -.21, smile: .83 }),
  frame(2.52, { x: .012, rz: -.01, headZ: .06, smile: .61 }),
  frame(2.9, {}),
];
const HELLO = [
  frame(0, {}),
  // Meet the viewer's eyes, acknowledge with one nod, then open the wave.
  frame(.16, { headX: -.04, smile: .57 }),
  frame(.32, { headX: .075, headZ: -.028, armR: -.22, smile: .70 }),
  frame(.49, { x: -.025, rz: .015, headX: -.025, headZ: -.055,
    armR: -.80, armRX: -.20, smile: .77 }),
  frame(.68, { x: -.04, rz: .025, headX: -.04, headZ: -.075,
    armR: -1.48, armRX: -.09, armL: .16, smile: .85 }),
  frame(.9, { x: -.035, rz: .02, headX: -.025, headZ: -.05, armR: -1.30, armRX: -.12, smile: .85 }),
  frame(1.14, { x: -.025, headX: -.01, headZ: -.025, armR: -1.40, armRX: -.10, smile: .77 }),
  frame(1.47, { armR: -.40, smile: .62 }),
  frame(1.8, {}),
];
const DONE = [
  frame(0, { headX: .12, gazeY: -.23, armLX: -.72, armRX: -.65, armL: .35, armR: -.35, work: .7 }),
  frame(.24, { headX: -.08, smile: .76, armLX: -.53, armRX: -.50,
    armL: .31, armR: -.31, work: .25 }),
  frame(.53, { sy: .84, rx: .08, headX: .045, gazeY: .03,
    armLX: -.42, armRX: -.45, armL: .40, armR: -.36, smile: .77 }),
  frame(.69, { y: .14, sy: 1.10, rx: -.045, headX: -.10,
    armL: 1.70, armR: -1.51, armLX: -.20, armRX: -.16,
    footL: .015, footR: .05, earL: -.17, earR: .13, smile: 1, mouthOpen: .58, spark: .1 }),
  frame(.88, { y: .34, sy: 1.025, rx: -.035, rz: -.035, headX: -.08, headZ: .055,
    armL: 2.12, armR: -2.28, armLX: -.07, armRX: -.12,
    footL: .04, footR: .075, smile: 1, mouthOpen: .60, spark: 1 }),
  frame(1.08, { y: .13, sy: 1.06, headX: .015, armL: 1.97, armR: -2.13,
    footL: .005, footR: .016, smile: 1, mouthOpen: .36, spark: .68 }),
  frame(1.20, { y: 0, sy: .82, rx: .065, headX: .09, armL: 1.58, armR: -1.85,
    earL: .23, earR: -.23, blink: .57, smile: .92, mouthOpen: .14, spark: .33 }),
  frame(1.35, { sy: .96, headX: .02, armL: 1.01, armR: -1.87,
    smile: .97, mouthOpen: .12, spark: .13 }),
  frame(1.55, { sy: 1.025, x: -.025, rz: -.045, headZ: .085,
    armL: .35, armR: -2.14, armRX: -.08, smile: .96 }),
  frame(1.83, { x: -.03, rz: -.045, headZ: .09,
    armL: .25, armR: -1.67, armRX: -.15, smile: .96 }),
  frame(2.05, { x: -.025, rz: -.035, headZ: .08,
    armL: .23, armR: -2.05, armRX: -.06, smile: .91 }),
  frame(2.28, { x: -.02, rz: -.025, headZ: .06,
    armL: .20, armR: -1.80, armRX: -.13, smile: .9 }),
  frame(2.65, { headZ: .04, armR: -.50, smile: .76 }),
  frame(3.15, {}),
];

function sampleScore(score, t) {
  if (t <= 0) return { ...score[0].pose };
  let index = 1;
  while (index < score.length - 1 && t > score[index].t) index++;
  const a = score[index - 1], b = score[index];
  const progress = smooth((t - a.t) / (b.t - a.t));
  const pose = {};
  for (const key of CHANNELS) pose[key] = mix(a.pose[key], b.pose[key], progress);
  return pose;
}

function tapArm(phase) {
  // Lift slowly, strike quickly, recoil, then leave the other hand its turn.
  const points = [[0, -.95], [.13, -1.16], [.26, -1.31], [.35, -.72], [.43, -.90], [.65, -.95], [1, -.95]];
  for (let i = 1; i < points.length; i++) {
    if (phase <= points[i][0]) {
      const a = points[i - 1], b = points[i];
      return mix(a[1], b[1], smooth((phase - a[0]) / (b[0] - a[0])));
    }
  }
  return -.95;
}

function automaticBlink(t) {
  // Deterministic irregular spacing plus one occasional natural double blink.
  const period = t % 12.7;
  for (const onset of [2.85, 6.62, 6.94, 10.9]) {
    const age = period - onset;
    if (age >= 0 && age < .17) {
      return age < .055 ? 1 - smooth(age / .055) : smooth((age - .055) / .115);
    }
  }
  return 1;
}

function workExchange(t) {
  const beat = Math.max(0, t - .50) % 4.3;
  return {
    eyes: windowed(beat, 2.78, 2.93, 3.59, 3.83),
    head: windowed(beat, 2.86, 3.10, 3.54, 3.95),
    nod: windowed(beat, 3.20, 3.29, 3.33, 3.48),
  };
}

function phaseLabel(state, t, reduced = false) {
  if (state === 'hello') return t < .22 ? '看着你 · 眼神聚焦' : t < .50 ? '轻轻点头回应' : t < 1.2 ? '向你挥手' : '放下手 · 回到正面';
  if (state === 'play') return t < .25 ? '看着你确认' : t < .59 ? '蹲下蓄力' : t < 1.12 ? '轻巧跃起' : t < 1.47 ? '软软落地' : t < 2.23 ? '回看你 · 得意眨眼' : '慢慢归位';
  if (state === 'done') return t < .3 ? '抬眼告诉你完成了' : t < .60 ? '收拢蓄力' : t < 1.12 ? '双手欢呼' : t < 1.43 ? '落地回弹' : t < 2.45 ? '向你开心挥手' : '回到正面 · 满足陪伴';
  if (state === 'work') {
    if (t < .55) return '低头准备 · 演示';
    if (!reduced && workExchange(t).eyes > .06) return '抬眼看你 · 点头回应 · 演示';
    return '交替处理小任务 · 演示';
  }
  return '正面看着你 · 安静陪伴';
}

export class PerformanceController {
  constructor() {
    this.reduced = false;
    this.reset();
  }

  reset() {
    this.state = 'idle';
    this.time = 0;
    this.elapsed = 0;
    this.pointer = { x: 0, y: 0, active: false };
    this._pointerAnchor = { x: 0, y: 0 };
    this._pointerStillSeconds = 60;
    this.pose = { ...NEUTRAL, state: 'idle', phase: '安静陪伴' };
    this._blendFrom = { ...NEUTRAL };
    this._blendTime = 1;
    this._previousYVelocity = 0;
    this._springs = {};
    for (const key of CHANNELS) {
      let frequency = 11, damping = 1;
      if (key.startsWith('head')) { frequency = 4.8; damping = .95; }
      else if (key.startsWith('ear')) { frequency = key === 'earL' ? 4.6 : 4.2; damping = .44; }
      else if (key.startsWith('arm')) { frequency = 7.7; damping = .90; }
      else if (key === 'gazeX' || key === 'gazeY') { frequency = 14; }
      else if (key === 'blink' || key === 'wink') { frequency = 26; }
      else if (key === 'y' || key === 'sy') { frequency = 17; }
      this._springs[key] = new Spring(NEUTRAL[key], frequency, damping);
    }
    return this.pose;
  }

  setState(name) {
    if (!STATES.has(name)) return false;
    if (name === this.state) return false;
    // A hover greeting never steals a deliberate performance or ongoing work.
    if (name === 'hello' && this.state !== 'idle') return false;
    this._blendFrom = { ...this.pose };
    this._blendTime = 0;
    this.state = name;
    this.elapsed = 0;
    return true;
  }

  setPointer(x, y, active = true) {
    const entering = !!active && !this.pointer.active;
    this.pointer.x = Number.isFinite(x) ? clamp(x, -1, 1) : 0;
    this.pointer.y = Number.isFinite(y) ? clamp(y, -1, 1) : 0;
    this.pointer.active = !!active;
    // Accumulate travel relative to the last meaningful point. Repeated pointer
    // events and subpixel jitter never keep the character staring to one side.
    const travel = Math.hypot(this.pointer.x - this._pointerAnchor.x,
      this.pointer.y - this._pointerAnchor.y);
    if (this.pointer.active && (entering || travel >= .065)) {
      this._pointerStillSeconds = 0;
      this._pointerAnchor = { x: this.pointer.x, y: this.pointer.y };
    } else if (!this.pointer.active) this._pointerStillSeconds = 60;
    if (entering && this.state === 'idle' && !this.reduced) this.setState('hello');
  }

  setReduced(value) {
    this.reduced = !!value;
    if (this.reduced) {
      const target = this._target();
      for (const key of CHANNELS) this._springs[key].reset(target[key]);
      this._previousYVelocity = 0;
      this.pose = { ...target, state: this.state, phase: phaseLabel(this.state, this.elapsed, true) };
    }
  }

  _target() {
    const t = this.elapsed;
    let target = { ...NEUTRAL };
    if (this.reduced) {
      // Reduced motion conveys state through a held expression and arm silhouette.
      if (this.state === 'work') Object.assign(target, { headX: .11, gazeY: -.25, armL: .35, armR: -.35, armLX: -.90, armRX: -.90, work: 1, smile: .27 });
      else if (this.state === 'done') Object.assign(target, { armL: .40, armR: -1.60, smile: .94 });
      else if (this.state === 'play' || this.state === 'hello') Object.assign(target, { headZ: -.08, armR: -.55, smile: .82 });
    } else if (this.state === 'play') target = sampleScore(PLAY, t);
    else if (this.state === 'done') target = sampleScore(DONE, t);
    else if (this.state === 'hello') target = sampleScore(HELLO, t);
    else if (this.state === 'work') {
      const ready = smooth(t / .55);
      const working = Math.max(0, t - .50);
      const left = (working / 1.38) % 1;
      const right = (left + .5) % 1;
      const leftContact = Math.exp(-Math.pow((left - .36) / .07, 2));
      const rightContact = Math.exp(-Math.pow((right - .36) / .07, 2));
      const contact = leftContact + rightContact;
      Object.assign(target, {
        x: .025 * Math.sin(left * TAU),
        rx: .085 * ready, sy: 1 - .013 * contact * ready,
        headX: (.115 + .027 * contact) * ready,
        headY: .035 * Math.sin(left * TAU) * ready,
        headZ: .025 * Math.sin(left * TAU + .3) * ready,
        armL: mix(.13, .42, ready), armR: mix(-.13, -.42, ready),
        armLX: tapArm(left) * ready, armRX: tapArm(right) * ready,
        gazeY: -.31 * ready, gazeX: .055 * Math.sin(left * TAU),
        smile: .28, work: ready,
        earL: .025 * contact, earR: -.02 * contact,
      });
      // Eyes arrive first; the head follows, gives a small acknowledgement, and
      // returns to the task. Hand mechanics and planted contact remain unchanged.
      const exchange = workExchange(t);
      target.gazeX *= 1 - exchange.eyes;
      target.gazeY *= 1 - exchange.eyes;
      target.headY *= 1 - exchange.head;
      target.headZ *= 1 - exchange.head;
      target.headX = mix(target.headX, -target.rx - .025, exchange.head)
        + .052 * exchange.nod;
      target.smile = mix(target.smile, .68, exchange.head);
    } else {
      const breath = Math.sin(this.time * TAU / 4.6);
      target.sy = 1 + .006 * breath;
      target.rx = .008 * Math.sin(this.time * TAU / 6.9);
      target.headX = -target.rx;
      target.headZ = .012 * Math.sin(this.time * TAU / 7.3 + .8);
      target.armL += .012 * breath;
      target.armR -= .010 * Math.sin(this.time * TAU / 4.6 - .23);
    }

    // Preserve volume instead of inflating the whole toy during a squash.
    target.sx *= Math.pow(target.sy, -.5);
    target.sz *= Math.pow(target.sy, -.35);

    // A new movement gets a short look; after 0.70 s the intended target is the
    // viewer again. Deliberate greetings/performances address the viewer directly.
    const attention = this._pointerAttention();
    const pointerWeight = this.state === 'idle' ? attention :
      this.state === 'work' ? .10 * attention * (1 - workExchange(t).eyes) : 0;
    const px = this.pointer.active ? this.pointer.x : 0;
    const py = this.pointer.active ? this.pointer.y : 0;
    target.gazeX += px * .48 * pointerWeight;
    target.gazeY += py * .34 * pointerWeight;
    target.headY += px * .11 * pointerWeight;
    target.headX -= py * .075 * pointerWeight;
    target.headY = clamp(target.headY, -.115 - target.ry, .115 - target.ry);
    target.headZ = clamp(target.headZ, -.14 - target.rz, .14 - target.rz);

    if (!this.reduced) {
      target.blink *= automaticBlink(this.time);
      // A quiet acknowledgement replaces the old unprompted sideways glance.
      if (this.state === 'idle' && attention < .02) {
        const nod = windowed(this.time % 15.4, 8.1, 8.24, 8.38, 8.66);
        target.headX += .035 * nod;
        target.smile += .12 * nod;
      }
    }
    return target;
  }

  _pointerAttention() {
    if (this.reduced || !this.pointer.active) return 0;
    return 1 - smooth((this._pointerStillSeconds - .22) / .48);
  }

  update(dt = 0) {
    if (!Number.isFinite(dt) || dt < 0) dt = 0;
    // Small integration slices keep moving-target springs consistent across frame rates.
    // No wall clock or randomness is used, so capture/replay remains deterministic.
    let remaining = dt;
    do {
      const step = Math.min(remaining, 1 / 120);
      this.time += step;
      this.elapsed += step;
      this._pointerStillSeconds = Math.min(60, this._pointerStillSeconds + step);
      this._blendTime += step;
      const duration = DURATIONS[this.state];
      if (duration && this.elapsed >= duration) this.setState('idle');
      let target = this._target();

      if (this.reduced) {
        for (const key of CHANNELS) this._springs[key].reset(target[key]);
      } else {
        const blend = easeOut(this._blendTime / .21);
        if (blend < 1) {
          for (const key of CHANNELS) target[key] = mix(this._blendFrom[key], target[key], blend);
        }
        // Ears respond to acceleration after the body, with unequal mass/damping.
        // Clamp acceleration so state interruptions never produce a violent ear whip.
        const yVelocity = this._springs.y.velocity;
        const acceleration = step > 0 ? clamp((yVelocity - this._previousYVelocity) / step, -35, 35) : 0;
        this._previousYVelocity = yVelocity;
        target.earL += acceleration * -.0048 + this._springs.rz.velocity * -.08;
        target.earR += acceleration * .0041 + this._springs.rz.velocity * -.065;
        for (const key of CHANNELS) this._springs[key].step(target[key], step);
      }
      for (const key of CHANNELS) this.pose[key] = this._springs[key].value;
      this.pose.y = Math.max(0, this.pose.y);
      this.pose.footL = Math.max(0, this.pose.footL);
      this.pose.footR = Math.max(0, this.pose.footR);
      for (const key of ['blink', 'wink', 'smile', 'mouthOpen', 'work', 'spark']) this.pose[key] = clamp(this.pose[key]);
      this.pose.gazeX = clamp(this.pose.gazeX, -1, 1);
      this.pose.gazeY = clamp(this.pose.gazeY, -1, 1);
      this.pose.state = this.state;
      this.pose.phase = phaseLabel(this.state, this.elapsed, this.reduced);
      this.pose.pointerAttention = this._pointerAttention();
      this.pose.pointerStillSeconds = this._pointerStillSeconds;
      this.pose.eyeContact = this.state === 'work' ? (this.reduced ? 0 : workExchange(this.elapsed).eyes) :
        this.state === 'idle' ? 1 - this._pointerAttention() : 1;
      remaining -= step;
    } while (remaining > 1e-9);
    // A fresh object prevents UI/capture clients from retaining a mutable old pose.
    return { ...this.pose };
  }
}
