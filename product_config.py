"""
PRODUCTION LINE SIMULATOR - CONFIGURABLE WITH GANTT CHARTS
===========================================================
All parameters can be changed at the top of this file.

GOAL: 1.5M WB + 2.5M BB = 4.0M Total

Run: python production_configurable.py
"""

import random
import csv
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Rectangle
import numpy as np

# =============================================================================
# CONFIGURATION - CHANGE THESE PARAMETERS
# =============================================================================

# TEAM CONFIGURATION
# Options: '1team', '2team_6-6', '2team_24/7'
TEAM_CONFIG = '2team_24/7'

# OVEN CONFIGURATION
NUM_OVENS = 5          # Ovens per set (5 or 6)
NUM_OVEN_SETS = 1      # Number of oven sets (1 or 2)

# SHEET CONFIGURATION
WB_SHEETS = 4          # Number of WB sheets
BB_SHEETS = 2          # Number of BB sheets

# TIME PARAMETERS (hours) - Base values for 5 ovens
BASE_FORM_TIME = 6     # Form time for 5 ovens
BASE_CUT_TIME = 8      # Cut time for 5 ovens
COOK_TIME = 10         # Cook time (constant)
CURE_WB_MIN = 24       # WB minimum cure time
CURE_WB_MAX = 36       # WB maximum cure time
CURE_BB = 0            # BB cure time (no cure needed)

# OUTPUT PER BATCH - Base values for 5 ovens
BASE_WB_PER_BATCH = 3000
BASE_BB_PER_BATCH = 6000

# PRODUCTION TARGETS
WB_TARGET = 1_040_000
BB_TARGET = 1_040_000

# TEAM 2 SHIFT HOURS (only used if TEAM_CONFIG = '1team')
TEAM2_START_HOUR = 6   # 6am
TEAM2_END_HOUR = 18    # 6pm

# SIMULATION SETTINGS
NUM_WEEKS = 52
MONTE_CARLO_RUNS = 50

# PRIORITY STRATEGY
# Options:
#   'ratio'          - Maintain WB:BB ratio (original)
#   'ratio_batches'  - Prioritize by batches needed to hit target
#   'wb_first'       - Always prioritize WB (has cure time)
#   'bb_first'       - Always prioritize BB (faster throughput)
#   'adaptive'       - Switch strategy based on progress toward goals
#   'cure_aware'     - Counts curing WB as "pending" output
#   'goal_focused'   - Prioritize whichever is furthest from 100% target
#   'wb_until_done'  - WB first until WB target hit, then BB only
#   'balanced_goal'  - Like goal_focused but accounts for cure time pipeline
PRIORITY_STRATEGY = 'ratio_batches'

# =============================================================================
# GANTT CHART CONFIGURATION
# =============================================================================

# Generate Gantt charts?
GENERATE_GANTT = True

# What type of Gantt chart?
# Options: 'resources' - Shows Form -> Cook -> Cure -> Cut rows (production flow)
#          'workers'   - Shows only what workers are doing (Team 1, Team 2)
#          'both'      - Generate both types
GANTT_TYPE = 'both'

# Chart period - how much time each individual chart covers
# Options: 'week' (168h), '2weeks' (336h), 'month' (672h)
GANTT_PERIOD = 'week'

# Total time frame to generate charts for
# Options: 'week1'   - First week only (1 chart if period=week)
#          '2weeks'  - First 2 weeks (2 charts if period=week, 1 if period=2weeks)
#          'month1'  - First month (~4 charts if period=week)
#          'month3'  - First 3 months (~13 charts if period=week)
#          'year'    - Full year (52 charts if period=week)
GANTT_TIMEFRAME = 'month3'

# =============================================================================
# END OF CONFIGURATION
# =============================================================================


class Batch:
    def __init__(self, id, product, form_start, form_time, cook_time, cure_time):
        self.id = id
        self.product = product
        self.form_start = form_start
        self.form_end = form_start + form_time
        self.cook_start = self.form_end
        self.cook_end = self.form_end + cook_time
        self.cure_start = self.cook_end
        self.cure_end = self.cook_end + cure_time
        self.cure_time = cure_time
        self.cut_start = None
        self.cut_end = None
        self.cut_progress = 0
        self.formed_by = None
        self.cut_by = None
        self.cut_sessions = []  # List of (start, end, team_num) for each cutting session
        self.current_cut_session_start = None  # Track ongoing session
        self.current_cut_team = None


