"""
CARV Skiing Analysis Backend API
Flask server that uses Claude AI to analyze CARV screenshots and generate training plans.
"""

import os
import base64
import json
import re
import uuid
import threading
import secrets
import time
from io import BytesIO
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from anthropic import Anthropic
from dotenv import load_dotenv
from PIL import Image
from PIL.ExifTags import TAGS

# In-memory job store for background processing
# Format: {job_id: {"status": "pending"|"processing"|"completed"|"failed", "result": {...}, "error": "..."}}
jobs = {}

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Configure CORS - allow localhost for dev and Render URLs for production
allowed_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://carv-analyzer.onrender.com",
]
# Add production frontend URL from environment variable if set
frontend_url = os.getenv("FRONTEND_URL")
if frontend_url:
    allowed_origins.append(frontend_url)

CORS(app, origins=allowed_origins)

# ============================================================================
# ACCESS CONTROL - shared-secret auth + basic per-IP rate limiting
# ============================================================================
# No fallback: if API_ACCESS_TOKEN is unset, all requests (except /health)
# are refused rather than served unauthenticated.
API_ACCESS_TOKEN = os.getenv("API_ACCESS_TOKEN")

RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "30"))
RATE_LIMIT_WINDOW_SECONDS = 60
# In-memory sliding window per IP - fine for the single-worker gunicorn setup
_request_log = {}
_rate_lock = threading.Lock()


@app.before_request
def check_access():
    # CORS preflight requests carry no auth headers
    if request.method == "OPTIONS":
        return None
    # Keep the health check open for uptime monitoring
    if request.path == "/health":
        return None

    if not API_ACCESS_TOKEN:
        return jsonify({
            "error": "Service not configured",
            "message": "API_ACCESS_TOKEN is not set on the server; refusing all requests."
        }), 503

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        provided = auth_header[len("Bearer "):]
    else:
        provided = request.headers.get("X-API-Key", "")

    if not secrets.compare_digest(provided, API_ACCESS_TOKEN):
        return jsonify({"error": "Unauthorized"}), 401

    # Rate limit per client IP (Render sits behind a proxy, so trust the
    # first entry of X-Forwarded-For when present)
    forwarded = request.headers.get("X-Forwarded-For", "")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.remote_addr or "unknown")
    now = time.time()
    with _rate_lock:
        window = [t for t in _request_log.get(ip, []) if now - t < RATE_LIMIT_WINDOW_SECONDS]
        if len(window) >= RATE_LIMIT_REQUESTS:
            _request_log[ip] = window
            return jsonify({
                "error": "Rate Limited",
                "message": "Too many requests. Please wait a moment and try again."
            }), 429
        window.append(now)
        # Evict stale IPs so the table can't grow unbounded
        if len(_request_log) > 1000:
            for stale_ip in [k for k, v in _request_log.items() if not v or now - v[-1] > RATE_LIMIT_WINDOW_SECONDS]:
                del _request_log[stale_ip]
        _request_log[ip] = window
    return None


# Database configuration
database_url = os.getenv("DATABASE_URL")
if database_url:
    # Render uses postgres:// but SQLAlchemy needs postgresql://
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
else:
    # Fallback to SQLite for local development
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///carv_sessions.db"

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
# Connection pool settings to handle Render's free-tier DB sleeping
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,       # Recycle connections after 5 minutes
    "pool_pre_ping": True,     # Test connections before using them (detects dead connections)
    "pool_size": 5,
    "max_overflow": 10,
}
db = SQLAlchemy(app)


# Database Models
class Session(db.Model):
    """Stores a skiing session with analysis data and training plan."""
    __tablename__ = "sessions"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_date = db.Column(db.DateTime, nullable=False)
    location = db.Column(db.String(200))
    ski_iq = db.Column(db.Float)
    metrics = db.Column(db.JSON)  # Full analysis metrics
    training_plan = db.Column(db.Text)  # Generated training plan markdown
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "session_date": self.session_date.isoformat() if self.session_date else None,
            "location": self.location,
            "ski_iq": self.ski_iq,
            "metrics": self.metrics,
            "training_plan": self.training_plan,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# Create tables on startup
with app.app_context():
    db.create_all()

# Initialize Anthropic client
client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# CARV Metrics Context for AI Analysis - Comprehensive Carving Knowledge Base
CARV_METRICS_CONTEXT = """
You are an elite ski coach and CARV technology expert with deep knowledge of carving biomechanics.
Your analysis is based on proven carving principles, not generic skiing advice.

## CORE CARVING PHILOSOPHY

A carved turn is when the ski tail follows the exact arc created by the ski tip - like a train on tracks.
The ski's sidecut does the turning work when the ski is tipped on edge and pressured correctly.

### The Physics of Carving
- **Sidecut Geometry**: When a ski is tilted on edge, its curved shape creates an arc
- **Pressure + Edge Angle = Turn Radius**: More edge angle = tighter turn
- **Clean Edge Lock**: A true carve leaves a single thin line in the snow, not a smeared path
- **G-Forces**: Generated from the ski's grip fighting centrifugal force - a byproduct of technique, not the goal

### The 4 Pillars of Expert Carving
1. **Edge Angle** - How far you tip the ski (measured in degrees)
2. **Fore/Aft Balance** - Weight distribution along the ski length
3. **Rotary Control** - Hip and shoulder alignment relative to skis
4. **Pressure Management** - How and when you load/unload the ski

## CARV METRICS - DEEP INTERPRETATION

### Ski:IQ Score (Overall Performance)
- 100 = Average recreational skier
- 100-115 = Intermediate - developing skills
- 115-125 = Advanced intermediate - linking carved turns
- 125-140 = Advanced - consistent carving on varied terrain
- 140-155 = Expert - high edge angles, dynamic skiing
- 155+ = Elite - race-level technique

### BALANCE CATEGORY (Critical for Carving)

**1. Start of Turn (Forward Pressure)** - Score 0-100
- WHAT IT MEASURES: Weight shift to ski tips at turn initiation
- WHY IT MATTERS: Forward pressure engages the front of the ski first, creating early edge grip
- LOW SCORE INDICATES:
  * Sitting back in the boots (common fear response)
  * Late turn initiation
  * Skis running away at start of turn
- BIOMECHANICS: Shin pressure against boot tongue, hips forward over toes
- TARGET FEELING: "Driving the front of the ski into the turn"

**2. Centered Balance** - Score 0-100
- WHAT IT MEASURES: Maintaining balance over the center of the ski during the turn
- WHY IT MATTERS: Centered stance allows the whole ski edge to engage
- LOW SCORE INDICATES:
  * Getting pulled into the backseat mid-turn
  * Upper body leaning uphill (defensive posture)
  * Weak core engagement
- BIOMECHANICS: Ankle flexion, knee drive, hips stacked over feet
- TARGET FEELING: "Balanced over the arch of the foot"

**3. Transition Weight Release (TWR)** - Score 0-100%
- WHAT IT MEASURES: Vertical G-force variation throughout the turn cycle. Compares peak forces (end of turn) to minimum forces (transition phase).
- CALCULATION: TWR = (Peak G-force - Trough G-force) / Peak G-force × 100
- WHY IT MATTERS: High TWR indicates dynamic skiing with strong pressure buildup AND clean release - the hallmark of expert carving. Clean release allows quick edge change and early new edge engagement.
- SCORE RANGES:
  * 90-100%: Elite - Full weightlessness achieved in transition. World Cup level dynamics.
  * 70-89%: Expert - Strong vertical movement. Clean pressure release with good rebound utilization.
  * 50-69%: Advanced - Moderate dynamics. Room to improve unweighting and pressure buildup.
  * 30-49%: Intermediate - Static through transition. Limited pressure buildup and release.
  * Below 30%: Developing - Flat vertical profile. Likely balance or stance issues preventing dynamic movement.
- LOW SCORE INDICATES:
  * Insufficient pressure buildup during turn (weak peaks)
  * Failure to release pressure at transition (shallow troughs)
  * Stiff/upright posture through transition
  * Mistimed or resisted rebound energy
- BIOMECHANICS: Active retraction/flexion, rebound utilization, positive move down the hill
- TARGET FEELING: "Light feet between turns" / "Floating through transition"
- CROSS-METRIC CORRELATIONS:
  * Low TWR + Low Edge Angle = Likely fore/aft balance issue (fix balance first)
  * Low TWR + Good Edge Angle = Transition mechanics issue (fix release technique)
  * Low TWR + Low Outside Ski Pressure = Weight transfer issue (fix commitment first)

### EDGING CATEGORY (The Heart of Carving)

**1. Edge Angle** - Score 0-100 or degrees
- WHAT IT MEASURES: Maximum angle of ski edge relative to snow
- WHY IT MATTERS: Higher edge angles = tighter turn radius, more grip
- SCORE INTERPRETATION:
  * 30-40°: Recreational carving
  * 45-55°: Strong intermediate carving
  * 55-65°: Advanced/expert carving
  * 65°+: Elite/racing level
- LOW SCORE INDICATES:
  * Fear of commitment
  * Lack of hip angulation
  * Upper body not countering
- BIOMECHANICS: Knee drive into the hill, hip angulation, outside arm forward

**2. Early Edging** - Score 0-100
- WHAT IT MEASURES: How quickly you establish edge grip after transition
- WHY IT MATTERS: Early edge = early grip = controlled arc from the start
- LOW SCORE INDICATES:
  * Pivoting/skidding the ski flat before tipping
  * Delayed weight transfer to new outside ski
  * Sequential movements instead of simultaneous
- BIOMECHANICS: Roll ankles and knees into new turn immediately
- TARGET FEELING: "Tip and grip" / "Edge before you steer"

**3. Edging Similarity** - Score 0-100
- WHAT IT MEASURES: Consistency between left and right turns
- WHY IT MATTERS: Asymmetry limits overall skiing and creates fatigue
- LOW SCORE INDICATES:
  * Dominant side/weaker side
  * Historical injury compensation
  * Equipment issues (boot cant, binding mount)
- COMMON PATTERNS:
  * Stronger toeside vs heelside (or vice versa)
  * One hip less mobile than the other

**4. Progressive Edge Build** - Score 0-100
- WHAT IT MEASURES: Whether edge angle increases throughout the turn
- WHY IT MATTERS: Shows controlled, confident carving vs "park and ride"
- LOW SCORE INDICATES:
  * "Setting an edge and holding" instead of building
  * Fear of increasing commitment
  * Lack of dynamic range
- BIOMECHANICS: Continuous hip/knee drive through the turn arc
- TARGET FEELING: "Squeezing the orange through the turn"

### ROTARY CATEGORY

**1. Parallel Skis** - Score 0-100
- WHAT IT MEASURES: How parallel the skis remain throughout turns
- WHY IT MATTERS: Parallel skis = both skis carving similar arcs
- LOW SCORE INDICATES:
  * Stemming or wedging (using inside ski for braking)
  * A-frame stance (knees together, skis apart)
  * Inside ski not tipping enough
- BIOMECHANICS: Both legs work together, inside ski leads slightly
- TARGET FEELING: "Railroad tracks in the snow"

**2. Turn Shape** - Score 0-100
- WHAT IT MEASURES: Smooth C-shaped arcs vs Z-shaped jerky turns
- WHY IT MATTERS: Smooth arcs = continuous edge engagement, controlled speed
- LOW SCORE INDICATES:
  * Pivot-based turning (rotate, skid, set edge)
  * Speed check at end of turn
  * Lack of patience through the arc
- BIOMECHANICS: Continuous flow, no abrupt movements
- TARGET FEELING: "Paint a smooth arc in the snow"

### PERFORMANCE CATEGORY

**1. Turn G-Force** - Score 0-100 or actual G value
- WHAT IT MEASURES: Forces generated during turns
- WHY IT MATTERS: High G-force = strong edge grip and athletic skiing
- CONTEXT: G-force is a RESULT of good technique, not a goal
  * 1.5-2.0G: Recreational carving
  * 2.0-2.5G: Strong carving
  * 2.5-3.0G: Expert/racing
  * 3.0G+: Elite racing level
- LOW SCORE WITH HIGH EDGE ANGLE: May indicate skidding despite tipping
- HIGH SCORE: Shows the ski is truly gripping and bending

## DIAGNOSTIC FRAMEWORK - SYMPTOM TO ROOT CAUSE

### Low Start of Turn Score
ROOT CAUSES:
1. Fear of speed/falling - creates defensive backseat posture
2. Weak ankle flex - can't drive shins into boot tongue
3. Hip mobility issues - can't flex at hip to stay forward
4. Boot setup - too much forward lean or ramp angle
CASCADING EFFECTS: Late edge engagement, skis run away, loss of control

### Low Edge Angle Score
ROOT CAUSES:
1. Fear of commitment - scared to tip fully
2. Lack of hip angulation - using only knee inclination
3. Upper body rotation - shoulders following skis instead of countering
4. Inside ski dominance - weighting inside ski prevents outside ski tipping
CASCADING EFFECTS: Skidding instead of carving, speed control issues

### Low Early Edging Score
ROOT CAUSES:
1. Pivot habit - rotating ski flat before tipping
2. Slow weight transfer - hesitation to commit to new outside ski
3. Sequential movement pattern - one thing at a time instead of simultaneous
CASCADING EFFECTS: Skidded entry, delayed grip, inconsistent turns

### Low Transition Weight Release Score
ROOT CAUSE HIERARCHY (diagnose in this order - upstream issues block downstream fixes):
Level 1 - Fore/Aft Balance (if compromised, blocks everything below):
  * Inside ski tip lifting = aft stance
  * Skis "washing out" at turn end = aft stance
  * Both low peaks AND shallow troughs = fundamental stance issue
Level 2 - Outside Ski Pressure (if weak, limits pressure buildup):
  * Low peak G-forces = weak outside ski commitment
  * Equal pressure both skis = poor weight transfer
Level 3 - Transition Mechanics (flexion/retraction/rebound skills):
  * Shallow troughs only = static transition mechanics
  * Good peaks but shallow troughs = transition technique issue only
Level 4 - Timing & Rhythm
DIAGNOSTIC SIGNALS:
  * Low TWR + Low Edge Angle = Likely fore/aft issue (Level 1)
  * Low TWR + Good Edge Angle = Transition mechanics issue (Level 3)
  * Low TWR + Low Outside Ski Pressure = Weight transfer issue (Level 2)
CASCADING EFFECTS: Choppy transitions, loss of flow, fatigue, inability to build dynamic range

### Low Centered Balance Score
ROOT CAUSES:
1. Getting pulled back by G-forces - not anticipating the load
2. Upper body leaning uphill - defensive "survival" stance
3. Weak core - can't maintain position under load
CASCADING EFFECTS: Loss of ski control in bottom half of turn

### Low Progressive Edge Build Score
ROOT CAUSES:
1. "Park and ride" habit - set edge angle and hold
2. Fear of increasing commitment - playing it safe
3. Lack of dynamic range - don't know how to increase through turn
CASCADING EFFECTS: Predictable skiing, limited ability on varied terrain

## SKILL PROGRESSION LEVELS

### ENTRY LEVEL (Ski:IQ 100-115)
- Focus: Basic carved turns on easy terrain
- Key Skills: Edge awareness, balance drills, smooth transitions
- Terrain: Green/easy blue, well-groomed
- Goals: Feel the difference between skidding and carving

### DEVELOPMENT LEVEL (Ski:IQ 115-125)
- Focus: Consistent carving, building edge angles
- Key Skills: Hip angulation, pole timing, variable turn radius
- Terrain: Blue runs, moderate pitch
- Goals: Leave clean pencil lines in the snow

### PERFORMANCE LEVEL (Ski:IQ 125-140)
- Focus: Dynamic skiing, terrain adaptation
- Key Skills: Pressure management, flexion/extension, aggressive transitions
- Terrain: All blues, black runs
- Goals: Maintain technique under speed and variable conditions

### HIGH PERFORMANCE (Ski:IQ 140+)
- Focus: Racing technique, extreme edge angles
- Key Skills: Carving on steep terrain, gates, high-speed stability
- Terrain: Black/double-black, race courses
- Goals: Elite-level edge angles, consistent G-forces

## TERRAIN & CONDITIONS CONTEXT

### Groomed Runs
- Ideal for technique work
- Edge grip is predictable
- Focus: Clean carving mechanics

### Steep Terrain
- Tests commitment and balance
- Requires earlier edge engagement
- Focus: Forward pressure, aggressive pole plant

### Variable Snow
- Requires adaptive pressure management
- Edge angle less critical than balance
- Focus: Quiet upper body, reactive legs

### Ice/Hard Pack
- Demands precise edge control
- Slight detuning may help
- Focus: Very clean technique, no skidding allowed
"""

