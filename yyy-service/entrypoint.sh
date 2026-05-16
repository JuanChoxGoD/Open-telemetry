#!/bin/sh
set -e

echo "=========================================="
echo "Iniciando yyy-service..."
echo "OpenTelemetry Service Name: $OTEL_SERVICE_NAME"
echo "=========================================="

# Opcional: Podrías usar opentelemetry-instrument aquí
# exec opentelemetry-instrument "$@"

# Por defecto ejecutamos el comando pasado desde el CMD del Dockerfile
exec "$@"
