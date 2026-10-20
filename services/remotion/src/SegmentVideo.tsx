import React from 'react';
import {
  AbsoluteFill,
  Audio,
  Img,
  Sequence,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import {SegmentProps} from './types';

const BG =
  'linear-gradient(160deg, #0b1026 0%, #141a3a 55%, #1d1040 100%)';
const FONT = '"Noto Sans CJK JP","Noto Sans JP",sans-serif';

const accent = (hue: number) => `hsl(${hue} 85% 62%)`;
const accent2 = (hue: number) => `hsl(${(hue + 60) % 360} 80% 65%)`;

/** CSS色文字列をRGBに分解（#rrggbb / rgb(r,g,b)） */
const parseColor = (c: string): [number, number, number] | null => {
  const hex = /^#?([0-9a-f]{6})$/i.exec(c.trim());
  if (hex) {
    const n = parseInt(hex[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  const rgb = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(c);
  return rgb ? [Number(rgb[1]), Number(rgb[2]), Number(rgb[3])] : null;
};

/** 決定的PRNG（mulberry32）: 同じシードで毎回同じ系列 */
const mulberry32 = (seed: number) => {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
};

/** レトロゲーム風の手続き生成背景: パララックスで流れる地形・ブロック・星 */
const GameBackground: React.FC<{seed: number; c1: string}> = ({seed, c1}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const t = frame / fps;
  const rand = mulberry32(seed);

  const stars = Array.from({length: 46}, () => ({
    x: rand() * width,
    y: rand() * height * 0.7,
    s: 2 + rand() * 5,
    v: 30 + rand() * 70,
  }));
  const blocks = Array.from({length: 14}, () => ({
    x: rand() * (width + 400),
    y: height * 0.62 + rand() * height * 0.3,
    w: 40 + rand() * 140,
    h: 20 + rand() * 60,
    v: 150 + rand() * 180,
    o: 0.10 + rand() * 0.14,
  }));
  const coins = Array.from({length: 8}, () => ({
    x: rand() * (width + 300),
    y: height * 0.2 + rand() * height * 0.45,
    r: 14 + rand() * 16,
    v: 110 + rand() * 60,
  }));
  const wrap = (x: number, span: number) =>
    ((x % span) + span) % span - 200;

  return (
    <AbsoluteFill style={{opacity: 0.85, overflow: 'hidden'}}>
      {stars.map((s, i) => (
        <div
          key={`s${i}`}
          style={{
            position: 'absolute',
            left: wrap(s.x - t * s.v, width + 400),
            top: s.y,
            width: s.s,
            height: s.s,
            background: 'rgba(255,255,255,0.25)',
            borderRadius: s.s > 5 ? 2 : 0,
          }}
        />
      ))}
      {coins.map((c, i) => (
        <div
          key={`c${i}`}
          style={{
            position: 'absolute',
            left: wrap(c.x - t * c.v, width + 400),
            top: c.y + Math.sin(t * 2 + i) * 12,
            width: c.r,
            height: c.r,
            borderRadius: c.r / 2,
            border: `3px solid ${c1}`,
            opacity: 0.5,
          }}
        />
      ))}
      {blocks.map((b, i) => (
        <div
          key={`b${i}`}
          style={{
            position: 'absolute',
            left: wrap(b.x - t * b.v, width + 500),
            top: b.y,
            width: b.w,
            height: b.h,
            background: `rgba(255,255,255,${b.o})`,
            borderTop: `4px solid rgba(255,255,255,${b.o + 0.15})`,
          }}
        />
      ))}
      {/* 地面ライン */}
      <div
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          top: height * 0.92,
          height: 3,
          background: 'rgba(255,255,255,0.12)',
        }}
      />
    </AbsoluteFill>
  );
};

/** 固定色から補助色を生成（彩度を落として明るく） */
const lighten = (c: string, amt = 0.25): string => {
  const p = parseColor(c);
  if (!p) return c;
  const [r, g, b] = p.map((v) => Math.round(v + (255 - v) * amt));
  return `rgb(${r}, ${g}, ${b})`;
};

const ProgressBar: React.FC<{
  progressStart: number;
  progressEnd: number;
  c1: string;
  c2: string;
}> = ({progressStart, progressEnd, c1, c2}) => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  const p = interpolate(
    frame,
    [0, Math.max(1, durationInFrames - 1)],
    [progressStart, progressEnd],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );
  return (
    <div
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        right: 0,
        height: 14,
        background: 'rgba(255,255,255,0.12)',
      }}
    >
      <div
        style={{
          height: '100%',
          width: `${p * 100}%`,
          background: `linear-gradient(90deg, ${c1}, ${c2})`,
        }}
      />
    </div>
  );
};