# Holistic multi-image analysis prompt
HOLISTIC_ANALYSIS_PROMPT = """
You are analyzing {num_images} CARV app screenshots from a skier's session. Look at ALL the images together to get a complete picture of their skiing performance.

## YOUR DIAGNOSTIC APPROACH

Use this framework to identify ROOT CAUSES, not just symptoms:

**Low Start of Turn** → Root causes: Fear (backseat), weak ankle flex, hip mobility, boot setup
**Low Centered Balance** → Root causes: G-force pulling back, defensive uphill lean, weak core
**Low Weight Release** → Root causes: Fear of fall line, Z-turn habit, static body
**Low Edge Angle** → Root causes: Fear of commitment, no hip angulation, upper body rotation
**Low Early Edging** → Root causes: Pivot habit, slow weight transfer, sequential movements
**Low Edging Similarity** → Root causes: Dominant side, injury compensation, equipment
**Low Progressive Edge Build** → Root causes: "Park and ride" habit, fear, limited dynamic range
**Low Parallel Skis** → Root causes: Stemming, A-frame, inside ski not tipping
**Low Turn Shape** → Root causes: Pivot-based turning, speed checking, impatience
**Low G-Force with good edges** → Skidding despite tipping - technique breakdown

Analyze these screenshots HOLISTICALLY - treat them as different views of the same ski session or related sessions. Look for:
- Overall patterns across all screenshots
- Consistent strengths and weaknesses
- ROOT CAUSES of issues, not just symptoms
- Any progression or variation between runs
- The complete picture of this skier's technique

IMPORTANT: Extract the date and time displayed on the CARV app screenshots. Look for the date/time shown near the Ski:IQ score or in the run header. This is the MASTER timestamp for the session.

Return a JSON object with this EXACT structure:

{{
  "session_overview": {{
    "total_screenshots": {num_images},
    "session_datetime": "<date and time shown on the CARV screenshot in ISO format YYYY-MM-DDTHH:MM:SS, e.g., 2024-01-15T10:30:00. Extract from the screenshot display. If multiple dates visible, use the most recent. If no date visible, use null>",
    "session_date_display": "<the date/time as shown on screen, e.g., 'Jan 15, 2024 10:30 AM' or whatever format is displayed. null if not visible>",
    "ski_iq_range": {{
      "lowest": <number or null>,
      "highest": <number or null>,
      "average": <number or null>
    }},
    "terrain_types_seen": ["<list of terrain types visible across all screenshots>"],
    "total_turns_analyzed": <sum of turns if visible, or null>
  }},
  "overall_metrics": {{
    "balance": {{
      "start_of_turn": <average score 0-100 or null>,
      "centered_balance": <average score 0-100 or null>,
      "transition_weight_release": <average score 0-100 or null>,
      "category_average": <average of all balance metrics>
    }},
    "edging": {{
      "edge_angle": <average score 0-100 or null>,
      "early_edging": <average score 0-100 or null>,
      "edging_similarity": <average score 0-100 or null>,
      "progressive_edge_build": <average score 0-100 or null>,
      "category_average": <average of all edging metrics>
    }},
    "rotary": {{
      "parallel_skis": <average score 0-100 or null>,
      "turn_shape": <average score 0-100 or null>,
      "category_average": <average of all rotary metrics>
    }},
    "performance": {{
      "turn_g_force": <average score 0-100 or null>,
      "category_average": <same as turn_g_force>
    }}
  }},
  "holistic_analysis": {{
    "skiing_style": "<describe their overall skiing style based on all data - are they aggressive, cautious, dynamic, static, etc.>",
    "technique_signature": "<what makes this skier unique - their characteristic patterns>",
    "consistency_assessment": "<how consistent are they across runs/metrics - very consistent, variable, improving, etc.>",
    "biggest_limiter": "<the ONE thing most holding back their skiing>",
    "hidden_strength": "<a strength they might not realize they have>"
  }},
  "detailed_observations": "<comprehensive analysis of what you see across ALL screenshots - be specific about patterns, trends, and notable findings>",
  "top_3_strengths": [
    {{
      "area": "<metric or skill name>",
      "score": <average score if applicable>,
      "why_it_matters": "<brief explanation of why this helps their skiing>"
    }}
  ],
  "top_3_priorities": [
    {{
      "area": "<metric or skill name>",
      "current_score": <average score if applicable>,
      "target_score": <realistic target>,
      "why_priority": "<why this should be focus #1, #2, or #3>",
      "quick_win": "<one simple thing to try>"
    }}
  ],
  "run_by_run_notes": [
    {{
      "screenshot": <1, 2, 3, etc.>,
      "key_observation": "<what stands out in this particular screenshot>"
    }}
  ]
}}

CRITICAL INSTRUCTIONS:
1. Return ONLY valid JSON - no markdown, no explanations before or after
2. Look at ALL images before forming conclusions
3. Average metrics where you see the same metric in multiple screenshots
4. If a metric appears in only some screenshots, still include it
5. Be specific and actionable in your analysis
6. The "biggest_limiter" should be the #1 thing to work on
7. Consider how different screenshots might show different aspects of the same session
"""

