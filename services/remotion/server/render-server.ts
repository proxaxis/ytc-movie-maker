import {bundle} from '@remotion/bundler';
import {renderMedia, selectComposition} from '@remotion/renderer';
import express from 'express';
import {execFile} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import {SegmentProps} from '../src/types';

const PORT = Number(process.env.PORT ?? 3100);
const SHARED = process.env.SHARED_DIR ?? '/shared-data';
const CONCURRENCY = Number(process.env.RENDER_CONCURRENCY ?? 2);

// バンドルは初回のみ行いキャッシュ（Chromium 起動は renderMedia 毎 = セグメント毎に新規）
let bundlePromise: Promise<string> | null = null;
const getServeUrl = () => {
  bundlePromise ??= bundle({
    entryPoint: path.resolve(process.cwd(), 'src/index.ts'),
    onProgress: (p) => {
      if (p % 20 === 0) console.log(`[bundle] ${p}%`);
    },
  });
  return bundlePromise;
};

const app = express();
app.use(express.json({limit: '5mb'}));

// shared-data の静的配信（<Audio> が http://localhost:PORT/shared/... で取得）
app.use('/shared', express.static(SHARED));

app.get('/health', (_req, res) => {
  res.json({ok: true});
});

type SegmentManifest = {
  jobId: string;
  title: string;
  fps: number;
  width: number;
  height: number;
  segments: Array<
    SegmentProps & {index: number; id: string}
  >;
};

// ---- セグメント個別レンダリング（OOM対策の核心） ----
app.post('/render-segment', async (req, res) => {
  try {
    const {manifest: manifestRel, segmentIndex} = req.body as {
      manifest: string;
      segmentIndex: number;
    };
    const manifestPath = path.join(SHARED, manifestRel);
    const manifest: SegmentManifest = JSON.parse(
      fs.readFileSync(manifestPath, 'utf-8'),
    );
    const seg = manifest.segments[segmentIndex];
    if (!seg) {
      res.status(404).json({error: `segment ${segmentIndex} not found`});
      return;
    }

    const inputProps: SegmentProps = {
      title: seg.title,
      chapterNumber: seg.chapterNumber,
      chapterCount: seg.chapterCount,
      bullets: seg.bullets,
      sentences: seg.sentences,
      progressStart: seg.progressStart,
      progressEnd: seg.progressEnd,
      durationInFrames: seg.durationInFrames,
      audioBaseUrl: `http://localhost:${PORT}/shared`,
    };

    const serveUrl = await getServeUrl();
    const composition = await selectComposition({
      serveUrl,
      id: 'SegmentVideo',
      inputProps,
    });

    const outDir = path.join(SHARED, 'jobs', manifest.jobId, 'renders');
    fs.mkdirSync(outDir, {recursive: true});
    const outPath = path.join(outDir, `seg_${String(segmentIndex).padStart(2, '0')}.mp4`);

    console.log(
      `[render] seg${segmentIndex} ${composition.durationInFrames}f -> ${outPath}`,
    );
    await renderMedia({
      composition,
      serveUrl,
      codec: 'h264',
      outputLocation: outPath,
      inputProps,
      concurrency: CONCURRENCY,
      // concat -c copy で安全に結合できるよう全セグメント同一設定で描画
      pixelFormat: 'yuv420p',
    });

    const rel = path.relative(SHARED, outPath);
    console.log(`[render] seg${segmentIndex} done`);
    res.json({file: rel});
  } catch (e) {
    console.error('[render] error', e);
    res.status(500).json({error: String(e)});
  }
});

// ---- FFmpeg concat（再エンコードなし -c copy） ----
app.post('/concat', async (req, res) => {
  try {
    const {dir, files, output} = req.body as {
      dir: string;
      files: string[];
      output: string;
    };
    const jobDir = path.join(SHARED, dir);
    const listPath = path.join(jobDir, 'concat.txt');
    const list = files
      .map((f) => `file '${path.join(SHARED, f).replace(/'/g, "'\\''")}'`)
      .join('\n');
    fs.writeFileSync(listPath, list + '\n');
    const outPath = path.join(jobDir, output);

    await new Promise<void>((resolve, reject) => {
      execFile(
        'ffmpeg',
        ['-y', '-f', 'concat', '-safe', '0', '-i', listPath, '-c', 'copy', outPath],
        (err, _stdout, stderr) => {
          if (err) reject(new Error(stderr));
          else resolve();
        },
      );
    });

    const rel = path.relative(SHARED, outPath);
    console.log(`[concat] ${files.length} segments -> ${rel}`);
    res.json({file: rel});
  } catch (e) {
    console.error('[concat] error', e);
    res.status(500).json({error: String(e)});
  }
});

app.listen(PORT, () => {
  console.log(`remotion-renderer listening on :${PORT}, shared=${SHARED}`);
});
