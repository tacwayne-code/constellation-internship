FROM python:3.12-slim-bookworm

WORKDIR /app
COPY app.py identityctl.py ./
RUN useradd --system --uid 65532 gateway
USER gateway
EXPOSE 8010
CMD ["python", "app.py"]