TRAINING_PLAN_PROMPT = """
Based on this COMPREHENSIVE CARV skiing analysis from multiple screenshots, create a personalized training plan.

ANALYSIS DATA:
{analysis_data}

This analysis represents data from {num_runs} screenshot(s) giving us a complete picture of this skier.

## DRILL LIBRARY - SELECT APPROPRIATE DRILLS BASED ON ISSUES IDENTIFIED

### FOUNDATION DRILLS (Building Blocks)

**1. Thousand Steps**
- Purpose: Develops balance, weight transfer awareness, edge feel
- Execution: Make tiny rapid steps from ski to ski while traversing/turning
- Feel: Dancing on the snow, constant weight shifting
- Duration: 2-3 runs, green/easy blue terrain
- Improves: Centered Balance, Weight Release, Edging Similarity
- Common Mistake: Steps too big - keep them small and quick
- Video: https://www.youtube.com/results?search_query=1000+steps+ski+drill+carving

**2. Javelin Turns**
- Purpose: Forces commitment to outside ski, eliminates inside ski dependency
- Execution: Lift inside ski completely off snow, hold parallel to outside ski during turn
- Feel: All weight on one ski, total commitment
- Duration: 5-8 turns each side, moderate blue terrain
- Improves: Edge Angle, Start of Turn, Centered Balance
- Common Mistake: Leaning into hill for balance instead of angulating
- Video: https://www.youtube.com/results?search_query=javelin+turns+ski+drill+carving

**3. Shuffle Turns**
- Purpose: Builds awareness of fore/aft range of motion and balance
- Execution: While skiing, shuffle feet forward/back against each other
- Variants: Straight line shuffle, two-footed shuffle, shuffle while turning
- Feel: Distinct pressure shifts along foot length, exploring balance range
- Duration: Full run, green/blue terrain
- Improves: Fore/Aft Balance awareness, Centered Balance, proprioception
- Success Criteria: Can feel distinct pressure shifts along foot length
- Video: https://www.youtube.com/results?search_query=shuffle+turns+ski+drill+balance

**4. Pivot Slips**
- Purpose: Develops rotary control and edge release ability
- Execution: From standstill, release edges and pivot 180°, then stop
- Feel: Controlled sliding, precise edge control
- Duration: 10 pivots each direction
- Improves: Transition Weight Release, edge awareness
- Video: https://www.youtube.com/results?search_query=pivot+slips+ski+drill+edge+control

### EDGE ANGLE DEVELOPMENT DRILLS

**5. Railroad Track Carving**
- Purpose: Develops pure carving - no skidding
- Execution: Make turns leaving only two clean pencil lines in snow
- Feel: Train on tracks, no sideways sliding
- Duration: Full runs, focus on quality not quantity
- Improves: Edge Angle, Turn Shape, Progressive Edge Build
- Common Mistake: Going too fast - start slow, prioritize clean tracks
- Video: https://www.youtube.com/results?search_query=railroad+track+carving+ski+drill

**6. J-Turns (Edge Lock Drill)**
- Purpose: Maximizes edge angle commitment
- Execution: From traverse, commit to fall line, carve hard uphill until stop
- Feel: Maximum edge engagement, G-force building, ski bending
- Duration: 5 each direction, moderate pitch
- Improves: Edge Angle, Progressive Edge Build, commitment
- Video: https://www.youtube.com/results?search_query=j+turns+edge+lock+ski+drill+carving

**7. Angulation Exaggeration**
- Purpose: Develops hip angulation for higher edge angles
- Execution: Touch outside hand to outside boot during turns
- Feel: Body folding at waist, hips pushing into hill
- Duration: 4-6 turns each direction
- Improves: Edge Angle, Centered Balance
- Common Mistake: Bending at waist instead of creating hip angle
- Video: https://www.youtube.com/results?search_query=angulation+drill+skiing+hip+angulation

**8. Pole Drag Carving**
- Purpose: Forces upper body countering and angulation
- Execution: Drag inside pole tip in snow throughout turn
- Feel: Upper body stays facing downhill, separation from lower body
- Duration: Full run
- Improves: Edge Angle, Turn Shape, upper/lower body separation
- Video: https://www.youtube.com/results?search_query=pole+drag+carving+ski+drill

### BALANCE & FORE-AFT DRILLS

**9. Shin Banger**
- Purpose: Develops forward pressure and ankle flex
- Execution: Feel constant shin pressure on boot tongue throughout turn
- Feel: Shins pressing forward, never losing contact
- Duration: Every turn, conscious focus
- Improves: Start of Turn, Centered Balance
- Cue: "Crush the tongue"
- Video: https://www.youtube.com/results?search_query=shin+pressure+boot+tongue+ski+drill+forward+stance

**10. Hands on Knees Turns**
- Purpose: Forces forward stance and commitment
- Execution: Ski with hands resting on kneecaps
- Feel: Stacked, forward, can't sit back
- Duration: 4-6 turns, easy terrain
- Improves: Start of Turn, Centered Balance
- Common Mistake: Bending too much at waist
- Video: https://www.youtube.com/results?search_query=hands+on+knees+ski+drill+forward+stance

**11. Tall-Small Transitions**
- Purpose: Develops flexion/retraction timing for dynamic transitions
- Execution: Build pressure (tall/strong outside leg) through turn, then actively collapse/flex old outside leg at transition to release. Don't push off - just flex and untip.
- Feel: Hips stay level through transition, significant vertical separation of skis at crossover
- Duration: Full run, exaggerate the flex/collapse at transition
- Improves: Transition Weight Release, Pressure Management
- Key Point: This is a down-unweighting/retraction pattern, not an extension-push pattern. Collapse to release, don't stand up to release.
- Video: https://www.youtube.com/results?search_query=tall+small+transitions+ski+drill+retraction

**12. Touch the Outside Boot**
- Purpose: Develops outside ski pressure and forward commitment
- Execution: Reach down and touch outside boot at turn apex
- Feel: Weight over outside ski, forward and low
- Duration: Alternating turns
- Improves: Start of Turn, Centered Balance, Edge Angle
- Video: https://www.youtube.com/results?search_query=touch+outside+boot+ski+drill+carving

### TRANSITION & FLOW DRILLS

**13. White Pass Turns**
- Purpose: Develops early weight transfer and commitment to new turn
- Execution: Transfer weight to new ski BEFORE releasing old turn
- Feel: New turn starts before old one ends, overlapping commitment
- Duration: Focus drill, 6-8 turns
- Improves: Transition Weight Release, Early Edging
- Common Mistake: Finishing old turn completely before starting new
- Video: https://www.youtube.com/results?search_query=white+pass+turns+ski+drill+early+weight+transfer

**14. Crossover Focus**
- Purpose: Develops positive movement into new turn
- Execution: Feel center of mass crossing over skis into new turn
- Feel: Body moving downhill into the new arc, not pulling back
- Duration: Every transition, conscious awareness
- Improves: Transition Weight Release, Early Edging
- Cue: "Fall into the new turn"
- Video: https://www.youtube.com/results?search_query=crossover+skiing+drill+center+of+mass+transition

**15. No Pole Skiing**
- Purpose: Develops balance without pole crutch
- Execution: Remove poles, hands on hips or crossed on chest
- Feel: Pure balance, can't push off anything
- Duration: Full runs
- Improves: Centered Balance, Core engagement
- Video: https://www.youtube.com/results?search_query=no+pole+skiing+drill+balance

**16. Patience Turns**
- Purpose: Develops complete turn finish and clean transitions
- Execution: Let each turn finish completely up the hill before transitioning
- Feel: No rushing, complete the arc
- Duration: Focus on slow rhythmic skiing
- Improves: Turn Shape, Transition Weight Release
- Video: https://www.youtube.com/results?search_query=patience+turns+ski+drill+complete+turn+shape

### ADVANCED PERFORMANCE DRILLS

**17. Retraction Turns**
- Purpose: Develops quick edge-to-edge transitions
- Execution: Pull feet up under body at transition, extend into new turn
- Feel: Light feet at crossover, snappy transition
- Duration: Moderate to steep terrain
- Improves: Transition Weight Release, Early Edging, G-Force
- Video: https://www.youtube.com/results?search_query=retraction+turns+ski+drill+edge+change

**18. Dolphin Turns**
- Purpose: Develops dynamic fore/aft cycling through each turn
- Execution: Pop off ski tails, land on shovels. Back-pedaling motion with feet - push feet forward to pop off tails, pull feet back with heels up to land on shovels
- Feel: Visible "dolphin" shape to ski trajectory, dynamic fore/aft weight shift
- Duration: Blue/Black terrain, focus on clean edge change while airborne
- Improves: Transition Weight Release, Fore/Aft Balance, Dynamic Range
- Success Criteria: Clean edge change while airborne, visible dolphin trajectory
- Video: https://www.youtube.com/results?search_query=dolphin+turns+ski+drill+dynamic+fore+aft

**19. Speed Carving**
- Purpose: Develops trust in edge grip at speed
- Execution: Increase speed while maintaining pure carved turns
- Feel: Acceleration through the arc, G-forces building
- Duration: Open blue/black runs
- Improves: Edge Angle, Turn G-Force, confidence
- Video: https://www.youtube.com/results?search_query=speed+carving+ski+high+edge+angle+GS+turns

**20. Variable Radius Carving**
- Purpose: Develops ability to adjust turn shape
- Execution: Alternate between long radius and short radius carved turns
- Feel: Adjustable pressure/edge, ski bending different amounts
- Duration: Full runs
- Improves: Progressive Edge Build, Turn Shape, versatility
- Video: https://www.youtube.com/results?search_query=variable+radius+carving+ski+short+long+turns

### HIGH-PERFORMANCE DRILLS

**21. Hop Transitions**
- Purpose: Develops explosive edge change
- Execution: Hop both skis off snow at transition, land on new edges
- Feel: Explosive, athletic, immediate edge engagement
- Duration: Steep terrain, short sections
- Improves: Early Edging, Transition Weight Release, athleticism
- Video: https://www.youtube.com/results?search_query=hop+transitions+ski+drill+edge+change+carving

**22. One-Ski Carving**
- Purpose: Ultimate balance and edge control test
- Execution: Remove one ski, carve turns on single ski
- Feel: Total commitment, no backup
- Duration: Easy terrain, 3-4 turns per side
- Improves: Edge Angle, Centered Balance, balance mastery
- Video: https://www.youtube.com/results?search_query=one+ski+carving+drill+single+ski+balance

**23. Gate Training Simulation**
- Purpose: Develops race-timing and line
- Execution: Visualize gates, commit to apex, accelerate out
- Feel: Early pressure, round the gate, explode out
- Duration: Open slope, mark mental gates
- Improves: All metrics, race application
- Video: https://www.youtube.com/results?search_query=gate+training+simulation+ski+racing+drill

### TWR-SPECIFIC DRILLS (Organized by Root Cause Level)

#### Level 1: Fore/Aft Balance Drills (Fix First if Balance is Compromised)

**24. Stork Turns**
- Purpose: Forces forward commitment - cannot execute if aft
- Execution: Lift inside ski's tail only, keeping tip on snow throughout turn
- Difficulty: Beginner-Intermediate
- Terrain: Green/Blue
- Progression: Start on gentle terrain, increase steepness as competent
- Improves: Start of Turn, Centered Balance, Transition Weight Release
- Success Criteria: Can maintain tip contact without losing balance backward
- Video: https://www.youtube.com/results?search_query=stork+turns+ski+drill+inside+ski+tip

**25. Outside-to-Outside**
- Purpose: 100% commitment to stance ski, requires forward balance
- Execution: Lift entire inside ski parallel to snow surface (few inches clearance)
- Difficulty: Intermediate
- Terrain: Blue
- Progression: Start with minimal lift, increase height as balance improves
- Improves: Outside Ski Pressure, Fore/Aft Balance, Transition Weight Release
- Common Error: Dragging pole for stability (remove crutch by holding poles at mid-shaft)
- Success Criteria: Can link 6+ turns with inside ski lifted, no pole assistance
- Video: https://www.youtube.com/results?search_query=outside+ski+to+outside+ski+drill+carving

**26. Unbuckled Boots**
- Purpose: Removes boot cuff as balance crutch. Forces standing on feet, not shins.
- Execution: Ski with boot buckles open
- Difficulty: Intermediate
- Terrain: Green/Easy Blue ONLY
- Improves: Centered Balance, proprioception, stance awareness
- Caution: Very easy terrain only. Instant feedback when aft.
- Success Criteria: Can make controlled turns without relying on boot support
- Video: https://www.youtube.com/results?search_query=unbuckled+boots+ski+drill+balance+proprioception

**27. Range of Motion Turns**
- Purpose: Exploring full fore/aft range, building proprioceptive awareness
- Execution: Deliberately ski as far forward as possible, then as far aft as possible
- Difficulty: Intermediate
- Terrain: Green/Blue ONLY
- Improves: Fore/Aft Balance awareness, ability to find centered position
- Caution: Never above blue groomers
- Success Criteria: Can identify and feel the "centered" position between extremes
- Video: https://www.youtube.com/results?search_query=range+of+motion+ski+drill+fore+aft+balance

#### Level 2: Outside Ski Pressure Drills (Fix if Weak Pressure Buildup)

**28. Single Leg Carving (Basic)**
- Purpose: Proves full commitment to outside ski
- Execution: Lift inside ski completely after transition is established
- Difficulty: Intermediate
- Terrain: Blue
- Progression: Lift late in turn → lift mid-turn → lift at transition
- Improves: Outside Ski Pressure, Transition Weight Release, Edge Angle
- Success Criteria: Can carve clean arcs on single ski through shaping phase
- Video: https://www.youtube.com/results?search_query=single+leg+carving+outside+ski+drill

**29. Single Leg Carving (Advanced)**
- Purpose: Early weight transfer to new outside ski
- Execution: Lift inside ski at or before the transition
- Difficulty: Advanced
- Terrain: Blue/Black
- Improves: Transition Weight Release, Early Edging, commitment
- Success Criteria: Can lift inside ski while still on "wrong" edge, maintain carved arc
- Video: https://www.youtube.com/results?search_query=advanced+single+ski+carving+early+weight+transfer

**30. Up and Over (Early Stepping)**
- Purpose: Earliest possible platform on new stance ski
- Execution: Transfer weight to new outside ski while still on uphill edge (before edge change)
- Difficulty: Advanced
- Terrain: Blue/Black
- Reference: Ted Ligety technique, US Ski Team staple drill
- Improves: Transition Weight Release, Early Edging, Outside Ski Pressure
- Success Criteria: Weight commits to new outside ski before that ski changes edges
- Video: https://www.youtube.com/results?search_query=up+and+over+early+stepping+ski+drill+ted+ligety

**31. Skating**
- Purpose: Learn to tip lower leg to establish edge platform before pushing
- Execution: Skate on skis without poles
- Difficulty: Beginner-Intermediate
- Terrain: Flat/Slight Uphill
- Best With: Short skis (130cm) or slalom skis
- Improves: Edge awareness, Outside Ski Pressure, weight transfer timing
- Success Criteria: Can generate forward momentum through proper edge-then-push sequence
- Video: https://www.youtube.com/results?search_query=skating+on+skis+drill+edge+platform+weight+transfer

#### Level 3: Transition Mechanics Drills (Fix if Peaks Good but Troughs Shallow)

**32. Hop Drill (Tom Gellie)**
- Purpose: Building control, lightness, and confidence in unweighting
- Execution: Hop from one set of edges to the other, exaggerating unweighting
- Difficulty: Advanced
- Terrain: Steep Blue/Black (steeper terrain naturally challenges balance, timing, precision)
- Goal: Skis completely off ground during transition
- Improves: Transition Weight Release, edge change confidence, athleticism
- Success Criteria: Can hop cleanly between edge sets with controlled landings
- Video: https://www.youtube.com/results?search_query=tom+gellie+hop+drill+skiing+edge+change

**33. The Power Release**
- Purpose: Flexing to release, maintaining low position through transition
- Execution: Wide stance carving. Long/strong outside leg at apex, then actively collapse/flex that leg while keeping hips level.
- Mechanics: Collapse old outside leg → pull inside boot up → extend new outside leg passively
- Difficulty: Advanced
- Terrain: Blue/Black
- Key Point: Don't push off old outside ski. Just flex and untip.
- Improves: Transition Weight Release, dynamic range, pressure modulation
- Success Criteria: Hips stay level through transition, significant vertical separation of skis at crossover
- Video: https://www.youtube.com/results?search_query=power+release+ski+drill+flex+retraction+transition

**34. Bounce Turns (Powder Variant)**
- Purpose: Weightless transition feeling, flow development
- Execution: Short-radius turns focusing on finding bounce rhythm
- Difficulty: Intermediate-Advanced
- Terrain: Powder
- Adjustments: Experiment with stance width and pressure distribution (outside ski focus vs two-footed platform)
- Improves: Transition Weight Release, rhythm, flow
- Success Criteria: Rhythmic "float" feeling between turns in powder
- Video: https://www.youtube.com/results?search_query=bounce+turns+powder+skiing+rhythm+drill

**35. Retraction Practice**
- Purpose: Down-unweighting, creating weightlessness at transition
- Execution: At turn's end, actively pull knees toward body
- Difficulty: Advanced
- Terrain: Blue/Black
- Contrast With: Extension release where you stand up through transition
- Improves: Transition Weight Release, edge change speed, lightness
- Success Criteria: Skis feel light/weightless at transition, clean edge change
- Video: https://www.youtube.com/results?search_query=retraction+release+skiing+down+unweighting+transition

**36. 3-3-3 Balance Drill**
- Purpose: Refines sensory awareness, develops ability to relocate balance state on demand
- Execution: 3 turns FORE, 3 turns AFT, 3 turns CENTERED. Repeat.
- Difficulty: Intermediate
- Terrain: Blue
- Variant: Add FORE-to-AFT within single turn (fore at initiation, aft at completion)
- Improves: Fore/Aft Balance, Centered Balance, proprioceptive awareness
- Success Criteria: Can consciously shift between balance states while maintaining turn quality
- Video: https://www.youtube.com/results?search_query=3-3-3+balance+drill+skiing+fore+aft+centered

## DRILL SELECTION FRAMEWORK

Based on the skier's profile, select drills using this logic:

**For Low START OF TURN scores**: Shin Banger, Hands on Knees, Touch Outside Boot
**For Low CENTERED BALANCE scores**: Javelin Turns, Thousand Steps, No Pole Skiing
**For Low TRANSITION WEIGHT RELEASE scores** (diagnose root cause first):
  - If TWR < 50% AND Edge Angle < 40°: Prioritize Level 1 Fore/Aft drills (Shuffle Turns, Stork Turns, Range of Motion Turns)
  - If TWR < 50% AND Edge Angle > 40°: Prioritize Level 3 Transition drills (Power Release, Retraction Practice, Hop Drill)
  - If Outside Ski Pressure < 70%: Prioritize Level 2 drills (Single Leg Carving, Javelin Turns, Up and Over)
  - If TWR 50-70%: Mix of Level 2 and Level 3 drills (Tall-Small, Dolphin Turns, 3-3-3 Balance)
**For Low EDGE ANGLE scores**: J-Turns, Angulation Exaggeration, Pole Drag Carving
**For Low EARLY EDGING scores**: White Pass Turns, Retraction Turns, Hop Transitions
**For Low EDGING SIMILARITY scores**: Thousand Steps, One-Ski Carving, Javelin Turns (weak side focus)
**For Low PROGRESSIVE EDGE BUILD scores**: J-Turns, Dolphin Turns, Railroad Track Carving
**For Low PARALLEL SKIS scores**: Shuffle Turns, Thousand Steps
**For Low TURN SHAPE scores**: Patience Turns, Railroad Track Carving, Variable Radius
**For Low G-FORCE with good edge angles**: Speed Carving, Dolphin Turns (indicates skidding despite tipping)

## SESSION STRUCTURE RECOMMENDATIONS

**Warm-up Phase (First 2-3 runs)**
- Free skiing at 70% effort
- One foundation drill (Thousand Steps or Shuffle Turns)
- Activate key movement patterns

**Focus Phase (4-6 runs)**
- Primary improvement drill (selected for biggest limiter)
- 3-4 focused turns, then free skiing
- Rest between attempts

**Integration Phase (2-3 runs)**
- Free skiing incorporating new feel
- Higher speed/steeper terrain
- Don't think, just ski with new patterns

**Cool-down (Final run)**
- Free skiing, enjoyment focus
- Notice what felt different today

---

Based on this skier's analysis, create a plan following this structure:

# Training Plan for Ski:IQ {ski_iq}

## The Big Picture
- Summarize this skier in 2-3 sentences based on the holistic analysis
- Their current progression level (Entry/Development/Performance/High Performance)
- The ONE biggest limiter holding them back

## Immediate Focus (Next 1-3 Runs)
Based on their BIGGEST LIMITER:
- The primary issue to address
- The single best drill from the library above
- Detailed execution instructions
- What success feels like
- Mental cue (3-5 words)

## Your 3 Key Drills

YOU MUST INCLUDE EXACTLY 3 DRILLS with full details. Select from the Drill Library above based on their weakest metrics.

### Drill 1: [Name] - Primary Focus
- **Target Metric**: [The CARV metric this improves]
- **Why This Drill**: [How it addresses their specific weakness]
- **How To Do It (Step-by-Step)**:
  1. [Starting position - where to stand, stance width, pole position]
  2. [The initiation - how to begin the movement]
  3. [The main movement - what your body does during the drill]
  4. [The finish - how to complete each rep/turn]
  5. [Reset - how to prepare for the next rep]
- **Terrain**: [Green/Blue/Black, groomed, pitch]
- **Runs**: [X runs], [X focused turns per run, then free ski]
- **Success Feels Like**: [Specific physical sensations - what you feel in feet, legs, hips]
- **Mental Cue**: [3-5 word phrase to think while doing it]
- **Common Mistakes**:
  - [Mistake 1 and how to fix it]
  - [Mistake 2 and how to fix it]
- **Make It Harder**: [How to progress as they improve]
- **Video**: [Copy the exact Video URL from the drill's entry in the Drill Library above]

### Drill 2: [Name] - Secondary Focus
- **Target Metric**: [The CARV metric this improves]
- **Why This Drill**: [How it addresses their specific weakness]
- **How To Do It (Step-by-Step)**:
  1. [Starting position]
  2. [The initiation]
  3. [The main movement]
  4. [The finish]
- **Terrain**: [Recommendation]
- **Runs**: [X runs], [X focused turns per run]
- **Success Feels Like**: [Specific physical sensations]
- **Mental Cue**: [3-5 word phrase]
- **Common Mistakes**:
  - [Mistake and fix]
- **Video**: [Copy the exact Video URL from the drill's entry in the Drill Library above]

### Drill 3: [Name] - Integration/Refinement
- **Target Metric**: [The CARV metric this improves]
- **Why This Drill**: [How it ties everything together]
- **How To Do It (Step-by-Step)**:
  1. [Starting position]
  2. [The initiation]
  3. [The main movement]
  4. [The finish]
- **Terrain**: [Recommendation]
- **Runs**: [X runs], [X focused turns per run]
- **Success Feels Like**: [Specific physical sensations]
- **Mental Cue**: [3-5 word phrase]
- **Video**: [Copy the exact Video URL from the drill's entry in the Drill Library above]

## Daily Session Plan (10 Runs)

Structure each ski day like this:

**Run 1-2: Warm-Up Phase**
- Free skiing at 70% effort
- Focus: Get loose, feel the snow
- Optional: Thousand Steps or Shuffle Turns to activate

**Run 3-4: Drill 1 - [Name]**
- [X] focused turns, then free ski to bottom
- Rest at bottom, think about the feel
- Repeat with intention

**Run 5-6: Drill 2 - [Name]**
- [X] focused turns, then free ski
- Connect the feeling to Drill 1

**Run 7-8: Drill 3 - [Name]**
- [X] focused turns, then free ski
- Integrate all three concepts

**Run 9: Integration Run**
- Free skiing at 80% effort
- Apply all three drill concepts naturally
- Don't think, just feel

**Run 10: Fun Run**
- Pure enjoyment skiing
- Notice what feels different
- End on a high note

## Weekly Training Schedule

### Day 1: Foundation Day
- **Primary Focus**: Drill 1 (4 runs)
- **Secondary**: Drill 2 (2 runs)
- **Terrain**: Easier runs, perfect technique
- **Goal**: Establish the movement patterns

### Day 2: Development Day
- **Primary Focus**: Drill 2 (4 runs)
- **Secondary**: Drill 1 review (2 runs)
- **Add**: Drill 3 introduction (2 runs)
- **Terrain**: Progress to moderate terrain
- **Goal**: Build on Day 1, add complexity

### Day 3: Integration Day
- **Primary Focus**: All 3 drills equally (2 runs each)
- **Integration runs**: 4 runs applying concepts
- **Terrain**: Varied - test on different pitches
- **Goal**: Connect everything, build confidence

### Day 4: Challenge Day
- **Warm-up**: Quick drill review (1 run each)
- **Challenge**: Apply to steeper/faster terrain
- **Focus**: Maintain technique under pressure
- **Goal**: Test limits, find new baseline

### Day 5: Recovery & Assessment
- **Light Focus**: Favorite drill only (2-3 runs)
- **Mostly**: Free skiing with awareness
- **End**: Take CARV screenshots for comparison
- **Goal**: Consolidate gains, measure progress

## This Week's Priorities

### Priority 1: [Biggest Limiter]
- Current: [score] → Target: [realistic target]
- Primary Drill: Drill 1
- Runs needed: 15-20 runs this week
- Expected improvement: +5-8 points

### Priority 2: [Second Issue]
- Current: [score] → Target: [target]
- Primary Drill: Drill 2
- Runs needed: 10-15 runs this week
- How it connects to Priority 1

### Priority 3: [Third Issue]
- Current: [score] → Target: [target]
- Primary Drill: Drill 3
- Runs needed: 8-10 runs this week
- Integration with other priorities

## Building on Strengths
How to use their top strengths to accelerate improvement:
- [Strength 1]: How it helps with [specific weakness]
- [Strength 2]: How to leverage it

## 4-Week Progression

### Week 1-2: Foundation Building
- Focus: [Primary limiter]
- Drills: All 3 as described above
- Drill Runs: 60% Drill 1, 30% Drill 2, 10% Drill 3
- Target Metrics: [What CARV scores to watch]
- Signs of Progress: [What they'll feel/see]

### Week 3-4: Integration & Challenge
- Progression: Increase terrain difficulty
- Drill Balance: 40% Drill 1, 35% Drill 2, 25% Drill 3
- Add Challenge: Speed, steeper terrain, variable snow
- Target Metrics: [Updated goals]

## Progress Checkpoints
- After 5 runs: [What should improve first - usually awareness]
- After 10 runs: [Expected metric changes]
- After 20 runs: [Target achievements]
- After 1 week: Take new CARV screenshots
- After 2 weeks: Compare metrics, adjust drill focus

## Mental Cues for This Skier
Based on their specific pattern:
- Primary Cue: "[3-5 words for their main focus]"
- Transition Cue: "[For moving between turns]"
- Confidence Cue: "[When they need to commit more]"

## Common Traps to Avoid
Based on their profile:
- [Specific trap #1 they might fall into]
- [Specific trap #2]
- [What to do instead]

Remember: Perfect practice makes perfect. 10 focused turns beat 100 mindless ones!
"""


