"""
MONTE CARLO RUNNER
Runs the existing production_v7.py simulation 100 times
with random WB cure times (24-36h) and collects statistics
"""

import sys
import statistics

# Import the simulator from production_v7
from production_v7 import ProductionSimulator

NUM_RUNS = 10000

def main():
    print("=" * 100)
    print(f"MONTE CARLO ANALYSIS - {NUM_RUNS} RUNS")
    print("Using production_v7.py simulation with random WB cure times (24-36 hours)")
    print("=" * 100)
    
    # Storage for results
    week_success = {w: {'wb': 0, 'bb': 0, 'both': 0} for w in range(2, 14)}
    week_totals = {w: {'wb': [], 'bb': [], 'wb_prod': [], 'bb_prod': [], 'wb_carry': [], 'bb_carry': []} for w in range(1, 14)}
    
    all_results = []
    full_success_count = 0
    
    print(f"\nRunning {NUM_RUNS} simulations...\n")
    
    for run in range(NUM_RUNS):
        # Create new simulator (no seed = random each time)
        sim = ProductionSimulator(wb_sheets=3, bb_sheets=2)
        result = sim.simulate(verbose=False)
        
        # Check each week
        weeks_both_met = 0
        for week in range(1, 14):
            wb_prod = result['weekly_wb'][week]
            bb_prod = result['weekly_bb'][week]
            wb_carry = result['wb_carryover'][week]
            bb_carry = result['bb_carryover'][week]
            
            wb_total = wb_prod + wb_carry
            bb_total = bb_prod + bb_carry
            
            # Store data
            week_totals[week]['wb'].append(wb_total)
            week_totals[week]['bb'].append(bb_total)
            week_totals[week]['wb_prod'].append(wb_prod)
            week_totals[week]['bb_prod'].append(bb_prod)
            week_totals[week]['wb_carry'].append(wb_carry)
            week_totals[week]['bb_carry'].append(bb_carry)
            
            # Check success (skip week 1 ramp-up)
            if week > 1:
                wb_ok = wb_total >= 20000
                bb_ok = bb_total >= 20000
                
                if wb_ok:
                    week_success[week]['wb'] += 1
                if bb_ok:
                    week_success[week]['bb'] += 1
                if wb_ok and bb_ok:
                    week_success[week]['both'] += 1
                    weeks_both_met += 1
        
        if weeks_both_met == 12:
            full_success_count += 1
        
        # Store summary
        all_results.append({
            'total_wb': sum(result['weekly_wb'].values()),
            'total_bb': sum(result['weekly_bb'].values()),
            'batches': len(result['batches']),
            'weeks_met': weeks_both_met,
            'worker_log_entries': len(result['worker_log']),
            'pause_count': sum(1 for log in result['worker_log'] if 'PARTIAL' in log[2])
        })
        
        # Progress
        if (run + 1) % 10 == 0:
            print(f"  Completed {run + 1}/{NUM_RUNS} runs...")
    
    # ==================== RESULTS ====================
    print("\n" + "=" * 100)
    print("RESULTS SUMMARY")
    print("=" * 100)
    
    # Overall success
    print(f"\n{'OVERALL SUCCESS RATE':^100}")
    print(f"\nFull Success (ALL weeks 2-13 meet BOTH targets): {full_success_count}/{NUM_RUNS} ({100*full_success_count/NUM_RUNS:.1f}%)")
    
    # Per-week breakdown
    print(f"\n{'PER-WEEK SUCCESS RATES':^100}")
    print("-" * 100)
    print(f"{'Week':<6} {'WB Success':>12} {'BB Success':>12} {'Both Met':>12} {'Avg WB Tot':>12} {'Avg BB Tot':>12} {'Avg WB Carry':>14} {'Avg BB Carry':>14}")
    print("-" * 100)
    
    for week in range(1, 14):
        avg_wb = statistics.mean(week_totals[week]['wb'])
        avg_bb = statistics.mean(week_totals[week]['bb'])
        avg_wb_carry = statistics.mean(week_totals[week]['wb_carry'])
        avg_bb_carry = statistics.mean(week_totals[week]['bb_carry'])
        
        if week == 1:
            print(f"{week:>4}   {'(ramp-up)':>12} {'(ramp-up)':>12} {'(ramp-up)':>12} {avg_wb:>11,.0f} {avg_bb:>11,.0f} {avg_wb_carry:>13,.0f} {avg_bb_carry:>13,.0f}")
        else:
            wb_pct = 100 * week_success[week]['wb'] / NUM_RUNS
            bb_pct = 100 * week_success[week]['bb'] / NUM_RUNS
            both_pct = 100 * week_success[week]['both'] / NUM_RUNS
            print(f"{week:>4}   {wb_pct:>11.1f}% {bb_pct:>11.1f}% {both_pct:>11.1f}% {avg_wb:>11,.0f} {avg_bb:>11,.0f} {avg_wb_carry:>13,.0f} {avg_bb_carry:>13,.0f}")
    
    # Production stats
    print(f"\n{'PRODUCTION STATISTICS':^100}")
    print("-" * 100)
    
    total_wb_list = [r['total_wb'] for r in all_results]
    total_bb_list = [r['total_bb'] for r in all_results]
    batch_counts = [r['batches'] for r in all_results]
    pause_counts = [r['pause_count'] for r in all_results]
    
    print(f"Total WB (13 weeks):  avg={statistics.mean(total_wb_list):>10,.0f}  min={min(total_wb_list):>10,}  max={max(total_wb_list):>10,}")
    print(f"Total BB (13 weeks):  avg={statistics.mean(total_bb_list):>10,.0f}  min={min(total_bb_list):>10,}  max={max(total_bb_list):>10,}")
    print(f"Batches per run:      avg={statistics.mean(batch_counts):>10.1f}  min={min(batch_counts):>10}  max={max(batch_counts):>10}")
    print(f"Cut pauses per run:   avg={statistics.mean(pause_counts):>10.1f}  min={min(pause_counts):>10}  max={max(pause_counts):>10}")
    
    # Worker utilization (calculate from worker_log)
    print(f"\n{'WORKER UTILIZATION':^100}")
    print("-" * 100)
    
    # Estimate: each batch needs Form (6h) + Cut (9h) = 15h of worker time
    avg_batches = statistics.mean(batch_counts)
    est_work_hours = avg_batches * 15  # Form + Cut per batch
    total_hours = 168 * 13
    utilization = 100 * est_work_hours / total_hours
    idle_hours = total_hours - est_work_hours
    
    print(f"Total available time:     {total_hours:>8} hours (13 weeks × 168 hours)")
    print(f"Estimated work time:      {est_work_hours:>8.0f} hours ({avg_batches:.0f} batches × 15h each)")
    print(f"Estimated idle time:      {idle_hours:>8.0f} hours")
    print(f"Worker utilization:       {utilization:>8.1f}%")
    print(f"Avg idle per week:        {idle_hours/13:>8.1f} hours ({100-utilization:.1f}%)")
    
    # Failure analysis
    print(f"\n{'FAILURE ANALYSIS':^100}")
    print("-" * 100)
    
    total_wb_failures = sum(NUM_RUNS - week_success[w]['wb'] for w in range(2, 14))
    total_bb_failures = sum(NUM_RUNS - week_success[w]['bb'] for w in range(2, 14))
    total_week_runs = NUM_RUNS * 12
    
    print(f"WB week-failures: {total_wb_failures:>4} / {total_week_runs} ({100*total_wb_failures/total_week_runs:.2f}%)")
    print(f"BB week-failures: {total_bb_failures:>4} / {total_week_runs} ({100*total_bb_failures/total_week_runs:.2f}%)")
    
    # Shortfall details
    wb_shortfalls = []
    bb_shortfalls = []
    
    for week in range(2, 14):
        for i in range(NUM_RUNS):
            if week_totals[week]['wb'][i] < 20000:
                wb_shortfalls.append(20000 - week_totals[week]['wb'][i])
            if week_totals[week]['bb'][i] < 20000:
                bb_shortfalls.append(20000 - week_totals[week]['bb'][i])
    
    if wb_shortfalls:
        print(f"\nWB shortfalls when missed: avg={statistics.mean(wb_shortfalls):,.0f}, min={min(wb_shortfalls):,}, max={max(wb_shortfalls):,}")
    else:
        print(f"\nWB: NO FAILURES - all weeks met target in all runs!")
    
    if bb_shortfalls:
        print(f"BB shortfalls when missed: avg={statistics.mean(bb_shortfalls):,.0f}, min={min(bb_shortfalls):,}, max={max(bb_shortfalls):,}")
    else:
        print(f"BB: NO FAILURES - all weeks met target in all runs!")
    
    # Worst weeks
    if total_wb_failures > 0 or total_bb_failures > 0:
        print(f"\nFailures by week:")
        for week in range(2, 14):
            wb_fail = NUM_RUNS - week_success[week]['wb']
            bb_fail = NUM_RUNS - week_success[week]['bb']
            if wb_fail > 0 or bb_fail > 0:
                print(f"  Week {week}: WB fails={wb_fail}, BB fails={bb_fail}")
    
    print("\n" + "=" * 100)
    print("SIMULATION COMPLETE")
    print("=" * 100)


if __name__ == "__main__":
    main()