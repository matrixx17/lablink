"""
LabLink AI Edge Agent

Watches for lab data files and uploads them to the central API.
Features:
- Automatic file format detection
- Retry logic with exponential backoff
- Dead letter queue for failed files
- Structured logging
"""

import argparse
import os
import sys
import time
import json
import shutil
import logging
from datetime import datetime
from typing import Optional, Tuple, Dict, Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Add parent directory to path for parsers import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parsers import parse_file, detect_format, ParsedResult

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("lablink-agent")


# --- Configuration ---

class AgentConfig:
    """Agent configuration with defaults."""

    def __init__(
        self,
        watch_folder: str,
        api_base: str = "http://localhost:8000",
        org_id: str = "default-org",
        max_retries: int = 3,
        retry_backoff: float = 1.0,
        max_backoff: float = 30.0,
        request_timeout: int = 30,
        max_failures: int = 5,
        enable_dead_letter: bool = True,
    ):
        self.watch_folder = watch_folder
        self.api_base = api_base.rstrip("/")
        self.org_id = org_id
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.max_backoff = max_backoff
        self.request_timeout = request_timeout
        self.max_failures = max_failures
        self.enable_dead_letter = enable_dead_letter

        # Derived paths
        self.failed_folder = os.path.join(watch_folder, ".failed")
        self.processed_folder = os.path.join(watch_folder, ".processed")


# --- HTTP Client with Retry ---

def create_http_session(config: AgentConfig) -> requests.Session:
    """
    Create an HTTP session with retry logic.

    Uses urllib3's Retry for automatic retries on:
    - Connection errors
    - 5xx server errors
    - 429 rate limiting
    """
    session = requests.Session()

    retry_strategy = Retry(
        total=config.max_retries,
        backoff_factor=config.retry_backoff,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST", "PUT", "DELETE"],
        raise_on_status=False,
    )

    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    return session


# --- Dead Letter Queue ---

