"""
Production Line Simulator API
Backend for Flutter web frontend
Deploy to Railway.app
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import random

app = Flask(__name__)
# Enable CORS for all origins (needed for Flutter web)
CORS(app, resources={r"/*": {"origins": "*", "methods": ["GET", "POST", "OPTIONS"], "allow_headers": ["Content-Type"]}})

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
        self.oven_set = 1  # Track which oven set was used


class ProductionSimulator:
    def __init__(self, config, collect_gantt_data=False):
        self.config = config
        
        # Extract config values with defaults
        num_ovens = config.get('num_ovens', 5)
        scale = num_ovens / 5
        
        # Times scale with number of ovens (more ovens = bigger batches = longer times)
        self.FORM_TIME = config.get('form_time', 6) * scale
        self.CUT_TIME = config.get('cut_time', 8) * scale
        self.WB_PER_BATCH = int(config.get('wb_per_batch', 3000) * scale)
        self.BB_PER_BATCH = int(config.get('bb_per_batch', 6000) * scale)
        
        # Multiple cook times for WB and BB
        self.WB_COOK_TIMES = config.get('wb_cook_times', [9.25, 16.0, 6.5, 12.167, 10.75, 11.167, 8.5, 6.33])
        self.WB_COOK_WEIGHTS = config.get('wb_cook_weights', [1.0] * len(self.WB_COOK_TIMES))
        self.BB_COOK_TIMES = config.get('bb_cook_times', [8.333, 10.0])
        self.BB_COOK_WEIGHTS = config.get('bb_cook_weights', [1.0] * len(self.BB_COOK_TIMES))
        
        # Ensure weights match times
        if len(self.WB_COOK_WEIGHTS) != len(self.WB_COOK_TIMES):
            self.WB_COOK_WEIGHTS = [1.0] * len(self.WB_COOK_TIMES)
        if len(self.BB_COOK_WEIGHTS) != len(self.BB_COOK_TIMES):
            self.BB_COOK_WEIGHTS = [1.0] * len(self.BB_COOK_TIMES)
        
        self.CURE_WB_MIN = config.get('cure_wb_min', 24)
        self.CURE_WB_MAX = config.get('cure_wb_max', 36)
        
        # Cure time distribution weights
        cure_range = int(self.CURE_WB_MAX - self.CURE_WB_MIN) + 1
        default_cure_weights = [1.0] * cure_range
        self.CURE_WEIGHTS = config.get('cure_weights', default_cure_weights)
        if len(self.CURE_WEIGHTS) != cure_range:
            self.CURE_WEIGHTS = default_cure_weights
        
        # Daily cleaning settings
        self.CLEANING_ENABLED = config.get('cleaning_enabled', True)
        self.FORM_CLEAN_TIME = config.get('form_clean_time', 1.0)
        self.OVEN_CLEAN_MIN = config.get('oven_clean_min', 1.0)
        self.OVEN_CLEAN_MAX = config.get('oven_clean_max', 1.0)
        
        # Oven clean time distribution weights
        oven_clean_range = int(self.OVEN_CLEAN_MAX - self.OVEN_CLEAN_MIN) + 1
        default_oven_weights = [1.0] * max(1, oven_clean_range)
        self.OVEN_CLEAN_WEIGHTS = config.get('oven_clean_weights', default_oven_weights)
        if len(self.OVEN_CLEAN_WEIGHTS) != oven_clean_range:
            self.OVEN_CLEAN_WEIGHTS = default_oven_weights
        
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
        
        self.STOP_AT_TARGET = config.get('stop_at_target', False)
        
        self.PRIORITY_STRATEGY = config.get('priority_strategy', 'ratio_batches')
        
        self.collect_gantt_data = collect_gantt_data
        self.all_batches = []
        self.cleaning_events = []
    
    def _get_weighted_cook_time(self, product):
        """Get a cook time using weighted distribution based on product type"""
        if product == 'WB':
            times = self.WB_COOK_TIMES
            weights = self.WB_COOK_WEIGHTS
        else:
            times = self.BB_COOK_TIMES
            weights = self.BB_COOK_WEIGHTS
        
        if not times:
            return 10.0  # Default fallback
        
        total_weight = sum(weights)
        if total_weight <= 0:
            return random.choice(times)
        
        # Normalize and select
        r = random.random() * total_weight
        cumulative = 0
        for i, w in enumerate(weights):
            cumulative += w
            if r <= cumulative:
                return times[i]
        
        return times[-1]
    
    def _get_weighted_oven_clean_time(self):
        """Get oven cleaning time using weighted distribution"""
        if self.OVEN_CLEAN_MIN >= self.OVEN_CLEAN_MAX:
            return self.OVEN_CLEAN_MIN
        
        weights = self.OVEN_CLEAN_WEIGHTS
        total_weight = sum(weights)
        if total_weight <= 0:
            return random.uniform(self.OVEN_CLEAN_MIN, self.OVEN_CLEAN_MAX)
        
        r = random.random() * total_weight
        cumulative = 0
        for i, w in enumerate(weights):
            cumulative += w
            if r <= cumulative:
                base_hour = self.OVEN_CLEAN_MIN + i
                return base_hour + random.random()
        
        return self.OVEN_CLEAN_MAX
    
    def _get_weighted_cure_time(self):
        """Get a cure time using weighted distribution"""
        weights = self.CURE_WEIGHTS
        total_weight = sum(weights)
        if total_weight <= 0:
            return random.uniform(self.CURE_WB_MIN, self.CURE_WB_MAX)
        
        # Normalize weights
        normalized = [w / total_weight for w in weights]
        
        # Random selection based on weights
        r = random.random()
        cumulative = 0
        for i, w in enumerate(normalized):
            cumulative += w
            if r <= cumulative:
                # Add some variation within the hour
                base_hour = self.CURE_WB_MIN + i
                return base_hour + random.random()  # e.g., 32.0 to 32.99
        
        # Fallback
        return self.CURE_WB_MAX
    
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
        
        # Form area is SHARED - only one team can use it at a time
        form_area_free = 0.0
        
        # Daily cleaning tracking - track actual time of last clean
        # Form area is shared, so only ONE cleaning time for both teams
        last_form_clean_time = -24.0  # Start needing clean
        last_oven1_clean_time = -24.0  # Track oven 1 cleaning separately
        last_oven2_clean_time = -24.0  # Track oven 2 cleaning separately
        
        # Cleaning events for Gantt chart
        cleaning_events = []
        
        def hours_since_form_clean(t):
            return t - last_form_clean_time
        
        def hours_since_oven1_clean(t):
            return t - last_oven1_clean_time
        
        def hours_since_oven2_clean(t):
            return t - last_oven2_clean_time
        
        def needs_form_clean(t):
            if not self.CLEANING_ENABLED:
                return False
            return hours_since_form_clean(t) >= 24.0
        
        def needs_oven1_clean(t):
            if not self.CLEANING_ENABLED:
                return False
            return hours_since_oven1_clean(t) >= 24.0
        
        def needs_oven2_clean(t):
            if not self.CLEANING_ENABLED:
                return False
            if self.NUM_OVEN_SETS < 2:
                return False
            return hours_since_oven2_clean(t) >= 24.0
        
        def must_clean_form_urgently(t):
            """Returns True if it's been 22+ hours - getting urgent"""
            if not self.CLEANING_ENABLED:
                return False
            return hours_since_form_clean(t) >= 22.0
        
        def must_clean_oven1_urgently(t):
            """Returns True if it's been 22+ hours - getting urgent"""
            if not self.CLEANING_ENABLED:
                return False
            return hours_since_oven1_clean(t) >= 22.0
        
        def must_clean_oven2_urgently(t):
            """Returns True if it's been 22+ hours - getting urgent"""
            if not self.CLEANING_ENABLED:
                return False
            if self.NUM_OVEN_SETS < 2:
                return False
            return hours_since_oven2_clean(t) >= 22.0
        
        def do_form_clean(team_num, t):
            nonlocal last_form_clean_time, form_area_free
            clean_end = t + self.FORM_CLEAN_TIME
            last_form_clean_time = t
            form_area_free = clean_end  # Form area blocked during cleaning
            if self.collect_gantt_data:
                cleaning_events.append({
                    'type': 'form_clean',
                    'team': team_num,
                    'start': t,
                    'end': clean_end
                })
            return clean_end
        
        def do_oven1_clean(team_num, t):
            nonlocal last_oven1_clean_time, oven1_free
            oven_clean_time = self._get_weighted_oven_clean_time()
            clean_end = t + oven_clean_time
            last_oven1_clean_time = t
            oven1_free = clean_end
            if self.collect_gantt_data:
                cleaning_events.append({
                    'type': 'oven_clean',
                    'team': team_num,
                    'oven_set': 1,
                    'start': t,
                    'end': clean_end
                })
            return clean_end
        
        def do_oven2_clean(team_num, t):
            nonlocal last_oven2_clean_time, oven2_free
            if self.NUM_OVEN_SETS < 2:
                return t  # No oven 2, return immediately
            oven_clean_time = self._get_weighted_oven_clean_time()
            clean_end = t + oven_clean_time
            last_oven2_clean_time = t
            oven2_free = clean_end
            if self.collect_gantt_data:
                cleaning_events.append({
                    'type': 'oven_clean',
                    'team': team_num,
                    'oven_set': 2,
                    'start': t,
                    'end': clean_end
                })
            return clean_end
        
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
            count = len([b for b in batches if b.product == 'WB' and (b.cut_end is None or b.cut_end > time)])
            # If stop_at_target is enabled and WB target is hit, return max to block new WB
            if self.STOP_AT_TARGET and total_wb >= self.WB_TARGET:
                return self.WB_SHEETS  # Return max sheets to block forming new WB
            return count
        
        def active_bb():
            count = len([b for b in batches if b.product == 'BB' and (b.cut_end is None or b.cut_end > time)])
            # If stop_at_target is enabled and BB target is hit, return max to block new BB
            if self.STOP_AT_TARGET and total_bb >= self.BB_TARGET:
                return self.BB_SHEETS  # Return max sheets to block forming new BB
            return count
        
        def curing_wb():
            return len([b for b in batches if b.product == 'WB' 
                       and b.cure_end > time and b.cut_end is None])
        
        def bb_cutting_machine_busy(exclude_set):
            """Check if BB cutting machine is in use (including paused BB cuts)
            Returns the batch if there's a paused BB, or True if actively cutting, or False if free"""
            # First check if any BB is currently being cut in this iteration
            for b in batches:
                if b.product == 'BB' and b.id in exclude_set:
                    return b  # Being cut right now
            # Then check for paused BB cuts
            for b in batches:
                if b.product == 'BB' and b.cut_progress > 0 and b.cut_end is None:
                    # BB has started cutting but not finished
                    return b  # Return the batch so we can prioritize it
            return None
        
        def ready_to_cut(exclude, team_num=None):
            bb_in_progress = bb_cutting_machine_busy(exclude)
            ready = []
            for b in batches:
                if b.cure_end <= time and b.cut_end is None and b.id not in exclude:
                    # If ANY BB is being cut or in progress, skip ALL other BBs
                    if b.product == 'BB' and bb_in_progress is not None:
                        # Only allow this BB if it's THE one in progress
                        if bb_in_progress != b:
                            continue
                    ready.append(b)
            
            def sort_key(b):
                # Highest priority: BB that's already in progress (must finish on BB machine)
                if b.product == 'BB' and b.cut_progress > 0:
                    return (0, -b.cut_progress, b.cure_end)  # More progress = higher priority
                # Second priority: Any batch already in progress (continue what we started)
                # Prefer the one with MORE progress (closer to being done)
                if b.cut_progress > 0:
                    return (1, -b.cut_progress, b.cure_end)
                # Default: oldest batch first (FIFO)
                return (2, 0, b.cure_end)
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
            nonlocal batch_id, oven1_free, oven2_free, wb_batches_formed, bb_batches_formed, form_area_free
            b = Batch(batch_id, product)
            batch_id += 1
            
            b.form_start = time
            b.form_end = time + self.FORM_TIME
            b.formed_by = team_num
            b.oven_set = oven_num  # Track which oven set is used
            
            # Form area is blocked until forming is done
            form_area_free = b.form_end
            
            # Randomly select cook time from available options based on weights
            cook_time = self._get_weighted_cook_time(product)
            
            # Batch goes straight into oven after forming
            # (caller must ensure oven will be free by form_end)
            b.cook_start = b.form_end
            b.cook_end = b.cook_start + cook_time
            
            if product == 'WB':
                # Use weighted random for cure time
                b.cure_time = self._get_weighted_cure_time()
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
            # Can only form if: deadline passed AND form area is free
            can_form = time >= deadline and form_area_free <= time
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
            
            # Check cleaning needs (time-based: 24+ hours since last clean)
            # Form area is SHARED - only one cleaning needed for both teams
            # Ovens are cleaned INDEPENDENTLY
            form_clean_needed = needs_form_clean(time)
            oven1_clean_needed = needs_oven1_clean(time)
            oven2_clean_needed = needs_oven2_clean(time)  # Returns False if only 1 oven set
            form_clean_urgent = must_clean_form_urgently(time)
            oven1_clean_urgent = must_clean_oven1_urgently(time)
            oven2_clean_urgent = must_clean_oven2_urgently(time)  # Returns False if only 1 oven set
            
            def get_best_oven():
                """Returns (oven_num, oven_free_time) for the oven that will be free soonest"""
                if self.NUM_OVEN_SETS == 2:
                    if oven1_free <= oven2_free:
                        return (1, oven1_free)
                    else:
                        return (2, oven2_free)
                return (1, oven1_free)
            
            def any_oven_free():
                """Check if any oven is currently free"""
                if self.NUM_OVEN_SETS == 2:
                    return oven1_free <= time or oven2_free <= time
                return oven1_free <= time
            
            def get_free_oven_needing_clean():
                """Get the number of a currently free oven that needs cleaning, or None"""
                if oven1_clean_needed and oven1_free <= time:
                    return 1
                if self.NUM_OVEN_SETS == 2 and oven2_clean_needed and oven2_free <= time:
                    return 2
                return None
            
            def get_urgent_oven_not_free():
                """Get an oven that urgently needs cleaning but isn't free, or None"""
                if oven1_clean_urgent and oven1_free > time:
                    return (1, oven1_free)
                if self.NUM_OVEN_SETS == 2 and oven2_clean_urgent and oven2_free > time:
                    return (2, oven2_free)
                return None
            
            # TEAM 1 WORK - Handles all forming and cleaning, cuts when idle
            if team1_free <= time:
                # PRIORITY 1: Form cleaning if 24+ hours since last clean AND form area is free
                if form_clean_needed and form_area_free <= time:
                    team1_free = do_form_clean(1, time)
                # PRIORITY 2: Oven cleaning if 24+ hours AND that specific oven is free
                elif get_free_oven_needing_clean() is not None:
                    oven_to_clean = get_free_oven_needing_clean()
                    if oven_to_clean == 1:
                        team1_free = do_oven1_clean(1, time)
                    else:
                        team1_free = do_oven2_clean(1, time)
                # PRIORITY 3: If any oven cleaning is URGENT (22+ hrs) and that oven not free
                elif get_urgent_oven_not_free() is not None:
                    urgent_oven, urgent_oven_free = get_urgent_oven_not_free()
                    wait_time = urgent_oven_free - time
                    ready = ready_to_cut(being_cut, 1)
                    if ready:
                        # Cut while waiting for oven (partial cut if needed)
                        b = ready[0]
                        being_cut.add(b.id)
                        if b.cut_by is None:
                            b.cut_by = 1
                        remaining = self.CUT_TIME - b.cut_progress
                        work = min(wait_time, remaining)
                        is_partial = (work < remaining)
                        cut(b, work, 1, is_partial=is_partial)
                        team1_free = time + work
                    else:
                        # Nothing to cut - wait for oven
                        team1_free = urgent_oven_free
                else:
                    # Check what we can do
                    ready = ready_to_cut(being_cut, 1)
                    
                    # Get the best oven (soonest to be free)
                    best_oven, best_oven_free = get_best_oven()
                    
                    # Can form if:
                    # 1. Form area is free
                    # 2. Sheets available  
                    # 3. Best oven will be free by the time forming finishes
                    sheets_available = (active_wb() < self.WB_SHEETS or active_bb() < self.BB_SHEETS)
                    oven_ready_after_form = best_oven_free <= (time + self.FORM_TIME)
                    can_form = (form_area_free <= time) and sheets_available and oven_ready_after_form
                    
                    # Time until we should start forming (so batch is ready when oven is free)
                    time_to_start_forming = max(0, best_oven_free - self.FORM_TIME - time)
                    
                    if can_form:
                        # Oven will be ready when forming finishes - form now!
                        wb_priority = get_priority()
                        if wb_priority:
                            if active_wb() < self.WB_SHEETS:
                                result = form('WB', best_oven, 1)
                                team1_free = result
                            elif active_bb() < self.BB_SHEETS:
                                result = form('BB', best_oven, 1)
                                team1_free = result
                            else:
                                # All sheets in use
                                if ready:
                                    b = ready[0]
                                    being_cut.add(b.id)
                                    if b.cut_by is None:
                                        b.cut_by = 1
                                    remaining = self.CUT_TIME - b.cut_progress
                                    cut(b, remaining, 1, is_partial=False)
                                    team1_free = time + remaining
                                else:
                                    next_events = [self.TOTAL_HOURS, form_area_free, best_oven_free]
                                    if self.NUM_OVEN_SETS == 2:
                                        next_events.append(oven2_free)
                                    for b in batches:
                                        if b.cure_end > time and b.cut_end is None:
                                            next_events.append(b.cure_end)
                                    team1_free = min(e for e in next_events if e > time)
                        else:
                            if active_bb() < self.BB_SHEETS:
                                result = form('BB', best_oven, 1)
                                team1_free = result
                            elif active_wb() < self.WB_SHEETS:
                                result = form('WB', best_oven, 1)
                                team1_free = result
                            else:
                                if ready:
                                    b = ready[0]
                                    being_cut.add(b.id)
                                    if b.cut_by is None:
                                        b.cut_by = 1
                                    remaining = self.CUT_TIME - b.cut_progress
                                    cut(b, remaining, 1, is_partial=False)
                                    team1_free = time + remaining
                                else:
                                    next_events = [self.TOTAL_HOURS, form_area_free, best_oven_free]
                                    if self.NUM_OVEN_SETS == 2:
                                        next_events.append(oven2_free)
                                    for b in batches:
                                        if b.cure_end > time and b.cut_end is None:
                                            next_events.append(b.cure_end)
                                    team1_free = min(e for e in next_events if e > time)
                    elif ready and time_to_start_forming > 0.5:
                        # Oven not ready yet, cut while waiting
                        b = ready[0]
                        being_cut.add(b.id)
                        if b.cut_by is None:
                            b.cut_by = 1
                        remaining = self.CUT_TIME - b.cut_progress
                        
                        # Stop cutting in time to form
                        if time_to_start_forming < remaining:
                            cut(b, time_to_start_forming, 1, is_partial=True)
                            team1_free = time + time_to_start_forming
                        else:
                            cut(b, remaining, 1, is_partial=False)
                            team1_free = time + remaining
                    elif ready:
                        # Can cut but forming time is soon - just cut normally
                        b = ready[0]
                        being_cut.add(b.id)
                        if b.cut_by is None:
                            b.cut_by = 1
                        remaining = self.CUT_TIME - b.cut_progress
                        cut(b, remaining, 1, is_partial=False)
                        team1_free = time + remaining
                    else:
                        # Nothing to do - wait for next event
                        next_events = [self.TOTAL_HOURS, form_area_free]
                        # Wait until it's time to start forming
                        if sheets_available:
                            next_events.append(best_oven_free - self.FORM_TIME)
                        for b in batches:
                            if b.cure_end > time and b.cut_end is None:
                                next_events.append(b.cure_end)
                        team1_free = min(e for e in next_events if e > time)
            
            # TEAM 2 WORK
            # In 2team_6-6 mode: Cutting only (no forming, no cleaning)
            # In 2team_24/7 mode: Full capability (forming, cleaning, cutting)
            if team2_enabled():
                if not team2_on(time):
                    team2_free = next_team2_start(time)
                elif team2_free <= time:
                    if self.TEAM_CONFIG == '2team_24/7':
                        # Full capability mode - Team 2 can form, clean, and cut
                        # Similar logic to Team 1 but uses oven 2 if available
                        
                        # PRIORITY 1: Form cleaning if needed and form area is free
                        if form_clean_needed and form_area_free <= time:
                            team2_free = do_form_clean(2, time)
                        # PRIORITY 2: Oven 2 cleaning if needed and oven 2 is free
                        elif self.NUM_OVEN_SETS == 2 and needs_oven2_clean(time) and oven2_free <= time:
                            team2_free = do_oven2_clean(2, time)
                        # PRIORITY 3: Oven 1 cleaning if oven 2 doesn't exist or doesn't need cleaning
                        elif oven1_clean_needed and oven1_free <= time:
                            team2_free = do_oven1_clean(2, time)
                        else:
                            # Check what we can do
                            ready = ready_to_cut(being_cut, 2)
                            
                            # Get the best oven for Team 2
                            # Prefer oven 2 if we have 2 sets, otherwise use oven 1
                            if self.NUM_OVEN_SETS == 2:
                                t2_best_oven = 2
                                t2_best_oven_free = oven2_free
                                # But if oven 1 is free sooner and oven 2 is busy, use oven 1
                                if oven1_free < oven2_free:
                                    t2_best_oven = 1
                                    t2_best_oven_free = oven1_free
                            else:
                                t2_best_oven = 1
                                t2_best_oven_free = oven1_free
                            
                            sheets_available = (active_wb() < self.WB_SHEETS or active_bb() < self.BB_SHEETS)
                            oven_ready_after_form = t2_best_oven_free <= (time + self.FORM_TIME)
                            can_form = (form_area_free <= time) and sheets_available and oven_ready_after_form
                            
                            time_to_start_forming = max(0, t2_best_oven_free - self.FORM_TIME - time)
                            
                            if can_form:
                                wb_priority = get_priority()
                                if wb_priority:
                                    if active_wb() < self.WB_SHEETS:
                                        result = form('WB', t2_best_oven, 2)
                                        team2_free = result
                                    elif active_bb() < self.BB_SHEETS:
                                        result = form('BB', t2_best_oven, 2)
                                        team2_free = result
                                    else:
                                        # All sheets in use - cut if possible
                                        if ready:
                                            b = ready[0]
                                            being_cut.add(b.id)
                                            if b.cut_by is None:
                                                b.cut_by = 2
                                            remaining = self.CUT_TIME - b.cut_progress
                                            cut(b, remaining, 2, is_partial=False)
                                            team2_free = time + remaining
                                        else:
                                            next_events = [self.TOTAL_HOURS, form_area_free, t2_best_oven_free]
                                            for b in batches:
                                                if b.cure_end > time and b.cut_end is None:
                                                    next_events.append(b.cure_end)
                                            team2_free = min(e for e in next_events if e > time)
                                else:
                                    if active_bb() < self.BB_SHEETS:
                                        result = form('BB', t2_best_oven, 2)
                                        team2_free = result
                                    elif active_wb() < self.WB_SHEETS:
                                        result = form('WB', t2_best_oven, 2)
                                        team2_free = result
                                    else:
                                        if ready:
                                            b = ready[0]
                                            being_cut.add(b.id)
                                            if b.cut_by is None:
                                                b.cut_by = 2
                                            remaining = self.CUT_TIME - b.cut_progress
                                            cut(b, remaining, 2, is_partial=False)
                                            team2_free = time + remaining
                                        else:
                                            next_events = [self.TOTAL_HOURS, form_area_free, t2_best_oven_free]
                                            for b in batches:
                                                if b.cure_end > time and b.cut_end is None:
                                                    next_events.append(b.cure_end)
                                            team2_free = min(e for e in next_events if e > time)
                            elif ready and time_to_start_forming > 0.5:
                                # Cut while waiting for oven
                                b = ready[0]
                                being_cut.add(b.id)
                                if b.cut_by is None:
                                    b.cut_by = 2
                                remaining = self.CUT_TIME - b.cut_progress
                                if time_to_start_forming < remaining:
                                    cut(b, time_to_start_forming, 2, is_partial=True)
                                    team2_free = time + time_to_start_forming
                                else:
                                    cut(b, remaining, 2, is_partial=False)
                                    team2_free = time + remaining
                            elif ready:
                                b = ready[0]
                                being_cut.add(b.id)
                                if b.cut_by is None:
                                    b.cut_by = 2
                                remaining = self.CUT_TIME - b.cut_progress
                                cut(b, remaining, 2, is_partial=False)
                                team2_free = time + remaining
                            else:
                                next_events = [self.TOTAL_HOURS, form_area_free]
                                if sheets_available:
                                    next_events.append(t2_best_oven_free - self.FORM_TIME)
                                for b in batches:
                                    if b.cure_end > time and b.cut_end is None:
                                        next_events.append(b.cure_end)
                                team2_free = min(e for e in next_events if e > time)
                    else:
                        # 2team_6-6 mode: Team 2 only cuts - no forming, no cleaning
                        ready = ready_to_cut(being_cut, 2)
                        shift_end = team2_shift_end(time)
                        time_until_shift_end = shift_end - time if shift_end != float('inf') else float('inf')
                        
                        if ready:
                            b = ready[0]
                            remaining = self.CUT_TIME - b.cut_progress
                            
                            # Don't start a NEW cut if shift ends in < 30 min
                            if time_until_shift_end < 0.5 and b.cut_progress == 0:
                                team2_free = next_team2_start(shift_end)
                            elif time_until_shift_end < remaining:
                                # Partial cut until shift ends
                                being_cut.add(b.id)
                                if b.cut_by is None:
                                    b.cut_by = 2
                                cut(b, time_until_shift_end, 2, is_partial=True)
                                team2_free = next_team2_start(shift_end)
                            else:
                                being_cut.add(b.id)
                                if b.cut_by is None:
                                    b.cut_by = 2
                                cut(b, remaining, 2, is_partial=False)
                                team2_free = time + remaining
                        else:
                            # No batches to cut - find next event to wake up at
                            next_events = [self.TOTAL_HOURS, shift_end]
                            for b in batches:
                                if b.cure_end > time and b.cut_end is None:
                                    next_events.append(b.cure_end)
                            team2_free = min(e for e in next_events if e > time)
            
            events = [self.TOTAL_HOURS, team1_free, oven1_free, oven1_free - self.FORM_TIME, form_area_free]
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
            self.cleaning_events = cleaning_events
        
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
        sim = ProductionSimulator(config, collect_gantt_data=True)
        result = sim.simulate()
        
        # Analyze bottlenecks with what-if testing if targets not met
        bottleneck = analyze_bottleneck_with_whatif(config, result)
        
        return jsonify({
            'success': True,
            'result': result,
            'bottleneck': bottleneck,
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


def analyze_bottleneck_with_whatif(config, result):
    """Analyze bottlenecks by testing what-if scenarios"""
    wb_pct = result['wb_pct']
    bb_pct = result['bb_pct']
    
    # Calculate current score (squared distance from 100% - lower is better)
    current_score = (100 - wb_pct) ** 2 + (100 - bb_pct) ** 2
    
    # If both targets met (or very close), no bottleneck
    if wb_pct >= 100 and bb_pct >= 100:
        return {
            'status': 'targets_met',
            'message': 'All production targets have been met!',
            'whatif_results': [],
            'suggestions': [],
        }
    
    # Run what-if scenarios
    whatif_results = []
    
    # Get current config values
    current_ovens = config.get('num_ovens', 5)
    current_team = config.get('team_config', '1team')
    current_wb_sheets = config.get('wb_sheets', 3)
    current_bb_sheets = config.get('bb_sheets', 2)
    
    # Test 1: Add 1 oven
    if current_ovens < 20:
        test_config = {**config, 'num_ovens': current_ovens + 1}
        test_result = ProductionSimulator(test_config).simulate()
        new_score = (100 - test_result['wb_pct']) ** 2 + (100 - test_result['bb_pct']) ** 2
        score_improvement = current_score - new_score  # Positive = better
        wb_change = test_result['wb_pct'] - wb_pct
        bb_change = test_result['bb_pct'] - bb_pct
        whatif_results.append({
            'change': f'Add 1 oven ({current_ovens} → {current_ovens + 1})',
            'type': 'oven',
            'score_improvement': round(score_improvement, 1),
            'wb_change': round(wb_change, 1),
            'bb_change': round(bb_change, 1),
            'new_wb_pct': round(test_result['wb_pct'], 1),
            'new_bb_pct': round(test_result['bb_pct'], 1),
            'meets_targets': test_result['wb_pct'] >= 100 and test_result['bb_pct'] >= 100,
        })
    
    # Test 2: Upgrade team configuration
    team_upgrades = {
        '1team': '2team_6-6',
        '2team_6-6': '2team_24-7',
    }
    if current_team in team_upgrades:
        new_team = team_upgrades[current_team]
        test_config = {**config, 'team_config': new_team}
        test_result = ProductionSimulator(test_config).simulate()
        new_score = (100 - test_result['wb_pct']) ** 2 + (100 - test_result['bb_pct']) ** 2
        score_improvement = current_score - new_score
        wb_change = test_result['wb_pct'] - wb_pct
        bb_change = test_result['bb_pct'] - bb_pct
        
        team_labels = {
            '2team_6-6': '2 Teams (6am-6pm)',
            '2team_24-7': '2 Teams (24/7)',
        }
        whatif_results.append({
            'change': f'Upgrade to {team_labels.get(new_team, new_team)}',
            'type': 'team',
            'score_improvement': round(score_improvement, 1),
            'wb_change': round(wb_change, 1),
            'bb_change': round(bb_change, 1),
            'new_wb_pct': round(test_result['wb_pct'], 1),
            'new_bb_pct': round(test_result['bb_pct'], 1),
            'meets_targets': test_result['wb_pct'] >= 100 and test_result['bb_pct'] >= 100,
        })
    
    # Test 3: Add 1 WB sheet
    if current_wb_sheets < 10:
        test_config = {**config, 'wb_sheets': current_wb_sheets + 1}
        test_result = ProductionSimulator(test_config).simulate()
        new_score = (100 - test_result['wb_pct']) ** 2 + (100 - test_result['bb_pct']) ** 2
        score_improvement = current_score - new_score
        wb_change = test_result['wb_pct'] - wb_pct
        bb_change = test_result['bb_pct'] - bb_pct
        whatif_results.append({
            'change': f'Add 1 WB sheet ({current_wb_sheets} → {current_wb_sheets + 1})',
            'type': 'wb_sheet',
            'score_improvement': round(score_improvement, 1),
            'wb_change': round(wb_change, 1),
            'bb_change': round(bb_change, 1),
            'new_wb_pct': round(test_result['wb_pct'], 1),
            'new_bb_pct': round(test_result['bb_pct'], 1),
            'meets_targets': test_result['wb_pct'] >= 100 and test_result['bb_pct'] >= 100,
        })
    
    # Test 4: Add 1 BB sheet
    if current_bb_sheets < 10:
        test_config = {**config, 'bb_sheets': current_bb_sheets + 1}
        test_result = ProductionSimulator(test_config).simulate()
        new_score = (100 - test_result['wb_pct']) ** 2 + (100 - test_result['bb_pct']) ** 2
        score_improvement = current_score - new_score
        wb_change = test_result['wb_pct'] - wb_pct
        bb_change = test_result['bb_pct'] - bb_pct
        whatif_results.append({
            'change': f'Add 1 BB sheet ({current_bb_sheets} → {current_bb_sheets + 1})',
            'type': 'bb_sheet',
            'score_improvement': round(score_improvement, 1),
            'wb_change': round(wb_change, 1),
            'bb_change': round(bb_change, 1),
            'new_wb_pct': round(test_result['wb_pct'], 1),
            'new_bb_pct': round(test_result['bb_pct'], 1),
            'meets_targets': test_result['wb_pct'] >= 100 and test_result['bb_pct'] >= 100,
        })
    
    # Sort by score improvement (highest first = best improvement)
    whatif_results.sort(key=lambda x: x['score_improvement'], reverse=True)
    
    # Generate suggestions based on results
    suggestions = []
    primary_bottleneck = None
    
    if whatif_results:
        best = whatif_results[0]
        
        if best['score_improvement'] > 0:
            # Determine primary bottleneck based on what helps most
            if best['type'] == 'oven':
                primary_bottleneck = {
                    'type': 'oven',
                    'severity': 'high' if best['score_improvement'] > 500 else 'medium',
                    'message': 'Oven capacity is limiting production',
                    'detail': f'Adding 1 oven: WB {best["wb_change"]:+.1f}%, BB {best["bb_change"]:+.1f}%'
                }
                suggestions.append(f'Add 1 oven (WB {best["wb_change"]:+.1f}%, BB {best["bb_change"]:+.1f}%)')
            elif best['type'] == 'team':
                primary_bottleneck = {
                    'type': 'labor',
                    'severity': 'high' if best['score_improvement'] > 500 else 'medium',
                    'message': 'Worker capacity is limiting production',
                    'detail': f'Adding workers: WB {best["wb_change"]:+.1f}%, BB {best["bb_change"]:+.1f}%'
                }
                suggestions.append(f'Upgrade team (WB {best["wb_change"]:+.1f}%, BB {best["bb_change"]:+.1f}%)')
            elif best['type'] == 'wb_sheet':
                primary_bottleneck = {
                    'type': 'wb_sheets',
                    'severity': 'high' if best['score_improvement'] > 500 else 'medium',
                    'message': 'WB sheet limit is constraining production',
                    'detail': f'Adding 1 WB sheet: WB {best["wb_change"]:+.1f}%, BB {best["bb_change"]:+.1f}%'
                }
                suggestions.append(f'Add 1 WB sheet (WB {best["wb_change"]:+.1f}%, BB {best["bb_change"]:+.1f}%)')
            elif best['type'] == 'bb_sheet':
                primary_bottleneck = {
                    'type': 'bb_sheets',
                    'severity': 'high' if best['score_improvement'] > 500 else 'medium',
                    'message': 'BB sheet limit is constraining production',
                    'detail': f'Adding 1 BB sheet: WB {best["wb_change"]:+.1f}%, BB {best["bb_change"]:+.1f}%'
                }
                suggestions.append(f'Add 1 BB sheet (WB {best["wb_change"]:+.1f}%, BB {best["bb_change"]:+.1f}%)')
        
        # Add other helpful changes as suggestions
        for item in whatif_results[1:4]:  # Next 3 best options
            if item['score_improvement'] > 0:
                suggestions.append(f'{item["change"]}: WB {item["wb_change"]:+.1f}%, BB {item["bb_change"]:+.1f}%')
        
        # Check if any single change meets targets
        meets_target = [w for w in whatif_results if w['meets_targets']]
        if meets_target:
            suggestions.insert(0, f"✓ '{meets_target[0]['change']}' would meet both targets!")
    
    # If no improvements found, suggest strategy change
    if not whatif_results or all(w['score_improvement'] <= 0 for w in whatif_results):
        suggestions.append('Try different priority strategies to optimize production balance')
        if wb_pct < bb_pct:
            suggestions.append('WB is lagging - try wb_first, cure_aware, or balanced_goal strategies')
        else:
            suggestions.append('BB is lagging - try bb_first or ratio_batches strategies')
    
    return {
        'status': 'bottleneck_found' if primary_bottleneck else 'analysis_complete',
        'primary': primary_bottleneck,
        'whatif_results': whatif_results,
        'suggestions': suggestions[:5],
        'current_production': {
            'wb_pct': round(wb_pct, 1),
            'bb_pct': round(bb_pct, 1),
        }
    }


@app.route('/test-strategies', methods=['POST'])
def test_strategies():
    """Test all strategies and return comparison"""
    config = request.json or {}
    
    strategies = ['ratio', 'ratio_batches', 'wb_first', 'bb_first', 'adaptive', 
                  'cure_aware', 'goal_focused', 'wb_until_done', 'balanced_goal']
    
    # Strategies to exclude from auto-recommendation (cause long wait times)
    excluded_from_recommendation = {'wb_first', 'bb_first'}
    
    results = []
    wb_target = config.get('wb_target', 1500000)
    bb_target = config.get('bb_target', 2500000)
    
    for strategy in strategies:
        test_config = {**config, 'priority_strategy': strategy}
        mc = run_monte_carlo(test_config, runs=20)  # Fewer runs for speed
        
        wb_pct = mc['avg_wb_pct']
        bb_pct = mc['avg_bb_pct']
        min_pct = min(wb_pct, bb_pct)
        avg_total = mc['avg_total']
        avg_wb = mc['avg_wb']
        avg_bb = mc['avg_bb']
        
        # Score: How close are we to hitting BOTH targets?
        # Perfect score = both at 100%, penalize being under OR over
        # Use distance from 100% for each product
        wb_distance = abs(100 - wb_pct)  # 0 = perfect, higher = worse
        bb_distance = abs(100 - bb_pct)  # 0 = perfect, higher = worse
        
        # Combined score: lower distance = better
        # We want to minimize total distance from targets
        # Invert so higher score = better (for sorting)
        # Max possible distance is ~200 (if both are 0% or 200%)
        score = 200 - (wb_distance + bb_distance)
        
        # Bonus: if both targets are met (>=100%), add bonus points
        both_met = wb_pct >= 100 and bb_pct >= 100
        if both_met:
            score += 50
        
        results.append({
            'strategy': strategy,
            'avg_wb': avg_wb,
            'avg_bb': avg_bb,
            'avg_total': avg_total,
            'wb_pct': wb_pct,
            'bb_pct': bb_pct,
            'min_pct': min_pct,
            'wb_distance': wb_distance,
            'bb_distance': bb_distance,
            'both_met': both_met,
            'score': score,
            'excluded_from_auto': strategy in excluded_from_recommendation
        })
    
    # Sort by score (highest = closest to both targets)
    results.sort(key=lambda x: x['score'], reverse=True)
    
    # Find best strategy (excluding wb_first and bb_first)
    best = None
    for r in results:
        if r['strategy'] not in excluded_from_recommendation:
            best = r['strategy']
            break
    
    # Fallback if somehow all are excluded
    if best is None:
        best = results[0]['strategy']
    
    return jsonify({
        'success': True,
        'strategies': results,  # Full list for frontend
        'results': results,     # Keep for backward compatibility
        'recommendation': best,
        'config': {
            'wb_target': wb_target,
            'bb_target': bb_target,
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
    return jsonify({
        'status': 'healthy',
        'message': 'GM Line Production API is running',
        'version': '1.0'
    })


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
                    # Check if this batch has multiple sessions total (was paused/resumed)
                    total_sessions = len(b.cut_sessions)
                    is_paused_batch = total_sessions > 1
                    
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
                            
                            color = colors['cut_wb'] if product == 'WB' else colors['cut_bb']
                            
                            for j, sess in enumerate(merged):
                                s = max(sess[0], start_hour)
                                e = min(sess[1], end_hour)
                                # Show as paused if: batch has multiple sessions AND this isn't the final session
                                is_final_session = (j == len(merged) - 1) and (sess[1] >= b.cut_end - 0.01 if b.cut_end else False)
                                show_paused = is_paused_batch and not is_final_session
                                if show_paused:
                                    ax.barh(y, e - s, left=s, height=0.6, color=color, edgecolor='black', 
                                           linewidth=0.5, hatch='///', alpha=0.8)
                                else:
                                    ax.barh(y, e - s, left=s, height=0.6, color=color, edgecolor='black', linewidth=0.5)
                                
                                bar_width = e - s
                                fontsize = 8 if bar_width > 5 else (6 if bar_width > 2 else 5)
                                ax.text((s + e) / 2, y, f'{product}{b.id}', ha='center', va='center', 
                                       fontsize=fontsize, color='white')
            
            # Draw cleaning events
            cleaning_events = getattr(sim, 'cleaning_events', [])
            for event in cleaning_events:
                event_start = event['start']
                event_end = event['end']
                event_type = event['type']
                team = event['team']
                
                if event_start >= end_hour or event_end <= start_hour:
                    continue
                
                s = max(event_start, start_hour)
                e = min(event_end, end_hour)
                
                if event_type == 'form_clean':
                    # Draw on form row
                    for i, (label, stage, team_filter) in enumerate(rows):
                        if stage == 'form':
                            if team_filter is None or team_filter == team:
                                y = y_positions[i]
                                ax.barh(y, e - s, left=s, height=0.6, color='#FFB6C1', 
                                       edgecolor='red', linewidth=1.5, hatch='\\\\')
                                if e - s > 1:
                                    ax.text((s + e) / 2, y, 'CLEAN', ha='center', va='center', 
                                           fontsize=6, color='darkred', fontweight='bold')
                
                elif event_type == 'oven_clean':
                    # Draw on cook/oven row - only on the specific oven that was cleaned
                    oven_set = event.get('oven_set', 1)
                    for i, (label, stage, team_filter) in enumerate(rows):
                        if stage == 'cook':
                            # team_filter here is actually oven_set filter for cook rows
                            if team_filter is None or team_filter == oven_set:
                                y = y_positions[i]
                                ax.barh(y, e - s, left=s, height=0.6, color='#DDA0DD', 
                                       edgecolor='purple', linewidth=1.5, hatch='\\\\')
                                if e - s > 1:
                                    ax.text((s + e) / 2, y, 'CLEAN', ha='center', va='center', 
                                           fontsize=6, color='purple', fontweight='bold')
            
            ax.set_yticks(y_positions)
            ax.set_yticklabels(y_labels)
            ax.set_xlim(start_hour, end_hour)
            ax.set_xlabel('Hours')
            
            # Draw grid lines: light grey every 8 hours, dark grey every 24 hours
            for hour in range(int(start_hour), int(end_hour) + 1, 8):
                if hour >= start_hour and hour <= end_hour:
                    if hour % 24 == 0:
                        # Dark grey for 24-hour marks (drawn second to take priority)
                        pass  # Will draw below
                    else:
                        # Light grey for 8-hour marks
                        ax.axvline(x=hour, color='lightgrey', linestyle='-', alpha=0.5, linewidth=0.8)
            
            # Draw 24-hour lines on top (dark grey)
            for hour in range(int(start_hour // 24) * 24, int(end_hour) + 1, 24):
                if hour >= start_hour and hour <= end_hour:
                    ax.axvline(x=hour, color='darkgrey', linestyle='-', alpha=0.8, linewidth=1.2)
            
            # Draw Team 2 working hours (dotted lines) if Team 2 is enabled
            if has_team2 and sim.TEAM_CONFIG == '2team_6-6':
                team2_start = sim.TEAM2_START  # e.g., 6
                team2_end = sim.TEAM2_END      # e.g., 18
                
                # For each day in the range, draw dotted lines at shift start/end
                for day in range(int(start_hour // 24), int(end_hour // 24) + 2):
                    shift_start = day * 24 + team2_start
                    shift_end = day * 24 + team2_end
                    
                    if shift_start >= start_hour and shift_start <= end_hour:
                        ax.axvline(x=shift_start, color='green', linestyle=':', alpha=0.7, linewidth=1.5)
                    if shift_end >= start_hour and shift_end <= end_hour:
                        ax.axvline(x=shift_end, color='green', linestyle=':', alpha=0.7, linewidth=1.5)
            
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
                mpatches.Patch(facecolor='#FFB6C1', edgecolor='red', hatch='\\\\', label='Form Clean'),
                mpatches.Patch(facecolor='#DDA0DD', edgecolor='purple', hatch='\\\\', label='Oven Clean'),
            ]
            ax.legend(handles=legend_elements, loc='upper right', fontsize=8, ncol=2)
            
        else:  # workers chart
            if has_team2:
                rows = [('Team 1', 1), ('Team 2', 2)]
            else:
                rows = [('Team 1', 1)]
            
            fig, ax = plt.subplots(figsize=(20, len(rows) * 1.5 + 2))
            
            y_labels = [r[0] for r in rows]
            y_positions = list(range(len(rows) - 1, -1, -1))
            
            # Draw cleaning events first (so they appear behind batches)
            cleaning_events = getattr(sim, 'cleaning_events', [])
            for event in cleaning_events:
                if event['start'] >= end_hour or event['end'] <= start_hour:
                    continue
                
                team = event.get('team', 1)
                event_type = event.get('type', '')
                
                for i, (label, team_num) in enumerate(rows):
                    if team_num == team:
                        y = y_positions[i]
                        s = max(event['start'], start_hour)
                        e = min(event['end'], end_hour)
                        
                        if event_type == 'form_clean':
                            color = '#FFB6C1'  # Light pink
                            edge_color = '#DC143C'
                            ax.barh(y, e - s, left=s, height=0.7, color=color, 
                                   edgecolor=edge_color, linewidth=1, hatch='\\\\', alpha=0.8)
                            if e - s > 2:
                                ax.text((s + e) / 2, y, 'FORM\nCLEAN', ha='center', va='center', 
                                       fontsize=6, fontweight='bold', color=edge_color)
                        elif event_type == 'oven_clean':
                            color = '#DDA0DD'  # Plum
                            edge_color = '#8B008B'
                            ax.barh(y, e - s, left=s, height=0.7, color=color,
                                   edgecolor=edge_color, linewidth=1, hatch='\\\\', alpha=0.8)
                            if e - s > 2:
                                ax.text((s + e) / 2, y, 'OVEN\nCLEAN', ha='center', va='center',
                                       fontsize=6, fontweight='bold', color=edge_color)
            
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
                    # Check if this batch has multiple sessions total (was paused/resumed)
                    total_sessions = len(b.cut_sessions)
                    is_paused_batch = total_sessions > 1
                    
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
                        
                        color = colors['cut_wb'] if product == 'WB' else colors['cut_bb']
                        
                        for j, sess in enumerate(merged):
                            s = max(sess[0], start_hour)
                            e = min(sess[1], end_hour)
                            # Show as paused if: batch has multiple sessions AND this isn't the final session
                            is_final_session = (j == len(merged) - 1) and (sess[1] >= b.cut_end - 0.01 if b.cut_end else False)
                            show_paused = is_paused_batch and not is_final_session
                            if show_paused:
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
            
            # Draw grid lines: light grey every 8 hours, dark grey every 24 hours
            for hour in range(int(start_hour), int(end_hour) + 1, 8):
                if hour >= start_hour and hour <= end_hour:
                    if hour % 24 == 0:
                        pass  # Will draw below
                    else:
                        ax.axvline(x=hour, color='lightgrey', linestyle='-', alpha=0.5, linewidth=0.8)
            
            # Draw 24-hour lines on top (dark grey)
            for hour in range(int(start_hour // 24) * 24, int(end_hour) + 1, 24):
                if hour >= start_hour and hour <= end_hour:
                    ax.axvline(x=hour, color='darkgrey', linestyle='-', alpha=0.8, linewidth=1.2)
            
            # Draw Team 2 working hours (dotted lines) if Team 2 is enabled
            if has_team2 and sim.TEAM_CONFIG == '2team_6-6':
                team2_start = sim.TEAM2_START
                team2_end = sim.TEAM2_END
                
                for day in range(int(start_hour // 24), int(end_hour // 24) + 2):
                    shift_start = day * 24 + team2_start
                    shift_end = day * 24 + team2_end
                    
                    if shift_start >= start_hour and shift_start <= end_hour:
                        ax.axvline(x=shift_start, color='green', linestyle=':', alpha=0.7, linewidth=1.5)
                    if shift_end >= start_hour and shift_end <= end_hour:
                        ax.axvline(x=shift_end, color='green', linestyle=':', alpha=0.7, linewidth=1.5)
            
            title = f'Worker Activity - Week {week} (Hours {start_hour}-{end_hour})'
            title += f'\n{sim.TEAM_CONFIG}, Strategy: {sim.PRIORITY_STRATEGY}'
            ax.set_title(title, fontsize=12, fontweight='bold')
            
            legend_elements = [
                mpatches.Patch(color=colors['form_wb'], label='Form WB'),
                mpatches.Patch(color=colors['form_bb'], label='Form BB'),
                mpatches.Patch(color=colors['cut_wb'], label='Cut WB'),
                mpatches.Patch(color=colors['cut_bb'], label='Cut BB'),
                mpatches.Patch(facecolor=colors['cut_wb'], hatch='///', label='Paused Cut'),
                mpatches.Patch(facecolor='#FFB6C1', edgecolor='#DC143C', hatch='\\\\', label='Form Clean'),
                mpatches.Patch(facecolor='#DDA0DD', edgecolor='#8B008B', hatch='\\\\', label='Oven Clean'),
            ]
            ax.legend(handles=legend_elements, loc='upper right', fontsize=8, ncol=2)
        
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