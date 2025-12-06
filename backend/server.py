"""
server.py - FastAPI server for IntelliOS log processing system
Provides endpoints for real-time log processing, topic matching, and local DDNA access
"""
import os
import sys
import logging
import glob
import requests
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Union
import json
from fastapi import FastAPI, Query, HTTPException, BackgroundTasks, Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn
from dotenv import load_dotenv

# Handle module imports that might not be available
try:
    from State_capturing_engine.browser_capture import capture_browser_states
    from State_capturing_engine.app_capture import capture_app_states
    STATE_CAPTURE_AVAILABLE = True
except ImportError as e:
    logger = logging.getLogger(__name__)
    logger.info("State_capturing_engine modules not available - capture functionality disabled")
    logger.debug(f"State capture import error: {e}")
    STATE_CAPTURE_AVAILABLE = False
    
try:
    from Restoration_engine.browser_restore import restore_browsers, resolve_browser_executable
    from Restoration_engine.app_restore import restore_apps
    RESTORATION_AVAILABLE = True
except ImportError as e:
    logger = logging.getLogger(__name__)
    logger.info("Restoration_engine modules not available - restoration functionality disabled")
    logger.debug(f"Restoration import error: {e}")
    RESTORATION_AVAILABLE = False

    def resolve_browser_executable(browser: str, exe_hint: Optional[str] = None) -> Optional[str]:
        return exe_hint

# Load environment variables
load_dotenv()

# Add parent directory to path to import IntelliOS modules
backend_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(backend_dir)
sys.path.insert(0, project_root)
sys.path.insert(0, backend_dir)

# Import IntelliOS modules with error handling
import importlib
logger = logging.getLogger(__name__)

# Set up the logger first
try:
    from logging_config import setup_logging
    setup_logging()  # Use environment variable LOG_LEVEL
except ImportError:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger.warning("Could not import logging_config. Using basic logging configuration.")

# Import other modules with fallbacks
SERVICES_AVAILABLE = {
    "log_fetcher": False,
    "regex_parsers": False,
    "llm_layer": False,
    "vector_db": False,
    "topics": False,
    "process_logs": False
}

try:
    from log_fetcher import fetch_windows_event_logs
    SERVICES_AVAILABLE["log_fetcher"] = True
except ImportError as e:
    logger.warning(f"Could not import log_fetcher: {e}")
    fetch_windows_event_logs = lambda *args, **kwargs: []

try:
    from regex_parsers import parse_with_regex
    SERVICES_AVAILABLE["regex_parsers"] = True
except ImportError as e:
    logger.warning(f"Could not import regex_parsers: {e}")
    parse_with_regex = lambda *args, **kwargs: None

try:
    from llm_layer import parse_with_llm
    SERVICES_AVAILABLE["llm_layer"] = True
except ImportError as e:
    logger.warning(f"Could not import llm_layer: {e}")
    parse_with_llm = lambda *args, **kwargs: None

try:
    # Allow disabling vector DB via environment variable for quick runs/tests
    if os.environ.get("DISABLE_VECTOR_DB") == "1":
        raise ImportError("vector_db disabled via DISABLE_VECTOR_DB=1")
    from vector_db import VectorDBManager
    SERVICES_AVAILABLE["vector_db"] = True
except ImportError as e:
    logger.warning(f"Could not import vector_db: {e}")
    # Will define a dummy VectorDBManager class later

try:
    from topics import TOPICS
    SERVICES_AVAILABLE["topics"] = True
except ImportError as e:
    logger.warning(f"Could not import topics: {e}")
    TOPICS = {}

try:
    from main import process_logs
    SERVICES_AVAILABLE["process_logs"] = True
except ImportError as e:
    logger.warning(f"Could not import process_logs: {e}")
    process_logs = lambda *args, **kwargs: []

# Create dummy VectorDBManager if needed
if not SERVICES_AVAILABLE["vector_db"]:
    class VectorDBManager:
        def __init__(self, *args, **kwargs):
            logger.warning("Using dummy VectorDBManager - vector database functionality is disabled")
        
        def add_logs(self, logs):
            return logs
            
        def add_logs_with_topic_matches(self, logs, n_matches=3):
            return logs
            
        def query_logs(self, query_text, n_results=5):
            logger.warning("Vector database query attempted but vector_db module is not available")
            return []
            
        def get_stats(self):
            return {
                "status": "disabled",
                "reason": "vector_db module not available"
            }
            
        def clear_collection(self):
            logger.warning("Vector database clear attempted but vector_db module is not available")
            return False

# Define paths
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_DDNA_DIR = os.path.join(BACKEND_DIR, 'local_ddna')

# Note: capture and restore modules imported from packages above (State_capturing_engine, Restoration_engine)

# Set up the logger
logger = logging.getLogger(__name__)
setup_logging()  # Use environment variable LOG_LEVEL