class DeadLetterQueue:
    """
    Manages failed files that couldn't be processed.

    Files are moved to a .failed subfolder with metadata about the failure.
    """

    def __init__(self, failed_folder: str):
        self.failed_folder = failed_folder
        os.makedirs(failed_folder, exist_ok=True)

    def move_to_failed(
        self,
        file_path: str,
        error: str,
        attempts: int,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """
        Move a file to the dead letter queue.

        Args:
            file_path: Path to the failed file
            error: Error message
            attempts: Number of processing attempts
            metadata: Additional metadata to store
        """
        filename = os.path.basename(file_path)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        failed_name = f"{timestamp}_{filename}"
        failed_path = os.path.join(self.failed_folder, failed_name)

        try:
            # Move the file
            shutil.move(file_path, failed_path)

            # Write metadata file
            meta_path = f"{failed_path}.meta.json"
            meta = {
                "original_path": file_path,
                "original_name": filename,
                "failed_at": datetime.now().isoformat(),
                "error": error,
                "attempts": attempts,
                "metadata": metadata or {},
            }
            with open(meta_path, "w") as f:
                json.dump(meta, f, indent=2)

            logger.warning(f"Moved to dead letter queue: {filename} -> {failed_name}")

        except Exception as e:
            logger.error(f"Failed to move to dead letter queue: {e}")

    def list_failed(self) -> list:
        """List files in the dead letter queue."""
        files = []
        for f in os.listdir(self.failed_folder):
            if f.endswith(".meta.json"):
                continue
            meta_path = os.path.join(self.failed_folder, f"{f}.meta.json")
            if os.path.exists(meta_path):
                with open(meta_path) as mf:
                    meta = json.load(mf)
                    meta["failed_file"] = f
                    files.append(meta)
        return files


# --- File Processor ---

class FileProcessor:
    """
    Processes files with retry logic and error handling.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self.session = create_http_session(config)
        self.dlq = DeadLetterQueue(config.failed_folder) if config.enable_dead_letter else None
        self._failure_counts: Dict[str, int] = {}

    def process_file(self, path: str) -> bool:
        """
        Process a file with full error handling.

        Returns:
            True if processing succeeded, False otherwise
        """
        filename = os.path.basename(path)
        logger.info(f"Processing: {filename}")

        # Track failure count
        file_key = os.path.abspath(path)
        attempts = self._failure_counts.get(file_key, 0) + 1
        self._failure_counts[file_key] = attempts

        try:
            # Step 1: Parse file
            parsed_result = self._parse_file(path)
            if parsed_result is None:
                self._handle_failure(path, "Failed to parse file", attempts)
                return False

            # Step 2: Get presigned URL
            presign_data = self._get_presigned_url(filename)
            if presign_data is None:
                self._handle_failure(path, "Failed to get presigned URL", attempts)
                return False

            url, fields, s3_key = presign_data

            # Step 3: Upload to S3
            if not self._upload_to_s3(path, url, fields):
                self._handle_failure(path, "Failed to upload to S3", attempts)
                return False

            logger.info(f"Uploaded: {filename} -> {s3_key}")

            # Step 4: Post manifest
            if not self._post_manifest(parsed_result, s3_key):
                self._handle_failure(path, "Failed to post manifest", attempts)
                return False

            # Success - reset failure count
            self._failure_counts.pop(file_key, None)
            logger.info(f"Successfully processed: {filename}")
            return True

        except Exception as e:
            logger.exception(f"Unexpected error processing {filename}: {e}")
            self._handle_failure(path, str(e), attempts)
            return False

    def _parse_file(self, path: str) -> Optional[ParsedResult]:
        """Parse file with error handling."""
        try:
            detected_format = detect_format(path)
            logger.debug(f"Detected format: {detected_format}")

            result = parse_file(path)

            if result.parse_warnings:
                for warning in result.parse_warnings:
                    logger.warning(f"Parse warning: {warning}")

            return result

        except Exception as e:
            logger.error(f"Parse error: {e}")
            return None

    def _get_presigned_url(self, filename: str) -> Optional[Tuple[str, dict, str]]:
        """Get presigned URL with retry."""
        try:
            response = self.session.post(
                f"{self.config.api_base}/api/v1/presign",
                json={"filename": filename, "org_id": self.config.org_id},
                timeout=self.config.request_timeout,
            )

            if response.status_code != 200:
                logger.error(f"Presign failed: {response.status_code} {response.text}")
                return None

            data = response.json()
            url = data["url"]
            fields = data["fields"]["fields"]
            s3_key = data["fields"]["key"]

            return url, fields, s3_key

        except requests.exceptions.ConnectionError as e:
            logger.error(f"Cannot connect to API: {self.config.api_base}")
            logger.debug(f"Connection error details: {e}")
            return None

        except requests.exceptions.Timeout:
            logger.error(f"API request timed out after {self.config.request_timeout}s")
            return None

        except Exception as e:
            logger.error(f"Presign error: {e}")
            return None

    def _upload_to_s3(self, path: str, url: str, fields: dict) -> bool:
        """Upload file to S3 with retry."""
        try:
            with open(path, "rb") as f:
                response = self.session.post(
                    url,
                    data=fields,
                    files={"file": f},
                    timeout=self.config.request_timeout * 2,  # Longer timeout for upload
                )

            if response.status_code not in (200, 201, 204):
                logger.error(f"Upload failed: {response.status_code} {response.text}")
                return False

            return True

        except requests.exceptions.ConnectionError:
            logger.error("Cannot connect to storage service")
            return False

        except requests.exceptions.Timeout:
            logger.error("Upload timed out")
            return False

        except Exception as e:
            logger.error(f"Upload error: {e}")
            return False

    def _post_manifest(self, result: ParsedResult, s3_key: str) -> bool:
        """Post manifest to API with retry."""
        manifest = {
            "org_id": self.config.org_id,
            "filename": result.source_file,
            "s3_key": s3_key,
            "size": result.file_size_bytes,
            "headers": result.headers,
            "stats": result.raw_stats,
            "instrument": result.instrument,
            "sample_id": result.metadata.get("sample_id"),
            "format_version": result.format_version,
        }

        if result.metadata:
            manifest["parsed_metadata"] = result.metadata

        if result.timestamp:
            manifest["acquisition_time"] = result.timestamp.isoformat()

        try:
            response = self.session.post(
                f"{self.config.api_base}/api/v1/events",
                json=manifest,
                timeout=self.config.request_timeout,
            )

            if response.status_code != 200:
                logger.error(f"Manifest post failed: {response.status_code} {response.text}")
                return False

            logger.debug(f"Manifest response: {response.json()}")
            return True

        except requests.exceptions.ConnectionError:
            logger.error("Cannot connect to API for manifest post")
            return False

        except Exception as e:
            logger.error(f"Manifest post error: {e}")
            return False

    def _handle_failure(self, path: str, error: str, attempts: int):
        """Handle processing failure."""
        filename = os.path.basename(path)

        if attempts >= self.config.max_failures:
            logger.error(
                f"Max failures ({self.config.max_failures}) reached for {filename}, "
                f"moving to dead letter queue"
            )
            if self.dlq:
                self.dlq.move_to_failed(
                    path,
                    error=error,
                    attempts=attempts,
                    metadata={
                        "org_id": self.config.org_id,
                        "api_base": self.config.api_base,
                    },
                )
            # Reset failure count
            self._failure_counts.pop(os.path.abspath(path), None)
        else:
            logger.warning(
                f"Processing failed for {filename} (attempt {attempts}/{self.config.max_failures}): {error}"
            )

    def check_api_health(self) -> bool:
        """Check if API is reachable."""
        try:
            response = self.session.get(
                f"{self.config.api_base}/healthz",
                timeout=5,
            )
            return response.status_code == 200
        except Exception:
            return False


# --- File Watcher ---

class Handler(FileSystemEventHandler):
    """File system event handler that processes new files."""

    SUPPORTED_EXTENSIONS = {".csv", ".tsv", ".txt", ".dat"}

    def __init__(self, processor: FileProcessor):
        super().__init__()
        self.processor = processor

    def on_created(self, event):
        if event.is_directory:
            return

        # Skip files in .failed or .processed folders
        if "/.failed/" in event.src_path or "/.processed/" in event.src_path:
            return

        ext = os.path.splitext(event.src_path)[1].lower()
        if ext in self.SUPPORTED_EXTENSIONS:
            # Small delay to ensure file is fully written
            time.sleep(0.5)
            self.processor.process_file(event.src_path)


# --- Main ---

def main():
    parser = argparse.ArgumentParser(
        description="LabLink AI Edge Agent - watches for lab data files and uploads to central API"
    )
    parser.add_argument(
        "--watch",
        required=True,
        help="Folder to watch for data files",
    )
    parser.add_argument(
        "--api",
        default="http://localhost:8000",
        help="API base URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--org",
        default="default-org",
        help="Organization ID (default: default-org)",
    )
    parser.add_argument(
        "--extensions",
        default=".csv,.tsv,.txt,.dat",
        help="Comma-separated file extensions to watch (default: .csv,.tsv,.txt,.dat)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Max retries for API calls (default: 3)",
    )
    parser.add_argument(
        "--max-failures",
        type=int,
        default=5,
        help="Max failures before moving to dead letter queue (default: 5)",
    )
    parser.add_argument(
        "--no-dlq",
        action="store_true",
        help="Disable dead letter queue",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    # Configure logging level
    if args.debug:
        logger.setLevel(logging.DEBUG)
        logging.getLogger().setLevel(logging.DEBUG)

    # Create config
    config = AgentConfig(
        watch_folder=args.watch,
        api_base=args.api,
        org_id=args.org,
        max_retries=args.max_retries,
        max_failures=args.max_failures,
        enable_dead_letter=not args.no_dlq,
    )

    # Update supported extensions
    if args.extensions:
        Handler.SUPPORTED_EXTENSIONS = set(
            ext.strip() if ext.startswith(".") else f".{ext.strip()}"
            for ext in args.extensions.split(",")
        )

    # Create folders
    os.makedirs(config.watch_folder, exist_ok=True)
    if config.enable_dead_letter:
        os.makedirs(config.failed_folder, exist_ok=True)

    # Create processor
    processor = FileProcessor(config)

    # Check API health
    logger.info(f"Checking API connectivity: {config.api_base}")
    if processor.check_api_health():
        logger.info("API is reachable")
    else:
        logger.warning(f"API is not reachable at {config.api_base}")
        logger.warning("Will retry when processing files")

    # Start watcher
    event_handler = Handler(processor)
    observer = Observer()
    observer.schedule(event_handler, config.watch_folder, recursive=False)
    observer.start()

    logger.info("=" * 50)
    logger.info("LabLink AI Edge Agent Started")
    logger.info("=" * 50)
    logger.info(f"Watch folder:  {config.watch_folder}")
    logger.info(f"API endpoint:  {config.api_base}")
    logger.info(f"Organization:  {config.org_id}")
    logger.info(f"Extensions:    {Handler.SUPPORTED_EXTENSIONS}")
    logger.info(f"Max retries:   {config.max_retries}")
    logger.info(f"Max failures:  {config.max_failures}")
    logger.info(f"Dead letter:   {'enabled' if config.enable_dead_letter else 'disabled'}")
    logger.info("=" * 50)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        observer.stop()

    observer.join()
    logger.info("Agent stopped")


if __name__ == "__main__":
    main()
