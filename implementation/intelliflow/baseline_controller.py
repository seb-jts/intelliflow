"""
Baseline Controller (Static Routing)
Always uses Path A with no adaptive behavior
"""

import logging
import time

from os_ken.base.app_manager import OSKenApp
from os_ken.controller import ofp_event
from os_ken.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
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
)
from intelliflow.telemetry import TelemetryCollector


class BaselineController(OSKenApp):
    """
    Baseline static routing controller
    """

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    # Diamond topology constants
    DPID_S1 = '0000000000000001'
    DPID_S2 = '0000000000000002'
    DPID_S3 = '0000000000000003'
    DPID_S4 = '0000000000000004'

    # Port mappings
    S1_PORT_H1 = 1
    S1_PORT_S2 = 2  # Path A
    S1_PORT_S3 = 3  # Path B (unused in baseline)

    S2_PORT_S1 = 1
    S2_PORT_S4 = 2

    S4_PORT_H2 = 1
    S4_PORT_S2 = 2
    S4_PORT_S3 = 3

    H1_IP = '10.0.0.1'
    H2_IP = '10.0.0.2'

    def __init__(self, *args, **kwargs):
        super(BaselineController, self).__init__(*args, **kwargs)
        self.datapaths = {}
        self.mac_port_map = {}
        self.flow_mod_count = 0

        # Run ID and output directory
        self._run_id = generate_run_id()
        self._run_dir = setup_run_directory("baseline", self._run_id)
        self.logger.info("[START] Run ID: %s", self._run_id)
        self.logger.info("[START] Output directory: %s", self._run_dir)

        # Persist controller logs to file
        self._log_handler = setup_controller_log(self._run_dir)

        # Telemetry
        self._telemetry_logger = TelemetryCSVLogger(self._run_dir)
        self.telemetry = TelemetryCollector(window_size=10)
        self.telemetry.set_telemetry_logger(self._telemetry_logger)

        # Configure link capacities
        self.telemetry.configure_link(self.DPID_S1, self.S1_PORT_S2, 50e6, "s1-s2")  # Path A: 50 Mbps
        self.telemetry.configure_link(self.DPID_S1, self.S1_PORT_S3, 75e6, "s1-s3")  # Path B: 75 Mbps
        self.telemetry.configure_link(self.DPID_S4, self.S4_PORT_H2, 75e6, "s4-h2")

        self.logger.info(
            "[START] Baseline controller initialized (static Path A routing)")

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
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
        self._add_flow(datapath, 0, match, actions, idle=0)

        self.logger.info("[START] Switch connected: %s", dpid)

        # Install static routes when all switches connected
        if len(self.datapaths) == 4:
            self._install_static_routes()
            spawn(self._stats_polling_loop)

    def _install_static_routes(self):
        """Install static forwarding rules for Path A."""
        self.logger.info("[START] Installing static routes (Path A only)")

        # s1: h1->h2 via s2
        if self.DPID_S1 in self.datapaths:
            dp = self.datapaths[self.DPID_S1]
            parser = dp.ofproto_parser

            # Forward to h2 via s2
            match = parser.OFPMatch(eth_type=0x0800, ipv4_dst=self.H2_IP)
            actions = [parser.OFPActionOutput(self.S1_PORT_S2)]
            self._add_flow(dp, 10, match, actions)

            # Forward to h1
            match = parser.OFPMatch(eth_type=0x0800, ipv4_dst=self.H1_IP)
            actions = [parser.OFPActionOutput(self.S1_PORT_H1)]
            self._add_flow(dp, 10, match, actions)

        # s2: forward between s1 and s4
        if self.DPID_S2 in self.datapaths:
            dp = self.datapaths[self.DPID_S2]
            parser = dp.ofproto_parser

            match = parser.OFPMatch(eth_type=0x0800, ipv4_dst=self.H2_IP)
            actions = [parser.OFPActionOutput(self.S2_PORT_S4)]
            self._add_flow(dp, 10, match, actions)

            match = parser.OFPMatch(eth_type=0x0800, ipv4_dst=self.H1_IP)
            actions = [parser.OFPActionOutput(self.S2_PORT_S1)]
            self._add_flow(dp, 10, match, actions)

        # s4: forward to h2 or back via s2
        if self.DPID_S4 in self.datapaths:
            dp = self.datapaths[self.DPID_S4]
            parser = dp.ofproto_parser

            match = parser.OFPMatch(eth_type=0x0800, ipv4_dst=self.H2_IP)
            actions = [parser.OFPActionOutput(self.S4_PORT_H2)]
            self._add_flow(dp, 10, match, actions)

            match = parser.OFPMatch(eth_type=0x0800, ipv4_dst=self.H1_IP)
            actions = [parser.OFPActionOutput(self.S4_PORT_S2)]
            self._add_flow(dp, 10, match, actions)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        dpid = dpid_to_str(datapath.id)
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)

        # Filter illegal protocols
        if pkt.get_protocol(ipv6) or pkt.get_protocol(lldp):
            return

        eth = pkt.get_protocol(ethernet)
        if not eth:
            return

        # Handle ARP by flooding
        if pkt.get_protocol(arp):
            self._flood_packet(datapath, in_port, msg)
            return

        # For any other packet, just flood
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
            time.sleep(1.0)

    def _flood_packet(self, datapath, in_port, msg):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        actions = [parser.OFPActionOutput(ofproto.OFPP_FLOOD)]
        out = parser.OFPPacketOut(
            datapath=datapath, buffer_id=msg.buffer_id,
            in_port=in_port, actions=actions, data=msg.data
        )
        datapath.send_msg(out)

    def _add_flow(self, datapath, priority, match, actions, idle=60, hard=0):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(
            ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath, priority=priority, match=match,
            instructions=inst, idle_timeout=idle, hard_timeout=hard
        )
        datapath.send_msg(mod)
        self.flow_mod_count += 1
        self.logger.info(
            "[FLOW] Flow installed on %s (total: %d)", dpid_to_str(datapath.id), self.flow_mod_count)