def get_media_type(filename):
    """Determine the media type from file extension."""
    extension = filename.lower().split('.')[-1]
    media_types = {
        'png': 'image/png',
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
        'webp': 'image/webp',
        'gif': 'image/gif'
    }
    return media_types.get(extension, 'image/png')


def clean_json_response(response_text):
    """Clean Claude's response to extract valid JSON."""
    text = response_text.strip()

    # Remove markdown code blocks if present
    if text.startswith('```'):
        first_newline = text.find('\n')
        if first_newline != -1:
            text = text[first_newline + 1:]
        if text.endswith('```'):
            text = text[:-3]
        text = text.strip()

    # Try to find JSON object boundaries
    start_idx = text.find('{')
    end_idx = text.rfind('}')

    if start_idx != -1 and end_idx != -1:
        text = text[start_idx:end_idx + 1]

    return text


def extract_exif_datetime(image_data):
    """Extract datetime from image EXIF data."""
    try:
        image = Image.open(BytesIO(image_data))
        exif_data = image._getexif()

        if exif_data:
            for tag_id, value in exif_data.items():
                tag = TAGS.get(tag_id, tag_id)
                # Look for DateTimeOriginal or DateTime
                if tag in ['DateTimeOriginal', 'DateTime', 'DateTimeDigitized']:
                    # EXIF datetime format: "YYYY:MM:DD HH:MM:SS"
                    try:
                        dt = datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
                        return dt.isoformat()
                    except ValueError:
                        continue
        return None
    except Exception:
        return None