const ChapterHeader: React.FC<{
  chapterNumber: number;
  chapterCount: number;
  title: string;
  c1: string;
  hasChar: boolean;
}> = ({chapterNumber, chapterCount, title, c1, hasChar}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const enter = spring({frame, fps, config: {damping: 18, stiffness: 90}});
  return (
    <div
      style={{
        position: 'absolute',
        top: 80,
        left: 110,
        right: hasChar ? 110 + CHARACTER_RESERVE : 110,
        opacity: enter,
        transform: `translateY(${30 * (1 - enter)}px)`,
      }}
    >
      <div
        style={{
          color: c1,
          fontSize: 34,
          fontWeight: 700,
          letterSpacing: 4,
          fontFamily: FONT,
        }}
      >
        CHAPTER {chapterNumber} / {chapterCount}
      </div>
      <div
        style={{
          color: 'white',
          fontSize: 72,
          fontWeight: 800,
          marginTop: 16,
          fontFamily: FONT,
          lineHeight: 1.25,
        }}
      >
        {title}
      </div>
    </div>
  );
};

// キャラクター立ち絵（春日部つむぎ等）: 右下に浮遊アニメーションで表示
const CHARACTER_RESERVE = 600; // 立ち絵表示時に右側に確保する幅(px)

const CharacterImg: React.FC<{src: string; wink?: string; speaking?: boolean}> = ({
  src,
  wink,
  speaking,
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const enter = spring({frame, fps, config: {damping: 15, stiffness: 60}});
  const t = frame / fps;
  const bob = Math.sin(t * 1.6) * 10; // ゆるい上下浮遊
  const tilt = Math.sin(t * 1.1) * 1.5; // ゆるい首振り
  const talk = speaking ? 1 + Math.abs(Math.sin(t * 7)) * 0.015 : 1; // 発話中の弾み
  // 約4秒周期で0.35秒だけウインク（まばたき演出）
  const winking =
    wink != null && frame % Math.round(fps * 4.0) < Math.round(fps * 0.35);
  const baseStyle: React.CSSProperties = {
    position: 'absolute',
    right: 40,
    bottom: 0,
    height: 860,
    objectFit: 'contain',
    opacity: enter,
    transform:
      `translateX(${80 * (1 - enter)}px) translateY(${bob}px) ` +
      `rotate(${tilt}deg) scale(${talk})`,
    transformOrigin: 'bottom center',
  };
  return (
    <>
      <Img src={src} style={baseStyle} />
      {wink ? <Img src={wink} style={{...baseStyle, opacity: enter * (winking ? 1 : 0)}} /> : null}
    </>
  );
};

const Bullets: React.FC<{
  bullets: string[];
  c1: string;
  hasChar: boolean;
  hasImage?: boolean;
}> = ({bullets, c1, hasChar, hasImage}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return (
    <div
      style={{
        position: 'absolute',
        top: hasImage ? 640 : 420,
        left: 140,
        right: hasChar ? 140 + CHARACTER_RESERVE : 140,
      }}
    >
      {bullets.map((b, i) => {
        const enter = spring({
          frame: frame - i * Math.round(fps * 0.6),
          fps,
          config: {damping: 16, stiffness: 100},
        });
        return (
          <div
            key={i}
            style={{
              display: 'flex',
              alignItems: 'center',
              marginTop: 30,
              opacity: enter,
              transform: `translateX(${40 * (1 - enter)}px)`,
            }}
          >
            <div
              style={{
                width: 18,
                height: 18,
                borderRadius: 9,
                background: c1,
                marginRight: 26,
                flexShrink: 0,
              }}
            />
            <div
              style={{
                color: 'rgba(255,255,255,0.92)',
                fontSize: 46,
                fontFamily: FONT,
                fontWeight: 600,
              }}
            >
              {b}
            </div>
          </div>
        );
      })}
    </div>
  );
};

const Subtitle: React.FC<{text: string; hasChar: boolean; c1: string}> = ({
  text,
  hasChar,
  c1,
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const enter = spring({
    frame,
    fps,
    config: {damping: 20, stiffness: 160},
    durationInFrames: Math.round(fps * 0.3),
  });
  return (
    <div
      style={{
        position: 'absolute',
        bottom: 70,
        left: 100,
        right: hasChar ? 100 + CHARACTER_RESERVE : 100,
        display: 'flex',
        justifyContent: 'center',
        opacity: enter,
      }}
    >
      <div
        style={{
          borderLeft: `8px solid ${c1}`,
          background: 'rgba(0,0,0,0.72)',
          color: 'white',
          fontSize: 52,
          fontWeight: 700,
          fontFamily: FONT,
          padding: '22px 44px',
          borderRadius: 16,
          textAlign: 'center',
          lineHeight: 1.45,
          maxWidth: hasChar ? 1700 - CHARACTER_RESERVE : 1700,
        }}
      >
        {text}
      </div>
    </div>
  );
};

export const SegmentVideo: React.FC<SegmentProps> = (props) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps;
  const hue = props.accentHue ?? 218;
  const c1 = props.accentColor ?? accent(hue);
  const c2 =
    props.accentColor2 ??
    (props.accentColor ? lighten(props.accentColor) : accent2(hue));

  // 現在話しているセリフ（startSec が最も近い過去のもの）
  let current: string | null = null;
  for (const s of props.sentences) {
    if (s.startSec <= t) current = s.text;
  }

  return (
    <AbsoluteFill style={{background: BG}}>
      {(props.bgStyle ?? 'game') !== 'none' ? (
        <GameBackground seed={props.bgSeed ?? hue} c1={c1} />
      ) : null}
      <ProgressBar
        progressStart={props.progressStart}
        progressEnd={props.progressEnd}
        c1={c1}
        c2={c2}
      />
      <ChapterHeader
        chapterNumber={props.chapterNumber}
        chapterCount={props.chapterCount}
        title={props.title}
        c1={c1}
        hasChar={!!props.characterImage}
      />
      {props.image ? (
        <Img
          src={props.image}
          style={{
            position: 'absolute',
            top: 370,
            left: 140,
            height: 240,
            borderRadius: 18,
            border: `3px solid ${c1}`,
            objectFit: 'cover',
          }}
        />
      ) : null}
      <Bullets
        bullets={props.bullets}
        c1={c1}
        hasChar={!!props.characterImage}
        hasImage={!!props.image}
      />
      {current ? (
        <Subtitle text={current} hasChar={!!props.characterImage} c1={c1} />
      ) : null}
      {props.characterImage ? (
        <CharacterImg
          src={props.characterImage}
          wink={props.characterWink}
          speaking={current != null}
        />
      ) : null}

      {/* セリフごとのナレーション音声をタイミング通りに配置 */}
      {props.sentences.map((s, i) => (
        <Sequence
          key={i}
          from={Math.round(s.startSec * fps)}
          durationInFrames={Math.max(1, Math.round(s.durationSec * fps))}
        >
          <Audio src={`${props.audioBaseUrl}${s.audio.startsWith('/') ? '' : '/'}${s.audio}`} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
