"""
Production Line Simulator API
Backend for Flutter web frontend
Deploy to Railway.app
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import random

app = Flask(__name__)
CORS(app)  # Enable CORS for Flutter web frontend

# =============================================================================
# SIMULATION CODE
# =============================================================================

class Batch:
    def __init__(self, id, product):
        self.id = id
        self.product = product
        self.form_start = None
        self.form_end = None
        self.cook_start = None
        self.cook_end = None
        self.cure_time = 0
        self.cure_start = None
        self.cure_end = None
        self.cut_start = None
        self.cut_end = None
        self.cut_progress = 0
        self.formed_by = None
        self.cut_by = None
        self.cut_sessions = []
        self.current_cut_session_start = None
        self.current_cut_team = None


class ProductionSimulator:
    def __init__(self, config, collect_gantt_data=False):
        self.config = config
        
        # Extract config values with defaults
        num_ovens = config.get('num_ovens', 5)
        scale = num_ovens / 5
        
        self.FORM_TIME = config.get('form_time', 6) * scale
        self.CUT_TIME = config.get('cut_time', 8) * scale
        self.WB_PER_BATCH = int(config.get('wb_per_batch', 3000) * scale)
        self.BB_PER_BATCH = int(config.get('bb_per_batch', 6000) * scale)
        
        self.COOK_TIME = config.get('cook_time', 10)
        self.CURE_WB_MIN = config.get('cure_wb_min', 24)
        self.CURE_WB_MAX = config.get('cure_wb_max', 36)
        
        self.WB_SHEETS = config.get('wb_sheets', 3)
        self.BB_SHEETS = config.get('bb_sheets', 2)
        
        self.WB_TARGET = config.get('wb_target', 1500000)
        self.BB_TARGET = config.get('bb_target', 2500000)
        self.TOTAL_TARGET = self.WB_TARGET + self.BB_TARGET
        
        self.WB_RATIO = self.WB_TARGET / self.TOTAL_TARGET if self.TOTAL_TARGET > 0 else 0.5
        self.BB_RATIO = self.BB_TARGET / self.TOTAL_TARGET if self.TOTAL_TARGET > 0 else 0.5
        
        self.WEEK_HOURS = 168
        self.NUM_WEEKS = config.get('num_weeks', 52)
        self.TOTAL_HOURS = self.WEEK_HOURS * self.NUM_WEEKS
        
        self.TEAM_CONFIG = config.get('team_config', '1team')
        self.NUM_OVEN_SETS = config.get('num_oven_sets', 1)
        self.TEAM2_START = config.get('team2_start', 6)
        self.TEAM2_END = config.get('team2_end', 18)
        
        self.PRIORITY_STRATEGY = config.get('priority_strategy', 'ratio_batches')
        
        self.collect_gantt_data = collect_gantt_data
        self.all_batches = []
    
    def simulate(self):
        time = 0.0
        batch_id = 0
        batches = []
        all_batches = []
        total_wb = 0
        total_bb = 0
        wb_batches_formed = 0
        bb_batches_formed = 0
        
        team1_free = 0.0
        team2_free = 0.0
        oven1_free = 0.0
        oven2_free = 0.0
        
        def team2_enabled():
            return self.TEAM_CONFIG != '1team'
        
        def team2_on(t):
            if self.TEAM_CONFIG == '2team_24/7':
                return True
            h = t % 24
            return self.TEAM2_START <= h < self.TEAM2_END
        
        def next_team2_start(t):
            if self.TEAM_CONFIG == '2team_24/7':
                return t
            h = t % 24
            if h < self.TEAM2_START:
                return t + (self.TEAM2_START - h)
            elif h >= self.TEAM2_END:
                return t + (24 - h) + self.TEAM2_START
            return t
        
        def team2_shift_end(t):
            if self.TEAM_CONFIG == '2team_24/7':
                return float('inf')
            h = t % 24
            if self.TEAM2_START <= h < self.TEAM2_END:
                return t + (self.TEAM2_END - h)
            return t
        
        def active_wb():
            return len([b for b in batches if b.product == 'WB' and (b.cut_end is None or b.cut_end > time)])
        
        def active_bb():
            return len([b for b in batches if b.product == 'BB' and (b.cut_end is None or b.cut_end > time)])
        
        def curing_wb():
            return len([b for b in batches if b.product == 'WB' 
                       and b.cure_end > time and b.cut_end is None])
        
        def ready_to_cut(exclude, team_num=None):
            ready = [b for b in batches 
                    if b.cure_end <= time and b.cut_end is None 
                    and b.id not in exclude]
            def sort_key(b):
                if b.cut_progress > 0:
                    if b.cut_by == team_num:
                        return (0, b.cure_end)
                    else:
                        return (1, b.cure_end)
                return (2, b.cure_end)
            return sorted(ready, key=sort_key)
        
        def get_priority():
            nonlocal total_wb, total_bb, wb_batches_formed, bb_batches_formed
            
            if self.PRIORITY_STRATEGY == 'ratio':
                total = total_wb + total_bb
                if total == 0:
                    return True
                return (total_wb / total) < self.WB_RATIO
            elif self.PRIORITY_STRATEGY == 'ratio_batches':
                wb_needed = max(0, (self.WB_TARGET - total_wb) / self.WB_PER_BATCH)
                bb_needed = max(0, (self.BB_TARGET - total_bb) / self.BB_PER_BATCH)
                return wb_needed >= bb_needed
            elif self.PRIORITY_STRATEGY == 'wb_first':
                return True
            elif self.PRIORITY_STRATEGY == 'bb_first':
                return False
            elif self.PRIORITY_STRATEGY == 'adaptive':
                wb_progress = total_wb / self.WB_TARGET if self.WB_TARGET > 0 else 1
                bb_progress = total_bb / self.BB_TARGET if self.BB_TARGET > 0 else 1
                return wb_progress < bb_progress
            elif self.PRIORITY_STRATEGY == 'cure_aware':
                pending_wb = curing_wb() * self.WB_PER_BATCH
                effective_wb = total_wb + pending_wb
                wb_needed = max(0, (self.WB_TARGET - effective_wb) / self.WB_PER_BATCH)
                bb_needed = max(0, (self.BB_TARGET - total_bb) / self.BB_PER_BATCH)
                return wb_needed >= bb_needed
            elif self.PRIORITY_STRATEGY == 'goal_focused':
                wb_pct = total_wb / self.WB_TARGET if self.WB_TARGET > 0 else 1
                bb_pct = total_bb / self.BB_TARGET if self.BB_TARGET > 0 else 1
                if wb_pct >= 1 and bb_pct >= 1:
                    return True
                return wb_pct < bb_pct
            elif self.PRIORITY_STRATEGY == 'wb_until_done':
                if total_wb < self.WB_TARGET:
                    return True
                return False
            elif self.PRIORITY_STRATEGY == 'balanced_goal':
                pending_wb = curing_wb() * self.WB_PER_BATCH
                effective_wb = total_wb + pending_wb
                wb_pct = effective_wb / self.WB_TARGET if self.WB_TARGET > 0 else 1
                bb_pct = total_bb / self.BB_TARGET if self.BB_TARGET > 0 else 1
                if wb_pct >= 1 and bb_pct >= 1:
                    return False
                return wb_pct < bb_pct
            return True
        
        def form(product, oven_num, team_num):
            nonlocal batch_id, oven1_free, oven2_free, wb_batches_formed, bb_batches_formed
            b = Batch(batch_id, product)
            batch_id += 1
            
            b.form_start = time
            b.form_end = time + self.FORM_TIME
            b.formed_by = team_num
            
            b.cook_start = b.form_end
            b.cook_end = b.cook_start + self.COOK_TIME
            
            if product == 'WB':
                b.cure_time = random.uniform(self.CURE_WB_MIN, self.CURE_WB_MAX)
                wb_batches_formed += 1
            else:
                b.cure_time = 0
                bb_batches_formed += 1
            
            b.cure_start = b.cook_end
            b.cure_end = b.cure_start + b.cure_time
            
            batches.append(b)
            if self.collect_gantt_data:
                all_batches.append(b)
            
            if oven_num == 1:
                oven1_free = b.cook_end
            else:
                oven2_free = b.cook_end
            
            return b.form_end
        
        def cut(batch, work_time, team_num, is_partial=False):
            nonlocal total_wb, total_bb
            
            if batch.cut_start is None:
                batch.cut_start = time
            
            if batch.current_cut_session_start is None:
                batch.current_cut_session_start = time
                batch.current_cut_team = team_num
            
            batch.cut_progress += work_time
            session_end = time + work_time
            
            if batch.cut_progress >= self.CUT_TIME - 0.01:
                batch.cut_end = session_end
                if batch.product == 'WB':
                    total_wb += self.WB_PER_BATCH
                else:
                    total_bb += self.BB_PER_BATCH
                batch.cut_sessions.append((batch.current_cut_session_start, session_end, batch.current_cut_team))
                batch.current_cut_session_start = None
                batch.current_cut_team = None
            elif is_partial:
                batch.cut_sessions.append((batch.current_cut_session_start, session_end, batch.current_cut_team))
                batch.current_cut_session_start = None
                batch.current_cut_team = None
        
        being_cut = set()
        sheets_claimed_wb = 0
        sheets_claimed_bb = 0
        
        def do_work(oven_num, deadline, shift_end=float('inf'), is_team2=False):
            nonlocal being_cut, sheets_claimed_wb, sheets_claimed_bb
            
            team_num = 2 if is_team2 else 1
            wb_priority = get_priority()
            can_form = time >= deadline
            if shift_end != float('inf'):
                can_form = can_form and (shift_end - time) >= self.FORM_TIME
            
            get_ready = lambda: ready_to_cut(being_cut, team_num)
            
            available_wb = self.WB_SHEETS - active_wb() - sheets_claimed_wb
            available_bb = self.BB_SHEETS - active_bb() - sheets_claimed_bb
            
            # Finish cuts with < 1 hour remaining (own cuts only)
            ready = get_ready()
            almost_done = [b for b in ready if (self.CUT_TIME - b.cut_progress) < 1.0 
                          and b.cut_progress > 0 and b.cut_by == team_num]
            if almost_done:
                b = almost_done[0]
                being_cut.add(b.id)
                remaining = self.CUT_TIME - b.cut_progress
                if shift_end != float('inf') and time + remaining > shift_end:
                    work = shift_end - time
                    if work > 0:
                        cut(b, work, team_num, is_partial=True)
                        return (next_team2_start(shift_end), None)
                    return time
                cut(b, remaining, team_num, is_partial=False)
                return (time + remaining, None)
            
            if can_form:
                if wb_priority:
                    if available_wb > 0:
                        sheets_claimed_wb += 1
                        return form('WB', oven_num, team_num)
                    elif available_bb > 0:
                        sheets_claimed_bb += 1
                        return form('BB', oven_num, team_num)
                else:
                    if available_bb > 0:
                        sheets_claimed_bb += 1
                        return form('BB', oven_num, team_num)
                    elif available_wb > 0:
                        sheets_claimed_wb += 1
                        return form('WB', oven_num, team_num)
                
                ready = get_ready()
                if ready:
                    b = ready[0]
                    being_cut.add(b.id)
                    if b.cut_by is None:
                        b.cut_by = team_num
                    remaining = self.CUT_TIME - b.cut_progress
                    cut(b, remaining, team_num, is_partial=False)
                    return (time + remaining, b.id)
            else:
                ready = get_ready()
                if ready:
                    b = ready[0]
                    window = min(deadline - time, shift_end - time)
                    
                    # Don't start NEW cut if window < 1 hour
                    if window < 1.0 and b.cut_progress == 0:
                        return time
                    
                    if window > 0:
                        being_cut.add(b.id)
                        if b.cut_by is None:
                            b.cut_by = team_num
                        remaining = self.CUT_TIME - b.cut_progress
                        work = min(window, remaining)
                        is_partial = (work < remaining)
                        cut(b, work, team_num, is_partial=is_partial)
                        new_free = time + work
                        if shift_end != float('inf') and new_free >= shift_end:
                            if is_partial:
                                return (next_team2_start(shift_end), None)
                            return (next_team2_start(shift_end), None)
                        if is_partial:
                            return (new_free, b.id)
                        return (new_free, None)
            return time
        
        # Main simulation loop
        while time < self.TOTAL_HOURS:
            batches = [b for b in batches if b.cut_end is None or b.cut_end > time]
            sheets_claimed_wb = 0
            sheets_claimed_bb = 0
            
            being_cut = set()
            for b in batches:
                if b.cut_start is not None and b.cut_end is None and b.cut_progress < self.CUT_TIME:
                    if b.cut_sessions:
                        last_session = b.cut_sessions[-1]
                        if last_session[1] > time:
                            being_cut.add(b.id)
            
            if team1_free <= time:
                deadline1 = oven1_free - self.FORM_TIME
                result = do_work(1, deadline1, is_team2=False)
                if isinstance(result, tuple):
                    team1_free = result[0]
                    if result[1] is not None:
                        being_cut.add(result[1])
                else:
                    team1_free = result
            
            if team2_enabled():
                if not team2_on(time):
                    team2_free = next_team2_start(time)
                elif team2_free <= time:
                    if self.NUM_OVEN_SETS == 2:
                        deadline2 = oven2_free - self.FORM_TIME
                        oven_num = 2
                    else:
                        deadline2 = oven1_free - self.FORM_TIME
                        oven_num = 1
                    shift_end = team2_shift_end(time)
                    result = do_work(oven_num, deadline2, shift_end, is_team2=True)
                    if isinstance(result, tuple):
                        team2_free = result[0]
                    else:
                        team2_free = result
            
            events = [self.TOTAL_HOURS, team1_free, oven1_free, oven1_free - self.FORM_TIME]
            if self.NUM_OVEN_SETS == 2:
                events.extend([oven2_free, oven2_free - self.FORM_TIME])
            if team2_enabled():
                events.append(team2_free)
                if self.TEAM_CONFIG == '2team_6-6':
                    events.append(team2_shift_end(time) if team2_on(time) else next_team2_start(time))
            for b in batches:
                if b.cure_end > time and b.cut_end is None:
                    events.append(b.cure_end)
            
            next_t = min(e for e in events if e > time)
            time = next_t if next_t > time else time + 0.1
        
        if self.collect_gantt_data:
            self.all_batches = all_batches
        
        wb_pct = 100 * total_wb / self.WB_TARGET if self.WB_TARGET > 0 else 0
        bb_pct = 100 * total_bb / self.BB_TARGET if self.BB_TARGET > 0 else 0
        
        return {
            'total_wb': total_wb,
            'total_bb': total_bb,
            'total': total_wb + total_bb,
            'wb_pct': wb_pct,
            'bb_pct': bb_pct,
            'wb_batches': wb_batches_formed,
            'bb_batches': bb_batches_formed
        }


def run_monte_carlo(config, runs=50):
    results = []
    for _ in range(runs):
        sim = ProductionSimulator(config)
        results.append(sim.simulate())
    
    return {
        'avg_wb': sum(r['total_wb'] for r in results) / runs,
        'avg_bb': sum(r['total_bb'] for r in results) / runs,
        'avg_total': sum(r['total'] for r in results) / runs,
        'avg_wb_pct': sum(r['wb_pct'] for r in results) / runs,
        'avg_bb_pct': sum(r['bb_pct'] for r in results) / runs,
        'min_total': min(r['total'] for r in results),
        'max_total': max(r['total'] for r in results),
    }


# =============================================================================
# API ENDPOINTS
# =============================================================================

@app.route('/')
def home():
    return jsonify({
        'name': 'Production Line Simulator API',
        'version': '1.0.0',
        'endpoints': {
            '/simulate': 'POST - Run single simulation',
            '/test-strategies': 'POST - Test all strategies',
            '/gantt-data': 'POST - Get Gantt chart data',
        }
    })


@app.route('/simulate', methods=['POST'])
def simulate():
    """Run a single simulation with given config"""
    config = request.json or {}
    
    try:
        sim = ProductionSimulator(config)
        result = sim.simulate()
        
        return jsonify({
            'success': True,
            'result': result,
            'config': {
                'wb_target': sim.WB_TARGET,
                'bb_target': sim.BB_TARGET,
                'wb_ratio': sim.WB_RATIO,
                'bb_ratio': sim.BB_RATIO,
                'team_config': sim.TEAM_CONFIG,
                'strategy': sim.PRIORITY_STRATEGY,
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/test-strategies', methods=['POST'])
def test_strategies():
    """Test all strategies and return comparison"""
    config = request.json or {}
    
    strategies = ['ratio', 'ratio_batches', 'wb_first', 'bb_first', 'adaptive', 
                  'cure_aware', 'goal_focused', 'wb_until_done', 'balanced_goal']
    
    results = []
    
    for strategy in strategies:
        test_config = {**config, 'priority_strategy': strategy}
        mc = run_monte_carlo(test_config, runs=20)  # Fewer runs for speed
        
        wb_pct = mc['avg_wb_pct']
        bb_pct = mc['avg_bb_pct']
        min_pct = min(wb_pct, bb_pct)
        
        # Score: prioritize meeting both goals
        score = min_pct + (wb_pct + bb_pct) / 10
        
        results.append({
            'strategy': strategy,
            'avg_wb': mc['avg_wb'],
            'avg_bb': mc['avg_bb'],
            'wb_pct': wb_pct,
            'bb_pct': bb_pct,
            'min_pct': min_pct,
            'score': score
        })
    
    # Sort by score
    results.sort(key=lambda x: x['score'], reverse=True)
    best = results[0]['strategy']
    
    return jsonify({
        'success': True,
        'results': results,
        'recommendation': best,
        'config': {
            'wb_target': config.get('wb_target', 1500000),
            'bb_target': config.get('bb_target', 2500000),
        }
    })


@app.route('/gantt-data', methods=['POST'])
def gantt_data():
    """Get batch data for Gantt chart visualization"""
    config = request.json or {}
    
    try:
        sim = ProductionSimulator(config, collect_gantt_data=True)
        result = sim.simulate()
        
        # Convert batches to JSON-serializable format
        batches_data = []
        for b in sim.all_batches:
            batches_data.append({
                'id': b.id,
                'product': b.product,
                'form_start': b.form_start,
                'form_end': b.form_end,
                'formed_by': b.formed_by,
                'cook_start': b.cook_start,
                'cook_end': b.cook_end,
                'cure_start': b.cure_start,
                'cure_end': b.cure_end,
                'cure_time': b.cure_time,
                'cut_start': b.cut_start,
                'cut_end': b.cut_end,
                'cut_by': b.cut_by,
                'cut_sessions': b.cut_sessions,
            })
        
        # Calculate wait times
        wait_times = []
        for b in sim.all_batches:
            if b.cure_end is not None and b.cut_end is not None:
                wait = b.cut_end - b.cure_end
                wait_times.append({
                    'batch': f'{b.product}{b.id}',
                    'product': b.product,
                    'cure_end': b.cure_end,
                    'cut_end': b.cut_end,
                    'wait': wait
                })
        
        # Sort by wait time
        wait_times.sort(key=lambda x: x['wait'], reverse=True)
        
        # Stats
        if wait_times:
            all_waits = [w['wait'] for w in wait_times]
            wb_waits = [w['wait'] for w in wait_times if w['product'] == 'WB']
            bb_waits = [w['wait'] for w in wait_times if w['product'] == 'BB']
            
            wait_stats = {
                'max': max(all_waits),
                'avg': sum(all_waits) / len(all_waits),
                'min': min(all_waits),
                'wb_max': max(wb_waits) if wb_waits else 0,
                'wb_avg': sum(wb_waits) / len(wb_waits) if wb_waits else 0,
                'bb_max': max(bb_waits) if bb_waits else 0,
                'bb_avg': sum(bb_waits) / len(bb_waits) if bb_waits else 0,
                'top_10': wait_times[:10]
            }
        else:
            wait_stats = {}
        
        return jsonify({
            'success': True,
            'result': result,
            'batches': batches_data,
            'wait_stats': wait_stats,
            'config': {
                'total_hours': sim.TOTAL_HOURS,
                'team_config': sim.TEAM_CONFIG,
                'strategy': sim.PRIORITY_STRATEGY,
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/health')
def health():
    """Health check endpoint for Railway"""
    return jsonify({'status': 'healthy'})


@app.route('/gantt-image', methods=['POST'])
def gantt_image():
    """Generate Gantt chart image and return as base64"""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import base64
    from io import BytesIO
    
    config = request.json or {}
    week = config.get('week', 1)
    chart_type = config.get('chart_type', 'resources')  # 'resources' or 'workers'
    seed = config.get('seed', 42)  # Use consistent seed for reproducible results
    
    try:
        # Set random seed for consistent results across weeks
        random.seed(seed)
        
        sim = ProductionSimulator(config, collect_gantt_data=True)
        result = sim.simulate()
        batches = sim.all_batches
        
        # Calculate hours for this week
        start_hour = (week - 1) * 168
        end_hour = week * 168
        total_weeks = sim.NUM_WEEKS
        
        # Filter relevant batches
        relevant_batches = [b for b in batches if b.form_start < end_hour and 
                          (b.cut_end is None or b.cut_end > start_hour or b.cure_end > start_hour)]
        
        if not relevant_batches:
            return jsonify({'success': False, 'error': f'No batches in week {week}'})
        
        # Determine configuration
        has_team2 = sim.TEAM_CONFIG != '1team'
        has_oven2 = sim.NUM_OVEN_SETS == 2
        
        # Colors
        colors = {
            'form_wb': '#87CEEB',
            'form_bb': '#4169E1',
            'cook_wb': '#FFA500',
            'cook_bb': '#FF8C00',
            'cure_wb': '#90EE90',
            'cut_wb': '#32CD32',
            'cut_bb': '#228B22',
        }
        
        if chart_type == 'resources':
            # Build row configuration
            rows = []
            if has_team2:
                rows.append(('Form (Team 1)', 'form', 1))
                rows.append(('Form (Team 2)', 'form', 2))
            else:
                rows.append(('Form', 'form', None))
            
            if has_oven2:
                rows.append(('Cook (Oven Set 1)', 'cook', 1))
                rows.append(('Cook (Oven Set 2)', 'cook', 2))
            else:
                rows.append(('Cook', 'cook', None))
            
            rows.append(('Cure (stacked)', 'cure', None))
            
            if has_team2:
                rows.append(('Cut (Team 1)', 'cut', 1))
                rows.append(('Cut (Team 2)', 'cut', 2))
            else:
                rows.append(('Cut', 'cut', None))
            
            fig, ax = plt.subplots(figsize=(20, len(rows) * 0.8 + 2))
            
            y_labels = [r[0] for r in rows]
            y_positions = list(range(len(rows) - 1, -1, -1))
            
            for b in relevant_batches:
                product = b.product
                
                # Form
                if b.form_start is not None and b.form_start < end_hour and b.form_end > start_hour:
                    form_team = b.formed_by or 1
                    for i, (label, stage, team_filter) in enumerate(rows):
                        if stage == 'form':
                            if team_filter is None or team_filter == form_team:
                                y = y_positions[i]
                                color = colors['form_wb'] if product == 'WB' else colors['form_bb']
                                s = max(b.form_start, start_hour)
                                e = min(b.form_end, end_hour)
                                ax.barh(y, e - s, left=s, height=0.6, color=color, edgecolor='black', linewidth=0.5)
                                if e - s > 3:
                                    ax.text((s + e) / 2, y, f'{product}{b.id}', ha='center', va='center', fontsize=7)
                
                # Cook
                if b.cook_start is not None and b.cook_start < end_hour and b.cook_end > start_hour:
                    oven_set = getattr(b, 'oven_set', 1)
                    for i, (label, stage, team_filter) in enumerate(rows):
                        if stage == 'cook':
                            if team_filter is None or team_filter == oven_set:
                                y = y_positions[i]
                                color = colors['cook_wb'] if product == 'WB' else colors['cook_bb']
                                s = max(b.cook_start, start_hour)
                                e = min(b.cook_end, end_hour)
                                ax.barh(y, e - s, left=s, height=0.6, color=color, edgecolor='black', linewidth=0.5)
                                ax.text((s + e) / 2, y, f'{product}{b.id}', ha='center', va='center', fontsize=7)
                
                # Cure (WB only)
                if product == 'WB' and b.cure_start is not None and b.cure_end is not None:
                    if b.cure_start < end_hour and b.cure_end > start_hour:
                        for i, (label, stage, team_filter) in enumerate(rows):
                            if stage == 'cure':
                                y = y_positions[i]
                                s = max(b.cure_start, start_hour)
                                e = min(b.cure_end, end_hour)
                                offset = (b.id % 3) * 0.25 - 0.25
                                ax.barh(y + offset, e - s, left=s, height=0.25, color=colors['cure_wb'], 
                                       edgecolor='black', linewidth=0.5, alpha=0.7 + (b.id % 3) * 0.1)
                                if e - s > 5:
                                    ax.text((s + e) / 2, y + offset, f'{product}{b.id}', ha='center', va='center', fontsize=6)
                
                # Cut
                if b.cut_sessions:
                    for i, (label, stage, team_filter) in enumerate(rows):
                        if stage == 'cut':
                            y = y_positions[i]
                            
                            # Merge sessions
                            merged = []
                            for sess in b.cut_sessions:
                                session_start, session_end, team_num = sess
                                if team_filter is not None and team_num != team_filter:
                                    continue
                                if session_start >= end_hour or session_end <= start_hour:
                                    continue
                                if merged and abs(merged[-1][1] - session_start) < 0.1 and merged[-1][2] == team_num:
                                    merged[-1] = (merged[-1][0], session_end, team_num)
                                else:
                                    merged.append((session_start, session_end, team_num))
                            
                            if not merged:
                                continue
                            
                            is_paused = len(merged) > 1
                            color = colors['cut_wb'] if product == 'WB' else colors['cut_bb']
                            
                            for sess in merged:
                                s = max(sess[0], start_hour)
                                e = min(sess[1], end_hour)
                                if is_paused:
                                    ax.barh(y, e - s, left=s, height=0.6, color=color, edgecolor='black', 
                                           linewidth=0.5, hatch='///', alpha=0.8)
                                else:
                                    ax.barh(y, e - s, left=s, height=0.6, color=color, edgecolor='black', linewidth=0.5)
                                
                                bar_width = e - s
                                fontsize = 8 if bar_width > 5 else (6 if bar_width > 2 else 5)
                                ax.text((s + e) / 2, y, f'{product}{b.id}', ha='center', va='center', 
                                       fontsize=fontsize, color='white')
            
            ax.set_yticks(y_positions)
            ax.set_yticklabels(y_labels)
            ax.set_xlim(start_hour, end_hour)
            ax.set_xlabel('Hours')
            
            # Day markers
            for day in range(int(start_hour // 24), int(end_hour // 24) + 1):
                day_hour = day * 24
                if start_hour <= day_hour <= end_hour:
                    color = 'red' if day % 7 == 0 else 'blue'
                    style = '--' if day % 7 == 0 else ':'
                    ax.axvline(x=day_hour, color=color, linestyle=style, alpha=0.5)
            
            title = f'Production Flow - Week {week} (Hours {start_hour}-{end_hour})'
            title += f'\n{sim.TEAM_CONFIG}, {sim.config.get("num_ovens", 5)} ovens, Strategy: {sim.PRIORITY_STRATEGY}'
            ax.set_title(title, fontsize=12, fontweight='bold')
            
            # Legend
            legend_elements = [
                mpatches.Patch(color=colors['form_wb'], label='Form WB'),
                mpatches.Patch(color=colors['form_bb'], label='Form BB'),
                mpatches.Patch(color=colors['cook_wb'], label='Cook WB'),
                mpatches.Patch(color=colors['cook_bb'], label='Cook BB'),
                mpatches.Patch(color=colors['cure_wb'], label='Cure WB'),
                mpatches.Patch(color=colors['cut_wb'], label='Cut WB'),
                mpatches.Patch(color=colors['cut_bb'], label='Cut BB'),
                mpatches.Patch(facecolor=colors['cut_wb'], hatch='///', label='Paused Cut'),
            ]
            ax.legend(handles=legend_elements, loc='upper right', fontsize=8)
            ax.grid(axis='x', alpha=0.3)
            
        else:  # workers chart
            if has_team2:
                rows = [('Team 1', 1), ('Team 2', 2)]
            else:
                rows = [('Team 1', 1)]
            
            fig, ax = plt.subplots(figsize=(20, len(rows) * 1.5 + 2))
            
            y_labels = [r[0] for r in rows]
            y_positions = list(range(len(rows) - 1, -1, -1))
            
            for b in relevant_batches:
                product = b.product
                
                # Form
                if b.form_start is not None and b.form_start < end_hour and b.form_end > start_hour:
                    form_team = b.formed_by or 1
                    for i, (label, team_num) in enumerate(rows):
                        if team_num == form_team:
                            y = y_positions[i]
                            color = colors['form_wb'] if product == 'WB' else colors['form_bb']
                            s = max(b.form_start, start_hour)
                            e = min(b.form_end, end_hour)
                            ax.barh(y + 0.2, e - s, left=s, height=0.35, color=color, edgecolor='black', linewidth=0.5)
                            if e - s > 3:
                                ax.text((s + e) / 2, y + 0.2, f'{product}{b.id}', ha='center', va='center', fontsize=6)
                
                # Cut
                if b.cut_sessions:
                    for i, (label, team_num) in enumerate(rows):
                        y = y_positions[i]
                        
                        team_sessions = [(s, e, t) for s, e, t in b.cut_sessions if t == team_num]
                        if not team_sessions:
                            continue
                        
                        merged = []
                        for sess in team_sessions:
                            session_start, session_end, tn = sess
                            if session_start >= end_hour or session_end <= start_hour:
                                continue
                            if merged and abs(merged[-1][1] - session_start) < 0.1:
                                merged[-1] = (merged[-1][0], session_end, tn)
                            else:
                                merged.append((session_start, session_end, tn))
                        
                        if not merged:
                            continue
                        
                        is_paused = len(merged) > 1
                        color = colors['cut_wb'] if product == 'WB' else colors['cut_bb']
                        
                        for sess in merged:
                            s = max(sess[0], start_hour)
                            e = min(sess[1], end_hour)
                            if is_paused:
                                ax.barh(y - 0.2, e - s, left=s, height=0.35, color=color, edgecolor='black',
                                       linewidth=0.5, hatch='///', alpha=0.8)
                            else:
                                ax.barh(y - 0.2, e - s, left=s, height=0.35, color=color, edgecolor='black', linewidth=0.5)
                            
                            bar_width = e - s
                            fontsize = 7 if bar_width > 5 else 5
                            ax.text((s + e) / 2, y - 0.2, f'{product}{b.id}', ha='center', va='center',
                                   fontsize=fontsize, color='white')
            
            ax.set_yticks(y_positions)
            ax.set_yticklabels(y_labels)
            ax.set_xlim(start_hour, end_hour)
            ax.set_xlabel('Hours')
            
            title = f'Worker Activity - Week {week} (Hours {start_hour}-{end_hour})'
            title += f'\n{sim.TEAM_CONFIG}, Strategy: {sim.PRIORITY_STRATEGY}'
            ax.set_title(title, fontsize=12, fontweight='bold')
            
            legend_elements = [
                mpatches.Patch(color=colors['form_wb'], label='Form WB'),
                mpatches.Patch(color=colors['form_bb'], label='Form BB'),
                mpatches.Patch(color=colors['cut_wb'], label='Cut WB'),
                mpatches.Patch(color=colors['cut_bb'], label='Cut BB'),
                mpatches.Patch(facecolor=colors['cut_wb'], hatch='///', label='Paused Cut'),
            ]
            ax.legend(handles=legend_elements, loc='upper right', fontsize=8)
            ax.grid(axis='x', alpha=0.3)
        
        plt.tight_layout()
        
        # Save to base64
        buffer = BytesIO()
        plt.savefig(buffer, format='png', dpi=120, bbox_inches='tight', facecolor='white')
        buffer.seek(0)
        image_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
        plt.close()
        
        # Calculate weekly production
        weekly_production = []
        for w in range(1, total_weeks + 1):
            week_start = (w - 1) * 168
            week_end = w * 168
            wb_produced = 0
            bb_produced = 0
            for b in batches:
                if b.cut_end is not None and week_start <= b.cut_end < week_end:
                    if b.product == 'WB':
                        wb_produced += sim.WB_PER_BATCH
                    else:
                        bb_produced += sim.BB_PER_BATCH
            weekly_production.append({
                'week': w,
                'wb': wb_produced,
                'bb': bb_produced
            })
        
        return jsonify({
            'success': True,
            'image': image_base64,
            'week': week,
            'total_weeks': total_weeks,
            'chart_type': chart_type,
            'result': result,
            'weekly_production': weekly_production
        })
        
    except Exception as e:
        import traceback
        return jsonify({'success': False, 'error': str(e), 'trace': traceback.format_exc()}), 400


if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)