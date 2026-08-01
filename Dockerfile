FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Installed version at runtime: bake the VERSION file into the image and
# expose it as an env var (overridable at build time via --build-arg
# APP_VERSION=<version>). The app reads APP_VERSION first, then falls back
# to /app/VERSION / other locations.
COPY VERSION /app/VERSION
ARG APP_VERSION
ENV APP_VERSION=${APP_VERSION}
COPY src/ src/
COPY public/ public/
VOLUME /data
EXPOSE 8001
CMD ["sh", "-c", "exec uvicorn src.app:app --host 0.0.0.0 --port ${PORT:-8001}"]
