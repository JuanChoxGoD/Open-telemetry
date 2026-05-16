import os
import logging
import sqlite3
import random
import time
from fastapi import FastAPI, HTTPException, Request

# OpenTelemetry Imports
from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter as GrpcSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter as GrpcMetricExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME

from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlite3 import SQLite3Instrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor

# Setup Resource
resource = Resource(attributes={
    SERVICE_NAME: os.environ.get("OTEL_SERVICE_NAME", "yyy-service")
})

# Setup Tracing
provider = TracerProvider(resource=resource)
otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
processor = BatchSpanProcessor(GrpcSpanExporter(endpoint=otlp_endpoint, insecure=True))
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

# Setup Metrics
metric_reader = PeriodicExportingMetricReader(GrpcMetricExporter(endpoint=otlp_endpoint, insecure=True))
meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
metrics.set_meter_provider(meter_provider)

# Setup Logging Correlated with Tracing
LoggingInstrumentor().instrument(set_logging_format=True)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Setup SQLite
db_path = "history.db"
conn = sqlite3.connect(db_path, check_same_thread=False)
cursor = conn.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS user_history (user_id INTEGER, orders_count INTEGER)")
cursor.execute("INSERT INTO user_history (user_id, orders_count) VALUES (1, 5)")
cursor.execute("INSERT INTO user_history (user_id, orders_count) VALUES (2, 10)")
conn.commit()

app = FastAPI()

# Auto-instrument FastAPI and SQLite
FastAPIInstrumentor.instrument_app(app)
SQLite3Instrumentor().instrument()

@app.get("/history/{user_id}")
async def get_history(user_id: int, request: Request):
    logger.info(f"Consultando historial para el usuario: {user_id}")
    
    # Manual span for business logic
    with tracer.start_as_current_span("process_history_lookup") as span:
        span.set_attribute("user.id", user_id)
        
        # Simulate some processing time (random latency to answer "Which operation generated latency?")
        sleep_time = random.uniform(0.1, 0.5)
        time.sleep(sleep_time)
        span.set_attribute("simulated_latency_seconds", sleep_time)
        
        try:
            # Query the database
            cursor.execute("SELECT orders_count FROM user_history WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            
            if not result:
                # Si el usuario no está, simulamos error para responder a la pregunta de errores
                if user_id > 100:
                    span.set_status(trace.status.Status(trace.status.StatusCode.ERROR, "User not found (simulated error)"))
                    logger.error(f"Error forzado: Usuario {user_id} no encontrado")
                    raise HTTPException(status_code=404, detail="User not found")
                    
                logger.info(f"Usuario {user_id} sin historial previo")
                return {"user_id": user_id, "previous_orders": 0}
            
            logger.info(f"Historial encontrado: {result[0]} órdenes previas")
            return {"user_id": user_id, "previous_orders": result[0]}
            
        except sqlite3.Error as e:
            span.record_exception(e)
            span.set_status(trace.status.Status(trace.status.StatusCode.ERROR, str(e)))
            logger.error(f"Error de Base de Datos: {str(e)}")
            raise HTTPException(status_code=500, detail="Database Error")

@app.get("/health")
def health_check():
    return {"status": "up"}
