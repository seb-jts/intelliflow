"""
Reactive Layer (NOS-inspired Event-Driven Mechanism)
"""

import logging
import time
import threading
from typing import Optional, Dict, Set, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum

from intelliflow.logging_config import EventCSVLogger


class MitigationState(Enum):
    """State of a mitigation action"""
    ACTIVE = "active"
    RECOVERING = "recovering"
    REFRACTORY = "refractory"


@dataclass
class MitigationAction:
    """ corrective action taken by the reactive layer"""
    timestamp: float
    source_link: Tuple[str, int]  # (dpid, port_no) of congested link
    target_path: str  # Path to redirect to
    priority: int  # Flow rule priority (higher than baseline)
    expiry: float  # When this mitigation expires
    state: MitigationState = MitigationState.ACTIVE
    
    @property
    def is_expired(self) -> bool:
        return time.time() > self.expiry


@dataclass 
class CongestionEvent:
    """Detected congestion event"""
    timestamp: float
    dpid: str
    port_no: int
    utilisation: float
    delta_utilisation: float
    queue_proxy: float


class ReactiveLayer:
    """
    NOS-inspired reactive layer for fast congestion response
    """

    def __init__(self,
                 telemetry,
                 monitoring_interval: float = 0.5,
                 util_threshold: float = 0.8,
                 derivative_threshold: float = 0.05,
                 refractory_period: float = 2.0,
                 recovery_threshold: float = 0.6,
                 recovery_duration: float = 1.0,
                 mitigation_duration: float = 3.0,
                 max_concurrent_mitigations: int = 3,
                 corrective_priority: int = 100):
  
        self.logger = logging.getLogger(__name__)
        self.telemetry = telemetry
        self.monitoring_interval = monitoring_interval
        self.util_threshold = util_threshold
        self.derivative_threshold = derivative_threshold
        self.refractory_period = refractory_period
        self.recovery_threshold = recovery_threshold
        self.recovery_duration = recovery_duration
        self.mitigation_duration = mitigation_duration
        self.max_concurrent_mitigations = max_concurrent_mitigations
        self.corrective_priority = corrective_priority
        
        # Active mitigations: {(dpid, port_no): MitigationAction}
        self._mitigations: Dict[Tuple[str, int], MitigationAction] = {}
        
        # Refractory tracking: {(dpid, port_no): last_mitigation_end_time}
        self._refractory: Dict[Tuple[str, int], float] = {}
        
        # Recovery tracking: {(dpid, port_no): recovery_start_time}
        self._recovery_start: Dict[Tuple[str, int], float] = {}
        
        # Mapping of links to alternate paths
        # Will be configured by controller based on topology
        self._reroute_map: Dict[Tuple[str, int], str] = {}
        
        # Mapping links to their alternate link (to check capacity)
        # {congested_link: alternate_link}
        self._alternate_link_map: Dict[Tuple[str, int], Tuple[str, int]] = {}
        
        # Minimum headroom required on alternate path to allow reroute
        self.alternate_headroom = 0.5  # Only reroute if alternate < 50% utilised
        
        # Callbacks
        self._mitigation_callback: Optional[Callable[[MitigationAction], None]] = None
        self._recovery_callback: Optional[Callable[[Tuple[str, int]], None]] = None
        
        # CSV event logger (set via set_event_logger)
        self._event_logger: Optional[EventCSVLogger] = None

        # Monitoring thread
        self._running = False
        self._thread: Optional[threading.Thread] = None
        
        # Statistics
        self.stats = {
            'mitigations_triggered': 0,
            'mitigations_expired': 0,
            'mitigations_recovered': 0,
            'congestion_events': 0
        }

    def set_event_logger(self, event_logger: EventCSVLogger):
        """Attach a CSV event logger for persistent event recording."""
        self._event_logger = event_logger

    def _log_event(self, event_type: str, link: Tuple[str, int],
                   utilisation: float = 0.0, delta_utilisation: float = 0.0,
                   state: str = "", details: str = ""):
        """Write an event to the CSV logger if attached."""
        if self._event_logger:
            link_id = f"{link[0]}:{link[1]}"
            self._event_logger.log(
                event_type=event_type,
                link_id=link_id,
                utilisation=utilisation,
                delta_utilisation=delta_utilisation,
                state=state,
                details=details,
            )

    def configure_reroute(self, link: Tuple[str, int], alternate_path: str,
                          alternate_link: Tuple[str, int] = None):
        """
        Configure which alternate path to use when a link is congested. """

        self._reroute_map[link] = alternate_path
        if alternate_link:
            self._alternate_link_map[link] = alternate_link

    def _has_alternate_capacity(self, link: Tuple[str, int]) -> bool:
        """Check if alternate path has enough capacity to accept rerouted traffic"""
        alternate_link = self._alternate_link_map.get(link)
        if not alternate_link:
            # No alternate link configured - allow reroute (backward compat)
            return True
        
        # Get current utilisation of alternate path
        stats = self.telemetry.get_link_stats(alternate_link[0], alternate_link[1])
        if not stats:
            # No data yet - don't reroute blindly
            return False
        
        # Only reroute if alternate has headroom
        max_allowed = 1.0 - self.alternate_headroom
        return stats.utilisation < max_allowed

    def set_mitigation_callback(self, callback: Callable[[MitigationAction], None]):
        """Set callback invoked when a new mitigation is triggered."""
        self._mitigation_callback = callback

    def set_recovery_callback(self, callback: Callable[[Tuple[str, int]], None]):
        """Set callback invoked when a mitigation is cleared."""
        self._recovery_callback = callback

    def _is_in_refractory(self, link: Tuple[str, int]) -> bool:
        """Check if a link is in refractory period."""
        if link not in self._refractory:
            return False
        return time.time() < self._refractory[link] + self.refractory_period

    def _check_congestion(self, dpid: str, port_no: int, 
                          utilisation: float, delta_util: float) -> bool:
        """
        Check if a link shows signs of congestion requiring mitigation.
       
        """
        link = (dpid, port_no)
        
        # Already being mitigated
        if link in self._mitigations:
            return False
        
        # In refractory period
        if self._is_in_refractory(link):
            self._log_event("trigger_blocked_refractory", link,
                            utilisation, delta_util,
                            state="refractory",
                            details="link in refractory period")
            return False
        
        # Under global cap
        active_count = sum(1 for m in self._mitigations.values() 
                         if m.state == MitigationState.ACTIVE)
        if active_count >= self.max_concurrent_mitigations:
            self._log_event("trigger_blocked_cap", link,
                            utilisation, delta_util,
                            state="",
                            details=f"global cap reached ({active_count}/{self.max_concurrent_mitigations})")
            return False
        
        # Check thresholds
        if utilisation > self.util_threshold and delta_util > self.derivative_threshold:
            return True
        
        return False

    def _trigger_mitigation(self, dpid: str, port_no: int,
                            utilisation: float, delta_util: float) -> Optional[MitigationAction]:
        """
        Trigger a mitigation action for a congested link.
    
        """
        link = (dpid, port_no)
        
        # Get alternate path
        alternate = self._reroute_map.get(link)
        if not alternate:
            return None
        
        now = time.time()
        action = MitigationAction(
            timestamp=now,
            source_link=link,
            target_path=alternate,
            priority=self.corrective_priority,
            expiry=now + self.mitigation_duration,
            state=MitigationState.ACTIVE
        )
        
        self._mitigations[link] = action
        self.stats['mitigations_triggered'] += 1
        self.stats['congestion_events'] += 1

        self.logger.info("[REACTIVE] Congestion on %s:%s util=%.2f delta=%.3f",
                         dpid, port_no, utilisation, delta_util)
        self.logger.info("[MITIGATE] Mitigation triggered on %s:%s -> %s",
                         dpid, port_no, alternate)

        self._log_event("spike_detected", link,
                        utilisation, delta_util,
                        state="active",
                        details=f"util={utilisation:.4f} delta={delta_util:.4f}")
        self._log_event("mitigation_installed", link,
                        utilisation, delta_util,
                        state="active",
                        details=f"target={alternate} expiry={action.expiry:.3f}")

        # Notify controller
        if self._mitigation_callback:
            self._mitigation_callback(action)
        
        return action

    def _check_recovery(self, link: Tuple[str, int], current_util: float):
        """
        Check if a mitigated link has recovered
        """
        if link not in self._mitigations:
            return
        
        mitigation = self._mitigations[link]
        
        # Check expiry first
        if mitigation.is_expired:
            self._clear_mitigation(link, reason='expired')
            return
        
        now = time.time()
        
        if current_util < self.recovery_threshold:
            # Start or continue recovery tracking
            if link not in self._recovery_start:
                self._recovery_start[link] = now
                mitigation.state = MitigationState.RECOVERING
                self._log_event("recovery_started", link,
                                current_util, 0.0,
                                state="recovering",
                                details=f"util below {self.recovery_threshold}")
            elif now - self._recovery_start[link] >= self.recovery_duration:
                # Recovery complete
                self._clear_mitigation(link, reason='recovered')
        else:
            # Reset recovery if utilisation rises again
            if link in self._recovery_start:
                self._log_event("recovery_reset", link,
                                current_util, 0.0,
                                state="active",
                                details=f"util rose above {self.recovery_threshold}")
                del self._recovery_start[link]
                mitigation.state = MitigationState.ACTIVE

    def _clear_mitigation(self, link: Tuple[str, int], reason: str = 'unknown'):
        """Clear a mitigation and enter refractory period."""
        if link not in self._mitigations:
            return
        
        del self._mitigations[link]
        
        if link in self._recovery_start:
            del self._recovery_start[link]
        
        # Enter refractory period
        self._refractory[link] = time.time()
        
        # Update stats
        if reason == 'expired':
            self.stats['mitigations_expired'] += 1
        elif reason == 'recovered':
            self.stats['mitigations_recovered'] += 1

        self.logger.info("[RECOVER] Mitigation cleared on %s:%s reason=%s",
                         link[0], link[1], reason)

        self._log_event(f"mitigation_cleared_{reason}", link,
                        state="refractory",
                        details=f"entering refractory for {self.refractory_period}s")
        self._log_event("refractory_entered", link,
                        state="refractory",
                        details=f"duration={self.refractory_period}s")

        # Notify controller
        if self._recovery_callback:
            self._recovery_callback(link)

    def _monitoring_loop(self):
        """Main monitoring loop (runs in separate thread)"""
        while self._running:
            try:
                self._monitor_iteration()
            except Exception as e:
                print(f"Error in reactive monitoring: {e}")
            
            time.sleep(self.monitoring_interval)

    def _monitor_iteration(self):

        # Get current stats for all links
        all_stats = self.telemetry.get_all_current_stats()
        
        for (dpid, port_no), stats in all_stats.items():
            link = (dpid, port_no)
            
            # Check for recovery on existing mitigations
            if link in self._mitigations:
                self._check_recovery(link, stats.utilisation)
                continue
            
            # Check for new congestion
            if self._check_congestion(dpid, port_no, 
                                      stats.utilisation, stats.delta_utilisation):
                # Verify alternate path has capacity before rerouting
                if not self._has_alternate_capacity(link):
                    self.logger.debug(
                        "[REACTIVE] Congestion on %s:%s but alternate path saturated - skipping reroute",
                        dpid, port_no)
                    self._log_event("trigger_blocked_alternate", link,
                                    stats.utilisation, stats.delta_utilisation,
                                    state="",
                                    details="alternate path saturated")
                    continue
                self._trigger_mitigation(dpid, port_no,
                                        stats.utilisation, stats.delta_utilisation)

    def start(self):
        """Start the monitoring loop"""
        if self._running:
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the monitoring loop"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def get_active_mitigations(self) -> Dict[Tuple[str, int], MitigationAction]:
        """Get currently active mitigations"""
        return {k: v for k, v in self._mitigations.items() 
                if v.state == MitigationState.ACTIVE}

    def get_stats(self) -> dict:
        """Get reactive layer statistics"""
        return {
            **self.stats,
            'active_mitigations': len(self._mitigations),
            'links_in_refractory': sum(1 for link in self._refractory 
                                       if self._is_in_refractory(link))
        }

    def force_clear_all(self):
        """Force clear all mitigations (for testing/reset)"""
        for link in list(self._mitigations.keys()):
            self._clear_mitigation(link, reason='forced')
