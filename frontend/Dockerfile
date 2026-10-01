# Frontend de pruebas para Cloud Run (solo librería estándar de Python)
FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app
COPY server.py .
COPY static ./static
RUN useradd --create-home --uid 10001 app
USER app
EXPOSE 8080
CMD ["python", "server.py"]