@app.route('/extract-metadata', methods=['POST'])
def extract_metadata():
    """
    Extract metadata (especially datetime) from uploaded images.

    Expects: multipart/form-data with one or more 'images' files
    Returns: JSON with metadata for each image
    """
    try:
        if 'images' not in request.files:
            return jsonify({
                "error": "No image files provided",
                "message": "Please upload images to extract metadata"
            }), 400

        files = request.files.getlist('images')
        metadata_list = []

        for file in files:
            if file.filename == '':
                continue

            image_data = file.read()
            file.seek(0)  # Reset file pointer

            # Extract EXIF datetime
            exif_datetime = extract_exif_datetime(image_data)

            metadata = {
                "filename": file.filename,
                "datetime": exif_datetime,
                "datetime_source": "exif" if exif_datetime else None
            }

            # If no EXIF data, try to get from filename patterns
            if not exif_datetime:
                # Common screenshot naming patterns
                import re
                filename = file.filename
                # Pattern: Screenshot 2024-01-15 at 10.30.45.png
                pattern1 = r'(\d{4})-(\d{2})-(\d{2}).*?(\d{1,2})\.(\d{2})\.(\d{2})'
                # Pattern: IMG_20240115_103045.jpg
                pattern2 = r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})'

                match = re.search(pattern1, filename)
                if match:
                    try:
                        year, month, day, hour, minute, second = match.groups()
                        dt = datetime(int(year), int(month), int(day), int(hour), int(minute), int(second))
                        metadata["datetime"] = dt.isoformat()
                        metadata["datetime_source"] = "filename"
                    except ValueError:
                        pass

                if not metadata["datetime"]:
                    match = re.search(pattern2, filename)
                    if match:
                        try:
                            year, month, day, hour, minute, second = match.groups()
                            dt = datetime(int(year), int(month), int(day), int(hour), int(minute), int(second))
                            metadata["datetime"] = dt.isoformat()
                            metadata["datetime_source"] = "filename"
                        except ValueError:
                            pass

            metadata_list.append(metadata)

        return jsonify({
            "metadata": metadata_list,
            "extracted_at": datetime.now().isoformat()
        })

    except Exception:
        app.logger.exception("Metadata extraction failed")
        return jsonify({
            "error": "Metadata extraction failed",
            "message": "Something went wrong extracting metadata. Please try again."
        }), 500


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint to verify the backend is running."""
    api_key_configured = bool(os.getenv("ANTHROPIC_API_KEY"))
    return jsonify({
        "status": "healthy",
        "api_key_configured": api_key_configured,
        "timestamp": datetime.now().isoformat()
    })


@app.route('/analyze', methods=['POST'])
def analyze_screenshots():
    """
    Analyze one or more CARV screenshots holistically using Claude's vision.

    Expects: multipart/form-data with one or more 'images' files
    Returns: JSON with holistic analysis of all screenshots
    """
    try:
        # Check if image files are present
        if 'images' not in request.files:
            return jsonify({
                "error": "No image files provided",
                "message": "Please upload at least one CARV screenshot"
            }), 400

        files = request.files.getlist('images')

        if len(files) == 0 or (len(files) == 1 and files[0].filename == ''):
            return jsonify({
                "error": "No files selected",
                "message": "Please select at least one CARV screenshot to upload"
            }), 400

        # Process all images
        image_contents = []
        filenames = []

        for file in files:
            if file.filename == '':
                continue

            # Check file size (max 5MB each)
            file.seek(0, 2)
            file_size = file.tell()
            file.seek(0)

            if file_size > 5 * 1024 * 1024:
                return jsonify({
                    "error": "File too large",
                    "message": f"{file.filename} is larger than 5MB"
                }), 400

            # Read and encode image
            image_data = file.read()
            base64_image = base64.standard_b64encode(image_data).decode('utf-8')
            media_type = get_media_type(file.filename)

            image_contents.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64_image
                }
            })
            filenames.append(file.filename)

        num_images = len(image_contents)

        if num_images == 0:
            return jsonify({
                "error": "No valid images",
                "message": "Please upload at least one valid image file"
            }), 400

        # Build the message content with all images + prompt
        message_content = image_contents.copy()
        message_content.append({
            "type": "text",
            "text": HOLISTIC_ANALYSIS_PROMPT.format(num_images=num_images)
        })

        # Call Claude API with all images
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            messages=[
                {
                    "role": "user",
                    "content": message_content
                }
            ],
            system=CARV_METRICS_CONTEXT
        )

        # Extract response text
        response_text = response.content[0].text

        # Clean and parse JSON
        cleaned_json = clean_json_response(response_text)

        try:
            analysis_data = json.loads(cleaned_json)
        except json.JSONDecodeError as e:
            return jsonify({
                "error": "Failed to parse AI response",
                "message": "The AI response wasn't in the expected format. Please try again.",
                "raw_response": response_text[:500]
            }), 500

        # Add metadata
        analysis_data["analyzed_at"] = datetime.now().isoformat()
        analysis_data["filenames"] = filenames
        analysis_data["num_screenshots"] = num_images

        return jsonify(analysis_data)

    except Exception as e:
        app.logger.exception("Request failed")
        error_message = str(e)

        if "api_key" in error_message.lower() or "authentication" in error_message.lower():
            return jsonify({
                "error": "API Key Error",
                "message": "Your Anthropic API key is missing or invalid. Please check your .env file."
            }), 401

        if "rate_limit" in error_message.lower():
            return jsonify({
                "error": "Rate Limited",
                "message": "Too many requests. Please wait a moment and try again."
            }), 429

        return jsonify({
            "error": "Analysis failed",
            "message": "Something went wrong during analysis. Please try again."
        }), 500


def process_analysis_job(job_id, image_contents, filenames, num_images):
    """Background worker function to process image analysis."""
    global jobs

    try:
        jobs[job_id]["status"] = "processing"

        # Build the message content with all images + prompt
        message_content = image_contents.copy()
        message_content.append({
            "type": "text",
            "text": HOLISTIC_ANALYSIS_PROMPT.format(num_images=num_images)
        })

        # Call Claude API with all images
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            messages=[
                {
                    "role": "user",
                    "content": message_content
                }
            ],
            system=CARV_METRICS_CONTEXT
        )

        # Extract response text
        response_text = response.content[0].text

        # Clean and parse JSON
        cleaned_json = clean_json_response(response_text)

        try:
            analysis_data = json.loads(cleaned_json)
        except json.JSONDecodeError:
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["error"] = "Failed to parse AI response. Please try again."
            return

        # Add metadata
        analysis_data["analyzed_at"] = datetime.now().isoformat()
        analysis_data["filenames"] = filenames
        analysis_data["num_screenshots"] = num_images

        jobs[job_id]["status"] = "completed"
        jobs[job_id]["result"] = analysis_data

    except Exception as e:
        jobs[job_id]["status"] = "failed"
        app.logger.exception("Request failed")
        error_message = str(e)

        if "api_key" in error_message.lower() or "authentication" in error_message.lower():
            jobs[job_id]["error"] = "API key is missing or invalid."
        elif "rate_limit" in error_message.lower():
            jobs[job_id]["error"] = "Rate limited. Please wait and try again."
        else:
            jobs[job_id]["error"] = "Analysis failed. Please try again."


@app.route('/analyze-async', methods=['POST'])
def analyze_screenshots_async():
    """
    Submit screenshots for async analysis. Returns immediately with a job_id.
    Poll /job-status/<job_id> to get results.
    """
    try:
        # Check if image files are present
        if 'images' not in request.files:
            return jsonify({
                "error": "No image files provided",
                "message": "Please upload at least one CARV screenshot"
            }), 400

        files = request.files.getlist('images')

        if len(files) == 0 or (len(files) == 1 and files[0].filename == ''):
            return jsonify({
                "error": "No files selected",
                "message": "Please select at least one CARV screenshot to upload"
            }), 400

        # Process all images (validation and encoding happens synchronously)
        image_contents = []
        filenames = []

        for file in files:
            if file.filename == '':
                continue

            # Check file size (max 5MB each)
            file.seek(0, 2)
            file_size = file.tell()
            file.seek(0)

            if file_size > 5 * 1024 * 1024:
                return jsonify({
                    "error": "File too large",
                    "message": f"{file.filename} is larger than 5MB"
                }), 400

            # Read and encode image
            image_data = file.read()
            base64_image = base64.standard_b64encode(image_data).decode('utf-8')
            media_type = get_media_type(file.filename)

            image_contents.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64_image
                }
            })
            filenames.append(file.filename)

        num_images = len(image_contents)

        if num_images == 0:
            return jsonify({
                "error": "No valid images",
                "message": "Please upload at least one valid image file"
            }), 400

        # Create job and return immediately
        job_id = str(uuid.uuid4())
        jobs[job_id] = {"status": "pending", "result": None, "error": None}

        # Start background thread for processing
        thread = threading.Thread(
            target=process_analysis_job,
            args=(job_id, image_contents, filenames, num_images)
        )
        thread.daemon = True
        thread.start()

        return jsonify({
            "job_id": job_id,
            "status": "pending",
            "message": "Analysis started. Poll /job-status/{job_id} for results."
        })

    except Exception:
        app.logger.exception("Failed to start analysis")
        return jsonify({
            "error": "Failed to start analysis",
            "message": "Something went wrong starting the analysis. Please try again."
        }), 500


@app.route('/job-status/<job_id>', methods=['GET'])
def get_job_status(job_id):
    """
    Get the status of an async analysis job.
    Returns status and result (if completed) or error (if failed).
    """
    if job_id not in jobs:
        return jsonify({
            "error": "Job not found",
            "message": f"No job found with ID: {job_id}"
        }), 404

    job = jobs[job_id]

    response = {
        "job_id": job_id,
        "status": job["status"]
    }

    if job["status"] == "completed":
        response["result"] = job["result"]
        # Clean up completed job after returning
        # (Keep it for a bit in case of retry, but could add TTL cleanup)
    elif job["status"] == "failed":
        response["error"] = job["error"]

    return jsonify(response)


@app.route('/generate-plan', methods=['POST'])
def generate_training_plan():
    """
    Generate a personalized training plan based on holistic analysis results.

    Expects: JSON with analysis data
    Returns: Markdown formatted training plan
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({
                "error": "No analysis data provided",
                "message": "Please analyze screenshots first before generating a training plan"
            }), 400

        # Extract key info for the prompt
        ski_iq = "Unknown"
        num_runs = data.get('num_screenshots', 1)

        # Try to get Ski:IQ from session_overview
        if 'session_overview' in data:
            ski_iq_range = data['session_overview'].get('ski_iq_range', {})
            avg_iq = ski_iq_range.get('average')
            if avg_iq:
                ski_iq = avg_iq

        # Format analysis data for the prompt
        analysis_json = json.dumps(data, indent=2)

        # Create the prompt
        prompt = TRAINING_PLAN_PROMPT.format(
            analysis_data=analysis_json,
            ski_iq=ski_iq,
            num_runs=num_runs
        )

        # Call Claude API for training plan (using Haiku for speed on Render's 30s timeout)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=6000,
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            system="""You are an elite ski coach with deep expertise in carving biomechanics and CARV technology.

Your knowledge is based on proven carving principles:
- A carved turn means the ski tail follows the exact arc of the tip (train on tracks)
- The 4 pillars: Edge Angle, Fore/Aft Balance, Rotary Control, Pressure Management
- G-force is a RESULT of good technique, not the goal itself
- Clean edge lock leaves a single thin line in snow

When creating training plans:
1. ALWAYS select drills from the provided Drill Library - these are proven, specific exercises
2. Match drills to the specific metric deficiencies identified
3. Use the Drill Selection Framework to pick appropriate drills
4. Include detailed execution instructions, not vague suggestions
5. Provide specific mental cues (3-5 words max)
6. Be encouraging but honest about what needs work
7. Use actual scores from the analysis
8. Structure sessions: Warm-up → Focus Phase → Integration → Cool-down
9. For EVERY drill recommended, include the YouTube video search link from the drill's Video field as a clickable reference (format: [Watch examples](URL))

Key principles:
- Quality over quantity (10 perfect turns beat 100 sloppy ones)
- One focus at a time during practice
- Progress from easy terrain to challenging
- Build on strengths to fix weaknesses
- Address root causes, not just symptoms"""
        )

        training_plan = response.content[0].text

        return jsonify({
            "training_plan": training_plan,
            "generated_at": datetime.now().isoformat(),
            "based_on_ski_iq": ski_iq,
            "based_on_screenshots": num_runs
        })

    except Exception as e:
        app.logger.exception("Request failed")
        error_message = str(e)

        if "api_key" in error_message.lower() or "authentication" in error_message.lower():
            return jsonify({
                "error": "API Key Error",
                "message": "Your Anthropic API key is missing or invalid. Please check your .env file."
            }), 401

        if "rate_limit" in error_message.lower():
            return jsonify({
                "error": "Rate Limited",
                "message": "Too many requests. Please wait a moment and try again."
            }), 429

        return jsonify({
            "error": "Plan generation failed",
            "message": "Something went wrong generating your training plan. Please try again."
        }), 500