# Initialize FastAPI app
app = FastAPI(
    title="IntelliOS API",
    description="API for IntelliOS Log Processing System",
    version="1.0.0",
)

# Add CORS middleware to allow cross-origin requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For production, specify exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize vector database manager
vector_db = VectorDBManager()

# Define response models
class LogEntry(BaseModel):
    event_type: str
    summary: str
    app_name: Optional[str] = None
    file_path: Optional[str] = None
    status: Optional[str] = None
    operation_code: Optional[str] = None
    event_subtype: Optional[str] = None
    topic_matches: Optional[List[Dict[str, Any]]] = None

class LogResponse(BaseModel):
    logs: List[LogEntry]
    total_logs_processed: int
    regex_parsed: int
    llm_parsed: int
    failed_to_parse: int
    status: str

class TopicListResponse(BaseModel):
    topics: Dict[str, str]
    status: str

# State restoration models
class RestoreRequest(BaseModel):
    state_file_path: Optional[str] = None
    browsers: Optional[List[Dict[str, Any]]] = None
    apps: Optional[List[Dict[str, Any]]] = None
    dry_run: bool = False

class RestoreResponse(BaseModel):
    status: str
    message: str
    details: Optional[Dict[str, bool]] = None


class RestorePreviewResponse(BaseModel):
    status: str
    apps: List[Dict[str, Any]]
    browsers: List[Dict[str, Any]]

class CaptureResponse(BaseModel):
    status: str
    message: str
    saved_at: str
    file_path: str
    state: Optional[Dict[str, Any]] = None


class CaptureRequest(BaseModel):
    state_file_path: str
    browser_ports_file: str
    
# Local DDNA models
class LocalDDNATopicsResponse(BaseModel):
    topics: List[str]
    count: int
    status: str

class LocalDDNALogEntry(BaseModel):
    event_type: str
    summary: str
    saved_at: str
    timestamp: str
    topic_score: Optional[float] = None
    topic_description: Optional[str] = None
    # Additional fields that might be present based on event_type
    app_name: Optional[str] = None
    exe_path: Optional[str] = None
    pid: Optional[int] = None
    window_count: Optional[int] = None
    captured_at: Optional[str] = None
    browser_name: Optional[str] = None
    url: Optional[str] = None
    domain: Optional[str] = None
    title: Optional[str] = None
    is_active: Optional[bool] = None
    tab_count: Optional[int] = None

class LocalDDNATopicResponse(BaseModel):
    topic: str
    logs: List[Union[LocalDDNALogEntry, Dict[str, Any]]]
    count: int
    status: str

class LocalDDNASearchResponse(BaseModel):
    query: str
    results: Dict[str, List[Dict[str, Any]]]
    total_matches: int
    matched_topics: int
    status: str

# In-memory queue for background processed logs
processed_logs_queue = []
# Processing statistics
processing_stats = {
    "total_logs": 0,
    "regex_parsed": 0,
    "llm_parsed": 0,
    "failed_to_parse": 0
}

# Process logs in the background
def process_logs_background(channel: str, hours: int, limit: int, with_topics: bool):
    """Background task to process logs"""
    global processed_logs_queue
    global processing_stats
    
    # Check if required services are available
    if not SERVICES_AVAILABLE["process_logs"]:
        logger.warning("process_logs function not available, cannot process logs")
        processing_stats = {
            "total_logs": 0,
            "regex_parsed": 0,
            "llm_parsed": 0,
            "failed_to_parse": 0,
            "status": "error",
            "message": "process_logs function not available"
        }
        processed_logs_queue = []
        return
    
    # Calculate the start time for log fetching
    fetch_since = datetime.now(timezone.utc) - timedelta(hours=hours)
    
    try:
        # Use the process_logs function from main.py
        parsed_logs = process_logs(channel, fetch_since, limit)
        
        # Calculate statistics (this needs to be done separately since process_logs doesn't return these values)
        # Only calculate stats if log_fetcher is available
        total_logs = 0
        regex_parsed = 0
        llm_parsed = 0
        
        if SERVICES_AVAILABLE["log_fetcher"]:
            try:
                for provider, message in fetch_windows_event_logs(channel, fetch_since):
                    total_logs += 1
                    if limit is not None and total_logs > limit:
                        break
                        
                    # Just count the logs to calculate stats, actual parsing is done by process_logs
                    if SERVICES_AVAILABLE["regex_parsers"] and parse_with_regex(provider, message) is not None:
                        regex_parsed += 1
                    elif SERVICES_AVAILABLE["llm_layer"] and parse_with_llm(provider, message) is not None:
                        llm_parsed += 1
            except Exception as e:
                logger.error(f"Error calculating statistics: {e}")
                # Continue with processing anyway
        
        # Store statistics
        processing_stats = {
            "total_logs": total_logs,
            "regex_parsed": regex_parsed,
            "llm_parsed": llm_parsed,
            "failed_to_parse": total_logs - regex_parsed - llm_parsed
        }
        
        # Match with topics if requested
        if with_topics and parsed_logs:
            try:
                enriched_logs = vector_db.add_logs(parsed_logs)
                # Store the enriched logs in the queue
                processed_logs_queue = enriched_logs
            except Exception as e:
                logger.error(f"Error enriching logs with topics: {e}")
                processed_logs_queue = parsed_logs
        else:
            # Store the regular logs in the queue
            processed_logs_queue = parsed_logs
        
        # Log processing summary
        logger.info(f"\nProcessing summary:")
        logger.info(f"Total logs processed: {total_logs}")
        if total_logs > 0:
            logger.info(f"Parsed with regex: {regex_parsed} ({regex_parsed/total_logs*100:.1f}%)")
            logger.info(f"Parsed with LLM: {llm_parsed} ({llm_parsed/total_logs*100:.1f}%)")
            logger.info(f"Failed to parse: {total_logs - regex_parsed - llm_parsed} ({(total_logs - regex_parsed - llm_parsed)/total_logs*100:.1f}%)")
            logger.info(f"Total successfully parsed: {len(parsed_logs)} ({len(parsed_logs)/total_logs*100:.1f}%)")
    except Exception as e:
        logger.error(f"Error processing logs: {e}")
        processing_stats = {
            "total_logs": 0,
            "regex_parsed": 0,
            "llm_parsed": 0,
            "failed_to_parse": 0,
            "status": "error",
            "message": str(e)
        }
        processed_logs_queue = []