class ProductionSimulator:
    def __init__(self, collect_gantt_data=False):
        scale = NUM_OVENS / 5
        self.FORM_TIME = BASE_FORM_TIME * scale
        self.CUT_TIME = BASE_CUT_TIME * scale
        self.WB_PER_BATCH = int(BASE_WB_PER_BATCH * scale)
        self.BB_PER_BATCH = int(BASE_BB_PER_BATCH * scale)
        
        self.COOK_TIME = COOK_TIME
        self.CURE_WB_MIN = CURE_WB_MIN
        self.CURE_WB_MAX = CURE_WB_MAX
        
        self.WB_SHEETS = WB_SHEETS
        self.BB_SHEETS = BB_SHEETS
        
        self.WB_TARGET = WB_TARGET
        self.BB_TARGET = BB_TARGET
        self.TOTAL_TARGET = WB_TARGET + BB_TARGET
        
        self.WB_RATIO = WB_TARGET / self.TOTAL_TARGET
        self.BB_RATIO = BB_TARGET / self.TOTAL_TARGET
        
        self.WEEK_HOURS = 168
        self.NUM_WEEKS = NUM_WEEKS
        self.TOTAL_HOURS = self.WEEK_HOURS * self.NUM_WEEKS
        
        self.TEAM2_START = TEAM2_START_HOUR
        self.TEAM2_END = TEAM2_END_HOUR
        
        self.collect_gantt_data = collect_gantt_data
        self.all_batches = []
    
    def simulate(self, verbose=False):
        time = 0.0
        batch_id = 0
        batches = []
        all_batches = []
        total_wb = 0
        total_bb = 0
        
        team1_free = 0.0
        team2_free = 0.0
        oven1_free = 0.0
        oven2_free = 0.0 if NUM_OVEN_SETS == 2 else float('inf')
        
        wb_batches_formed = 0
        bb_batches_formed = 0
        
        def team2_enabled():
            return TEAM_CONFIG in ['2team_6-6', '2team_24/7']
        
        def team2_on(t):
            if TEAM_CONFIG == '2team_24/7':
                return True
            elif TEAM_CONFIG == '2team_6-6':
                return self.TEAM2_START <= (t % 24) < self.TEAM2_END
            return False
        
        def next_team2_start(t):
            if TEAM_CONFIG == '2team_24/7':
                return t
            h = t % 24
            if h < self.TEAM2_START:
                return t + (self.TEAM2_START - h)
            elif h >= self.TEAM2_END:
                return t + (24 - h) + self.TEAM2_START
            return t
        
        def team2_shift_end(t):
            if TEAM_CONFIG == '2team_24/7':
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
            # A batch is ready to cut if cured, not finished, and not currently being cut
            ready = [b for b in batches 
                    if b.cure_end <= time and b.cut_end is None 
                    and b.id not in exclude]
            # Prioritize: 
            # 1) batches this team already started (own cuts first)
            # 2) batches another team started (pick up paused cuts)
            # 3) fresh batches by cure_end time
            def sort_key(b):
                if b.cut_progress > 0:
                    if b.cut_by == team_num:
                        return (0, b.cure_end)  # Own cut - highest priority
                    else:
                        return (1, b.cure_end)  # Other team's paused cut
                return (2, b.cure_end)  # Fresh batch
            return sorted(ready, key=sort_key)
        
        def ready_to_cut_wb_first(exclude, team_num=None):
            ready = [b for b in batches 
                    if b.cure_end <= time and b.cut_end is None 
                    and b.id not in exclude]
            def sort_key(b):
                if b.cut_progress > 0:
                    if b.cut_by == team_num:
                        return (0, 0 if b.product == 'WB' else 1, b.cure_end)
                    else:
                        return (1, 0 if b.product == 'WB' else 1, b.cure_end)
                return (2, 0 if b.product == 'WB' else 1, b.cure_end)
            return sorted(ready, key=sort_key)
        
        def ready_to_cut_bb_first(exclude, team_num=None):
            ready = [b for b in batches 
                    if b.cure_end <= time and b.cut_end is None 
                    and b.id not in exclude]
            def sort_key(b):
                if b.cut_progress > 0:
                    if b.cut_by == team_num:
                        return (0, 0 if b.product == 'BB' else 1, b.cure_end)
                    else:
                        return (1, 0 if b.product == 'BB' else 1, b.cure_end)
                return (2, 0 if b.product == 'BB' else 1, b.cure_end)
            return sorted(ready, key=sort_key)
        
        def get_priority():
            nonlocal total_wb, total_bb, wb_batches_formed, bb_batches_formed
            
            if PRIORITY_STRATEGY == 'ratio':
                total = total_wb + total_bb
                if total == 0:
                    return True
                return (total_wb / total) < self.WB_RATIO
            elif PRIORITY_STRATEGY == 'ratio_batches':
                wb_needed = max(0, (self.WB_TARGET - total_wb) / self.WB_PER_BATCH)
                bb_needed = max(0, (self.BB_TARGET - total_bb) / self.BB_PER_BATCH)
                return wb_needed >= bb_needed
            elif PRIORITY_STRATEGY == 'wb_first':
                return True
            elif PRIORITY_STRATEGY == 'bb_first':
                return False
            elif PRIORITY_STRATEGY == 'adaptive':
                wb_progress = total_wb / self.WB_TARGET if self.WB_TARGET > 0 else 1
                bb_progress = total_bb / self.BB_TARGET if self.BB_TARGET > 0 else 1
                return wb_progress < bb_progress
            elif PRIORITY_STRATEGY == 'cure_aware':
                pending_wb = curing_wb() * self.WB_PER_BATCH
                effective_wb = total_wb + pending_wb
                wb_needed = max(0, (self.WB_TARGET - effective_wb) / self.WB_PER_BATCH)
                bb_needed = max(0, (self.BB_TARGET - total_bb) / self.BB_PER_BATCH)
                return wb_needed >= bb_needed
            elif PRIORITY_STRATEGY == 'goal_focused':
                wb_pct = total_wb / self.WB_TARGET if self.WB_TARGET > 0 else 1
                bb_pct = total_bb / self.BB_TARGET if self.BB_TARGET > 0 else 1
                if wb_pct >= 1 and bb_pct >= 1:
                    return True
                return wb_pct < bb_pct
            elif PRIORITY_STRATEGY == 'wb_until_done':
                if total_wb < self.WB_TARGET:
                    return True
                return False
            elif PRIORITY_STRATEGY == 'balanced_goal':
                pending_wb = curing_wb() * self.WB_PER_BATCH
                effective_wb = total_wb + pending_wb
                wb_pct = effective_wb / self.WB_TARGET if self.WB_TARGET > 0 else 1
                bb_pct = total_bb / self.BB_TARGET if self.BB_TARGET > 0 else 1
                if wb_pct >= 1 and bb_pct >= 1:
                    return False
                return wb_pct < bb_pct
            elif PRIORITY_STRATEGY in ['team2_wb_cut', 'team2_bb_cut']:
                wb_needed = max(0, (self.WB_TARGET - total_wb) / self.WB_PER_BATCH)
                bb_needed = max(0, (self.BB_TARGET - total_bb) / self.BB_PER_BATCH)
                return wb_needed >= bb_needed
            else:
                total = total_wb + total_bb
                if total == 0:
                    return True
                return (total_wb / total) < self.WB_RATIO
        
        def form(product, oven_num, team_num):
            nonlocal batch_id, oven1_free, oven2_free, wb_batches_formed, bb_batches_formed
            batch_id += 1
            cure = random.uniform(self.CURE_WB_MIN, self.CURE_WB_MAX) if product == 'WB' else 0
            b = Batch(batch_id, product, time, self.FORM_TIME, self.COOK_TIME, cure)
            b.formed_by = team_num
            batches.append(b)
            all_batches.append(b)
            
            if oven_num == 1:
                oven1_free = b.cook_end
            else:
                oven2_free = b.cook_end
            
            if product == 'WB':
                wb_batches_formed += 1
            else:
                bb_batches_formed += 1
            
            return b.form_end
        
        def cut(batch, hours, team_num, is_partial=False):
            nonlocal total_wb, total_bb
            
            if batch.cut_start is None:
                batch.cut_start = time
                batch.cut_by = team_num
            
            # Start a new session if not already cutting
            if batch.current_cut_session_start is None:
                batch.current_cut_session_start = time
                batch.current_cut_team = team_num
            
            batch.cut_progress += hours
            session_end = time + hours
            
            # Check if cut is complete
            if batch.cut_progress >= self.CUT_TIME:
                actual_end = time + (self.CUT_TIME - (batch.cut_progress - hours))
                batch.cut_end = actual_end
                session_end = actual_end
                
                # End the current session
                batch.cut_sessions.append((batch.current_cut_session_start, session_end, batch.current_cut_team))
                batch.current_cut_session_start = None
                batch.current_cut_team = None
                
                if batch.cut_end <= self.TOTAL_HOURS:
                    if batch.product == 'WB':
                        total_wb += self.WB_PER_BATCH
                    else:
                        total_bb += self.BB_PER_BATCH
            elif is_partial:
                # Partial cut - end the session, will resume later
                batch.cut_sessions.append((batch.current_cut_session_start, session_end, batch.current_cut_team))
                batch.current_cut_session_start = None
                batch.current_cut_team = None
        
        def do_work(oven_num, deadline, shift_end=float('inf'), is_team2=False):
            nonlocal being_cut, sheets_claimed_wb, sheets_claimed_bb
            
            team_num = 2 if is_team2 else 1
            wb_priority = get_priority()
            can_form = time >= deadline
            if shift_end != float('inf'):
                can_form = can_form and (shift_end - time) >= self.FORM_TIME
            
            # Pass team_num so we prioritize our own paused cuts over other team's
            get_ready = lambda: ready_to_cut(being_cut, team_num)
            
            # Check available sheets (including ones claimed this time step)
            available_wb = self.WB_SHEETS - active_wb() - sheets_claimed_wb
            available_bb = self.BB_SHEETS - active_bb() - sheets_claimed_bb
            
            # Check if there's a batch with < 1 hour of cutting left that THIS TEAM started - finish it first
            ready = get_ready()
            almost_done = [b for b in ready if (self.CUT_TIME - b.cut_progress) < 1.0 
                          and b.cut_progress > 0 and b.cut_by == team_num]
            if almost_done:
                b = almost_done[0]
                being_cut.add(b.id)
                remaining = self.CUT_TIME - b.cut_progress
                # Check shift end for Team 2
                if shift_end != float('inf') and time + remaining > shift_end:
                    # Can't finish before shift ends, do partial
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
                    # Track which team is cutting this batch (first team to cut it)
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
                    
                    # Don't start a NEW cut if window is less than 1 hour
                    # But DO continue a cut we already started
                    if window < 1.0 and b.cut_progress == 0:
                        return time  # Wait for forming instead of starting new cut
                    
                    if window > 0:
                        being_cut.add(b.id)
                        # Track which team is cutting this batch (first team to cut it)
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
            
            # Find batches currently being actively cut
            # A batch is being actively cut if:
            # - cut has started (cut_start is not None)
            # - cut has not ended (cut_end is None)
            # - the worker assigned to it is still busy (their free time > current time)
            being_cut = set()
            for b in batches:
                if b.cut_start is not None and b.cut_end is None and b.cut_progress < self.CUT_TIME:
                    # This batch is partially cut - check if someone is actively working on it
                    # by checking if their session end time > current time
                    if b.cut_sessions:
                        last_session = b.cut_sessions[-1]
                        if last_session[1] > time:  # Session end > current time
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
                    if NUM_OVEN_SETS == 2:
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
            if NUM_OVEN_SETS == 2:
                events.extend([oven2_free, oven2_free - self.FORM_TIME])
            if team2_enabled():
                events.append(team2_free)
                if TEAM_CONFIG == '2team_6-6':
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
        total_pct = 100 * (total_wb + total_bb) / self.TOTAL_TARGET if self.TOTAL_TARGET > 0 else 0
        
        return {
            'total_wb': total_wb,
            'total_bb': total_bb,
            'total': total_wb + total_bb,
            'wb_pct': wb_pct,
            'bb_pct': bb_pct,
            'total_pct': total_pct,
            'wb_batches': wb_batches_formed,
            'bb_batches': bb_batches_formed
        }