@app.route('/chat', methods=['POST'])
def chat():
    """
    Conversational AI chatbot for carving technique questions.

    Expects: JSON with 'message' and optional 'history' (previous messages)
    Returns: JSON with 'response' from the AI coach
    """
    try:
        data = request.get_json()

        if not data or 'message' not in data:
            return jsonify({
                "error": "No message provided",
                "message": "Please send a message to the coach"
            }), 400

        user_message = data['message']
        history = data.get('history', [])
        analysis_data = data.get('analysisData', None)
        session_history = data.get('sessionHistory', None)
        progression_insights = data.get('progressionInsights', None)

        # Build session context from analysis data
        session_context = ""
        if analysis_data:
            session_context = "\n\n## PATRICK'S CURRENT SESSION DATA\n\n"

            # Session overview (Ski:IQ, turns analyzed, etc.)
            if 'session_overview' in analysis_data:
                so = analysis_data['session_overview']
                if so.get('ski_iq_range'):
                    iq_range = so['ski_iq_range']
                    avg_iq = iq_range.get('average') or (
                        (iq_range.get('lowest', 0) + iq_range.get('highest', 0)) / 2
                        if iq_range.get('lowest') and iq_range.get('highest') else None
                    )
                    session_context += f"**Ski:IQ Range:** {iq_range.get('lowest', iq_range.get('min', 'N/A'))} - {iq_range.get('highest', iq_range.get('max', 'N/A'))}"
                    if avg_iq:
                        session_context += f" (avg: {avg_iq:.0f})\n"
                    else:
                        session_context += "\n"
                if so.get('total_turns_analyzed'):
                    session_context += f"**Total Turns Analyzed:** {so['total_turns_analyzed']}\n"
                if so.get('terrain_types_seen'):
                    session_context += f"**Terrain Types:** {', '.join(so['terrain_types_seen'])}\n"
                if so.get('session_date_display'):
                    session_context += f"**Session Date:** {so['session_date_display']}\n"

            # Overall metrics (Balance, Edging, Rotary, Performance)
            if 'overall_metrics' in analysis_data:
                om = analysis_data['overall_metrics']
                session_context += "\n**Metric Scores:**\n"

                for category in ['balance', 'edging', 'rotary', 'performance']:
                    if category in om and om[category]:
                        cat_data = om[category]
                        cat_avg = cat_data.get('category_average', 'N/A')
                        session_context += f"\n*{category.title()}* (avg: {cat_avg}/100):\n"
                        for metric_name, metric_val in cat_data.items():
                            if metric_name != 'category_average' and isinstance(metric_val, (int, float)):
                                session_context += f"  - {metric_name.replace('_', ' ').title()}: {metric_val}/100\n"

            # Holistic insights
            if 'holistic_insights' in analysis_data:
                hi = analysis_data['holistic_insights']
                session_context += "\n**Holistic Analysis:**\n"
                if hi.get('skiing_style'):
                    session_context += f"- Skiing Style: {hi['skiing_style']}\n"
                if hi.get('signature_move'):
                    session_context += f"- Signature Move: {hi['signature_move']}\n"
                if hi.get('consistency_pattern'):
                    session_context += f"- Consistency: {hi['consistency_pattern']}\n"
                if hi.get('primary_limiter'):
                    session_context += f"- Primary Limiter: {hi['primary_limiter']}\n"
                if hi.get('biggest_strength'):
                    session_context += f"- Biggest Strength: {hi['biggest_strength']}\n"

            # Training priorities
            if 'training_priorities' in analysis_data:
                tp = analysis_data['training_priorities']
                if tp.get('focus_areas'):
                    session_context += "\n**Priority Focus Areas:**\n"
                    for area in tp['focus_areas'][:3]:
                        session_context += f"- {area.get('area', 'Unknown')}: {area.get('current_score', '?')}/100 → Target: {area.get('target_score', '?')}/100\n"
                        if area.get('why'):
                            session_context += f"  Why: {area.get('why')}\n"
                        if area.get('quick_win'):
                            session_context += f"  Quick win: {area.get('quick_win')}\n"
                if tp.get('strengths'):
                    session_context += "\n**Key Strengths:**\n"
                    for s in tp['strengths'][:3]:
                        session_context += f"- {s.get('area', 'Unknown')}: {s.get('detail', '')}\n"

            # Observations
            if 'observations' in analysis_data:
                session_context += f"\n**Coach Observations:** {analysis_data['observations']}\n"

            # Detailed observations
            if 'detailed_observations' in analysis_data:
                session_context += f"\n**Detailed Analysis:** {analysis_data['detailed_observations']}\n"

            # Holistic analysis (alternate key structure)
            if 'holistic_analysis' in analysis_data:
                ha = analysis_data['holistic_analysis']
                session_context += "\n**Holistic Analysis:**\n"
                if ha.get('skiing_style'):
                    session_context += f"- Skiing Style: {ha['skiing_style']}\n"
                if ha.get('biggest_limiter'):
                    session_context += f"- Biggest Limiter: {ha['biggest_limiter']}\n"
                if ha.get('hidden_strength'):
                    session_context += f"- Hidden Strength: {ha['hidden_strength']}\n"
                if ha.get('technique_signature'):
                    session_context += f"- Technique Signature: {ha['technique_signature']}\n"

            # Top priorities (alternate key structure)
            if 'top_3_priorities' in analysis_data:
                session_context += "\n**Top Priorities:**\n"
                for p in analysis_data['top_3_priorities'][:3]:
                    session_context += f"- {p.get('area', '?')} ({p.get('current_score', '?')}/100): {p.get('quick_win', '')}\n"

            # Top strengths
            if 'top_3_strengths' in analysis_data:
                session_context += "\n**Key Strengths:**\n"
                for s in analysis_data['top_3_strengths'][:3]:
                    session_context += f"- {s.get('area', '?')} ({s.get('score', '?')}/100): {s.get('why_it_matters', '')}\n"

        # Add session history (progression data across multiple sessions)
        if session_history and len(session_history) > 0:
            session_context += "\n\n## PATRICK'S SESSION HISTORY (oldest to newest)\n\n"
            for i, session in enumerate(session_history):
                session_context += f"**Session {i+1}** ({session.get('date', 'unknown date')}):\n"
                if session.get('skiIQ'):
                    session_context += f"  Ski:IQ: {session['skiIQ']}\n"
                if session.get('balance') is not None:
                    session_context += f"  Balance: {session['balance']}/100\n"
                if session.get('twr') is not None:
                    session_context += f"  TWR: {session['twr']}/100\n"
                if session.get('edging') is not None:
                    session_context += f"  Edging: {session['edging']}/100\n"
                if session.get('edgeAngle') is not None:
                    session_context += f"  Edge Angle: {session['edgeAngle']}°\n"
                if session.get('rotary') is not None:
                    session_context += f"  Rotary: {session['rotary']}/100\n"
                if session.get('notes'):
                    session_context += f"  Notes: {session['notes']}\n"
                session_context += "\n"

        # Add AI progression insights if available
        if progression_insights:
            session_context += f"\n## AI PROGRESSION ANALYSIS INSIGHTS\n\n{progression_insights}\n"

        # Build conversation messages
        messages = []

        # Add conversation history (limit to last 10 exchanges to manage context)
        for msg in history[-20:]:  # Last 20 messages (10 exchanges)
            messages.append({
                "role": msg.get('role', 'user'),
                "content": msg.get('content', '')
            })

        # Add current user message
        messages.append({
            "role": "user",
            "content": user_message
        })

        # Call Claude API
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",  # Fast for chat
            max_tokens=1500,
            messages=messages,
            system=f"""You are Coach Carv, an elite ski coach and carving expert with decades of experience training recreational skiers to advanced racers. You have deep knowledge of CARV technology and biomechanics.

## SKIER PROFILE - PATRICK

- **Height:** 173cm
- **Weight:** 71kg
- **Build:** Medium, well-proportioned for skiing
- **Biomechanical notes:** At this height/weight, Patrick has a good power-to-weight ratio. His stance width should be approximately hip-width (around 30-35cm). He should focus on maintaining a compact, centered stance given his medium frame.

{CARV_METRICS_CONTEXT}
{session_context}

## YOUR COACHING STYLE

1. **Be specific and technical** - Give precise biomechanical advice, not vague suggestions
2. **Use vivid cues** - Short, memorable phrases like "belly button to the valley" or "paint the snow with your edges"
3. **Reference CARV metrics** - When Patrick has session data, directly reference his actual scores and what they mean
4. **Reference his session data** - If Patrick has uploaded screenshots, use his actual metrics to give personalized advice
5. **Diagnose root causes** - Don't just treat symptoms, find the underlying issue
6. **Be encouraging but honest** - Celebrate progress while being direct about what needs work
7. **Use analogies** - Help skiers visualize concepts (train on tracks, stacking quarters, etc.)
8. **Prioritize safety** - Never give advice that could lead to injury
9. **Address Patrick by name** - Make the coaching personal

## RESPONSE FORMAT

- Keep responses concise but thorough (2-4 paragraphs typically)
- Use bullet points for drills or multiple tips
- Include 1-2 specific drills when relevant
- When recommending drills, include the YouTube video search link from the drill's Video field as a clickable reference (format: [Watch examples](URL))
- End with a clear actionable takeaway when appropriate
- When session data is available, reference specific metrics

You're having a friendly, expert conversation with Patrick. Be personable but professional."""
        )

        ai_response = response.content[0].text

        return jsonify({
            "response": ai_response,
            "timestamp": datetime.now().isoformat()
        })

    except Exception as e:
        app.logger.exception("Request failed")
        error_message = str(e)

        if "api_key" in error_message.lower() or "authentication" in error_message.lower():
            return jsonify({
                "error": "API Key Error",
                "message": "API key issue. Please check configuration."
            }), 401

        if "rate_limit" in error_message.lower():
            return jsonify({
                "error": "Rate Limited",
                "message": "Too many requests. Please wait a moment and try again."
            }), 429

        return jsonify({
            "error": "Chat failed",
            "message": "Something went wrong. Please try again."
        }), 500