# Routes
@app.get("/", tags=["Root"])
async def read_root():
    """Root endpoint - health check"""
    return {
        "status": "online", 
        "message": "IntelliOS API is running",
        "services": SERVICES_AVAILABLE,
        "available_endpoints": [
            "/api/local-ddna/topics", 
            "/api/local-ddna/topic/{topic}", 
            "/api/local-ddna/search", 
            "/api/local-ddna/latest",
            "/api/local-ddna/stats"
        ]
    }

@app.get("/api/topics", response_model=TopicListResponse, tags=["Topics"])
async def get_topics():
    """Get list of available topics with descriptions"""
    return {
        "topics": TOPICS,
        "status": "success"
    }

@app.post("/api/process-logs", response_model=LogResponse, tags=["Logs"])
async def process_logs_endpoint(
    background_tasks: BackgroundTasks,
    channel: str = Query("System", description="Windows Event Log channel to process"),
    hours: int = Query(1, description="Process logs from the last N hours"),
    limit: int = Query(10, description="Limit the number of logs to process"),
    with_topics: bool = Query(True, description="Match logs with topics")
):
    """
    Process logs in the background and return a job ID
    The actual log processing happens in the background
    """
    # Clear the queue
    global processed_logs_queue
    processed_logs_queue = []
    
    # Start background processing
    background_tasks.add_task(process_logs_background, channel, hours, limit, with_topics)
    
    return {
        "logs": [],
        "total_logs_processed": 0,
        "regex_parsed": 0,
        "llm_parsed": 0,
        "failed_to_parse": 0,
        "status": "processing_started"
    }

@app.get("/api/logs", response_model=LogResponse, tags=["Logs"])
async def get_logs():
    """Get the most recently processed logs"""
    global processed_logs_queue
    global processing_stats
    
    if not processed_logs_queue:
        return {
            "logs": [],
            "total_logs_processed": 0,
            "regex_parsed": 0,
            "llm_parsed": 0,
            "failed_to_parse": 0,
            "status": "no_logs_processed"
        }
    
    return {
        "logs": processed_logs_queue,
        "total_logs_processed": processing_stats["total_logs"],
        "regex_parsed": processing_stats["regex_parsed"],
        "llm_parsed": processing_stats["llm_parsed"],
        "failed_to_parse": processing_stats["failed_to_parse"],
        "status": "success"
    }

@app.get("/api/real-time-logs", tags=["Logs"])
async def get_real_time_logs(
    channel: str = Query("System", description="Windows Event Log channel to process"),
    hours: int = Query(1, description="Process logs from the last N hours"),
    limit: int = Query(5, description="Limit the number of logs to process"),
    with_topics: bool = Query(True, description="Match logs with topics")
):
    """
    Get real-time logs from Windows Event Log with topic matching
    This is a synchronous endpoint that processes logs and returns them immediately
    """
    # Calculate the start time for log fetching
    fetch_since = datetime.now(timezone.utc) - timedelta(hours=hours)
    
    # Process logs
    parsed_logs = process_logs(channel, fetch_since, limit)
    
    # Match with topics if requested
    if with_topics and parsed_logs:
        enriched_logs = vector_db.add_logs(parsed_logs)
        return {
            "logs": enriched_logs,
            "count": len(enriched_logs),
            "channel": channel,
            "hours": hours,
            "with_topics": with_topics,
            "status": "success"
        }
    else:
        return {
            "logs": parsed_logs,
            "count": len(parsed_logs),
            "channel": channel,
            "hours": hours,
            "with_topics": with_topics,
            "status": "success"
        }

