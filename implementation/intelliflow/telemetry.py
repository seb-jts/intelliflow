"""
Telemetry Collection Module

Collects network statistics for both predictive and reactive layers
"""

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, Optional, Deque
from os_ken.lib.dpid import dpid_to_str

from intelliflow.logging_config import TelemetryCSVLogger


@dataclass
class PortStats:
    """Statistics for single port"""
    timestamp: float
    tx_bytes: int
    rx_bytes: int
    tx_packets: int
    rx_packets: int
    tx_dropped: int = 0
    rx_dropped: int = 0


@dataclass
class LinkStats:
    """Computed statistics for a link"""
    timestamp: float
    utilisation: float  
    utilisation_raw: float  
    delta_utilisation: float  
    queue_proxy: float  
    
    @property
    def is_congested(self) -> bool:
        """Simple congestion heuristic """
        return self.utilisation > 0.8 and self.delta_utilisation > 0


@dataclass
class LinkConfig:
    """Configuration for a monitored link"""
    dpid: str
    port_no: int
    capacity_bps: float = 100e6  # Default 100 Mbps
    name: str = ""


class TelemetryCollector:
    """
    Collects and processes network telemetry for IntelliFlow
    
    """

    def __init__(self, window_size: int = 10, capacity_default: float = 100e6):
       
        self.window_size = window_size
        self.capacity_default = capacity_default
        
        # Port stats: {dpid: {port_no: deque of PortStats}}
        self._port_stats: Dict[str, Dict[int, Deque[PortStats]]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=window_size + 1))
        )
        
        # Link configurations: {(dpid, port_no): LinkConfig}
        self._link_configs: Dict[tuple, LinkConfig] = {}
        
        # Computed link stats: {(dpid, port_no): deque of LinkStats}
        self._link_stats: Dict[tuple, Deque[LinkStats]] = defaultdict(
            lambda: deque(maxlen=window_size)
        )

        # CSV telemetry logger (set via set_telemetry_logger)
        self._telemetry_logger: Optional[TelemetryCSVLogger] = None

    def set_telemetry_logger(self, telemetry_logger: TelemetryCSVLogger):
        """CSV logger for persistent telemetry recording"""
        self._telemetry_logger = telemetry_logger

    def configure_link(self, dpid: str, port_no: int, capacity_bps: float, name: str = ""):
        """Register link with ts capacity for utilisation calculation"""
        key = (dpid, port_no)
        self._link_configs[key] = LinkConfig(
            dpid=dpid, port_no=port_no, capacity_bps=capacity_bps, name=name
        )

    def record_port_stats(self, dpid, port_no: int, tx_bytes: int, rx_bytes: int,
                          tx_packets: int, rx_packets: int,
                          tx_dropped: int = 0, rx_dropped: int = 0,
                          timestamp: Optional[float] = None):
        """
        Record new port statistics from OpenFlow PortStatsReply.
        
        """
        dpid_str = dpid_to_str(dpid) if isinstance(dpid, int) else dpid
        ts = timestamp or time.time()
        
        stats = PortStats(
            timestamp=ts,
            tx_bytes=tx_bytes,
            rx_bytes=rx_bytes,
            tx_packets=tx_packets,
            rx_packets=rx_packets,
            tx_dropped=tx_dropped,
            rx_dropped=rx_dropped
        )
        
        self._port_stats[dpid_str][port_no].append(stats)
        
        # Compute derived link stats if we have at least 2 samples
        self._compute_link_stats(dpid_str, port_no)

    def _compute_link_stats(self, dpid: str, port_no: int):
        """Compute utilisation and queue proxy from raw port stats"""
        port_history = self._port_stats[dpid][port_no]
        if len(port_history) < 2:
            return
        
        prev_stats = port_history[-2]
        curr_stats = port_history[-1]
        
        dt = curr_stats.timestamp - prev_stats.timestamp
        if dt <= 0:
            return
        
        # Compute bytes/second (TX direction - what we're sending out)
        delta_bytes = curr_stats.tx_bytes - prev_stats.tx_bytes
        bytes_per_sec = delta_bytes / dt
        
        # Get link capacity for normalisation
        key = (dpid, port_no)
        config = self._link_configs.get(key)
        capacity_bps = config.capacity_bps if config else self.capacity_default
        capacity_bytes_per_sec = capacity_bps / 8
        
        # Normalised utilisation [0, 1]
        utilisation = min(1.0, bytes_per_sec / capacity_bytes_per_sec) if capacity_bytes_per_sec > 0 else 0.0
        
        # Compute delta_utilisation (derivative) from previous link stats
        link_history = self._link_stats[key]
        if link_history:
            prev_util = link_history[-1].utilisation
            delta_util = utilisation - prev_util
        else:
            delta_util = 0.0
        
        # Queue proxy: combination of utilisation level and growth
        # High utilisation + positive growth indicates queue buildup
        queue_proxy = utilisation * 0.7 + max(0, delta_util) * 0.3
        
        link_stats = LinkStats(
            timestamp=curr_stats.timestamp,
            utilisation=utilisation,
            utilisation_raw=bytes_per_sec,
            delta_utilisation=delta_util,
            queue_proxy=queue_proxy
        )
        
        self._link_stats[key].append(link_stats)

        # Persist to CSV
        if self._telemetry_logger:
            link_id = f"{dpid}:{port_no}"
            self._telemetry_logger.log(
                link_id=link_id,
                utilisation=utilisation,
                delta_utilisation=delta_util,
                queue_proxy=queue_proxy,
            )

    def get_link_stats(self, dpid: str, port_no: int) -> Optional[LinkStats]:
        """Get most recent link statistics."""
        key = (dpid, port_no)
        history = self._link_stats.get(key)
        return history[-1] if history else None

    def get_link_history(self, dpid: str, port_no: int, n: Optional[int] = None) -> list:
        """
        Get historical link statistics for LSTM input.
        
        """
        key = (dpid, port_no)
        history = self._link_stats.get(key, deque())
        n = n or self.window_size
        return list(history)[-n:]

    def get_utilisation_window(self, dpid: str, port_no: int, n: Optional[int] = None) -> list:
        """
        Get utilisation values as a list for LSTM input.
     
        """
        history = self.get_link_history(dpid, port_no, n)
        return [s.utilisation for s in history]

    def get_all_current_stats(self) -> Dict[tuple, LinkStats]:
        """Get current stats for all monitored links."""
        result = {}
        for key, history in self._link_stats.items():
            if history:
                result[key] = history[-1]
        return result

    def get_congested_links(self, util_threshold: float = 0.8,
                            derivative_threshold: float = 0.0) -> list:
        """
        Find links showing signs of congestion
        """
        congested = []
        for (dpid, port_no), history in self._link_stats.items():
            if not history:
                continue
            stats = history[-1]
            if stats.utilisation > util_threshold and stats.delta_utilisation > derivative_threshold:
                congested.append((dpid, port_no, stats))
        return congested

    def clear(self):
        """Clear all collected statistics"""
        self._port_stats.clear()
        self._link_stats.clear()