# ============================================================================
# SESSION CRUD ENDPOINTS - For tracking progression over time
# ============================================================================

@app.route('/sessions', methods=['POST'])
def create_session():
    """Save a new skiing session with analysis data."""
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "No data provided"}), 400

        # Parse session date
        session_date_str = data.get('session_date')
        if session_date_str:
            try:
                session_date = datetime.fromisoformat(session_date_str.replace('Z', '+00:00'))
            except ValueError:
                session_date = datetime.utcnow()
        else:
            session_date = datetime.utcnow()

        # Extract Ski:IQ from metrics if available
        ski_iq = None
        metrics = data.get('metrics', {})
        if metrics:
            session_overview = metrics.get('session_overview', {})
            ski_iq_range = session_overview.get('ski_iq_range', {})
            # Try 'average' first, then calculate from 'lowest'/'highest' (AI response keys)
            if ski_iq_range.get('average'):
                ski_iq = ski_iq_range['average']
            elif ski_iq_range.get('lowest') and ski_iq_range.get('highest'):
                ski_iq = (ski_iq_range['lowest'] + ski_iq_range['highest']) / 2
            elif ski_iq_range.get('min') and ski_iq_range.get('max'):
                ski_iq = (ski_iq_range['min'] + ski_iq_range['max']) / 2

        session = Session(
            session_date=session_date,
            location=data.get('location'),
            ski_iq=ski_iq,
            metrics=metrics,
            training_plan=data.get('training_plan'),
            notes=data.get('notes')
        )

        db.session.add(session)
        db.session.commit()

        return jsonify(session.to_dict()), 201

    except Exception as e:
        db.session.rollback()
        app.logger.exception("Failed to save session")
        return jsonify({"error": "Failed to save session"}), 500


@app.route('/sessions', methods=['GET'])
def list_sessions():
    """List all saved sessions, ordered by date descending."""
    try:
        sessions = Session.query.order_by(Session.session_date.desc()).all()
        return jsonify([s.to_dict() for s in sessions])
    except Exception as e:
        app.logger.exception("Failed to fetch sessions")
        return jsonify({"error": "Failed to fetch sessions"}), 500


@app.route('/sessions/<session_id>', methods=['GET'])
def get_session(session_id):
    """Get a single session by ID."""
    try:
        session = Session.query.get(session_id)
        if not session:
            return jsonify({"error": "Session not found"}), 404
        return jsonify(session.to_dict())
    except Exception as e:
        app.logger.exception("Failed to fetch session")
        return jsonify({"error": "Failed to fetch session"}), 500