@app.post("/api/query-logs", tags=["Logs"])
async def query_logs(
    query: str = Query(..., description="Query text to search for"),
    results: int = Query(5, description="Number of results to return")
):
    """Query the vector database for logs matching the query"""
    try:
        logs = vector_db.query_logs(query, results)
        return {
            "logs": logs,
            "count": len(logs),
            "query": query,
            "status": "success"
        }
    except Exception as e:
        logger.error(f"Error querying logs: {e}")
        raise HTTPException(status_code=500, detail=f"Error querying logs: {str(e)}")

@app.post("/api/clear-vector-db", tags=["Database"])
async def clear_vector_db():
    """Clear the vector database"""
    try:
        success = vector_db.clear_collection()
        if success:
            return {"status": "success", "message": "Vector database cleared successfully"}
        else:
            return {"status": "error", "message": "Failed to clear vector database"}
    except Exception as e:
        logger.error(f"Error clearing vector database: {e}")
        raise HTTPException(status_code=500, detail=f"Error clearing vector database: {str(e)}")

@app.get("/api/vector-db-stats", tags=["Database"])
async def get_vector_db_stats():
    """Get statistics about the vector database"""
    try:
        stats = vector_db.get_stats()
        return {
            "stats": stats,
            "status": "success"
        }
    except Exception as e:
        logger.error(f"Error getting vector database stats: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting vector database stats: {str(e)}")

