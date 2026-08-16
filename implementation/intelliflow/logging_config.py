"""
logging
"""
import csv
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional


def generate_run_id() -> str:
    """Generate a timestamp for ID"""
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def setup_run_directory(controller_type: str, run_id: str,
                        base_dir: str = "results") -> Path:
    """
    Create and return the output directory
    """
    run_dir = Path(base_dir) / controller_type / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def setup_controller_log(run_dir: Path,
                         logger_name: str = None) -> logging.FileHandler:
    """
    Add a file handler
    """
    log_path = run_dir / "controller.log"
    formatter = logging.Formatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler = logging.FileHandler(str(log_path), mode="w")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    if logger_name:
        target_logger = logging.getLogger(logger_name)
    else:
        target_logger = logging.getLogger()

    target_logger.addHandler(file_handler)
    return file_handler


class CSVLogger:
    """
    Generic CSV logger that writes a header once and appends rows
    """

    def __init__(self, filepath: Path, columns: list):
        self.filepath = filepath
        self.columns = columns
        self._file = open(str(filepath), "w", newline="")
        self._writer = csv.DictWriter(self._file, fieldnames=columns)
        self._writer.writeheader()
        self._file.flush()

    def log(self, row: dict):
        """Write a single row. Missing keys become empty strings."""
        filtered = {k: row.get(k, "") for k in self.columns}
        self._writer.writerow(filtered)
        self._file.flush()

    def close(self):
        """Close the underlying file."""
        if self._file and not self._file.closed:
            self._file.close()


class TelemetryCSVLogger:
    """CSV logger for per-interval telemetry data."""

    COLUMNS = [
        "timestamp",
        "link_id",
        "utilisation",
        "delta_utilisation",
        "queue_proxy",
    ]

    def __init__(self, run_dir: Path):
        self._csv = CSVLogger(run_dir / "telemetry.csv", self.COLUMNS)

    def log(self, link_id: str, utilisation: float,
            delta_utilisation: float, queue_proxy: float):
        self._csv.log({
            "timestamp": f"{time.time():.6f}",
            "link_id": link_id,
            "utilisation": f"{utilisation:.6f}",
            "delta_utilisation": f"{delta_utilisation:.6f}",
            "queue_proxy": f"{queue_proxy:.6f}",
        })

    def close(self):
        self._csv.close()


class EventCSVLogger:
    """CSV logger for reactive layer events."""

    COLUMNS = [
        "timestamp",
        "event_type",
        "link_id",
        "utilisation",
        "delta_utilisation",
        "state",
        "details",
    ]

    def __init__(self, run_dir: Path):
        self._csv = CSVLogger(run_dir / "events.csv", self.COLUMNS)

    def log(self, event_type: str, link_id: str,
            utilisation: float = 0.0, delta_utilisation: float = 0.0,
            state: str = "", details: str = ""):
        self._csv.log({
            "timestamp": f"{time.time():.6f}",
            "event_type": event_type,
            "link_id": link_id,
            "utilisation": f"{utilisation:.6f}",
            "delta_utilisation": f"{delta_utilisation:.6f}",
            "state": state,
            "details": details,
        })

    def close(self):
        self._csv.close()


class DecisionCSVLogger:
    """CSV logger for predictive layer planning decisions."""

    COLUMNS = [
        "timestamp",
        "pred_A",
        "pred_B",
        "selected_path",
        "bias",
        "confidence",
    ]

    def __init__(self, run_dir: Path):
        self._csv = CSVLogger(run_dir / "decisions.csv", self.COLUMNS)

    def log(self, pred_a: float, pred_b: float, selected_path: str,
            bias: float = 0.05, confidence: float = 0.0):
        self._csv.log({
            "timestamp": f"{time.time():.6f}",
            "pred_A": f"{pred_a:.6f}",
            "pred_B": f"{pred_b:.6f}",
            "selected_path": selected_path,
            "bias": f"{bias:.4f}",
            "confidence": f"{confidence:.6f}",
        })

    def close(self):
        self._csv.close()
