#!/bin/sh
# VOICEVOX Engine の話者一覧を表示し、.env の VOICEVOX_SPEAKER 選定に使う。
#   ./services/tts/list-speakers.sh
set -eu
HOST="${VOICEVOX_HOST:-http://localhost:50021}"

curl -sf "${HOST}/speakers" \
  | python3 -c "
import json, sys
for sp in json.load(sys.stdin):
    styles = ', '.join(f\"{s['name']}={s['id']}\" for s in sp['styles'])
    print(f\"{sp['name']}: {styles}\")
"
echo
echo "-> .env の VOICEVOX_SPEAKER に上記のIDを設定してください"