# State Restoration endpoints
@app.post("/api/capture", response_model=CaptureResponse, tags=["State Management"])
async def capture_state(request: CaptureRequest):
    """
    Capture current system state and save it to a file

    Returns:
        CaptureResponse with status and message
    """
    if not STATE_CAPTURE_AVAILABLE:
        raise HTTPException(
            status_code=501,
            detail="State capturing functionality not available"
        )
    
    try:
        # Ensure output directory exists
        os.makedirs(os.path.dirname(request.state_file_path), exist_ok=True)

        if not os.path.exists(request.browser_ports_file):
            raise HTTPException(
                status_code=404,
                detail=f"Browser ports file not found: {request.browser_ports_file}"
            )
        
        # Read browser ports file
        browser_ports_data = {}
        try:
            with open(request.browser_ports_file, 'r', encoding='utf-8') as f:
                browser_ports_data = json.load(f)
        except Exception as e:
            logger.error(f"Error reading browser ports file: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Error reading browser ports file: {str(e)}"
            )

        # Capture browser states
        browsers = []
        try:
            browsers = capture_browser_states(browser_ports_data)
        except Exception as e:
            logger.error(f"Error capturing browser states: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Error capturing browser states: {str(e)}"
            )

        # Capture app states and normalize to state schema (use 'items')
        apps = []
        try:
            raw_apps = capture_app_states()
            for a in (raw_apps or []):
                items = a.get('files') or a.get('items') or []
                apps.append({
                    'name': a.get('name'),
                    'pid': a.get('pid'),
                    'exe': a.get('exe'),
                    'cmdline': a.get('cmdline'),
                    'items': items,
                    'windowInfo': a.get('windowInfo')
                })
        except Exception as e:
            logger.error(f"Error capturing app states: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Error capturing app states: {str(e)}"
            )
        
        # Create state object
        state = {
            "saved_at": datetime.now().isoformat(),
            "user": os.environ.get("USERNAME", ""),
            "browsers": browsers,
            "apps": apps
        }
        
        # Save state to file
        with open(request.state_file_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
            
        return CaptureResponse(
            status="success",
            message="State captured successfully",
            saved_at=state["saved_at"],
            file_path=request.state_file_path,
            state=state
        )
            
    except Exception as e:
        logger.error(f"Error capturing state: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error capturing state: {str(e)}"
        )

@app.post("/api/restore", response_model=RestoreResponse, tags=["State Management"])
async def restore_state(request: RestoreRequest):
    """
    Restore system state from a state file
    
    Args:
        request: RestoreRequest containing the path to the state file
        
    Returns:
        RestoreResponse with status and message
        
    Raises:
        HTTPException: If there are any errors during the restoration process
    """
    if not RESTORATION_AVAILABLE:
        raise HTTPException(
            status_code=501,
            detail="State restoration functionality not available"
        )
        
    try:
        state: Dict[str, Any] = {}
        if request.apps is not None or request.browsers is not None:
            state = {
                'apps': request.apps or [],
                'browsers': request.browsers or [],
            }
        else:
            if not request.state_file_path:
                raise HTTPException(
                    status_code=400,
                    detail="Either state_file_path or inlined apps/browsers must be provided"
                )

            if not os.path.exists(request.state_file_path):
                raise HTTPException(
                    status_code=404,
                    detail=f"State file not found: {request.state_file_path}"
                )

            # Read state file
            try:
                with open(request.state_file_path, 'r', encoding='utf-8') as f:
                    state = json.load(f)
            except Exception as e:
                logger.error(f"Error reading state file: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Error reading state file: {str(e)}"
                )

        restoration_details = {
            "browsers_restored": False,
            "apps_restored": False
        }

        if request.dry_run:
            missing_executables = []
            for browser in state.get('browsers', []):
                resolved = resolve_browser_executable(browser.get('browser', ''), browser.get('exe'))
                if not resolved or not os.path.exists(resolved):
                    missing_executables.append({
                        'browser': browser.get('browser'),
                        'requested_exe': browser.get('exe'),
                        'resolved_exe': resolved,
                    })

            return RestoreResponse(
                status="dry_run",
                message="Restore dry-run completed",
                details={
                    'browsers_restored': False,
                    'apps_restored': False,
                    'missing_browser_executables': missing_executables,
                }
            )

        # Restore browsers
        try:
            restore_browsers(state)
            restoration_details["browsers_restored"] = True
        except Exception as e:
            logger.error(f"Error restoring browsers: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Error restoring browsers: {str(e)}"
            )

        # Restore apps
        try:
            restore_apps(state)
            restoration_details["apps_restored"] = True
        except Exception as e:
            logger.error(f"Error restoring apps: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Error restoring apps: {str(e)}"
            )

        return RestoreResponse(
            status="success",
            message="State restored successfully",
            details=restoration_details
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Unexpected error in restore_state: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )


@app.post("/api/restore/preview", response_model=RestorePreviewResponse, tags=["State Management"])
async def preview_restore(request: RestoreRequest):
    if not RESTORATION_AVAILABLE:
        raise HTTPException(
            status_code=501,
            detail="State restoration functionality not available"
        )

    try:
        state: Dict[str, Any] = {}
        if request.apps is not None or request.browsers is not None:
            state = {
                'apps': request.apps or [],
                'browsers': request.browsers or [],
            }
        else:
            if not request.state_file_path:
                raise HTTPException(
                    status_code=400,
                    detail="Either state_file_path or inlined apps/browsers must be provided"
                )

            if not os.path.exists(request.state_file_path):
                raise HTTPException(
                    status_code=404,
                    detail=f"State file not found: {request.state_file_path}"
                )

            with open(request.state_file_path, 'r', encoding='utf-8') as f:
                state = json.load(f)

        browsers_preview: List[Dict[str, Any]] = []
        for browser in state.get('browsers', []):
            resolved = resolve_browser_executable(browser.get('browser', ''), browser.get('exe'))
            browsers_preview.append({
                'browser': browser.get('browser'),
                'requested_exe': browser.get('exe'),
                'resolved_exe': resolved,
                'windows': browser.get('windows', []),
            })

        return RestorePreviewResponse(
            status='success',
            apps=state.get('apps', []),
            browsers=browsers_preview,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in preview_restore: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )

# Local DDNA endpoints
@app.get("/api/local-ddna/topics", response_model=LocalDDNATopicsResponse, tags=["Local DDNA"])
async def get_local_ddna_topics():
    """Get list of available local DDNA topics (JSON files)"""
    try:
        # Check if local DDNA directory exists
        if not os.path.exists(LOCAL_DDNA_DIR):
            return {
                "topics": [],
                "count": 0,
                "status": "warning",
                "message": f"Local DDNA directory not found at: {LOCAL_DDNA_DIR}"
            }
            
        # Get list of JSON files in the local DDNA directory
        json_files = glob.glob(os.path.join(LOCAL_DDNA_DIR, "*.json"))
        
        # Extract topic names from file paths
        topics = [os.path.splitext(os.path.basename(f))[0] for f in json_files]
        topics.sort()  # Sort alphabetically for consistency
        
        return {
            "topics": topics,
            "count": len(topics),
            "status": "success"
        }
    except Exception as e:
        logger.error(f"Error getting local DDNA topics: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error getting local DDNA topics: {str(e)}"
        )

@app.get("/api/local-ddna/topic/{topic}", response_model=LocalDDNATopicResponse, tags=["Local DDNA"])
async def get_local_ddna_topic(
    topic: str = Path(..., description="Topic name (without .json extension)"),
    limit: int = Query(100, description="Maximum number of logs to return"),
    offset: int = Query(0, description="Number of logs to skip from the beginning"),
    sort_by: str = Query("timestamp", description="Field to sort by"),
    sort_order: str = Query("desc", description="Sort order (asc or desc)")
):
    """Get logs for a specific local DDNA topic"""
    try:
        # Build file path
        file_path = os.path.join(LOCAL_DDNA_DIR, f"{topic}.json")
        
        # Check if file exists
        if not os.path.exists(file_path):
            raise HTTPException(
                status_code=404,
                detail=f"Topic file not found: {topic}.json"
            )
            
        # Read JSON file
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                logs = json.load(f)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=500,
                detail=f"Invalid JSON in topic file: {topic}.json"
            )
            
        # Validate sort order
        if sort_order.lower() not in ["asc", "desc"]:
            sort_order = "desc"
        
        # Sort logs
        reverse = sort_order.lower() == "desc"
        logs = sorted(logs, key=lambda x: x.get(sort_by, ""), reverse=reverse)
        
        # Apply pagination
        paginated_logs = logs[offset:offset + limit] if limit > 0 else logs[offset:]
        
        return {
            "topic": topic,
            "logs": paginated_logs,
            "count": len(paginated_logs),
            "total": len(logs),
            "status": "success"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting local DDNA topic {topic}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error getting local DDNA topic {topic}: {str(e)}"
        )

