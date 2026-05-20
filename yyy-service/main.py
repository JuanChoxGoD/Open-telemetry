import os
import logging
import pymysql
import random
import time
from fastapi import FastAPI, HTTPException, Request

from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter as GrpcSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter as GrpcMetricExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME

from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.pymysql import PyMySQLInstrumentor
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

# Setup Cloud SQL Connection
def get_db_connection():
    """Crea una conexión a Cloud SQL"""
    connection = pymysql.connect(
        host=os.environ.get("DB_HOST", "127.0.0.1"),
        user=os.environ.get("DB_USER", "root"),
        password=os.environ.get("DB_PASSWORD", ""),
        database=os.environ.get("DB_NAME", "yyy_service"),
        port=int(os.environ.get("DB_PORT", "3306")),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor
    )
    return connection

# Initialize database table
def init_db():
    """Inicializa la tabla en Cloud SQL"""
    conn = None
    try:
        logger.info("="*60)
        logger.info("Iniciando inicialización de base de datos...")
        logger.info(f"  - Host: {os.environ.get('DB_HOST', '127.0.0.1')}")
        logger.info(f"  - Base de datos: {os.environ.get('DB_NAME', 'yyy_service')}")
        logger.info(f"  - Puerto: {os.environ.get('DB_PORT', '3306')}")
        
        logger.info("Intentando conectar a la base de datos...")
        conn = get_db_connection()
        logger.info("✓ Conexión exitosa a la base de datos")
        
        cursor = conn.cursor()
        
        logger.info("Verificando/creando tabla user_history...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_history (
                user_id INT PRIMARY KEY,
                orders_count INT NOT NULL
            )
        """)
        
        conn.commit()
        logger.info("✓ Tabla user_history creada/verificada exitosamente")
        logger.info("="*60)
        cursor.close()
        
    except pymysql.err.ProgrammingError as e:
        logger.error(f"✗ Error de sintaxis SQL: {str(e)}")
        raise
    except pymysql.err.OperationalError as e:
        logger.error(f"✗ Error operacional (conexión/acceso): {str(e)}")
        logger.error(f"  - Código: {e.args[0] if e.args else 'N/A'}")
        raise
    except pymysql.Error as e:
        logger.error(f"✗ Error de PyMySQL: {str(e)}")
        logger.error(f"  - Código: {e.args[0] if e.args else 'N/A'}")
        raise
    except Exception as e:
        logger.error(f"✗ Error inesperado: {str(e)}")
        raise
    finally:
        if conn:
            try:
                conn.close()
                logger.info("Conexión a la base de datos cerrada")
            except Exception as e:
                logger.error(f"Error cerrando la conexión: {str(e)}")

# Initialize database on startup
logger.info("="*60)
logger.info("Iniciando aplicación yyy-service")
logger.info("="*60)

try:
    init_db()
    logger.info("✓ Base de datos inicializada correctamente")
except Exception as e:
    logger.critical(f"✗ CRÍTICO: No se pudo inicializar la base de datos")
    logger.critical(f"  Error: {str(e)}")
    logger.critical("La aplicación continuará, pero fallará al intentar acceder a la BD")

app = FastAPI()

# Auto-instrument FastAPI and PyMySQL
FastAPIInstrumentor.instrument_app(app)
PyMySQLInstrumentor().instrument()

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
        
        conn = None
        try:
            # Query the database
            conn = get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute("SELECT orders_count FROM user_history WHERE user_id = %s", (user_id,))
            result = cursor.fetchone()
            
            if not result:
                # Si el usuario no está, simulamos error para responder a la pregunta de errores
                if user_id > 100:
                    span.set_status(trace.status.Status(trace.status.StatusCode.ERROR, "User not found (simulated error)"))
                    logger.error(f"Error forzado: Usuario {user_id} no encontrado")
                    raise HTTPException(status_code=404, detail="User not found")
                    
                logger.info(f"Usuario {user_id} sin historial previo")
                return {"user_id": user_id, "previous_orders": 0}
            
            orders_count = result.get("orders_count") if isinstance(result, dict) else result[0]
            logger.info(f"Historial encontrado: {orders_count} órdenes previas")
            return {"user_id": user_id, "previous_orders": orders_count}
            
        except pymysql.Error as e:
            span.record_exception(e)
            span.set_status(trace.status.Status(trace.status.StatusCode.ERROR, str(e)))
            logger.error(f"Error de Base de Datos: {str(e)}")
            raise HTTPException(status_code=500, detail="Database Error")
        finally:
            if conn:
                conn.close()

@app.get("/health")
def health_check():
    return {"status": "up"}
