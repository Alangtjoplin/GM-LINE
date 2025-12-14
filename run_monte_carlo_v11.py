"""
MONTE CARLO RUNNER FOR PRODUCTION v11
- 52-week simulation with 8-hour cut time
- Run multiple simulations to analyze variation and statistics
- Configurable number of runs
"""

import random
import sys

# Import the simulator from v11
from production_v11 import ProductionSimulator


def run_monte_carlo(num_runs=100, verbose=True):
    """
    Run multiple simulations and collect statistics
    
    Args:
        num_runs: Number of simulation runs (default 100)
        verbose: Print progress updates
    
    Returns:
        Dictionary with all results and statistics
    """
    
    if verbose:
        print("=" * 80)
        print(f"MONTE CARLO ANALYSIS - {num_runs} RUNS")
        print("=" * 80)
    
    # Get targets from simulator
    sim_template = ProductionSimulator()
    
    if verbose:
        print(f"\nConfiguration:")
        print(f"  WB Sheets: {sim_template.WB_SHEETS}")
        print(f"  BB Sheets: {sim_template.BB_SHEETS}")
        print(f"  Cut Time: {sim_template.CUT_TIME}h")
        print(f"  WB Cure: {sim_template.CURE_WB_MIN}-{sim_template.CURE_WB_MAX}h (random)")
        print(f"\nTargets:")
        print(f"  WB: {sim_template.ANNUAL_WB_TARGET:,}")
        print(f"  BB: {sim_template.ANNUAL_BB_TARGET:,}")
        print(f"\nRunning {num_runs} simulations...")
    
    # Storage for results
    all_results = []
    all_wb_totals = []
    all_bb_totals = []
    all_batches = []
    all_weekly_wb = {w: [] for w in range(1, 53)}
    all_weekly_bb = {w: [] for w in range(1, 53)}
    
    for run in range(num_runs):
        # Create new simulator (no seed = random each time)
        sim = ProductionSimulator()
        result = sim.simulate()
        
        # Store results
        all_results.append(result)
        all_wb_totals.append(result['total_wb'])
        all_bb_totals.append(result['total_bb'])
        all_batches.append(len(result['batches']))
        
        for w in range(1, 53):
            all_weekly_wb[w].append(result['weekly_wb'].get(w, 0))
            all_weekly_bb[w].append(result['weekly_bb'].get(w, 0))
        
        if verbose and (run + 1) % max(1, num_runs // 10) == 0:
            print(f"  Completed {run + 1}/{num_runs}...")
    
    # Calculate statistics
    avg_wb = sum(all_wb_totals) / num_runs
    avg_bb = sum(all_bb_totals) / num_runs
    avg_batches = sum(all_batches) / num_runs
    
    min_wb = min(all_wb_totals)
    max_wb = max(all_wb_totals)
    min_bb = min(all_bb_totals)
    max_bb = max(all_bb_totals)
    
    combined = [all_wb_totals[i] + all_bb_totals[i] for i in range(num_runs)]
    avg_combined = sum(combined) / num_runs
    
    # Print results
    if verbose:
        print(f"\n{'ANNUAL TOTALS':=^80}")
        print(f"\n{'':20} {'Target':>12} {'Avg':>12} {'Min':>12} {'Max':>12} {'%Target':>10}")
        print("-" * 80)
        print(f"{'WB':<20} {sim_template.ANNUAL_WB_TARGET:>12,} {avg_wb:>12,.0f} {min_wb:>12,} {max_wb:>12,} {100*avg_wb/sim_template.ANNUAL_WB_TARGET:>9.1f}%")
        print(f"{'BB':<20} {sim_template.ANNUAL_BB_TARGET:>12,} {avg_bb:>12,.0f} {min_bb:>12,} {max_bb:>12,} {100*avg_bb/sim_template.ANNUAL_BB_TARGET:>9.1f}%")
        print(f"{'COMBINED':<20} {sim_template.ANNUAL_WB_TARGET + sim_template.ANNUAL_BB_TARGET:>12,} {avg_combined:>12,.0f} {min(combined):>12,} {max(combined):>12,} {100*avg_combined/(sim_template.ANNUAL_WB_TARGET + sim_template.ANNUAL_BB_TARGET):>9.1f}%")
        
        print(f"\n{'VARIATION ANALYSIS':=^80}")
        print(f"\nWB Range: {max_wb - min_wb:,} units ({100*(max_wb-min_wb)/avg_wb:.2f}% of avg)")
        print(f"BB Range: {max_bb - min_bb:,} units ({100*(max_bb-min_bb)/avg_bb:.2f}% of avg)")
        
        print(f"\n{'BATCH STATISTICS':=^80}")
        print(f"\nTotal batches: avg={avg_batches:.1f}, min={min(all_batches)}, max={max(all_batches)}")
        print(f"Batches/week: {avg_batches/52:.2f}")
        
        # WB vs BB batch breakdown
        wb_batch_counts = []
        bb_batch_counts = []
        for r in all_results:
            wb_b = sum(1 for b in r['batches'].values() if b['product'] == 'WB')
            bb_b = sum(1 for b in r['batches'].values() if b['product'] == 'BB')
            wb_batch_counts.append(wb_b)
            bb_batch_counts.append(bb_b)
        
        print(f"WB batches: avg={sum(wb_batch_counts)/num_runs:.1f}, min={min(wb_batch_counts)}, max={max(wb_batch_counts)}")
        print(f"BB batches: avg={sum(bb_batch_counts)/num_runs:.1f}, min={min(bb_batch_counts)}, max={max(bb_batch_counts)}")
        
        print(f"\n{'WEEKLY AVERAGES':=^80}")
        
        weekly_wb_avgs = {w: sum(all_weekly_wb[w])/num_runs for w in range(1, 53)}
        weekly_bb_avgs = {w: sum(all_weekly_bb[w])/num_runs for w in range(1, 53)}
        
        avg_weekly_wb = sum(weekly_wb_avgs.values()) / 52
        avg_weekly_bb = sum(weekly_bb_avgs.values()) / 52
        
        print(f"\nWB per week: avg={avg_weekly_wb:,.0f}")
        print(f"BB per week: avg={avg_weekly_bb:,.0f}")
        
        print(f"\n{'SHORTFALL ANALYSIS':=^80}")
        
        wb_shortfall = sim_template.ANNUAL_WB_TARGET - avg_wb
        bb_shortfall = sim_template.ANNUAL_BB_TARGET - avg_bb
        
        print(f"\nWB Shortfall: {wb_shortfall:,.0f} ({100*wb_shortfall/sim_template.ANNUAL_WB_TARGET:.1f}% of target)")
        print(f"BB Shortfall: {bb_shortfall:,.0f} ({100*bb_shortfall/sim_template.ANNUAL_BB_TARGET:.1f}% of target)")
        
        # Success rate (if we defined targets as weekly minimums)
        print(f"\n{'WEEKLY SUCCESS ANALYSIS':=^80}")
        
        weekly_wb_target = sim_template.ANNUAL_WB_TARGET / 52
        weekly_bb_target = sim_template.ANNUAL_BB_TARGET / 52
        
        wb_success_weeks = 0
        bb_success_weeks = 0
        total_weeks = num_runs * 52
        
        for w in range(1, 53):
            for val in all_weekly_wb[w]:
                if val >= weekly_wb_target:
                    wb_success_weeks += 1
            for val in all_weekly_bb[w]:
                if val >= weekly_bb_target:
                    bb_success_weeks += 1
        
        print(f"\nWeeks meeting weekly target ({weekly_wb_target:,.0f} WB, {weekly_bb_target:,.0f} BB):")
        print(f"  WB: {wb_success_weeks}/{total_weeks} ({100*wb_success_weeks/total_weeks:.1f}%)")
        print(f"  BB: {bb_success_weeks}/{total_weeks} ({100*bb_success_weeks/total_weeks:.1f}%)")
        
        print("\n" + "=" * 80)
    
    return {
        'num_runs': num_runs,
        'all_wb_totals': all_wb_totals,
        'all_bb_totals': all_bb_totals,
        'all_batches': all_batches,
        'all_weekly_wb': all_weekly_wb,
        'all_weekly_bb': all_weekly_bb,
        'avg_wb': avg_wb,
        'avg_bb': avg_bb,
        'min_wb': min_wb,
        'max_wb': max_wb,
        'min_bb': min_bb,
        'max_bb': max_bb,
        'avg_batches': avg_batches,
        'all_results': all_results
    }


def quick_stats(num_runs=100):
    """Quick summary without full details"""
    
    print(f"Running {num_runs} simulations...")
    
    wb_totals = []
    bb_totals = []
    
    for i in range(num_runs):
        sim = ProductionSimulator()
        result = sim.simulate()
        wb_totals.append(result['total_wb'])
        bb_totals.append(result['total_bb'])
        
        if (i + 1) % 100 == 0:
            print(f"  {i+1}/{num_runs}...")
    
    print(f"\nResults ({num_runs} runs):")
    print(f"  WB: {sum(wb_totals)/num_runs:,.0f} avg ({min(wb_totals):,} - {max(wb_totals):,})")
    print(f"  BB: {sum(bb_totals)/num_runs:,.0f} avg ({min(bb_totals):,} - {max(bb_totals):,})")
    print(f"  Total: {(sum(wb_totals)+sum(bb_totals))/num_runs:,.0f} avg")


if __name__ == "__main__":
    # Default to 100 runs, or use command line argument
    num_runs = 100
    
    if len(sys.argv) > 1:
        try:
            num_runs = int(sys.argv[1])
        except ValueError:
            print(f"Usage: python {sys.argv[0]} [num_runs]")
            print(f"  num_runs: Number of simulations (default 100)")
            sys.exit(1)
    
    results = run_monte_carlo(num_runs)