def generate_resources_gantt(batches, start_hour, end_hour, filename):
    """Generate Gantt chart: Form -> Cook -> Cure -> Cut flow
    With 2 teams: separate rows for Team 1 and Team 2 forming/cutting
    With 2 oven sets: separate rows for each oven set
    """
    
    relevant_batches = [b for b in batches if b.form_start < end_hour and 
                        (b.cut_end is None or b.cut_end > start_hour or b.cure_end > start_hour)]
    
    if not relevant_batches:
        print(f"  No batches in range {start_hour}-{end_hour}h")
        return
    
    has_team2 = TEAM_CONFIG != '1team'
    has_oven2 = NUM_OVEN_SETS == 2
    
    # Build row structure dynamically
    y_positions = {}
    y_labels = []
    y = 0
    
    # Cut rows (bottom)
    if has_team2:
        y_positions['cut_team2'] = y
        y_labels.append('Cut (Team 2)')
        y += 1
        y_positions['cut_team1'] = y
        y_labels.append('Cut (Team 1)')
        y += 1
    else:
        y_positions['cut'] = y
        y_labels.append('Cut')
        y += 1
    
    # Cure row
    y_positions['cure'] = y
    y_labels.append('Cure (stacked)')
    y += 1
    
    # Cook rows
    if has_oven2:
        y_positions['cook_oven2'] = y
        y_labels.append('Cook (Oven 2)')
        y += 1
        y_positions['cook_oven1'] = y
        y_labels.append('Cook (Oven 1)')
        y += 1
    else:
        y_positions['cook'] = y
        y_labels.append('Cook')
        y += 1
    
    # Form rows (top)
    if has_team2:
        y_positions['form_team2'] = y
        y_labels.append('Form (Team 2)')
        y += 1
        y_positions['form_team1'] = y
        y_labels.append('Form (Team 1)')
        y += 1
    else:
        y_positions['form'] = y
        y_labels.append('Form')
        y += 1
    
    num_rows = y
    fig_height = max(6, num_rows * 1.0)
    fig, ax = plt.subplots(figsize=(16, fig_height))
    
    colors = {
        'form_wb': '#87CEEB', 'form_bb': '#1E3A5F',
        'cook_wb': '#FFB347', 'cook_bb': '#FF6B35',
        'cure_wb': '#98FB98',
        'cut_wb': '#32CD32', 'cut_bb': '#228B22',
    }
    
    bar_height = 0.7
    cure_bars = []
    
    for b in relevant_batches:
        product = b.product
        
        def clip(start, end):
            return max(start, start_hour), min(end, end_hour)
        
        # FORM - by team
        if b.form_start < end_hour and b.form_end > start_hour:
            if has_team2 and b.formed_by:
                y = y_positions[f'form_team{b.formed_by}']
            else:
                y = y_positions['form']
            s, e = clip(b.form_start, b.form_end)
            color = colors[f'form_{product.lower()}']
            rect = Rectangle((s, y - bar_height/2), e - s, bar_height,
                            facecolor=color, edgecolor='black', linewidth=0.5)
            ax.add_patch(rect)
            if e - s > 3:
                ax.text((s + e) / 2, y, f'{product}{b.id}', ha='center', va='center', 
                       fontsize=8, color='black' if product == 'WB' else 'white')
        
        # COOK - by oven (use formed_by to determine oven for 2 oven sets)
        if b.cook_start < end_hour and b.cook_end > start_hour:
            if has_oven2 and b.formed_by:
                # Team 1 uses Oven 1, Team 2 uses Oven 2
                y = y_positions[f'cook_oven{b.formed_by}']
            else:
                y = y_positions['cook']
            s, e = clip(b.cook_start, b.cook_end)
            color = colors[f'cook_{product.lower()}']
            rect = Rectangle((s, y - bar_height/2), e - s, bar_height,
                            facecolor=color, edgecolor='black', linewidth=0.5)
            ax.add_patch(rect)
            if e - s > 3:
                ax.text((s + e) / 2, y, f'{product}{b.id}', ha='center', va='center', 
                       fontsize=8, color='white')
        
        # CURE (stacked) - stays on single row with wrapping
        if b.cure_time > 0 and b.cure_start < end_hour and b.cure_end > start_hour:
            s, e = clip(b.cure_start, b.cure_end)
            
            # Find stack level
            stack_level = 0
            for (cs, ce, _, level) in cure_bars:
                if not (e <= cs or s >= ce):
                    stack_level = max(stack_level, level + 1)
            cure_bars.append((s, e, b.id, stack_level))
            
            y = y_positions['cure']
            
            # Wrap stack level to stay within bounds (max 3 levels before wrapping)
            max_stack_levels = 3
            display_level = stack_level % max_stack_levels
            y_offset = display_level * 0.22
            
            # Vary alpha slightly based on actual level to show depth
            alpha = 0.9 - (stack_level // max_stack_levels) * 0.15
            alpha = max(0.5, alpha)
            
            rect = Rectangle((s, y - bar_height/2 + y_offset), e - s, bar_height * 0.35,
                            facecolor=colors['cure_wb'], edgecolor='black', linewidth=0.5, alpha=alpha)
            ax.add_patch(rect)
            # Position text in center of the bar
            text_y = y - bar_height/2 + y_offset + bar_height * 0.35 / 2
            ax.text((s + e) / 2, text_y, f'WB{b.id}', ha='center', va='center', fontsize=6)
        
        # CUT - by team, merge consecutive sessions from SAME team only
        if b.cut_sessions:
            merged_sessions = []
            for session_start, session_end, team_num in b.cut_sessions:
                # Only merge if same team AND consecutive (no gap)
                if (merged_sessions and 
                    abs(merged_sessions[-1][1] - session_start) < 0.1 and
                    merged_sessions[-1][2] == team_num):
                    merged_sessions[-1] = (merged_sessions[-1][0], session_end, merged_sessions[-1][2])
                else:
                    merged_sessions.append((session_start, session_end, team_num))
            
            has_pause = len(merged_sessions) > 1
            
            for idx, (session_start, session_end, team_num) in enumerate(merged_sessions):
                if session_start < end_hour and session_end > start_hour:
                    if has_team2 and team_num:
                        y = y_positions[f'cut_team{team_num}']
                    else:
                        y = y_positions['cut']
                    s, e = clip(session_start, session_end)
                    color = colors[f'cut_{product.lower()}']
                    
                    hatch_pattern = '///' if has_pause else None
                    rect = Rectangle((s, y - bar_height/2), e - s, bar_height,
                                    facecolor=color, edgecolor='black', linewidth=0.5,
                                    hatch=hatch_pattern)
                    ax.add_patch(rect)
                    # Always show label, adjust font size based on bar width
                    bar_width = e - s
                    if bar_width > 5:
                        fontsize = 8
                    elif bar_width > 2:
                        fontsize = 6
                    else:
                        fontsize = 5
                    ax.text((s + e) / 2, y, f'{product}{b.id}', ha='center', va='center', 
                           fontsize=fontsize, color='white')
    
    ax.set_xlim(start_hour, end_hour)
    ax.set_ylim(-0.5, num_rows - 0.5)
    ax.set_yticks(range(num_rows))
    ax.set_yticklabels(y_labels, fontsize=10)
    ax.set_xlabel('Hours', fontsize=11)
    
    # Day markers
    for day in range(int(start_hour // 24), int(end_hour // 24) + 2):
        day_hour = day * 24
        if start_hour <= day_hour <= end_hour:
            ax.axvline(x=day_hour, color='red', linestyle='--', alpha=0.4, linewidth=1)
            ax.text(day_hour + 1, num_rows - 0.7, f'Day {day}', fontsize=9, color='red', alpha=0.8)
    
    # Shift markers for 6-6 schedule
    if TEAM_CONFIG == '2team_6-6':
        for day in range(int(start_hour // 24), int(end_hour // 24) + 1):
            shift_start = day * 24 + TEAM2_START_HOUR
            shift_end_time = day * 24 + TEAM2_END_HOUR
            if start_hour <= shift_start <= end_hour:
                ax.axvline(x=shift_start, color='blue', linestyle=':', alpha=0.5)
            if start_hour <= shift_end_time <= end_hour:
                ax.axvline(x=shift_end_time, color='blue', linestyle=':', alpha=0.5)
    
    week_num = int(start_hour // 168) + 1
    title = f'Production Flow - Week {week_num} (Hours {start_hour:.0f}-{end_hour:.0f})'
    title += f'\n{TEAM_CONFIG}, {NUM_OVENS} ovens x {NUM_OVEN_SETS} set(s), {WB_SHEETS} WB + {BB_SHEETS} BB sheets, Strategy: {PRIORITY_STRATEGY}'
    ax.set_title(title, fontsize=12, fontweight='bold')
    
    legend_elements = [
        mpatches.Patch(color=colors['form_wb'], label='Form WB'),
        mpatches.Patch(color=colors['form_bb'], label='Form BB'),
        mpatches.Patch(color=colors['cook_wb'], label='Cook WB'),
        mpatches.Patch(color=colors['cook_bb'], label='Cook BB'),
        mpatches.Patch(color=colors['cure_wb'], label='Cure WB'),
        mpatches.Patch(color=colors['cut_wb'], label='Cut WB'),
        mpatches.Patch(color=colors['cut_bb'], label='Cut BB'),
        mpatches.Patch(facecolor=colors['cut_wb'], hatch='///', label='Paused Cut', edgecolor='black'),
    ]
    ax.legend(handles=legend_elements, loc='upper right', fontsize=8)
    ax.grid(axis='x', alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(filename, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()
    print(f"  Saved: {filename}")

def generate_workers_gantt(batches, start_hour, end_hour, filename):
    """Generate Gantt chart showing only worker activities"""
    
    relevant_batches = [b for b in batches if b.form_start < end_hour and 
                        (b.cut_end is None or b.cut_end > start_hour)]
    
    if not relevant_batches:
        print(f"  No batches in range {start_hour}-{end_hour}h")
        return
    
    has_team2 = TEAM_CONFIG != '1team'
    fig_height = 5 if has_team2 else 3
    fig, ax = plt.subplots(figsize=(16, fig_height))
    
    colors = {
        'form_wb': '#87CEEB', 'form_bb': '#1E3A5F',
        'cut_wb': '#32CD32', 'cut_bb': '#228B22',
    }
    
    if has_team2:
        y_positions = {'team1': 1, 'team2': 0}
        y_labels = ['Team 2', 'Team 1']
    else:
        y_positions = {'team1': 0}
        y_labels = ['Team 1']
    
    bar_height = 0.7
    
    for b in relevant_batches:
        product = b.product
        
        def clip(start, end):
            return max(start, start_hour), min(end, end_hour)
        
        # FORM
        if b.form_start < end_hour and b.form_end > start_hour and b.formed_by:
            y = y_positions[f'team{b.formed_by}']
            s, e = clip(b.form_start, b.form_end)
            color = colors[f'form_{product.lower()}']
            rect = Rectangle((s, y - bar_height/2), e - s, bar_height,
                            facecolor=color, edgecolor='black', linewidth=0.5)
            ax.add_patch(rect)
            # Always show label
            bar_width = e - s
            fontsize = 8 if bar_width > 5 else (6 if bar_width > 2 else 5)
            ax.text((s + e) / 2, y, f'F-{product}{b.id}', ha='center', va='center', 
                   fontsize=fontsize, color='black' if product == 'WB' else 'white')
        
        # CUT - merge consecutive sessions and show all cutting
        # Sessions with gaps indicate paused cuts - show with hatching
        if b.cut_sessions:
            # Merge consecutive sessions (no gap between them)
            merged_sessions = []
            for session_start, session_end, team_num in b.cut_sessions:
                if merged_sessions and abs(merged_sessions[-1][1] - session_start) < 0.1 and merged_sessions[-1][2] == team_num:
                    # Extend the previous session (same team, no gap)
                    merged_sessions[-1] = (merged_sessions[-1][0], session_end, merged_sessions[-1][2])
                else:
                    merged_sessions.append((session_start, session_end, team_num))
            
            # Check if this batch had paused cuts (more than one merged session)
            has_pause = len(merged_sessions) > 1
            
            for idx, (session_start, session_end, team_num) in enumerate(merged_sessions):
                if session_start < end_hour and session_end > start_hour and team_num:
                    y = y_positions[f'team{team_num}']
                    s, e = clip(session_start, session_end)
                    color = colors[f'cut_{product.lower()}']
                    
                    # Add hatching if this was a paused cut
                    hatch_pattern = '///' if has_pause else None
                    rect = Rectangle((s, y - bar_height/2), e - s, bar_height,
                                    facecolor=color, edgecolor='black', linewidth=0.5,
                                    hatch=hatch_pattern)
                    ax.add_patch(rect)
                    # Always show label
                    bar_width = e - s
                    fontsize = 8 if bar_width > 5 else (6 if bar_width > 2 else 5)
                    ax.text((s + e) / 2, y, f'X-{product}{b.id}', ha='center', va='center', 
                           fontsize=fontsize, color='white')
    
    ax.set_xlim(start_hour, end_hour)
    if has_team2:
        ax.set_ylim(-0.5, 1.5)
        ax.set_yticks([0, 1])
    else:
        ax.set_ylim(-0.5, 0.5)
        ax.set_yticks([0])
    ax.set_yticklabels(y_labels, fontsize=11)
    ax.set_xlabel('Hours', fontsize=11)
    
    # Day markers
    for day in range(int(start_hour // 24), int(end_hour // 24) + 2):
        day_hour = day * 24
        if start_hour <= day_hour <= end_hour:
            ax.axvline(x=day_hour, color='red', linestyle='--', alpha=0.4, linewidth=1)
            y_text = 1.35 if has_team2 else 0.4
            ax.text(day_hour + 1, y_text, f'Day {day}', fontsize=9, color='red', alpha=0.8)
    
    # Shift markers for 6-6
    if TEAM_CONFIG == '2team_6-6':
        for day in range(int(start_hour // 24), int(end_hour // 24) + 1):
            shift_start = day * 24 + TEAM2_START_HOUR
            shift_end = day * 24 + TEAM2_END_HOUR
            if start_hour <= shift_start <= end_hour:
                ax.axvline(x=shift_start, color='blue', linestyle=':', alpha=0.5)
            if start_hour <= shift_end <= end_hour:
                ax.axvline(x=shift_end, color='blue', linestyle=':', alpha=0.5)
    
    week_num = int(start_hour // 168) + 1
    title = f'Worker Activity - Week {week_num} (Hours {start_hour:.0f}-{end_hour:.0f})'
    title += f'\n{TEAM_CONFIG}, Strategy: {PRIORITY_STRATEGY}'
    ax.set_title(title, fontsize=12, fontweight='bold')
    
    legend_elements = [
        mpatches.Patch(color=colors['form_wb'], label='Form WB'),
        mpatches.Patch(color=colors['form_bb'], label='Form BB'),
        mpatches.Patch(color=colors['cut_wb'], label='Cut WB'),
        mpatches.Patch(color=colors['cut_bb'], label='Cut BB'),
        mpatches.Patch(facecolor=colors['cut_wb'], hatch='///', label='Paused Cut', edgecolor='black'),
    ]
    ax.legend(handles=legend_elements, loc='upper right', fontsize=9)
    ax.grid(axis='x', alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(filename, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()
    print(f"  Saved: {filename}")


def generate_all_gantt_charts(sim):
    """Generate Gantt charts based on configuration"""
    
    batches = sim.all_batches
    
    # Period (each chart)
    period_map = {'week': 168, '2weeks': 336, 'month': 672}
    period_hours = period_map.get(GANTT_PERIOD, 168)
    
    # Timeframe (total)
    timeframe_map = {'week1': 168, '2weeks': 336, 'month1': 672, 'month3': 2184, 'year': 8736}
    total_hours = timeframe_map.get(GANTT_TIMEFRAME, 168)
    
    # Generate chunks
    chunks = []
    start = 0
    chart_num = 1
    while start < total_hours:
        end = min(start + period_hours, total_hours)
        chunks.append((start, end, chart_num))
        start = end
        chart_num += 1
    
    print(f"\nGenerating Gantt charts...")
    print(f"  Type: {GANTT_TYPE}")
    print(f"  Period: {GANTT_PERIOD} ({period_hours}h per chart)")
    print(f"  Timeframe: {GANTT_TIMEFRAME} ({total_hours}h total)")
    print(f"  Charts to generate: {len(chunks)}")
    print()
    
    for start_h, end_h, num in chunks:
        if GANTT_TYPE == 'resources':
            generate_resources_gantt(batches, start_h, end_h, f'gantt_resources_{GANTT_PERIOD}{num}.png')
        elif GANTT_TYPE == 'workers':
            generate_workers_gantt(batches, start_h, end_h, f'gantt_workers_{GANTT_PERIOD}{num}.png')
        elif GANTT_TYPE == 'both':
            generate_resources_gantt(batches, start_h, end_h, f'gantt_resources_{GANTT_PERIOD}{num}.png')
            generate_workers_gantt(batches, start_h, end_h, f'gantt_workers_{GANTT_PERIOD}{num}.png')


def run_monte_carlo(runs=MONTE_CARLO_RUNS):
    results = []
    for _ in range(runs):
        sim = ProductionSimulator()
        results.append(sim.simulate())
    
    return {
        'avg_wb': sum(r['total_wb'] for r in results) / runs,
        'avg_bb': sum(r['total_bb'] for r in results) / runs,
        'avg_total': sum(r['total'] for r in results) / runs,
        'avg_wb_pct': sum(r['wb_pct'] for r in results) / runs,
        'avg_bb_pct': sum(r['bb_pct'] for r in results) / runs,
        'avg_total_pct': sum(r['total_pct'] for r in results) / runs,
        'min_total': min(r['total'] for r in results),
        'max_total': max(r['total'] for r in results),
        'avg_wb_batches': sum(r['wb_batches'] for r in results) / runs,
        'avg_bb_batches': sum(r['bb_batches'] for r in results) / runs
    }


def test_all_strategies():
    global PRIORITY_STRATEGY
    
    strategies = ['ratio', 'ratio_batches', 'wb_first', 'bb_first', 'adaptive', 
                  'cure_aware', 'goal_focused', 'wb_until_done', 'balanced_goal']
    results = []
    
    print("\n" + "=" * 110)
    print("TESTING ALL PRIORITY STRATEGIES")
    print("=" * 110)
    wb_ratio = 100 * WB_TARGET / (WB_TARGET + BB_TARGET)
    bb_ratio = 100 * BB_TARGET / (WB_TARGET + BB_TARGET)
    print(f"\nConfiguration: {TEAM_CONFIG}, {NUM_OVENS} ovens, {NUM_OVEN_SETS} set(s), {WB_SHEETS} WB + {BB_SHEETS} BB sheets")
    print(f"Targets: WB={WB_TARGET:,} ({wb_ratio:.0f}%) | BB={BB_TARGET:,} ({bb_ratio:.0f}%)\n")
    
    for strategy in strategies:
        PRIORITY_STRATEGY = strategy
        print(f"  Testing '{strategy}'...", end=' ', flush=True)
        mc = run_monte_carlo(runs=50)
        
        wb_gap = max(0, 100 - mc['avg_wb_pct'])
        bb_gap = max(0, 100 - mc['avg_bb_pct'])
        min_pct = min(mc['avg_wb_pct'], mc['avg_bb_pct'])
        
        results.append({
            'strategy': strategy, 'wb': mc['avg_wb'], 'bb': mc['avg_bb'],
            'total': mc['avg_total'], 'wb_pct': mc['avg_wb_pct'], 'bb_pct': mc['avg_bb_pct'],
            'total_pct': mc['avg_total_pct'], 'wb_gap': wb_gap, 'bb_gap': bb_gap,
            'combined_gap': wb_gap + bb_gap, 'min_pct': min_pct
        })
        
        print(f"WB: {mc['avg_wb_pct']:.0f}% | BB: {mc['avg_bb_pct']:.0f}% | Min: {min_pct:.0f}%")
    
    print("\n" + "-" * 110)
    print(f"{'Strategy':<15} {'WB':>10} {'WB%':>6} {'BB':>10} {'BB%':>6} {'Min%':>6} {'Gap':>6} {'Score':>8}")
    print("-" * 110)
    for r in results:
        score = r['min_pct'] - (r['combined_gap'] * 0.5)
        r['score'] = score
        print(f"{r['strategy']:<15} {r['wb']:>10,.0f} {r['wb_pct']:>5.0f}% "
              f"{r['bb']:>10,.0f} {r['bb_pct']:>5.0f}% "
              f"{r['min_pct']:>5.0f}% {r['combined_gap']:>5.0f}% {score:>7.1f}")
    print("-" * 110)
    
    best_score = max(results, key=lambda x: x['score'])
    print(f"\nRECOMMENDATION: Use '{best_score['strategy']}' for best balance.")
    
    return results


def main():
    global PRIORITY_STRATEGY
    
    print("=" * 80)
    print("PRODUCTION LINE SIMULATOR")
    print("=" * 80)
    
    scale = NUM_OVENS / 5
    print(f"""
CONFIGURATION:
  Team: {TEAM_CONFIG} | Ovens: {NUM_OVENS} × {NUM_OVEN_SETS} set(s) | Sheets: {WB_SHEETS} WB, {BB_SHEETS} BB
  Strategy: {PRIORITY_STRATEGY}
  Form: {BASE_FORM_TIME * scale:.1f}h | Cut: {BASE_CUT_TIME * scale:.1f}h | WB/batch: {int(BASE_WB_PER_BATCH * scale):,} | BB/batch: {int(BASE_BB_PER_BATCH * scale):,}
  Targets: WB {WB_TARGET:,} ({100*WB_TARGET/(WB_TARGET+BB_TARGET):.0f}%) | BB {BB_TARGET:,} ({100*BB_TARGET/(WB_TARGET+BB_TARGET):.0f}%)
""")
    
    # First, test all strategies to find the best one
    results = test_all_strategies()
    
    # Find the best strategy
    best_result = max(results, key=lambda x: x['score'])
    best_strategy = best_result['strategy']
    
    # Generate Gantt charts using the best strategy
    if GENERATE_GANTT:
        print("\n" + "=" * 80)
        print("GENERATING GANTT CHARTS")
        print("=" * 80)
        print(f"  Using best strategy: '{best_strategy}' (Score: {best_result['score']:.1f})")
        
        # Set the strategy to the best one
        PRIORITY_STRATEGY = best_strategy
        
        sim = ProductionSimulator(collect_gantt_data=True)
        result = sim.simulate()
        
        print(f"  Simulation result: WB {result['wb_pct']:.1f}% | BB {result['bb_pct']:.1f}%")
        
        generate_all_gantt_charts(sim)
        
        # Analyze wait times (cure_end to cut_end)
        print("\n" + "=" * 80)
        print("WAIT TIME ANALYSIS (Cure End → Cut End)")
        print("=" * 80)
        
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
        
        if wait_times:
            # Sort by wait time descending
            wait_times.sort(key=lambda x: x['wait'], reverse=True)
            
            # Overall stats
            all_waits = [w['wait'] for w in wait_times]
            wb_waits = [w['wait'] for w in wait_times if w['product'] == 'WB']
            bb_waits = [w['wait'] for w in wait_times if w['product'] == 'BB']
            
            print(f"\n  Overall:")
            print(f"    Max wait:  {max(all_waits):.1f}h")
            print(f"    Avg wait:  {sum(all_waits)/len(all_waits):.1f}h")
            print(f"    Min wait:  {min(all_waits):.1f}h")
            
            if wb_waits:
                print(f"\n  WB batches:")
                print(f"    Max wait:  {max(wb_waits):.1f}h")
                print(f"    Avg wait:  {sum(wb_waits)/len(wb_waits):.1f}h")
            
            if bb_waits:
                print(f"\n  BB batches:")
                print(f"    Max wait:  {max(bb_waits):.1f}h")
                print(f"    Avg wait:  {sum(bb_waits)/len(bb_waits):.1f}h")
            
            # Top 10 longest waits
            print(f"\n  Top 10 longest waits:")
            print(f"    {'Batch':<8} {'Cure End':>10} {'Cut End':>10} {'Wait':>8}")
            print(f"    {'-'*8} {'-'*10} {'-'*10} {'-'*8}")
            for w in wait_times[:10]:
                print(f"    {w['batch']:<8} {w['cure_end']:>10.1f}h {w['cut_end']:>10.1f}h {w['wait']:>7.1f}h")


if __name__ == "__main__":
    main()