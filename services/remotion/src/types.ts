export type Sentence = {
  text: string;
  /** shared-data からの相対パス (例: jobs/xxx/audio/ch1/s00.wav) */
  audio: string;
  startSec: number;
  durationSec: number;
};

export type SegmentProps = {
  title: string;
  chapterNumber: number;
  chapterCount: number;
  bullets: string[];
  sentences: Sentence[];
  /** 動画全体に対するこのセグメントの進捗範囲 0..1 */
  progressStart: number;
  progressEnd: number;
  durationInFrames: number;
  /** 例: http://localhost:3100/shared */
  audioBaseUrl: string;
  /** ジョブ固有のアクセント色相 0-360（テンプレ感抑制・バリエーション用） */
  accentHue?: number;
  /** キャラクター立ち絵（基本）のURL */
  characterImage?: string;
  /** キャラクター立ち絵（ウインク差分）のURL。指定時は周期的に切替表示 */
  characterWink?: string;
};