@app.get("/api/local-ddna/search", response_model=LocalDDNASearchResponse, tags=["Local DDNA"])
async def search_local_ddna(
    query: str = Query(..., description="Search query string"),
    case_sensitive: bool = Query(False, description="Case sensitive search"),
    topics: str = Query(None, description="Comma-separated list of topics to search in")
):
    """Search across all local DDNA topic files or specific topics"""
    try:
        # Check if local DDNA directory exists
        if not os.path.exists(LOCAL_DDNA_DIR):
            return {
                "query": query,
                "results": {},
                "total_matches": 0,
                "matched_topics": 0,
                "status": "warning",
                "message": f"Local DDNA directory not found at: {LOCAL_DDNA_DIR}"
            }
        
        # Determine which topics to search
        topic_list = []
        if topics:
            topic_list = [t.strip() for t in topics.split(",")]
            # Validate that specified topics exist
            json_files = [f for f in os.listdir(LOCAL_DDNA_DIR) if f.endswith('.json')]
            available_topics = [os.path.splitext(f)[0] for f in json_files]
            topic_list = [t for t in topic_list if t in available_topics]
        else:
            # Search all topics
            json_files = [f for f in os.listdir(LOCAL_DDNA_DIR) if f.endswith('.json')]
            topic_list = [os.path.splitext(f)[0] for f in json_files]
        
        # Perform search across topics
        results = {}
        total_matches = 0
        
        for topic in topic_list:
            file_path = os.path.join(LOCAL_DDNA_DIR, f"{topic}.json")
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    logs = json.load(f)
                
                # Search in logs
                topic_matches = []
                for log in logs:
                    # Convert log to string for searching
                    log_str = json.dumps(log, ensure_ascii=False)
                    
                    # Perform search
                    if case_sensitive:
                        if query in log_str:
                            topic_matches.append(log)
                    else:
                        if query.lower() in log_str.lower():
                            topic_matches.append(log)
                
                # Add results if there are matches
                if topic_matches:
                    results[topic] = topic_matches
                    total_matches += len(topic_matches)
            except Exception as e:
                logger.warning(f"Error searching in topic {topic}: {e}")
                # Continue with other topics
                
        return {
            "query": query,
            "results": results,
            "total_matches": total_matches,
            "matched_topics": len(results),
            "status": "success"
        }
    except Exception as e:
        logger.error(f"Error searching local DDNA: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error searching local DDNA: {str(e)}"
        )

@app.get("/api/local-ddna/latest", tags=["Local DDNA"])
async def get_latest_local_ddna(
    topics: str = Query(None, description="Comma-separated list of topics to get latest logs from"),
    limit: int = Query(5, description="Number of latest logs per topic")
):
    """Get the latest logs from each topic or specified topics"""
    try:
        # Check if local DDNA directory exists
        if not os.path.exists(LOCAL_DDNA_DIR):
            return {
                "latest_logs": {},
                "status": "warning",
                "message": f"Local DDNA directory not found at: {LOCAL_DDNA_DIR}"
            }
            
        # Determine which topics to get
        topic_list = []
        if topics:
            topic_list = [t.strip() for t in topics.split(",")]
            # Validate that specified topics exist
            json_files = [f for f in os.listdir(LOCAL_DDNA_DIR) if f.endswith('.json')]
            available_topics = [os.path.splitext(f)[0] for f in json_files]
            topic_list = [t for t in topic_list if t in available_topics]
        else:
            # Get from all topics
            json_files = [f for f in os.listdir(LOCAL_DDNA_DIR) if f.endswith('.json')]
            topic_list = [os.path.splitext(f)[0] for f in json_files]
        
        # Get latest logs from each topic
        latest_logs = {}
        
        for topic in topic_list:
            file_path = os.path.join(LOCAL_DDNA_DIR, f"{topic}.json")
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    logs = json.load(f)
                
                # Sort logs by timestamp in descending order
                logs_sorted = sorted(logs, key=lambda x: x.get("timestamp", ""), reverse=True)
                
                # Get the latest logs up to the limit
                latest_logs[topic] = logs_sorted[:limit]
                
            except Exception as e:
                logger.warning(f"Error getting latest logs from topic {topic}: {e}")
                # Continue with other topics
        
        return {
            "latest_logs": latest_logs,
            "topics_count": len(latest_logs),
            "status": "success"
        }
    except Exception as e:
        logger.error(f"Error getting latest local DDNA logs: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error getting latest local DDNA logs: {str(e)}"
        )

