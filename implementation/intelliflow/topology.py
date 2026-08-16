"""
IntelliFlow Topology

Path A via s2: 50 Mbps, 2 ms per link
Path B via s3: 75 Mbps, 10 ms per link
"""

from mininet.topo import Topo
from mininet.link import TCLink


class DiamondTopology(Topo):
   
    def __init__(self, bottleneck_bw=75, bottleneck_delay='10ms',
                 main_bw=75, main_delay='5ms',
                 path_a_bw=None, path_a_delay=None, **kwargs):
        self.bottleneck_bw = bottleneck_bw
        self.bottleneck_delay = bottleneck_delay
        self.main_bw = main_bw
        self.main_delay = main_delay
        # path_a_bw/path_a_delay allow Path A to differ from host-link params

        self.path_a_bw = path_a_bw if path_a_bw is not None else main_bw
        self.path_a_delay = path_a_delay if path_a_delay is not None else main_delay
        super(DiamondTopology, self).__init__(**kwargs)

    def build(self):
        # Add switches
        s1 = self.addSwitch('s1', dpid='0000000000000001')
        s2 = self.addSwitch('s2', dpid='0000000000000002')
        s3 = self.addSwitch('s3', dpid='0000000000000003')
        s4 = self.addSwitch('s4', dpid='0000000000000004')

        # Add hosts
        h1 = self.addHost('h1', ip='10.0.0.1/24', mac='00:00:00:00:00:01')
        h2 = self.addHost('h2', ip='10.0.0.2/24', mac='00:00:00:00:00:02')

        # Host links
        self.addLink(h1, s1, cls=TCLink, bw=self.main_bw, delay=self.main_delay)
        self.addLink(h2, s4, cls=TCLink, bw=self.main_bw, delay=self.main_delay)

        # Path A: s1 - s2 - s4
        self.addLink(s1, s2, cls=TCLink, bw=self.path_a_bw, delay=self.path_a_delay)
        self.addLink(s2, s4, cls=TCLink, bw=self.path_a_bw, delay=self.path_a_delay)

        # Path B: s1 - s3 - s4
        self.addLink(s1, s3, cls=TCLink, bw=self.bottleneck_bw, delay=self.bottleneck_delay)
        self.addLink(s3, s4, cls=TCLink, bw=self.bottleneck_bw, delay=self.bottleneck_delay)


class DiamondTopologyWithQueues(DiamondTopology):
    """
    Diamond topology
    """

    def __init__(self, max_queue_size=100, **kwargs):
        self.max_queue_size = max_queue_size
        super(DiamondTopologyWithQueues, self).__init__(**kwargs)

    def build(self):
        # Add switches
        s1 = self.addSwitch('s1', dpid='0000000000000001')
        s2 = self.addSwitch('s2', dpid='0000000000000002')
        s3 = self.addSwitch('s3', dpid='0000000000000003')
        s4 = self.addSwitch('s4', dpid='0000000000000004')

        # Add hosts
        h1 = self.addHost('h1', ip='10.0.0.1/24', mac='00:00:00:00:00:01')
        h2 = self.addHost('h2', ip='10.0.0.2/24', mac='00:00:00:00:00:02')

        # Host links
        self.addLink(h1, s1, cls=TCLink, bw=self.main_bw, delay=self.main_delay,
                     max_queue_size=self.max_queue_size)
        self.addLink(h2, s4, cls=TCLink, bw=self.main_bw, delay=self.main_delay,
                     max_queue_size=self.max_queue_size)

        # Path A: lower capacity, lower latency
        self.addLink(s1, s2, cls=TCLink, bw=self.path_a_bw, delay=self.path_a_delay,
                     max_queue_size=self.max_queue_size)
        self.addLink(s2, s4, cls=TCLink, bw=self.path_a_bw, delay=self.path_a_delay,
                     max_queue_size=self.max_queue_size)

        # Path B: higher capacity, higher latency
        self.addLink(s1, s3, cls=TCLink, bw=self.bottleneck_bw, delay=self.bottleneck_delay,
                     max_queue_size=self.max_queue_size)
        self.addLink(s3, s4, cls=TCLink, bw=self.bottleneck_bw, delay=self.bottleneck_delay,
                     max_queue_size=self.max_queue_size)


# Topology registry for Mininet
topos = {
    'diamond': (lambda: DiamondTopology()),
    'diamondQueues': (lambda: DiamondTopologyWithQueues()),
    'diamondBottleneck25': (lambda: DiamondTopology(bottleneck_bw=25)),
    'diamondEval': (lambda: DiamondTopology(
        main_bw=75, main_delay='5ms',
        path_a_bw=50, path_a_delay='2ms',
        bottleneck_bw=75, bottleneck_delay='10ms',
    )),
}
