FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY pyproject.toml README.md constraints.txt ./
COPY src ./src
RUN pip install --no-cache-dir -c constraints.txt . && useradd --uid 10001 --create-home appuser \
    && mkdir /data && chown appuser:appuser /data
USER appuser
ENV CITEPAPER_DATABASE=/data/citepaper.sqlite3
EXPOSE 8000
CMD ["cite-paper", "serve", "--host", "0.0.0.0"]