@app.get("/api/local-ddna/stats", tags=["Local DDNA"])
async def get_local_ddna_stats():
    """Get statistics about local DDNA data"""
    try:
        # Check if local DDNA directory exists
        if not os.path.exists(LOCAL_DDNA_DIR):
            return {
                "topics": 0,
                "total_logs": 0,
                "logs_by_topic": {},
                "status": "warning",
                "message": f"Local DDNA directory not found at: {LOCAL_DDNA_DIR}"
            }
            
        # Get list of JSON files
        json_files = [f for f in os.listdir(LOCAL_DDNA_DIR) if f.endswith('.json')]
        
        # Calculate statistics
        stats = {
            "topics": len(json_files),
            "logs_by_topic": {},
            "total_logs": 0,
            "logs_by_event_type": {},
            "timestamp_range": {
                "earliest": None,
                "latest": None
            }
        }
        
        for json_file in json_files:
            topic = os.path.splitext(json_file)[0]
            file_path = os.path.join(LOCAL_DDNA_DIR, json_file)
            
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    logs = json.load(f)
                    
                # Count logs in this topic
                log_count = len(logs)
                stats["logs_by_topic"][topic] = log_count
                stats["total_logs"] += log_count
                
                # Count event types
                for log in logs:
                    event_type = log.get("event_type", "unknown")
                    stats["logs_by_event_type"][event_type] = stats["logs_by_event_type"].get(event_type, 0) + 1
                    
                    # Track timestamp range
                    if log.get("timestamp"):
                        if not stats["timestamp_range"]["earliest"] or log["timestamp"] < stats["timestamp_range"]["earliest"]:
                            stats["timestamp_range"]["earliest"] = log["timestamp"]
                        if not stats["timestamp_range"]["latest"] or log["timestamp"] > stats["timestamp_range"]["latest"]:
                            stats["timestamp_range"]["latest"] = log["timestamp"]
                            
            except Exception as e:
                logger.warning(f"Error processing topic {topic}: {e}")
                stats["logs_by_topic"][topic] = -1  # Indicate error
        
        return {
            **stats,
            "status": "success"
        }
    except Exception as e:
        logger.error(f"Error getting local DDNA stats: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error getting local DDNA stats: {str(e)}"
        )

