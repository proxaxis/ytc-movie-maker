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

const ProgressBar: React.FC<{
  progressStart: number;
  progressEnd: number;
  hue: number;
}> = ({progressStart, progressEnd, hue}) => {
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
          background: `linear-gradient(90deg, ${accent(hue)}, ${accent2(hue)})`,
        }}
      />
    </div>
  );
};

const ChapterHeader: React.FC<{
  chapterNumber: number;
  chapterCount: number;
  title: string;
  hue: number;
  hasChar: boolean;
}> = ({chapterNumber, chapterCount, title, hue, hasChar}) => {
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
          color: accent(hue),
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

const CharacterImg: React.FC<{src: string; wink?: string}> = ({src, wink}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const enter = spring({frame, fps, config: {damping: 15, stiffness: 60}});
  const bob = Math.sin((frame / fps) * 1.6) * 10; // ゆるい上下浮遊
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
    transform: `translateX(${80 * (1 - enter)}px) translateY(${bob}px)`,
  };
  return (
    <>
      <Img src={src} style={baseStyle} />
      {wink ? <Img src={wink} style={{...baseStyle, opacity: enter * (winking ? 1 : 0)}} /> : null}
    </>
  );
};

const Bullets: React.FC<{bullets: string[]; hue: number; hasChar: boolean}> = ({
  bullets,
  hue,
  hasChar,
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return (
    <div
      style={{
        position: 'absolute',
        top: 420,
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
                background: accent(hue),
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

const Subtitle: React.FC<{text: string; hasChar: boolean}> = ({text, hasChar}) => {
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

  // 現在話しているセリフ（startSec が最も近い過去のもの）
  let current: string | null = null;
  for (const s of props.sentences) {
    if (s.startSec <= t) current = s.text;
  }

  return (
    <AbsoluteFill style={{background: BG}}>
      <ProgressBar
        progressStart={props.progressStart}
        progressEnd={props.progressEnd}
        hue={hue}
      />
      <ChapterHeader
        chapterNumber={props.chapterNumber}
        chapterCount={props.chapterCount}
        title={props.title}
        hue={hue}
        hasChar={!!props.characterImage}
      />
      <Bullets bullets={props.bullets} hue={hue} hasChar={!!props.characterImage} />
      {current ? (
        <Subtitle text={current} hasChar={!!props.characterImage} />
      ) : null}
      {props.characterImage ? (
        <CharacterImg src={props.characterImage} wink={props.characterWink} />
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
