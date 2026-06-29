FROM python:3.11-slim

WORKDIR /app

# Install the package (which also exposes the "lol-stats" console script) and
# all its runtime dependencies declared in pyproject.toml.
COPY . .
RUN pip install --no-cache-dir .

# Railway injects $PORT at runtime; default to 8000 for local runs.
ENV PORT=8000
EXPOSE 8000

# Bind to 0.0.0.0 so the service is reachable from outside the container.
# sh -c is required so ${PORT} is expanded at runtime.
CMD ["sh", "-c", "lol-stats serve --host 0.0.0.0 --port ${PORT:-8000}"]
