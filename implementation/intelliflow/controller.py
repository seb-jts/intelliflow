"""
IntelliFlow Controller

OpenFlow 1.3 implementation for OSKen/Ryu framework
"""

import os
import time
import threading
from os_ken.base.app_manager import OSKenApp
from os_ken.controller import ofp_event
from os_ken.controller.handler import (
    HANDSHAKE_DISPATCHER, CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
)
from os_ken.ofproto import ofproto_v1_3
from os_ken.lib.packet import packet
from os_ken.lib.packet.ethernet import ethernet
from os_ken.lib.packet.ipv4 import ipv4
from os_ken.lib.packet.arp import arp
from os_ken.lib.packet.ipv6 import ipv6
from os_ken.lib.packet.lldp import lldp
from os_ken.lib.dpid import dpid_to_str
from os_ken.lib.hub import spawn

from intelliflow.logging_config import (
    generate_run_id,
    setup_run_directory,
    setup_controller_log,
    TelemetryCSVLogger,
    EventCSVLogger,
    DecisionCSVLogger,
)
from intelliflow.telemetry import TelemetryCollector
from intelliflow.predictive_layer import PredictiveLayer, Path, PlanningDecision
from intelliflow.reactive_layer import ReactiveLayer, MitigationAction


class IntelliFlowController(OSKenApp):
    """
    IntelliFlow hybrid load balancing controller.
    """

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    
    ILLEGAL_PROTOCOLS = [ipv6, lldp]
    
    # Diamond topology constants
    DPID_S1 = '0000000000000001'
    DPID_S2 = '0000000000000002'
    DPID_S3 = '0000000000000003'
    DPID_S4 = '0000000000000004'
    
    # Port mappings
    S1_PORT_H1 = 1
    S1_PORT_S2 = 2  # Path A
    S1_PORT_S3 = 3  # Path B
    
    S2_PORT_S1 = 1
    S2_PORT_S4 = 2
    
    S3_PORT_S1 = 1
    S3_PORT_S4 = 2
    
    # s4 ports: h2 connected first, then s2, then s3
    S4_PORT_H2 = 1
    S4_PORT_S2 = 2
    S4_PORT_S3 = 3
    
    # Host addresses
    H1_MAC = '00:00:00:00:00:01'
    H2_MAC = '00:00:00:00:00:02'
    H1_IP = '10.0.0.1'
    H2_IP = '10.0.0.2'
    
    # Flow priorities
    PRIORITY_TABLE_MISS = 0
    PRIORITY_ARP = 10
    PRIORITY_BASELINE = 50
    PRIORITY_CORRECTIVE = 100

    def __init__(self, *args, **kwargs):
        super(IntelliFlowController, self).__init__(*args, **kwargs)
        
        # Run ID and output directory
        self._run_id = generate_run_id()
        self._run_dir = setup_run_directory("intelliflow", self._run_id)
        self.logger.info("[START] Run ID: %s", self._run_id)
        self.logger.info("[START] Output directory: %s", self._run_dir)

        # Persist controller logs to file
        self._log_handler = setup_controller_log(self._run_dir)

        # CSV loggers
        self._telemetry_logger = TelemetryCSVLogger(self._run_dir)
        self._event_logger = EventCSVLogger(self._run_dir)
        self._decision_logger = DecisionCSVLogger(self._run_dir)

        # Track connected switches
        self.datapaths = {}
        
        # MAC learning table (fallback for unknown traffic)
        self.mac_port_map = {}
        
        # Telemetry collector
        self.telemetry = TelemetryCollector(window_size=10)
        self.telemetry.set_telemetry_logger(self._telemetry_logger)
        self._configure_link_capacities()
        
        # Predictive layer
        self.predictive = PredictiveLayer(
            telemetry=self.telemetry,
            planning_interval=5.0,  # T_plan = 5s
            window_size=10,  # W = 10 samples
            horizon=1  # H = 1 step
        )
        self.predictive.set_decision_callback(self._on_planning_decision)
        self.predictive.set_decision_logger(self._decision_logger)
        
        # Load trained LSTM model
        model_path = os.path.join(os.path.dirname(__file__), 'training/models/lstm_model.pt')
        if os.path.exists(model_path):
            self.predictive.load_models(model_path)
            self.logger.info("[PREDICT] Loaded LSTM model from %s", model_path)
        else:
            self.logger.warning("[PREDICT] No trained model found - using untrained LSTM")
        
        # Reactive layer
        self.reactive = ReactiveLayer(
            telemetry=self.telemetry,
            monitoring_interval=0.5,  # dt = 500ms
            util_threshold=0.8,
            derivative_threshold=0.05,
            mitigation_duration=3.0,
            refractory_period=2.0
        )
        self.reactive.set_mitigation_callback(self._on_mitigation_triggered)
        self.reactive.set_recovery_callback(self._on_mitigation_cleared)
        self.reactive.set_event_logger(self._event_logger)
        
        # Current routing state
        self.current_path = Path.PATH_A  # Default to high-capacity path
        self._corrective_active = False
        self._corrective_target = None  # Track which path corrective flow targets
        
        # Flow cache to prevent duplicate installations
        self._installed_flows = set()  # (dpid, match_key)
        
        # Stats polling
        self._stats_interval = 1.0  # Poll every 1s for telemetry
        self._stats_thread = None
        
        # Flow modification counter (for evaluation)
        self.flow_mod_count = 0
        
        self.logger.info("[START] IntelliFlow controller initialized")

    def _configure_link_capacities(self):
        """Configure link capacities for proper utilisation calculation."""
        # Path A links (50 Mbps, matching diamondEval topology)
        self.telemetry.configure_link(self.DPID_S1, self.S1_PORT_S2, 50e6, "s1-s2")
        self.telemetry.configure_link(self.DPID_S2, self.S2_PORT_S4, 50e6, "s2-s4")
        
        # Path B links (75 Mbps, matching diamondEval topology)
        self.telemetry.configure_link(self.DPID_S1, self.S1_PORT_S3, 75e6, "s1-s3")
        self.telemetry.configure_link(self.DPID_S3, self.S3_PORT_S4, 75e6, "s3-s4")
        
        # Host links (75 Mbps, never the bottleneck)
        self.telemetry.configure_link(self.DPID_S1, self.S1_PORT_H1, 75e6, "s1-h1")
        self.telemetry.configure_link(self.DPID_S4, self.S4_PORT_H2, 75e6, "s4-h2")

    def _configure_layers(self):
        """Configure predictive and reactive layers after topology discovery."""
        # Configure predictive layer paths
        # Monitor outbound port from s1 to detect path congestion
        self.predictive.configure_paths(
            path_a_link=(self.DPID_S1, self.S1_PORT_S2),
            path_b_link=(self.DPID_S1, self.S1_PORT_S3)
        )
        
        # Configure reactive layer reroutes
        self.reactive.configure_reroute(
            (self.DPID_S1, self.S1_PORT_S2),
            "path_b",
            alternate_link=(self.DPID_S1, self.S1_PORT_S3)
        )
        self.reactive.configure_reroute(
            (self.DPID_S1, self.S1_PORT_S3),
            "path_a",
            alternate_link=(self.DPID_S1, self.S1_PORT_S2)
        )

    # OpenFlow Event Handlers

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        """Handle switch connection and install table-miss flow."""
        datapath = ev.msg.datapath
        dpid = dpid_to_str(datapath.id)
        
        self.datapaths[dpid] = datapath
        self.mac_port_map[dpid] = {}
        
        # Install table-miss flow
        match = datapath.ofproto_parser.OFPMatch()
        actions = [datapath.ofproto_parser.OFPActionOutput(
            datapath.ofproto.OFPP_CONTROLLER,
            datapath.ofproto.OFPCML_NO_BUFFER
        )]
        self._add_flow(datapath, self.PRIORITY_TABLE_MISS, match, actions, idle=0)
        
        self.logger.info("[START] Switch connected: %s", dpid)
        
        # Start layers when all switches are connected
        if len(self.datapaths) == 4:  # All diamond switches connected
            self._start_intelliflow()

    @set_ev_cls(ofp_event.EventOFPErrorMsg, [HANDSHAKE_DISPATCHER, CONFIG_DISPATCHER, MAIN_DISPATCHER])
    def error_handler(self, ev):
        """Handle OpenFlow errors."""
        error = ev.msg.datapath.ofproto.ofp_error_to_jsondict(ev.msg.type, ev.msg.code)
        self.logger.error("[ERROR] OpenFlow error: type=%s, code=%s", error.get('type'), error.get('code'))

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        """Handle packet-in events."""
        msg = ev.msg
        datapath = msg.datapath
        dpid = dpid_to_str(datapath.id)
        in_port = msg.match['in_port']
        
        pkt = packet.Packet(msg.data)
        
        # Filter illegal protocols
        if self._is_illegal_packet(pkt):
            return
        
        eth = pkt.get_protocol(ethernet)
        if not eth:
            return
        
        # Handle ARP specially for connectivity
        arp_pkt = pkt.get_protocol(arp)
        if arp_pkt:
            self._handle_arp(datapath, in_port, eth, arp_pkt, msg)
            return
        
        # Handle IPv4 traffic with path selection
        ip_pkt = pkt.get_protocol(ipv4)
        if ip_pkt:
            self._handle_ipv4(datapath, dpid, in_port, eth, ip_pkt, msg)
            return
        
        # Fallback: flood unknown traffic
        self._flood_packet(datapath, in_port, msg)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply_handler(self, ev):
        """Handle port statistics reply for telemetry."""
        datapath = ev.msg.datapath
        dpid = dpid_to_str(datapath.id)
        
        for stat in ev.msg.body:
            if stat.port_no < datapath.ofproto.OFPP_MAX:
                self.telemetry.record_port_stats(
                    dpid=dpid,
                    port_no=stat.port_no,
                    tx_bytes=stat.tx_bytes,
                    rx_bytes=stat.rx_bytes,
                    tx_packets=stat.tx_packets,
                    rx_packets=stat.rx_packets,
                    tx_dropped=stat.tx_dropped,
                    rx_dropped=stat.rx_dropped
                )

    # Packet Handling

    def _is_illegal_packet(self, pkt):
        """Check if packet contains illegal protocols."""
        for proto in self.ILLEGAL_PROTOCOLS:
            if pkt.get_protocol(proto):
                return True
        return False

    def _handle_arp(self, datapath, in_port, eth, arp_pkt, msg):
        """Handle ARP packets - flood to enable host discovery."""
        dpid = dpid_to_str(datapath.id)
        
        # Learn source MAC
        self.mac_port_map[dpid][eth.src] = in_port
        
        # Flood ARP
        self._flood_packet(datapath, in_port, msg)

    def _handle_ipv4(self, datapath, dpid, in_port, eth, ip_pkt, msg):
        """Handle IPv4 packets with intelligent path selection."""
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        
        # Learn source MAC
        self.mac_port_map[dpid][eth.src] = in_port
        
        # Path selection only at ingress switch (s1) for h1->h2 traffic
        if dpid == self.DPID_S1 and ip_pkt.src == self.H1_IP and ip_pkt.dst == self.H2_IP:
            out_port = self._get_path_output_port()
            actions = [parser.OFPActionOutput(out_port)]
            self._send_packet(datapath, msg, actions)
            return
        
        # For reverse traffic (h2->h1) or intermediate switches, use shortest path
        out_port = self._get_forwarding_port(dpid, eth.dst, ip_pkt)
        if out_port:
            actions = [parser.OFPActionOutput(out_port)]
            
            # Only install if not already installed (dedup)
            flow_key = (dpid, ip_pkt.dst)
            if flow_key not in self._installed_flows:
                match = parser.OFPMatch(
                    eth_type=0x0800,
                    ipv4_dst=ip_pkt.dst
                )
                self._add_flow(datapath, self.PRIORITY_BASELINE, match, actions)
                self._installed_flows.add(flow_key)
            
            self._send_packet(datapath, msg, actions)
        else:
            self._flood_packet(datapath, in_port, msg)

    def _get_path_output_port(self) -> int:
        """Get output port based on current path selection."""
        if self._corrective_active:
            # Corrective action overrides baseline
            return self.S1_PORT_S3 if self.current_path == Path.PATH_A else self.S1_PORT_S2
        
        if self.current_path == Path.PATH_A:
            return self.S1_PORT_S2
        else:
            return self.S1_PORT_S3

    def _get_forwarding_port(self, dpid, dst_mac, ip_pkt) -> int:
        """Get forwarding port for intermediate switches and return traffic."""
        # Static routing for diamond topology
        if dpid == self.DPID_S2:
            if ip_pkt.dst == self.H2_IP:
                return self.S2_PORT_S4
            elif ip_pkt.dst == self.H1_IP:
                return self.S2_PORT_S1
                
        elif dpid == self.DPID_S3:
            if ip_pkt.dst == self.H2_IP:
                return self.S3_PORT_S4
            elif ip_pkt.dst == self.H1_IP:
                return self.S3_PORT_S1
                
        elif dpid == self.DPID_S4:
            if ip_pkt.dst == self.H2_IP:
                return self.S4_PORT_H2
            elif ip_pkt.dst == self.H1_IP:
                # Return via Path A by default
                return self.S4_PORT_S2
                
        elif dpid == self.DPID_S1:
            if ip_pkt.dst == self.H1_IP:
                return self.S1_PORT_H1
        
        return None

    def _flood_packet(self, datapath, in_port, msg):
        """Flood a packet to all ports except input."""
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        actions = [parser.OFPActionOutput(ofproto.OFPP_FLOOD)]
        self._send_packet(datapath, msg, actions)

    def _send_packet(self, datapath, msg, actions):
        """Send a packet out."""
        parser = datapath.ofproto_parser
        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=msg.match['in_port'],
            actions=actions,
            data=msg.data
        )
        datapath.send_msg(out)

    # Flow Management

    def _add_flow(self, datapath, priority, match, actions, idle=60, hard=0):
        """Install a flow rule."""
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=inst,
            idle_timeout=idle,
            hard_timeout=hard
        )
        datapath.send_msg(mod)
        self.flow_mod_count += 1
        
        self.logger.info("[FLOW] Flow installed on %s (total: %d)", dpid_to_str(datapath.id), self.flow_mod_count)

    def _delete_flows_by_match(self, datapath, match, priority=None):
        """Delete flows matching criteria."""
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        
        mod = parser.OFPFlowMod(
            datapath=datapath,
            command=ofproto.OFPFC_DELETE,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            match=match
        )
        datapath.send_msg(mod)
        self.flow_mod_count += 1

    def _install_baseline_path(self, path: Path):
        """Install baseline forwarding rules for selected path."""
        if self.DPID_S1 not in self.datapaths:
            return
        
        datapath = self.datapaths[self.DPID_S1]
        parser = datapath.ofproto_parser
        
        # Determine output port
        out_port = self.S1_PORT_S2 if path == Path.PATH_A else self.S1_PORT_S3
        
        match = parser.OFPMatch(
            eth_type=0x0800,
            ipv4_src=self.H1_IP,
            ipv4_dst=self.H2_IP
        )
        actions = [parser.OFPActionOutput(out_port)]
        
        # Delete old rule first
        self._delete_flows_by_match(datapath, match)
        
        # Install new baseline rule
        self._add_flow(datapath, self.PRIORITY_BASELINE, match, actions)
        
        self.current_path = path
        self.logger.info("[PREDICT] Baseline path set to %s", path.value)

    def _install_corrective_flow(self, target_path: str):
        """Install high-priority corrective flow rule."""
        if self.DPID_S1 not in self.datapaths:
            return
        
        datapath = self.datapaths[self.DPID_S1]
        parser = datapath.ofproto_parser
        
        # Determine output port (opposite of current path)
        out_port = self.S1_PORT_S2 if target_path == "path_a" else self.S1_PORT_S3
        
        match = parser.OFPMatch(
            eth_type=0x0800,
            ipv4_src=self.H1_IP,
            ipv4_dst=self.H2_IP
        )
        actions = [parser.OFPActionOutput(out_port)]
        
        # Install with higher priority and short timeout
        self._add_flow(
            datapath, 
            self.PRIORITY_CORRECTIVE, 
            match, 
            actions,
            idle=0,  # No idle timeout
            hard=3   # Hard timeout = mitigation duration
        )
        
        self._corrective_active = True
        self.logger.info("[REACTIVE] Corrective flow installed: reroute to %s", target_path)

    def _clear_corrective_flow(self):
        """Clear corrective flow rules."""
        if self.DPID_S1 not in self.datapaths:
            return
        
        datapath = self.datapaths[self.DPID_S1]
        parser = datapath.ofproto_parser
        
        match = parser.OFPMatch(
            eth_type=0x0800,
            ipv4_src=self.H1_IP,
            ipv4_dst=self.H2_IP
        )
        
        # Delete high-priority corrective rules

        self._delete_flows_by_match(datapath, match)
        
        # Reinstall baseline
        self._install_baseline_path(self.current_path)
        
        self._corrective_active = False
        self._corrective_target = None
        self.logger.info("[RECOVER] Corrective flow cleared, baseline restored")

    # Layer Callbacks

    def _on_planning_decision(self, decision: PlanningDecision):
        """Callback when predictive layer makes a new decision."""
        self.logger.info(
            "[PREDICT] Predictive decision: %s (A:%.2f, B:%.2f)",
            decision.selected_path.value,
            decision.predicted_util_path_a,
            decision.predicted_util_path_b
        )
        
        # Only update if not in corrective mode and path changed
        if not self._corrective_active and decision.selected_path != self.current_path:
            self._install_baseline_path(decision.selected_path)

    def _on_mitigation_triggered(self, action: MitigationAction):
        """Callback when reactive layer triggers mitigation."""
        # Prevent oscillation: don't reroute if corrective already active
        if self._corrective_active:
            self.logger.info("[REACTIVE] Ignoring mitigation (corrective already active): %s", action.source_link)
            return
        
        self.logger.warning(
            "[MITIGATE] Mitigation triggered! Rerouting to %s (congested link: %s)",
            action.target_path, action.source_link
        )
        self._corrective_target = action.target_path
        self._install_corrective_flow(action.target_path)

    def _on_mitigation_cleared(self, link: tuple):
        """Callback when reactive layer clears mitigation."""
        self.logger.info("[RECOVER] Mitigation cleared for link %s", link)
        self._clear_corrective_flow()

    #  Stats Polling

    def _request_port_stats(self):
        """Request port statistics from all switches."""
        for dpid, datapath in self.datapaths.items():
            parser = datapath.ofproto_parser
            req = parser.OFPPortStatsRequest(datapath, 0, datapath.ofproto.OFPP_ANY)
            datapath.send_msg(req)

    def _stats_polling_loop(self):
        """Periodic stats polling loop."""
        while True:
            self._request_port_stats()
            time.sleep(self._stats_interval)

    # Starting

    def _start_intelliflow(self):
        """Start IntelliFlow layers after all switches connected."""
        self.logger.info("[START] All switches connected - starting IntelliFlow layers")
        
        # Configure layers with topology information
        self._configure_layers()
        
        # Install initial baseline path
        self._install_baseline_path(Path.PATH_A)
        
        # Start stats polling
        spawn(self._stats_polling_loop)
        
        # Start predictive layer
        self.predictive.start()
        self.logger.info("[PREDICT] Predictive layer started (T_plan=5s)")
        
        # Start reactive layer
        self.reactive.start()
        self.logger.info("[REACTIVE] Reactive layer started (dt=500ms)")

    def get_stats(self) -> dict:
        """Get controller statistics for evaluation."""
        return {
            'flow_mod_count': self.flow_mod_count,
            'current_path': self.current_path.value,
            'corrective_active': self._corrective_active,
            'reactive_stats': self.reactive.get_stats(),
            'telemetry_links': len(self.telemetry.get_all_current_stats())
        }
