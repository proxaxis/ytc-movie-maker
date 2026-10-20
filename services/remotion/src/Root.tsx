import React from 'react';
import {Composition} from 'remotion';
import {SegmentVideo} from './SegmentVideo';
import {SegmentProps} from './types';

const defaultProps: SegmentProps = {
  title: 'サンプルチャプター',
  chapterNumber: 1,
  chapterCount: 6,
  bullets: ['要点1', '要点2', '要点3'],
  sentences: [],
  progressStart: 0,
  progressEnd: 1 / 6,
  durationInFrames: 300,
  audioBaseUrl: '',
};

export const Root: React.FC = () => {
  return (
    <Composition
      id="SegmentVideo"
      component={SegmentVideo}
      durationInFrames={300}
      fps={30}
      width={1920}
      height={1080}
      defaultProps={defaultProps}
      // セグメントごとに manifest の durationInFrames で尺を決定
      calculateMetadata={({props}) => ({
        durationInFrames: Math.max(1, props.durationInFrames ?? 300),
      })}
    />
  );
};
