"""
Predictive Layer (LSTM Planning Loop)
"""

import logging
import time
import threading
from typing import Optional, Dict, List, Callable
from dataclasses import dataclass
from enum import Enum

import torch
import torch.nn as nn

from intelliflow.logging_config import DecisionCSVLogger


class Path(Enum):
    """Available paths in diamond topology."""
    PATH_A = "path_a"
    PATH_B = "path_b"


@dataclass
class PlanningDecision:
    """Output of the predictive layer."""
    timestamp: float
    selected_path: Path
    predicted_util_path_a: float
    predicted_util_path_b: float
    confidence: float  # 0-1 indicator of prediction confidence

    @property
    def reason(self) -> str:
        return f"A:{self.predicted_util_path_a:.2f} B:{self.predicted_util_path_b:.2f}"


class LSTMPredictor(nn.Module):
    """
    Simple single-layer LSTM for link utilisation prediction.

    """

    def __init__(self, input_size: int = 1, hidden_size: int = 32,
                 num_layers: int = 1, output_size: int = 1):
        super(LSTMPredictor, self).__init__()
        self.hidden_size = hidden_size
        self.num_layers = num_layers

        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True
        )
        self.fc = nn.Linear(hidden_size, output_size)
        self.sigmoid = nn.Sigmoid()  # Output in [0, 1] for utilisation

    def forward(self, x):
        
        # LSTM output: (batch, seq_len, hidden_size)
        lstm_out, _ = self.lstm(x)
        # Take the last time step
        last_hidden = lstm_out[:, -1, :]
        # Predict utilisation
        out = self.fc(last_hidden)
        out = self.sigmoid(out)
        return out

    def predict(self, utilisation_sequence: List[float]) -> float:
        
        self.eval()
        with torch.no_grad():
            x = torch.tensor(utilisation_sequence, dtype=torch.float32)
            x = x.unsqueeze(0).unsqueeze(-1)  # (1, seq_len, 1)
            pred = self.forward(x)
            return pred.item()


class PredictiveLayer:
    """
    Predictive planning layer for IntelliFlow.
    """

    def __init__(self,
                 telemetry,
                 planning_interval: float = 5.0,
                 window_size: int = 10,
                 horizon: int = 1,
                 model_path: Optional[str] = None):
       
        self.logger = logging.getLogger(__name__)
        self.telemetry = telemetry
        self.planning_interval = planning_interval
        self.window_size = window_size
        self.horizon = horizon

        # LSTM models (one per path/link to predict)
        self.model_path_a = LSTMPredictor()
        self.model_path_b = LSTMPredictor()

        # Load pre-trained weights if provided
        if model_path:
            self.load_models(model_path)

        # Path configuration: (dpid, port_no) for bottleneck link of each path
        # These will be set by the controller based on topology
        # (dpid, port_no) for s2 bottleneck
        self.path_a_link: Optional[tuple] = None
        # (dpid, port_no) for s3 bottleneck
        self.path_b_link: Optional[tuple] = None

        # Current decision
        self.current_decision: Optional[PlanningDecision] = None

        # Callback for notifying controller of new decisions
        self._decision_callback: Optional[Callable[[
            PlanningDecision], None]] = None

        # CSV decision logger (set via set_decision_logger)
        self._decision_logger: Optional[DecisionCSVLogger] = None

        # Planning thread
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def set_decision_logger(self, decision_logger: DecisionCSVLogger):
        """Attach a CSV logger for persistent decision recording."""
        self._decision_logger = decision_logger

    def configure_paths(self, path_a_link: tuple, path_b_link: tuple):
        """
        Configure which links to monitor for each path.

        """
        self.path_a_link = path_a_link
        self.path_b_link = path_b_link

    def set_decision_callback(self, callback: Callable[[PlanningDecision], None]):
        """Set callback to be invoked when a new planning decision is made"""
        self._decision_callback = callback

    def load_models(self, model_path: str):
        """Load pre-trained model weights    """
        try:
            checkpoint = torch.load(model_path, map_location='cpu')
            self.model_path_a.load_state_dict(
                checkpoint.get('path_a', checkpoint))
            self.model_path_b.load_state_dict(
                checkpoint.get('path_b', checkpoint))
            self.logger.info("[PREDICT] Models loaded from %s", model_path)
        except Exception as e:
            self.logger.error("[PREDICT] Could not load model from %s: %s", model_path, e)

    def save_models(self, model_path: str):
        """Save current model weights"""
        torch.save({
            'path_a': self.model_path_a.state_dict(),
            'path_b': self.model_path_b.state_dict()
        }, model_path)

    def predict_utilisation(self, path: Path) -> float:
        """
        Predict future utilisation for a path.

        """
        if path == Path.PATH_A:
            link = self.path_a_link
            model = self.model_path_a
        else:
            link = self.path_b_link
            model = self.model_path_b

        if not link:
            return 0.5  # Default if not configured

        dpid, port_no = link
        history = self.telemetry.get_utilisation_window(
            dpid, port_no, self.window_size)

        if len(history) < self.window_size:
            # Not enough data - return current utilisation or default
            return history[-1] if history else 0.5

        # Use LSTM to predict
        return model.predict(history)

    def compute_decision(self) -> PlanningDecision:
        """
        Compute a new planning decision based on predictions.

        """
        pred_a = self.predict_utilisation(Path.PATH_A)
        pred_b = self.predict_utilisation(Path.PATH_B)

        # Select path with lower predicted utilisation
        # With symmetric paths (both 75 Mbps), either choice is valid
        # Add small bias towards Path A when predictions are close
        bias = 0.05

        if pred_b < pred_a - bias:
            selected_path = Path.PATH_B
        else:
            selected_path = Path.PATH_A

        # Confidence based on prediction difference
        diff = abs(pred_a - pred_b)
        # Higher difference = higher confidence
        confidence = min(1.0, diff * 2)

        decision = PlanningDecision(
            timestamp=time.time(),
            selected_path=selected_path,
            predicted_util_path_a=pred_a,
            predicted_util_path_b=pred_b,
            confidence=confidence
        )

        self.current_decision = decision

        self.logger.info("[PREDICT] Path A: %.3f, Path B: %.3f -> %s",
                         pred_a, pred_b, selected_path.value)

        # Persist to CSV
        if self._decision_logger:
            self._decision_logger.log(
                pred_a=pred_a,
                pred_b=pred_b,
                selected_path=selected_path.value,
                bias=bias,
                confidence=confidence,
            )

        return decision

    def _planning_loop(self):
        """Main planning loop (runs in separate thread)"""
        while self._running:
            try:
                decision = self.compute_decision()

                if self._decision_callback:
                    self._decision_callback(decision)

            except Exception as e:
                self.logger.error("[PREDICT] Planning cycle error: %s", e)

            # Wait for next planning interval
            time.sleep(self.planning_interval)

    def start(self):
        """Start planning loop"""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._planning_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the planning loop."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def get_current_decision(self) -> Optional[PlanningDecision]:
        """Get the most recent planning decision"""
        return self.current_decision
