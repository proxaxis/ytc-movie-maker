import React from 'react';
import {
  AbsoluteFill,
  Audio,
  Sequence,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import {SegmentProps} from './types';

const BG =
  'linear-gradient(160deg, #0b1026 0%, #141a3a 55%, #1d1040 100%)';
const ACCENT = '#4f8dff';

const ProgressBar: React.FC<{
  progressStart: number;
  progressEnd: number;
}> = ({progressStart, progressEnd}) => {
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
          background: `linear-gradient(90deg, ${ACCENT}, #8f6bff)`,
        }}
      />
    </div>
  );
};

const ChapterHeader: React.FC<{
  chapterNumber: number;
  chapterCount: number;
  title: string;
}> = ({chapterNumber, chapterCount, title}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const enter = spring({frame, fps, config: {damping: 18, stiffness: 90}});
  return (
    <div
      style={{
        position: 'absolute',
        top: 80,
        left: 110,
        right: 110,
        opacity: enter,
        transform: `translateY(${30 * (1 - enter)}px)`,
      }}
    >
      <div
        style={{
          color: ACCENT,
          fontSize: 34,
          fontWeight: 700,
          letterSpacing: 4,
          fontFamily: 'sans-serif',
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
          fontFamily: 'sans-serif',
          lineHeight: 1.25,
        }}
      >
        {title}
      </div>
    </div>
  );
};

const Bullets: React.FC<{bullets: string[]}> = ({bullets}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return (
    <div style={{position: 'absolute', top: 420, left: 140, right: 140}}>
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
                background: ACCENT,
                marginRight: 26,
                flexShrink: 0,
              }}
            />
            <div
              style={{
                color: 'rgba(255,255,255,0.92)',
                fontSize: 46,
                fontFamily: 'sans-serif',
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

const Subtitle: React.FC<{text: string}> = ({text}) => {
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
        right: 100,
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
          fontFamily: 'sans-serif',
          padding: '22px 44px',
          borderRadius: 16,
          textAlign: 'center',
          lineHeight: 1.45,
          maxWidth: 1700,
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
      />
      <ChapterHeader
        chapterNumber={props.chapterNumber}
        chapterCount={props.chapterCount}
        title={props.title}
      />
      <Bullets bullets={props.bullets} />
      {current ? <Subtitle text={current} /> : null}

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
