#!/usr/bin/env python3

"""
Results Parser for evaluation
"""

import json
import sys
from pathlib import Path


def parse_iperf_json(filepath):
    """Parse iperf3 JSON output file."""
    try:
        with open(filepath) as f:
            content = f.read()

        # Handle multiple JSON objects (concatenated from bursts)
        results = []
        decoder = json.JSONDecoder()
        idx = 0
        while idx < len(content):
            content = content[idx:].lstrip()
            if not content:
                break
            try:
                obj, end_idx = decoder.raw_decode(content)
                results.append(obj)
                idx = end_idx
            except json.JSONDecodeError:
                break

        return results
    except Exception as e:
        print(f"Error parsing {filepath}: {e}")
        return []


def extract_metrics(iperf_results):
    """Extract key metrics from iperf3 results"""
    metrics = {
        'throughput_mbps': [],
        'jitter_ms': [],
        'lost_packets': 0,
        'total_packets': 0,
        'loss_percent': 0.0,
    }

    for result in iperf_results:
        if 'end' not in result:
            continue

        end = result['end']

        # UDP results
        if 'sum' in end:
            summary = end['sum']
            if 'bits_per_second' in summary:
                metrics['throughput_mbps'].append(summary['bits_per_second'] / 1e6)
            if 'jitter_ms' in summary:
                metrics['jitter_ms'].append(summary['jitter_ms'])
            if 'lost_packets' in summary:
                metrics['lost_packets'] += summary['lost_packets']
            if 'packets' in summary:
                metrics['total_packets'] += summary['packets']

        # Check streams
        if 'streams' in end:
            for stream in end['streams']:
                if 'udp' in stream:
                    udp = stream['udp']
                    if 'bits_per_second' in udp:
                        metrics['throughput_mbps'].append(udp['bits_per_second'] / 1e6)
                    if 'jitter_ms' in udp:
                        metrics['jitter_ms'].append(udp['jitter_ms'])
                    if 'lost_packets' in udp:
                        metrics['lost_packets'] += udp['lost_packets']
                    if 'packets' in udp:
                        metrics['total_packets'] += udp['packets']

    # Calculate averages
    if metrics['throughput_mbps']:
        metrics['avg_throughput_mbps'] = sum(metrics['throughput_mbps']) / len(metrics['throughput_mbps'])
    else:
        metrics['avg_throughput_mbps'] = 0.0

    if metrics['jitter_ms']:
        metrics['avg_jitter_ms'] = sum(metrics['jitter_ms']) / len(metrics['jitter_ms'])
    else:
        metrics['avg_jitter_ms'] = 0.0

    if metrics['total_packets'] > 0:
        metrics['loss_percent'] = (metrics['lost_packets'] / metrics['total_packets']) * 100
    else:
        metrics['loss_percent'] = 0.0

    return metrics

# Directory / file resolution helpers

def _find_latest_run_dir(results_dir, controller_name):
    """Return the most recent run directory for a controller, or None."""
    controller_dir = results_dir / controller_name
    if not controller_dir.is_dir():
        return None
    run_dirs = sorted([d for d in controller_dir.iterdir() if d.is_dir()])
    return run_dirs[-1] if run_dirs else None


def _load_scenario_file(filepath):
    """Parse a single scenario file and return its metrics, or None."""
    if not filepath.exists():
        return None
    results = parse_iperf_json(filepath)
    if not results:
        return None
    return extract_metrics(results)


_SCENARIO_ALIASES = {
    'steady':    ('steady',),
    'burst':     ('burst', 'bursty'),
    'sustained': ('sustained',),
    'mismatch':  ('mismatch',),
}


def _load_scenarios_from_dir(run_dir):
    """Load all recognised scenarios from a single run directory"""
    scenarios = {}
    for canonical, aliases in _SCENARIO_ALIASES.items():
        for alias in aliases:
            m = _load_scenario_file(run_dir / f"{alias}.json")
            if m is not None:
                scenarios[canonical] = m
                break
    return scenarios

