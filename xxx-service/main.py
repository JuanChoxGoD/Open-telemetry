import os
import logging
import requests
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

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
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor

# Setup Resource
resource = Resource(attributes={
    SERVICE_NAME: os.environ.get("OTEL_SERVICE_NAME", "xxx-service")
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
meter = metrics.get_meter(__name__)

# Custom Metric: Counter for orders received
orders_counter = meter.create_counter(
    "orders_received_total",
    description="Total number of orders received",
)

# Setup Logging Correlated with Tracing
LoggingInstrumentor().instrument(set_logging_format=True)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

app = FastAPI()

# Auto-instrument FastAPI and Requests
FastAPIInstrumentor.instrument_app(app)
RequestsInstrumentor().instrument()

# Configure YYY_SERVICE_URL - For Cloud Run use full HTTPS URL
YYY_SERVICE_URL = os.environ.get("YYY_SERVICE_URL", "http://localhost:8001")

logger.info("="*60)
logger.info("Iniciando aplicación xxx-service")
logger.info(f"YYY_SERVICE_URL configurado como: {YYY_SERVICE_URL}")
logger.info("="*60)

class OrderRequest(BaseModel):
    user_id: int
    product_id: int
    quantity: int

@app.post("/order")
async def create_order(order: OrderRequest):
    logger.info(f"Recibiendo petición de nueva orden para user_id: {order.user_id}")
    
    # Custom metric increment
    orders_counter.add(1, {"endpoint": "/order"})
    
    # Manual span for validation
    with tracer.start_as_current_span("validate_order") as span:
        if order.quantity <= 0:
            span.record_exception(ValueError("Invalid quantity"))
            span.set_status(trace.status.Status(trace.status.StatusCode.ERROR, "Quantity must be > 0"))
            logger.error(f"✗ Validación fallida para user_id {order.user_id}: Cantidad inválida.")
            raise HTTPException(status_code=400, detail="Quantity must be greater than 0")
        span.set_attribute("user.id", order.user_id)
        span.set_attribute("product.id", order.product_id)
        logger.info("✓ Validación exitosa")

    logger.info(f"Llamando a yyy-service en: {YYY_SERVICE_URL}/history/{order.user_id}")
    
    # Call yyy-service with detailed error handling
    try:
        history_url = f"{YYY_SERVICE_URL}/history/{order.user_id}"
        logger.info(f"URL completa: {history_url}")
        
        response = requests.get(history_url, timeout=10)
        logger.info(f"Status code de yyy-service: {response.status_code}")
        
        response.raise_for_status()
        history_data = response.json()
        logger.info(f"✓ Historial obtenido correctamente de yyy-service: {history_data}")
        
    except requests.exceptions.ConnectionError as e:
        logger.error(f"✗ Error de conexión a yyy-service: {str(e)}")
        logger.error(f"  URL intentada: {YYY_SERVICE_URL}")
        logger.error(f"  Verificar que YYY_SERVICE_URL esté correcta")
        raise HTTPException(status_code=502, detail="Cannot connect to inventory service")
        
    except requests.exceptions.Timeout as e:
        logger.error(f"✗ Timeout conectando a yyy-service: {str(e)}")
        raise HTTPException(status_code=504, detail="Inventory service timeout")
        
    except requests.exceptions.HTTPError as e:
        logger.error(f"✗ Error HTTP de yyy-service: {str(e)}")
        logger.error(f"  Status: {response.status_code}")
        logger.error(f"  Response: {response.text}")
        raise HTTPException(status_code=response.status_code, detail=response.json().get("detail", "Error from inventory service"))
        
    except requests.RequestException as e:
        logger.error(f"✗ Error genérico llamando a yyy-service: {str(e)}")
        raise HTTPException(status_code=502, detail="Error communicating with inventory service")
    
    return {"status": "Order created", "history": history_data}

@app.get("/health")
def health_check():
    return {"status": "up"}