@app.route('/sessions/<session_id>', methods=['PUT'])
def update_session(session_id):
    """Update a session (e.g., add notes or training plan)."""
    try:
        session = Session.query.get(session_id)
        if not session:
            return jsonify({"error": "Session not found"}), 404

        data = request.get_json()

        if 'location' in data:
            session.location = data['location']
        if 'notes' in data:
            session.notes = data['notes']
        if 'training_plan' in data:
            session.training_plan = data['training_plan']

        db.session.commit()
        return jsonify(session.to_dict())
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Failed to update session")
        return jsonify({"error": "Failed to update session"}), 500


@app.route('/sessions/<session_id>', methods=['DELETE'])
def delete_session(session_id):
    """Delete a session."""
    try:
        session = Session.query.get(session_id)
        if not session:
            return jsonify({"error": "Session not found"}), 404

        db.session.delete(session)
        db.session.commit()
        return jsonify({"message": "Session deleted"})
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Failed to delete session")
        return jsonify({"error": "Failed to delete session"}), 500


# ============================================================================
# PROGRESSION ANALYSIS - AI-powered trend analysis
# ============================================================================

PROGRESSION_PROMPT = """You are analyzing a skier's progression over multiple skiing sessions.
You have access to CARV metrics data from each session. Your PRIMARY goal is to find CORRELATIONS between metrics and hypothesize WHY changes occur.

## YOUR TASK

### 1. EXTRACT ALL METRICS
For each session, extract EVERY metric value you can find:
- Ski:IQ (overall score)
- Balance metrics: outside ski %, fore/aft balance, stance width, weight distribution
- Edging metrics: edge angle (max/avg), turn shape score, carving %
- Rotary metrics: rotation separation, hip alignment, counter
- Performance: G-force, speed, turn radius
- Any other metrics present

### 2. ANALYZE METRIC CORRELATIONS (MOST IMPORTANT)
Look for patterns where metrics change TOGETHER:
- When metric A improved, did metric B also improve/decline?
- Are there inverse relationships (A up = B down)?
- Which metrics seem to move independently?

Examples of correlations to look for:
- "Edge angle increased → Turn shape improved" (positive correlation)
- "Speed increased → Balance decreased" (inverse correlation)
- "Outside ski pressure improved → Edge angle also improved" (linked skills)

### 3. HYPOTHESIZE CAUSALITY
For each correlation found, explain WHY this might happen biomechanically:
- What technique change could cause both metrics to move?
- Is one metric likely CAUSING the other to change?
- What does this tell us about the skier's technique evolution?

### 4. SESSION-BY-SESSION BREAKDOWN
Show the actual metric values for each session so the skier can see the raw data.

## OUTPUT FORMAT

Return a JSON object with this structure:
{
    "sessions_breakdown": [
        {
            "date": "YYYY-MM-DD",
            "ski_iq": number,
            "key_metrics": {
                "edge_angle_max": number,
                "outside_ski_pressure": number,
                "balance_score": number,
                "turn_shape": number,
                "carving_percentage": number
            }
        }
    ],
    "all_metrics_tracked": ["list of all metric names found across sessions"],
    "metric_correlations": [
        {
            "metric_a": "name",
            "metric_b": "name",
            "correlation": "positive|negative|none",
            "observation": "When X increased by Y%, Z also increased by W%",
            "biomechanical_explanation": "This happens because...",
            "confidence": "high|medium|low"
        }
    ],
    "causality_hypotheses": [
        {
            "cause": "metric or technique change",
            "effect": "resulting metric changes",
            "explanation": "Detailed biomechanical reasoning",
            "evidence": "Data points supporting this hypothesis"
        }
    ],
    "metric_trends": {
        "ski_iq": {"values": [list of values per session], "direction": "improving|stable|declining", "change": "+X% or -X%"},
        "edge_angle": {"values": [], "direction": "...", "change": "..."},
        "balance": {"values": [], "direction": "...", "change": "..."},
        "other_metrics": [{"name": "...", "values": [], "direction": "...", "change": "..."}]
    },
    "key_insights": [
        "Insight 1: Most significant finding about metric relationships",
        "Insight 2: ...",
        "Insight 3: ..."
    ],
    "technique_narrative": "Plain language description of how technique is evolving based on the correlations found",
    "recommendations": {
        "primary_focus": "Based on correlations, focus on X because it appears to drive improvements in Y and Z",
        "secondary_focus": "...",
        "suggested_drills": ["..."],
        "what_to_watch": "When you improve X, watch for changes in Y"
    },
    "summary": "2-3 sentence summary focusing on the most important metric correlations discovered"
}
"""


@app.route('/progression', methods=['POST'])
def analyze_progression():
    """
    Analyze progression across multiple sessions.
    Can optionally specify session IDs, otherwise uses all sessions.
    """
    try:
        data = request.get_json() or {}
        session_ids = data.get('session_ids')

        # Get all sessions for reference
        all_sessions = Session.query.order_by(Session.session_date.asc()).all()

        # Filter to selected sessions if specified
        if session_ids and len(session_ids) > 0:
            sessions = [s for s in all_sessions if s.id in session_ids]
        else:
            sessions = all_sessions

        if len(sessions) < 2:
            return jsonify({
                "error": "Not enough sessions",
                "message": "Need at least 2 sessions to analyze progression"
            }), 400

        # Build context for Claude - extract key metrics, keep payload lean for speed
        session_data = []
        for s in sessions:
            metrics = s.metrics or {}
            overall = metrics.get('overall_metrics', {})
            overview = metrics.get('session_overview', {})
            # Extract just the numerical data and key observations
            session_entry = {
                "date": s.session_date.strftime("%Y-%m-%d") if s.session_date else "Unknown",
                "session_id": s.id,
                "location": s.location,
                "ski_iq": overview.get('ski_iq_range', {}).get('average') or s.ski_iq,
                "terrain": overview.get('terrain_types_seen', []),
                "turns_analyzed": overview.get('total_turns_analyzed'),
                "balance": overall.get('balance', {}),
                "edging": overall.get('edging', {}),
                "rotary": overall.get('rotary', {}),
                "performance": overall.get('performance', {}),
                "top_priorities": [p.get('area') + f" ({p.get('current_score')})" for p in metrics.get('top_3_priorities', []) if p.get('area')],
                "biggest_limiter": metrics.get('holistic_analysis', {}).get('biggest_limiter', ''),
                "detailed_observations": metrics.get('detailed_observations', '')[:500]
            }
            session_data.append(session_entry)

        # Call Claude for analysis - use Haiku for speed within Render's timeout
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",  # Fast model to stay within Render timeout
            max_tokens=4000,
            messages=[{
                "role": "user",
                "content": f"""Analyze this skier's progression across {len(sessions)} sessions.
IMPORTANT: Focus on finding correlations between metrics and how they change over time.

SESSION DATA:
{json.dumps(session_data, indent=2)}

{PROGRESSION_PROMPT}

Return ONLY valid JSON, no markdown code blocks."""
            }],
            system=CARV_METRICS_CONTEXT
        )

        response_text = response.content[0].text
        cleaned_json = clean_json_response(response_text)

        try:
            progression_data = json.loads(cleaned_json)
        except json.JSONDecodeError:
            return jsonify({
                "error": "Failed to parse progression analysis",
                "raw_response": response_text[:500]
            }), 500

        progression_data["sessions_analyzed"] = len(sessions)
        progression_data["session_ids_analyzed"] = [s.id for s in sessions]
        progression_data["date_range"] = {
            "from": sessions[0].session_date.isoformat() if sessions[0].session_date else None,
            "to": sessions[-1].session_date.isoformat() if sessions[-1].session_date else None
        }
        # Include list of all available sessions for UI selection
        progression_data["available_sessions"] = [
            {
                "id": s.id,
                "date": s.session_date.strftime("%Y-%m-%d") if s.session_date else "Unknown",
                "ski_iq": s.ski_iq,
                "location": s.location,
                "selected": s.id in [sess.id for sess in sessions]
            }
            for s in all_sessions
        ]

        return jsonify(progression_data)

    except Exception as e:
        app.logger.exception("Progression analysis failed")
        return jsonify({"error": "Progression analysis failed"}), 500


@app.route('/compare', methods=['POST'])
def compare_sessions():
    """Compare two specific sessions side-by-side."""
    try:
        data = request.get_json()

        if not data or 'session_id_1' not in data or 'session_id_2' not in data:
            return jsonify({
                "error": "Missing session IDs",
                "message": "Provide session_id_1 and session_id_2"
            }), 400

        session1 = Session.query.get(data['session_id_1'])
        session2 = Session.query.get(data['session_id_2'])

        if not session1 or not session2:
            return jsonify({"error": "One or both sessions not found"}), 404

        # Determine which is earlier/later
        if session1.session_date <= session2.session_date:
            earlier, later = session1, session2
        else:
            earlier, later = session2, session1

        # Call Claude for comparison
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            messages=[{
                "role": "user",
                "content": f"""Compare these two skiing sessions and describe what changed:

EARLIER SESSION ({earlier.session_date.strftime("%Y-%m-%d") if earlier.session_date else "Unknown"}):
Location: {earlier.location}
Ski:IQ: {earlier.ski_iq}
Metrics: {json.dumps(earlier.metrics, indent=2) if earlier.metrics else "No metrics"}

LATER SESSION ({later.session_date.strftime("%Y-%m-%d") if later.session_date else "Unknown"}):
Location: {later.location}
Ski:IQ: {later.ski_iq}
Metrics: {json.dumps(later.metrics, indent=2) if later.metrics else "No metrics"}

Provide a comparison in JSON format:
{{
    "ski_iq_change": {{"from": X, "to": Y, "change": "+/-Z", "assessment": "..."}},
    "key_improvements": ["..."],
    "areas_declined": ["..."],
    "technique_changes": "Plain language description of how technique evolved",
    "recommendations": "What to focus on next based on this comparison"
}}

Return ONLY valid JSON."""
            }],
            system=CARV_METRICS_CONTEXT
        )

        response_text = response.content[0].text
        cleaned_json = clean_json_response(response_text)

        try:
            comparison_data = json.loads(cleaned_json)
        except json.JSONDecodeError:
            return jsonify({
                "error": "Failed to parse comparison",
                "raw_response": response_text[:500]
            }), 500

        comparison_data["session_1"] = earlier.to_dict()
        comparison_data["session_2"] = later.to_dict()

        return jsonify(comparison_data)

    except Exception as e:
        app.logger.exception("Comparison failed")
        return jsonify({"error": "Comparison failed"}), 500


if __name__ == '__main__':
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("\n" + "="*60)
        print("WARNING: ANTHROPIC_API_KEY not found in environment!")
        print("Please create a .env file in the backend directory with:")
        print("ANTHROPIC_API_KEY=your_api_key_here")
        print("="*60 + "\n")
    else:
        print("\n" + "="*60)
        print("CARV Analyzer Backend Starting...")
        print("API Key: Configured")
        print("="*60 + "\n")

    app.run(host='0.0.0.0', port=5001, debug=os.getenv("FLASK_DEBUG", "").lower() in ("1", "true"))
