FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 既定では依存サービスの healthy 後に起動し、RUN_ON_STARTUP に応じて
# ジョブを1回実行→アイドル待機（exec での再実行が可能）
CMD ["python", "main.py", "--run-mode", "env"]