"""
def _load_scenarios(results_dir, controller_name):
    scenarios = {}
    for canonical, aliases in _SCENARIO_ALIASES.items():
        for alias in aliases:
            files = sorted(results_dir.glob(f'{controller_name}_{alias}_*.json'))
            if files:
                m = _load_scenario_file(files[-1])
                if m is not None:
                    scenarios[canonical] = m
                    break
    return scenarios
"""

def load_controller_results(results_dir, controller_name):
    """
    Load scenarios for a controller
    """
    run_dir = _find_latest_run_dir(results_dir, controller_name)
    if run_dir is not None:
        scenarios = _load_scenarios_from_dir(run_dir)
        if scenarios:
            return scenarios, run_dir.name

    # Legacy flat structure: results/<controller>_<scenario>_<ts>.json
    # scenarios = _load_scenarios_flat(results_dir, controller_name)
    # if scenarios:
    #    return scenarios, 'flat'

    return {}, None


def _improvement_label(diff):
    
    if abs(diff) < 1e-6:
        return '[SAME]'
    return '[BETTER]' if diff > 0 else '[WORSE]'

_SCENARIOS_DISPLAY = [
    ('steady',    '[STEADY]    STEADY TRAFFIC (10 Mbps baseline)'),
    ('burst',     '[BURST]     BURST TRAFFIC (60 Mbps x3)'),
    ('sustained', '[SUSTAINED] SUSTAINED HIGH LOAD (55 Mbps)'),
    ('mismatch',  '[MISMATCH]  PREDICTION MISMATCH'),
]


def print_summary(name, scenarios, run_label=None):
    """Print formatted summary of experiment results."""
    print(f"\n{'='*60}")
    header = f"  {name.upper()} EXPERIMENT RESULTS"
    if run_label and run_label != 'flat':
        header += f"  (run {run_label})"
    print(header)
    print(f"{'='*60}")

    present = []
    for key, title in _SCENARIOS_DISPLAY:
        m = scenarios.get(key)
        if m is None:
            continue
        present.append((key, m))
        print(f"\n{title}:")
        print(f"   Throughput:  {m['avg_throughput_mbps']:.2f} Mbps")
        print(f"   Jitter:      {m['avg_jitter_ms']:.3f} ms")
        print(f"   Packet Loss: {m['loss_percent']:.2f}%")

    if not present:
        print("\n  No scenario data found.")
        print(f"{'='*60}\n")
        return None

    # Warn about missing scenarios
    for key, title in _SCENARIOS_DISPLAY:
        if key not in scenarios:
            print(f"\n  WARNING: {key}.json not found -- skipped")

    # Overall averages across available scenarios
    n = len(present)
    avg_tp = sum(m['avg_throughput_mbps'] for _, m in present) / n
    avg_jt = sum(m['avg_jitter_ms'] for _, m in present) / n
    avg_lp = sum(m['loss_percent'] for _, m in present) / n

    print(f"\n[OVERALL] OVERALL ({n} scenarios):")
    print(f"   Average Throughput:  {avg_tp:.2f} Mbps")
    print(f"   Average Jitter:      {avg_jt:.3f} ms")
    print(f"   Average Packet Loss: {avg_lp:.2f}%")
    print(f"{'='*60}\n")

    return {
        'name': name,
        'scenarios': {k: m for k, m in present},
        'overall_throughput': avg_tp,
        'overall_loss': avg_lp,
        'overall_jitter': avg_jt,
    }


