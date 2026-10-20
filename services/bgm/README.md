# BGM ライブラリの用意

DOVA-SYNDROME / 効果音ラボ には公式APIがないため、楽曲は **手動でダウンロードして
ライブラリに登録** する運用です（自動スクレイピングは各サイトの利用規約に触れる
可能性があるため実装していません）。

## 登録手順

1. 楽曲をダウンロードする
   - DOVA-SYNDROME: https://dova-s.jp/ （フリーBGM）
   - 効果音ラボ: https://soundeffect-lab.info/ （フリー効果音・一部BGM）
   - **各曲のページでライセンス条件を必ず確認** すること（クレジット表記要否など）

2. mp3/wav を `shared-data/bgm/` にコピーする

3. `shared-data/bgm/library.json` の `tracks` にエントリを追加する

```json
{
  "file": "my_bgm.mp3",
  "title": "曲名",
  "artist": "作曲者",
  "source": "DOVA-SYNDROME",
  "url": "https://dova-s.jp/bgm/play12345.html",
  "license": "利用規約の要点（例: 商用可・クレジット表記）",
  "mood": ["calm"]
}
```

`mood` は LLM が生成する `bgm_mood`（英単語）と照合される。
複数登録可。例: `calm`, `epic`, `upbeat`, `dark`, `relaxing`, `suspense`。

## 動作

- 台本生成（LLM 1段階目）が `bgm_mood` を出力 → orchestrator が mood 一致の
  トラックをランダム選択（一致なし＝全件からランダム）
- `POST /finalize` で BGM をループ再生しつつ、ナレーションをキーにした
  **sidechaincompress ダッキング** と **loudnorm (-14 LUFS)** を適用
- `title/artist/source/url/license` がジョブの `description.txt` に自動転記され、
  YouTube 概要欄にそのまま貼れる（クレジット表記の徹底 = 規約遵守・権利関係の証跡）

## 広告はく奪・Content ID リスク低減の注意点

- Content ID 登録済み楽曲や JASRAC/NexTone 管理楽曲は避ける（DOVA の楽曲ページに
  「著作権フリー」等の表記があるものを選ぶ）
- 同一曲を全動画で使い回さない（ライブラリを複数曲にして mood で分散させる）
- 効果音ラボの素材は主に効果音向け。BGMとして短いループを使う場合もライセンス確認
