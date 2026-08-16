# IntelliFlow

Hybrid SDN load balancing framework combining LSTM-based traffic prediction with NOS-inspired reactive congestion control, built on OSKen/Ryu OpenFlow 1.3.

## Quick Start

```bash
# In container terminal 1: Start controller
cd intelliflow && ./run.sh controller

# In container terminal 2: Start Mininet
cd intelliflow && ./run.sh mininet

# In Mininet CLI:
h2 iperf3 -s &
h1 bash traffic/bursty.sh
```

## Training

```bash
cd intelliflow/training
python generate_traces.py -n 20 -d 600 -o data/traces
python train_lstm.py -t data/traces -o models --epochs 100
```

## How to Evaluate

1. Run IntelliFlow experiment
```bash
./run.sh controller           # Terminal 1
./run.sh mininet              # Terminal 2
mininet> h2 iperf3 -s &
mininet> h1 bash evaluate.sh intelliflow
mininet> exit
```

2. Run Baseline experiment
```bash
./run.sh baseline             # Terminal 1
./run.sh mininet              # Terminal 2
mininet> h2 iperf3 -s &
mininet> h1 bash evaluate.sh baseline
```

3. Run prediction-only experiment
```bash
./run.sh predictive
./run.sh mininet
mininet> h2 iperf3 -s &
mininet> h1 bash evaluate.sh predictive
```

4. Compare results
```bash
python3 parse_results.py results compare
```

## Acknowledgements

This project is built on top of:

[Tutorial: OpenFlow with OSKen](https://github.com/scc333-networking/tutorial-ken) by Davis, Eleanor; Rotsos, Haris; Swarbrick, Tom; Fantom, Will

[Ryu Tutorial Solution](https://github.com/scc333-networking/tutorial-solution-osken) by Rotsos, Haris; Swarbrick, Tom; Fantom, Will

Other resources used:

[Sequence Models and Long-Short Term Memory Networks](https://docs.pytorch.org/tutorials/beginner/nlp/sequence_models_tutorial.html) by PyTorch

[Mininet: Rapid Prototyping for Software-Defined Networks](http://mininet.org) by Lantz, B., Heller, B., and McKeown, N.

[Welcome to the documentation of os_ken](https://docs.openstack.org/os-ken/latest/) by OpenStack

[OpenStack os_ken repository](https://opendev.org/openstack/os-ken) by OpenStack

[Random Early Detection Gateways for Congestion Avoidance](https://personal.utdallas.edu/~jjue/cs6390/papers/red.pdf) by Floyd, S., Jacobson, V.

## Use of Generative AI

Claude Sonnet 4.6 (2026). Anthropic. https://claude.ai.
- Was used to help brainstorm a basic template architecture for IntelliFlow
- Was used to help brainstorm a structure for the evaluations
- Was used to help with debugging and tracing error messages
- Was used to refine code comments and make them more detailed

ChatGPT 5.3 (2026). OpenAI. https://chatgpt.com.
- Was used to explain complicated concepts in regards to the research conducted for the literature review
- Was used to suggest how to evaluate the system’s performance
