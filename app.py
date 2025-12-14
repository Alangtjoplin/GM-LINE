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


if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)