def compare_results(results_dir):
    """Compare all available controller results (intelliflow, baseline, predictive)."""
    results_dir = Path(results_dir)

    controller_names = ['intelliflow', 'baseline', 'predictive']
    loaded = {}
    runs = {}

    for name in controller_names:
        print(f"  Loading {name} results...")
        scenarios, run_label = load_controller_results(results_dir, name)
        if scenarios:
            loaded[name] = scenarios
            runs[name] = run_label
        else:
            print(f"  WARNING: No {name} results found -- skipping")

    if len(loaded) < 2:
        print("  ERROR: Need at least two controllers with results to compare.")
        return

    print("\n" + "=" * 60)
    print("  COMPARISON: " + " vs ".join(n.upper() for n in loaded))
    for name in loaded:
        if runs[name] and runs[name] != 'flat':
            print(f"  {name} run: {runs[name]}")
    print("=" * 60)

    # Find scenarios present in all loaded controllers
    common = sorted(set.intersection(*(set(s) for s in loaded.values())))
    if not common:
        print("\n  No common scenarios to compare.")
        print("=" * 60 + "\n")
        return

    names = list(loaded.keys())

    for key in common:
        print(f"\n  --- {key.upper()} ---")

        # Throughput: higher is better
        print(f"\n  Throughput:")
        for name in names:
            m = loaded[name][key]
            print(f"    {name:14s} {m['avg_throughput_mbps']:.2f} Mbps")
        # Pairwise improvements vs baseline (if present)
        if 'baseline' in loaded:
            bl_tp = loaded['baseline'][key]['avg_throughput_mbps']
            for name in names:
                if name == 'baseline':
                    continue
                diff = loaded[name][key]['avg_throughput_mbps'] - bl_tp
                print(f"    {name} vs baseline: {diff:+.2f} Mbps {_improvement_label(diff)}")

        # Packet loss: lower is better
        print(f"\n  Packet Loss:")
        for name in names:
            m = loaded[name][key]
            print(f"    {name:14s} {m['loss_percent']:.2f}%")
        if 'baseline' in loaded:
            bl_lp = loaded['baseline'][key]['loss_percent']
            for name in names:
                if name == 'baseline':
                    continue
                diff = bl_lp - loaded[name][key]['loss_percent']
                print(f"    {name} vs baseline: {diff:+.2f}% {_improvement_label(diff)}")

        # Jitter: lower is better
        print(f"\n  Jitter:")
        for name in names:
            m = loaded[name][key]
            print(f"    {name:14s} {m['avg_jitter_ms']:.3f} ms")
        if 'baseline' in loaded:
            bl_jt = loaded['baseline'][key]['avg_jitter_ms']
            for name in names:
                if name == 'baseline':
                    continue
                diff = bl_jt - loaded[name][key]['avg_jitter_ms']
                print(f"    {name} vs baseline: {diff:+.3f} ms {_improvement_label(diff)}")

    print("\n" + "=" * 60 + "\n")

# CLI entry point

def main():
    if len(sys.argv) < 3:
        print("Usage:")
        print("  parse_results.py <results_dir> compare")
        print("  parse_results.py <results_dir> <name> <timestamp>")
        print("  parse_results.py <results_dir> <name>            (latest run)")
        sys.exit(1)

    results_dir = Path(sys.argv[1])

    # compare mode
    if sys.argv[2] == 'compare':
        compare_results(results_dir)
        return

    name = sys.argv[2]

    # legacy
    if len(sys.argv) >= 4:
        timestamp = sys.argv[3]
        scenarios = {}
        for canonical in ('steady', 'burst', 'sustained'):
            filepath = results_dir / f"{name}_{canonical}_{timestamp}.json"
            if filepath.exists():
                m = _load_scenario_file(filepath)
                if m is not None:
                    scenarios[canonical] = m
                else:
                    print(f"  WARNING: could not parse {filepath.name}")
            else:
                print(f"  WARNING: {filepath.name} not found -- skipped")
        run_label = timestamp
    else:
        # auto mode
        scenarios, run_label = load_controller_results(results_dir, name)

    if not scenarios:
        print(f"  ERROR: No results found for '{name}'.")
        sys.exit(1)

    summary = print_summary(name, scenarios, run_label)

    # Save summary JSON
    if summary is not None:
        if run_label and run_label != 'flat':
            summary_file = results_dir / f"{name}_summary_{run_label}.json"
        else:
            summary_file = results_dir / f"{name}_summary.json"
        with open(summary_file, 'w') as f:
            json.dump(summary, f, indent=2, default=str)
        print(f"Summary saved to: {summary_file}")

    # Attempt comparison if both controllers have data
    compare_results(results_dir)


if __name__ == '__main__':
    main()
