FROM python:3.12-slim

WORKDIR /app

# 依存ライブラリを先にインストールしてレイヤーキャッシュを活用する
COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY *.py ./

CMD ["python", "main.py"]