# Sync workspace to remote cloud - core function
def sync_workspace_to_cloud_direct(username: str, workspace_name: str, state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Direct function to sync workspace data to remote cloud DDNA database
    Can be called programmatically without HTTP overhead
    
    Args:
        username: Username for the workspace
        workspace_name: Name of the workspace
        state: State data containing apps, browsers, etc.
        
    Returns:
        Dict with status, message, and optional response
    """
    try:
        remote_api_url = "https://intellios-database.onrender.com/api/workspace"
        
        payload = {
            "username": username,
            "workspace_name": workspace_name,
            "state": state
        }
        
        logger.info(f"Syncing workspace '{workspace_name}' for user '{username}' to cloud")
        
        # Make request to remote API with 60 second timeout
        response = requests.post(remote_api_url, json=payload, timeout=60)
        
        if response.status_code == 200 or response.status_code == 201:
            result = response.json()
            logger.info(f"Workspace sync successful: {result.get('message', 'Success')}")
            return {
                "status": "success",
                "message": result.get("message", "Workspace synced successfully"),
                "response": result
            }
        else:
            error_msg = f"Remote API returned status {response.status_code}: {response.text}"
            logger.error(f"Workspace sync failed: {error_msg}")
            return {
                "status": "error",
                "message": error_msg
            }
            
    except requests.exceptions.Timeout:
        error_msg = "Request timed out. The remote server might be slow or unavailable."
        logger.error(f"Workspace sync timeout: {error_msg}")
        return {
            "status": "error",
            "message": error_msg
        }
    except requests.exceptions.ConnectionError:
        error_msg = "Could not connect to remote server. Check your internet connection."
        logger.error(f"Workspace sync connection error: {error_msg}")
        return {
            "status": "error",
            "message": error_msg
        }
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        logger.error(f"Workspace sync error: {error_msg}")
        return {
            "status": "error",
            "message": error_msg
        }

# Sync workspace to remote cloud endpoint
class SyncWorkspaceRequest(BaseModel):
    username: str
    workspace_name: str
    state: Dict[str, Any]

class SyncWorkspaceResponse(BaseModel):
    status: str
    message: str
    response: Optional[Dict[str, Any]] = None

@app.post("/api/sync-workspace", response_model=SyncWorkspaceResponse, tags=["Workspace Sync"])
async def sync_workspace_to_cloud(request: SyncWorkspaceRequest):
    """
    Sync workspace data to remote cloud DDNA database (HTTP endpoint wrapper)
    
    Args:
        request: SyncWorkspaceRequest containing username, workspace_name, and state
        
    Returns:
        SyncWorkspaceResponse with status and message
    """
    # Call the direct function
    result = sync_workspace_to_cloud_direct(
        username=request.username,
        workspace_name=request.workspace_name,
        state=request.state
    )
    
    if result["status"] == "success":
        return SyncWorkspaceResponse(
            status=result["status"],
            message=result["message"],
            response=result.get("response")
        )
    else:
        # Return error response without raising HTTPException
        return SyncWorkspaceResponse(
            status=result["status"],
            message=result["message"],
            response=None
        )

# ============================================
# Custom Topic Management
# ============================================

def add_custom_topic_direct(topic_name: str, description: str, examples: List[str] = None) -> Dict[str, Any]:
    """
    Add a new user-defined custom topic to the vector database.
    This function can be called directly from other Python code (like UI).
    
    Args:
        topic_name: Unique name for the topic (e.g., "game_development", "cybersecurity")
        description: Detailed description of what this topic represents
        examples: Optional list of example text snippets that represent this topic
        
    Returns:
        Dictionary with status, message, and topic details
    """
    try:
        logger.info(f"Adding custom topic: {topic_name}")
        
        # Check if vector DB is available
        if not SERVICES_AVAILABLE.get("vector_db") or vector_db is None:
            return {
                "status": "error",
                "message": "Vector database service not available"
            }
        
        # Call the vector_db method
        result = vector_db.add_custom_topic(
            topic_name=topic_name,
            description=description,
            examples=examples or []
        )
        
        return result
        
    except Exception as e:
        error_msg = f"Error adding custom topic: {str(e)}"
        logger.error(error_msg)
        return {
            "status": "error",
            "message": error_msg
        }

def get_all_topics_direct() -> Dict[str, Any]:
    """
    Get all topics including custom user-defined topics.
    This function can be called directly from other Python code (like UI).
    
    Returns:
        Dictionary with all topics and their descriptions
    """
    try:
        logger.info("Fetching all topics")
        
        # Check if vector DB is available
        if not SERVICES_AVAILABLE.get("vector_db") or vector_db is None:
            return {
                "status": "error",
                "message": "Vector database service not available",
                "topics": {}
            }
        
        # Call the vector_db method
        result = vector_db.get_all_topics()
        
        return result
        
    except Exception as e:
        error_msg = f"Error fetching topics: {str(e)}"
        logger.error(error_msg)
        return {
            "status": "error",
            "message": error_msg,
            "topics": {}
        }

# Request/Response models for custom topics
class AddCustomTopicRequest(BaseModel):
    topic_name: str
    description: str
    examples: Optional[List[str]] = None

class CustomTopicResponse(BaseModel):
    status: str
    message: str
    topic: Optional[Dict[str, Any]] = None

class AllTopicsResponse(BaseModel):
    status: str
    topics: Dict[str, Any]
    total_count: Optional[int] = None
    message: Optional[str] = None

@app.post("/api/topics/custom", response_model=CustomTopicResponse, tags=["Topics"])
async def add_custom_topic(request: AddCustomTopicRequest):
    """
    Add a new user-defined custom topic to the vector database (HTTP endpoint wrapper)
    
    Args:
        request: AddCustomTopicRequest containing topic_name, description, and optional examples
        
    Returns:
        CustomTopicResponse with status, message, and topic details
    """
    # Call the direct function
    result = add_custom_topic_direct(
        topic_name=request.topic_name,
        description=request.description,
        examples=request.examples
    )
    
    if result["status"] == "success":
        return CustomTopicResponse(
            status=result["status"],
            message=result["message"],
            topic=result.get("topic")
        )
    else:
        return CustomTopicResponse(
            status=result["status"],
            message=result["message"],
            topic=None
        )

@app.get("/api/topics/all", response_model=AllTopicsResponse, tags=["Topics"])
async def get_all_topics_endpoint():
    """
    Get all topics including custom user-defined topics (HTTP endpoint wrapper)
    
    Returns:
        AllTopicsResponse with all topics and their descriptions
    """
    # Call the direct function
    result = get_all_topics_direct()
    
    return AllTopicsResponse(
        status=result["status"],
        topics=result.get("topics", {}),
        total_count=result.get("total_count"),
        message=result.get("message")
    )

if __name__ == "__main__":
    # Run the server
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run("server:app", port=8001, reload=True)
