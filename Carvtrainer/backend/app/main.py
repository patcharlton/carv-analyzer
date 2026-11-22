"""
CARV Skiing Analysis Backend API
Flask server that uses Claude AI to analyze CARV screenshots and generate training plans.
"""

import os
import base64
import copy
import hmac
import logging
import time
import json
import re
import uuid
import threading
from io import BytesIO
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from anthropic import Anthropic
from dotenv import load_dotenv
from PIL import Image
from PIL.ExifTags import TAGS

try:  # gunicorn app.main:app (from the backend folder)
    from app.carv_metrics import CURRENT_CARV_GUIDE, default_ski_iq_version, describe_metrics, normalise_analysis
    from app.training_plan import (PLAN_TOOL, PlanError, assemble_plan, drills_practised, history_entry, parse_stored_plan,
                                   system_blocks as plan_system_blocks, user_message as plan_user_message)
except ImportError:  # python app/main.py
    from carv_metrics import CURRENT_CARV_GUIDE, default_ski_iq_version, describe_metrics, normalise_analysis
    from training_plan import (PLAN_TOOL, PlanError, assemble_plan, drills_practised, history_entry, parse_stored_plan,
                               system_blocks as plan_system_blocks, user_message as plan_user_message)


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

logging.basicConfig(level=logging.INFO)

# Uploads: up to 10 screenshots of 5MB each, plus form overhead
MAX_FILES_PER_UPLOAD = 10
app.config["MAX_CONTENT_LENGTH"] = 55 * 1024 * 1024

# ============================================================================
# LOGIN - one shared password, checked here rather than in the browser
# ============================================================================
# APP_PASSWORD: the site password. SECRET_KEY: signs login tokens (any long random string).
# Without both, every route except /health refuses requests rather than running unprotected.
APP_PASSWORD = os.getenv("APP_PASSWORD")
SECRET_KEY = os.getenv("SECRET_KEY")
LOGIN_TOKEN_MAX_AGE = 30 * 24 * 3600  # stay signed in on a device for 30 days
PUBLIC_PATHS = {"/health", "/login"}

_token_signer = URLSafeTimedSerializer(SECRET_KEY, salt="carv-login") if SECRET_KEY else None
_failed_logins = {}  # ip -> list of failure timestamps (per process; enough to slow guessing)
MAX_FAILED_LOGINS = 10
FAILED_LOGIN_WINDOW = 15 * 60


def _client_ip():
    # Render's proxy appends the real client address last; earlier entries can be faked by the client
    forwarded = request.headers.get("X-Forwarded-For", "")
    return forwarded.split(",")[-1].strip() or request.remote_addr or "unknown"


@app.route('/login', methods=['POST'])
def login():
    if not APP_PASSWORD or not _token_signer:
        return jsonify({"error": "Login not configured", "message": "The server has no APP_PASSWORD or SECRET_KEY set."}), 503

    ip = _client_ip()
    now = time.time()
    recent = [t for t in _failed_logins.get(ip, []) if now - t < FAILED_LOGIN_WINDOW]
    if len(recent) >= MAX_FAILED_LOGINS:
        return jsonify({"error": "Too many attempts", "message": "Too many wrong passwords. Please wait 15 minutes."}), 429

    password = str((request.get_json(silent=True) or {}).get("password", ""))
    if not hmac.compare_digest(password.encode(), APP_PASSWORD.encode()):
        recent.append(now)
        _failed_logins[ip] = recent
        return jsonify({"error": "Incorrect password", "message": "Incorrect password"}), 401

    _failed_logins.pop(ip, None)
    return jsonify({"token": _token_signer.dumps({"v": 1}), "expires_in": LOGIN_TOKEN_MAX_AGE})


@app.before_request
def require_login():
    """Every route except /health and /login needs a valid login token."""
    if request.method == "OPTIONS" or request.path in PUBLIC_PATHS:
        return None
    if not APP_PASSWORD or not _token_signer:
        return jsonify({"error": "Login not configured", "message": "The server has no APP_PASSWORD or SECRET_KEY set."}), 503
    header = request.headers.get("Authorization", "")
    token = header[7:] if header.startswith("Bearer ") else ""
    try:
        _token_signer.loads(token, max_age=LOGIN_TOKEN_MAX_AGE)
    except SignatureExpired:
        return jsonify({"error": "Login expired", "message": "Please sign in again."}), 401
    except BadSignature:
        return jsonify({"error": "Not signed in", "message": "Please sign in."}), 401
    return None


@app.errorhandler(413)
def upload_too_large(_error):
    return jsonify({"error": "Upload too large", "message": "Too much data in one upload. Send fewer or smaller screenshots."}), 413

# Database configuration
database_url = os.getenv("DATABASE_URL")
if database_url:
    # Render gives postgres:// or postgresql://. Name the psycopg2 driver explicitly:
    # SQLAlchemy 2.1 defaults postgresql:// to psycopg 3, which isn't installed.
    for prefix in ("postgres://", "postgresql://"):
        if database_url.startswith(prefix):
            database_url = "postgresql+psycopg2://" + database_url[len(prefix):]
            break
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


class Job(db.Model):
    """A screenshot analysis running in the background. Kept in the database (not in memory)
    so polling works across workers and a restart leaves a clear 'failed' instead of a 404."""
    __tablename__ = "analysis_jobs"

    id = db.Column(db.String(36), primary_key=True)
    status = db.Column(db.String(20), nullable=False, default="pending")  # pending|processing|completed|failed
    result = db.Column(db.JSON)
    error = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


JOB_KEEP_SECONDS = 3600       # delete finished jobs after an hour
JOB_STALE_SECONDS = 10 * 60   # a job not updated for this long was lost (worker restarted)

# Create tables on startup (new tables only; existing ones are left as they are)
with app.app_context():
    db.create_all()


def set_job(job_id, **fields):
    """Update a job from the background thread (which needs its own app context)."""
    with app.app_context():
        job = db.session.get(Job, job_id)
        if job is None:
            return
        for key, value in fields.items():
            setattr(job, key, value)
        db.session.commit()

# Initialize Anthropic client. Each attempt is capped so a slow reply fails cleanly:
# request routes stay inside gunicorn's 120s timeout (55s x 2 attempts), and the
# background analysis stays inside the frontend's 3-minute polling window.
client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), timeout=55.0, max_retries=1)
ANALYSIS_TIMEOUT_SECONDS = 80.0

# Screenshot analysis (vision). claude-sonnet-4-20250514, used here before, was retired on 15 June 2026.
ANALYSIS_MODEL = "claude-sonnet-5-5"
# Fast model for training plans, chat, progression and comparisons (kept quick for Render's request limits)
FAST_MODEL = "claude-haiku-4-5-20251001"


def create_analysis_message(**kwargs):
    """
    Call the analysis model. Medium effort keeps a multi-screenshot analysis well inside the
    frontend's 3-minute polling window; max_tokens covers thinking as well as the JSON reply.
    The server-side fallback re-runs the request on another model if this one declines it.
    """
    return client.with_options(timeout=ANALYSIS_TIMEOUT_SECONDS).beta.messages.create(
        model=ANALYSIS_MODEL,
        max_tokens=16000,
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        **kwargs,
    )


def get_reply_text(response):
    """The reply's text. Newer models put a thinking block first, so never read content[0]."""
    return "".join(block.text for block in response.content if getattr(block, "type", None) == "text")


class ModelReplyError(Exception):
    """A model reply that can't be used; the message is safe to show the user."""


def request_json_reply(create, attempts=2):
    """
    Call `create()` (a function making one model request) and parse the JSON reply.
    A reply that isn't valid JSON is retried once; refusals and cut-off replies are not.
    """
    for attempt in range(attempts):
        response = create()
        if response.stop_reason == "refusal":
            raise ModelReplyError("The AI declined this request. Please try again with CARV screenshots only.")
        if response.stop_reason == "max_tokens":
            raise ModelReplyError("The AI reply was too long and got cut off. Try fewer screenshots at once.")
        try:
            return json.loads(clean_json_response(get_reply_text(response)))
        except json.JSONDecodeError:
            app.logger.warning("Model reply was not valid JSON (attempt %d of %d)", attempt + 1, attempts)
    raise ModelReplyError("The AI reply wasn't in the expected format. Please try again.")


def api_error_message(error):
    """A user-facing message for an exception from a model call (details go to the log)."""
    text = str(error).lower()
    if "api_key" in text or "authentication" in text:
        return "The server's Anthropic API key is missing or invalid."
    if "rate_limit" in text:
        return "Rate limited. Please wait a moment and try again."
    if "timeout" in text or "timed out" in text:
        return "The AI took too long to reply. Please try again."
    return "Analysis failed. Please try again."

# CARV Metrics Context for AI Analysis - Comprehensive Carving Knowledge Base
CARV_METRICS_CONTEXT = """
You are an elite ski coach and CARV technology expert with deep knowledge of carving biomechanics.
Your analysis is based on proven carving principles, not generic skiing advice.
""" + CURRENT_CARV_GUIDE + """

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

## CARV METRICS - DEEP INTERPRETATION (older detailed notes; the section above takes precedence)

### Ski:IQ Score (Overall Performance) - rough guide only, not Carv's official bands
- 100 = Average recreational skier
- 100-115 = Intermediate - developing skills
- 115-125 = Advanced intermediate - linking carved turns
- 125-140 = Advanced - consistent carving on varied terrain
- 140-155 = Expert - high edge angles, dynamic skiing
- 155+ = Elite - race-level technique

### FORE/AFT BALANCE (coaching concepts - Carv no longer shows these two as scores)
Evidence today: Carv's Balance rating (Ok / Good / Great), Weight Release, and skis running
away at the start or end of the turn. Never report a Start of Turn or Centered Balance number.

**1. Forward pressure at the start of the turn**
- WHAT IT IS: Weight shift to ski tips at turn initiation
- WHY IT MATTERS: Forward pressure engages the front of the ski first, creating early edge grip
- LOW SCORE INDICATES:
  * Sitting back in the boots (common fear response)
  * Late turn initiation
  * Skis running away at start of turn
- BIOMECHANICS: Shin pressure against boot tongue, hips forward over toes
- TARGET FEELING: "Driving the front of the ski into the turn"

**2. Staying centred through the turn**
- WHAT IT IS: Maintaining balance over the center of the ski during the turn
- WHY IT MATTERS: Centered stance allows the whole ski edge to engage
- LOW SCORE INDICATES:
  * Getting pulled into the backseat mid-turn
  * Upper body leaning uphill (defensive posture)
  * Weak core engagement
- BIOMECHANICS: Ankle flexion, knee drive, hips stacked over feet
- TARGET FEELING: "Balanced over the arch of the foot"

**3. Weight Release** (Carv metric, %; older sessions call it Transition Weight Release / TWR)
- CARV'S DEFINITION: The percentage of weight you release during the transition between turns.
- HOW TO THINK ABOUT IT: Compares peak forces (end of turn) to minimum forces (transition phase),
  roughly (Peak G-force - Trough G-force) / Peak G-force × 100. This formula is a coaching model, not Carv's published method.
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

**4. Edge Build** (Carv metric, %; older sessions call it Progressive Edge Build)
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

### Weak forward pressure at turn start (Balance rating below Great, skis running away early)
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

### Low Weight Release Score
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

### Pulled back or off-centre mid-turn (Balance rating below Great)
ROOT CAUSES:
1. Getting pulled back by G-forces - not anticipating the load
2. Upper body leaning uphill - defensive "survival" stance
3. Weak core - can't maintain position under load
CASCADING EFFECTS: Loss of ski control in bottom half of turn

### Low Edge Build Score
ROOT CAUSES:
1. "Park and ride" habit - set edge angle and hold
2. Fear of increasing commitment - playing it safe
3. Lack of dynamic range - don't know how to increase through turn
CASCADING EFFECTS: Predictable skiing, limited ability on varied terrain

### Turn Closure outside its zone / Carv flags "Smoothly linking turns"
ROOT CAUSES:
1. Turns not finished: skis released before they come round across the slope (low closure),
   often with a narrow Corridor and fast Tempo
2. Over-finished turns: skidding or braking at the end of the turn (high closure), often a
   speed-control habit
3. Late or abrupt transitions: the next turn starts with a pivot rather than a roll onto the new edges
EVIDENCE TO USE: Tempo (turns/min), Corridor (m), Radius (m), Turn Shape, Weight Release
CASCADING EFFECTS: Choppy rhythm, inconsistent speed, turns that do not link into one flowing line

### Low Edging Smoothness Score
ROOT CAUSES:
1. Edge set abruptly ("click" onto the edge) instead of rolled on progressively
2. Edge dumped or released suddenly at the end of the turn
3. Stiff ankles and knees, so edge angle changes in steps rather than continuously
CASCADING EFFECTS: Skis chatter or lose grip, harder to link turns smoothly

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

Judge every metric against its dashed performance zone on screen, not against generic numbers.

**Balance rating below Great** → Root causes: Fear (backseat), weak ankle flex, G-force pulling back, defensive uphill lean
**Low Weight Release** → Root causes: Fear of fall line, Z-turn habit, static body
**Low Edge Angle** → Root causes: Fear of commitment, no hip angulation, upper body rotation
**Low Early Edging** → Root causes: Pivot habit, slow weight transfer, sequential movements
**Low Edging Smoothness** → Root causes: Edge set abruptly, sudden release, stiff ankles/knees
**Low Edging Similarity** → Root causes: Dominant side, injury compensation, equipment
**Low Edge Build** → Root causes: "Park and ride" habit, fear, limited dynamic range
**Low Parallel Skis** → Root causes: Stemming, A-frame, inside ski not tipping
**Low Turn Shape** → Root causes: Pivot-based turning, speed checking, impatience
**Turn Closure outside its zone** → Root causes: Turns not finished, or over-finished/braking; check Tempo, Corridor, Radius
**Low G-Force with good edges** → Skidding despite tipping - but on SHORT TURNS lower G-Force is expected

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
    "total_turns_analyzed": <sum of turns if visible, or null>,
    "location": "<resort/location as shown, e.g. 'Les Houches (Chamonix), FR', or null>",
    "ski_iq_version": "<'stelvio' if the screenshots show the Carv app 10 layout (bottom tabs You / Activity / Community / Coaching with a round record button), 'wasatch' if they show the older layout (bottom tabs Home / Coaching / Ski / Community / More), otherwise null>"
  }},
  "carv_metrics": {{
    "<one entry per metric visible in the Ski:IQ Metrics list, using these keys only: parallel_skis, turn_shape, turn_closure, early_edging, edging_smoothness, edging_similarity, edge_build, edge_angle, weight_release, g_force, slope_pitch>": {{
      "value": <the number exactly as shown, without the unit: % metrics as 0-100, edge_angle and slope_pitch in degrees, g_force in g>,
      "zone": "<'below', 'in' or 'above' - where the value marker sits relative to the dashed performance zone band; null if you cannot tell>"
    }}
  }},
  "run_details": {{
    "run_name": "<run or segment name if shown, e.g. 'Kandahar Supérieur La Cassure', else null>",
    "technique": "<technique mode chip as shown, e.g. 'carving', 'short turns', 'parallel'; null if not shown>",
    "basis": "<e.g. 'Top 8 turns' if shown, else null>",
    "skill_highlights": [{{"number": <skill number shown, e.g. 1>, "name": "<e.g. 'Turning without skidding'>", "status": "<as shown, e.g. 'Nailed it' or 'One to work on'>"}}],
    "pitch_deg": <number or null>,
    "snow_surface": "<as shown, e.g. 'Smooth', or null>",
    "carving_turns": {{"count": <e.g. 8 or null>, "of": <e.g. 8 or null>}},
    "tempo_tpm": <turns per minute or null>,
    "corridor_m": <metres or null>,
    "radius_m": <metres or null>,
    "balance_rating": "<'Ok', 'Good' or 'Great' - the highlighted level on the Balance bar, or null>",
    "segment": {{"time": "<as shown, e.g. '1m 52s', or null>", "turns": <number or null>, "vert": "<as shown with unit, e.g. '710 ft', or null>", "distance": "<as shown with unit, e.g. '0.8 mi', or null>"}},
    "badges": ["<badge names shown, e.g. 'Grim Ripper'>"],
    "metric_definitions": {{"<metric key>": "<Carv's definition text, only if a Metric Details screen is included>"}},
    "turn_by_turn": [{{"metric": "<metric key>", "turns": [{{"side": "<'L' or 'R' if marked>", "value": <number>, "counted_in_ski_iq": <true for Ski:IQ turns, false for other turns>}}]}}]
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
6. The "biggest_limiter" should be the #1 thing to work on. If Carv's Skill Highlights mark a skill "One to work on", the limiter should match it or explain why it differs
7. Consider how different screenshots might show different aspects of the same session
8. Read values exactly as displayed. Never estimate a metric, field or zone that is not visible: use null or leave the key out
9. Do NOT report Start of Turn or Centered Balance scores; Carv no longer shows them
10. Coach the technique that was skied (see the technique notes in your instructions), and compare against the performance zones
11. Screenshots of different runs (different location, date or time) should still be read individually: describe each in run_by_run_notes
"""

# The training plan prompt, schema and drill library live in training_plan.py and drills.py


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
        if len(files) > MAX_FILES_PER_UPLOAD:
            return jsonify({
                "error": "Too many files",
                "message": f"Please upload at most {MAX_FILES_PER_UPLOAD} screenshots at a time"
            }), 400
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

    except Exception as e:
        app.logger.exception("Metadata extraction failed")
        return jsonify({
            "error": "Metadata extraction failed",
            "message": "Could not read the screenshots' dates."
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


def process_analysis_job(job_id, image_contents, filenames, num_images):
    """Background worker: analyse the screenshots and store the result on the job."""
    try:
        set_job(job_id, status="processing")

        message_content = image_contents + [{
            "type": "text",
            "text": HOLISTIC_ANALYSIS_PROMPT.format(num_images=num_images)
        }]
        analysis_data = request_json_reply(lambda: create_analysis_message(
            messages=[{"role": "user", "content": message_content}],
            system=CARV_METRICS_CONTEXT,
        ))
        if not isinstance(analysis_data, dict):
            raise ModelReplyError("The AI reply wasn't in the expected format. Please try again.")

        # Units, legacy fields, category averages and Ski:IQ version
        normalise_analysis(analysis_data)
        analysis_data["analyzed_at"] = datetime.now().isoformat()
        analysis_data["filenames"] = filenames
        analysis_data["num_screenshots"] = num_images

        set_job(job_id, status="completed", result=analysis_data)

    except ModelReplyError as e:
        set_job(job_id, status="failed", error=str(e))
    except Exception as e:
        app.logger.error("Analysis job %s failed: %s", job_id, e)
        set_job(job_id, status="failed", error=api_error_message(e))


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
        if len(files) > MAX_FILES_PER_UPLOAD:
            return jsonify({
                "error": "Too many files",
                "message": f"Please upload at most {MAX_FILES_PER_UPLOAD} screenshots at a time"
            }), 400

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

        # Create the job (and clear out old ones), then return immediately
        job_id = str(uuid.uuid4())
        cutoff = datetime.utcnow() - timedelta(seconds=JOB_KEEP_SECONDS)
        Job.query.filter(Job.created_at < cutoff).delete()
        db.session.add(Job(id=job_id, status="pending"))
        db.session.commit()

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

    except Exception as e:
        app.logger.exception("Failed to start analysis")
        return jsonify({
            "error": "Failed to start analysis",
            "message": "Could not start the analysis. Please try again."
        }), 500


@app.route('/job-status/<job_id>', methods=['GET'])
def get_job_status(job_id):
    """
    Get the status of an async analysis job.
    Returns status and result (if completed) or error (if failed).
    """
    job = db.session.get(Job, job_id)
    if job is None:
        return jsonify({
            "error": "Job not found",
            "message": "This analysis is no longer available. Please analyze again."
        }), 404

    # A job nobody has updated for a while was lost when its worker restarted
    if job.status in ("pending", "processing") and job.updated_at and \
            (datetime.utcnow() - job.updated_at).total_seconds() > JOB_STALE_SECONDS:
        job.status = "failed"
        job.error = "The server restarted during this analysis. Please analyze again."
        db.session.commit()

    response = {"job_id": job.id, "status": job.status}
    if job.status == "completed":
        response["result"] = job.result
    elif job.status == "failed":
        response["error"] = job.error
    return jsonify(response)


@app.route('/generate-plan', methods=['POST'])
def generate_training_plan():
    """
    Generate a personalised training plan from an analysis.

    Expects JSON: the analysis object (as returned by /analyze-async), optionally with
    "days_available" (1-7, default 5), "runs_per_day" (4-20, default 10) and "session_id"
    (a saved session to exclude from history).
    Returns: {"training_plan": <structured plan>, ...}
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not (data.get('carv_metrics') or data.get('overall_metrics') or data.get('holistic_analysis')):
        return jsonify({
            "error": "No analysis data provided",
            "message": "Please analyze screenshots first before generating a training plan"
        }), 400

    def bounded_int(value, default, low, high):
        try:
            return max(low, min(high, int(value)))
        except (TypeError, ValueError):
            return default

    days_available = bounded_int(data.pop('days_available', None), 5, 1, 7)
    runs_per_day = bounded_int(data.pop('runs_per_day', None), 10, 4, 20)
    exclude_session_id = data.pop('session_id', None)

    analysis = normalise_analysis(copy.deepcopy(data))
    overview = analysis.get('session_overview') or {}
    ski_iq = (overview.get('ski_iq_range') or {}).get('average')

    # The three most recent saved sessions before this one, so the plan can build on them
    history = []
    try:
        query = Session.query
        session_dt = overview.get('session_datetime')
        if session_dt:
            try:
                query = query.filter(Session.session_date < datetime.fromisoformat(str(session_dt).replace('Z', '+00:00')))
            except ValueError:
                pass
        if exclude_session_id:
            query = query.filter(Session.id != exclude_session_id)
        history = [history_entry(s) for s in query.order_by(Session.session_date.desc()).limit(3).all()]
    except Exception as e:  # history is a bonus; never block a plan on it
        app.logger.warning("Could not load session history for plan: %s", e)

    try:
        response = client.messages.create(
            model=FAST_MODEL,  # fast model keeps plan generation inside Render's request limits
            max_tokens=8000,
            system=plan_system_blocks(CARV_METRICS_CONTEXT),
            tools=[PLAN_TOOL],
            tool_choice={"type": "tool", "name": PLAN_TOOL["name"]},
            messages=[{"role": "user", "content": plan_user_message(analysis, history, days_available, runs_per_day)}],
        )
    except Exception as e:
        error_message = str(e)
        app.logger.error("Plan generation failed: %s", error_message)
        if "api_key" in error_message.lower() or "authentication" in error_message.lower():
            return jsonify({"error": "API Key Error", "message": "The Anthropic API key is missing or invalid."}), 401
        if "rate_limit" in error_message.lower():
            return jsonify({"error": "Rate Limited", "message": "Too many requests. Please wait a moment and try again."}), 429
        return jsonify({"error": "Plan generation failed", "message": "Something went wrong generating your training plan. Please try again."}), 500

    if response.stop_reason == "max_tokens":
        return jsonify({"error": "Plan cut off", "message": "The training plan was too long and got cut off. Please try again."}), 502

    tool_input = next((block.input for block in response.content if getattr(block, "type", None) == "tool_use"), None)
    try:
        plan = assemble_plan(
            tool_input,
            ski_iq=ski_iq,
            days_available=days_available,
            runs_per_day=runs_per_day,
            generated_at=datetime.now().isoformat(),
        )
    except PlanError as e:
        return jsonify({"error": "Plan generation failed", "message": f"{e} Please try again."}), 502

    usage = getattr(response, "usage", None)
    if usage is not None:
        app.logger.info("Plan tokens: input %s, cache read %s, cache write %s, output %s",
                        usage.input_tokens, getattr(usage, "cache_read_input_tokens", None),
                        getattr(usage, "cache_creation_input_tokens", None), usage.output_tokens)

    return jsonify({
        "training_plan": plan,
        "generated_at": plan["generated_at"],
        "based_on_ski_iq": ski_iq,
        "based_on_sessions": len(history),
    })


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

            # Carv metrics with their real units, zones and run details (current analyses)
            if analysis_data.get('carv_metrics') or analysis_data.get('run_details'):
                session_context += "\n**Carv Metrics and Run Details:**\n" + describe_metrics(analysis_data) + "\n"

            # Overall metrics (Balance, Edging, Rotary, Performance) - older saved analyses
            elif 'overall_metrics' in analysis_data:
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
                    version = session.get('skiIQVersion')
                    version_note = {'stelvio': ' (Ski:IQ 26 scoring)', 'wasatch': ' (pre-26 scoring)'}.get(version, '')
                    session_context += f"  Ski:IQ: {session['skiIQ']}{version_note}\n"
                if session.get('technique') or session.get('snowSurface') or session.get('pitch') is not None:
                    session_context += f"  Technique: {session.get('technique') or '?'}, snow: {session.get('snowSurface') or '?'}, pitch: {session.get('pitch') if session.get('pitch') is not None else '?'}°\n"
                if session.get('balance') is not None:
                    session_context += f"  Balance: {session['balance']}/100\n"
                if session.get('twr') is not None:
                    session_context += f"  Weight Release: {session['twr']}%\n"
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
            model=FAST_MODEL,  # Fast for chat
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

        ai_response = get_reply_text(response)

        return jsonify({
            "response": ai_response,
            "timestamp": datetime.now().isoformat()
        })

    except Exception as e:
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

        app.logger.error("Chat failed: %s", error_message)
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
        metrics = data.get('metrics') or {}
        if isinstance(metrics, dict) and metrics:
            normalise_analysis(metrics)
            session_overview = metrics.get('session_overview', {})
            ski_iq_range = session_overview.get('ski_iq_range', {})
            # Try 'average' first, then calculate from 'lowest'/'highest' (AI response keys)
            if ski_iq_range.get('average') is not None:
                ski_iq = ski_iq_range['average']
            elif ski_iq_range.get('lowest') is not None and ski_iq_range.get('highest') is not None:
                ski_iq = (ski_iq_range['lowest'] + ski_iq_range['highest']) / 2
            elif ski_iq_range.get('min') is not None and ski_iq_range.get('max') is not None:
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
        return jsonify({"error": "Failed to save session", "message": "Database error. Please try again."}), 500


@app.route('/sessions', methods=['GET'])
def list_sessions():
    """List all saved sessions, ordered by date descending."""
    try:
        sessions = Session.query.order_by(Session.session_date.desc()).all()
        return jsonify([s.to_dict() for s in sessions])
    except Exception as e:
        app.logger.exception("Failed to fetch sessions")
        return jsonify({"error": "Failed to fetch sessions", "message": "Database error. Please try again."}), 500


@app.route('/sessions/<session_id>', methods=['GET'])
def get_session(session_id):
    """Get a single session by ID."""
    try:
        session = db.session.get(Session, session_id)
        if not session:
            return jsonify({"error": "Session not found"}), 404
        return jsonify(session.to_dict())
    except Exception as e:
        app.logger.exception("Failed to fetch session")
        return jsonify({"error": "Failed to fetch session", "message": "Database error. Please try again."}), 500


@app.route('/sessions/<session_id>', methods=['PUT'])
def update_session(session_id):
    """Update a session (e.g., add notes or training plan)."""
    try:
        session = db.session.get(Session, session_id)
        if not session:
            return jsonify({"error": "Session not found"}), 404

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "No data provided"}), 400

        if 'location' in data:
            session.location = data['location']
        if 'notes' in data:
            session.notes = data['notes']
        if 'training_plan' in data:
            session.training_plan = data['training_plan']
        if 'session_date' in data and data['session_date']:
            try:
                session.session_date = datetime.fromisoformat(str(data['session_date']).replace('Z', '+00:00'))
            except ValueError:
                return jsonify({"error": "Invalid session_date"}), 400
        if isinstance(data.get('metrics'), dict):
            metrics = normalise_analysis(data['metrics'])
            session.metrics = metrics
            ski_iq_range = (metrics.get('session_overview') or {}).get('ski_iq_range') or {}
            if ski_iq_range.get('average') is not None:
                session.ski_iq = ski_iq_range['average']

        db.session.commit()
        return jsonify(session.to_dict())
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Failed to update session")
        return jsonify({"error": "Failed to update session", "message": "Database error. Please try again."}), 500


@app.route('/sessions/<session_id>', methods=['DELETE'])
def delete_session(session_id):
    """Delete a session."""
    try:
        session = db.session.get(Session, session_id)
        if not session:
            return jsonify({"error": "Session not found"}), 404

        db.session.delete(session)
        db.session.commit()
        return jsonify({"message": "Session deleted"})
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Failed to delete session")
        return jsonify({"error": "Failed to delete session", "message": "Database error. Please try again."}), 500


# ============================================================================
# PROGRESSION ANALYSIS - AI-powered trend analysis
# ============================================================================

PROGRESSION_PROMPT = """You are analyzing a skier's progression over multiple skiing sessions.
You have access to CARV metrics data from each session. Your PRIMARY goal is to find CORRELATIONS between metrics and hypothesize WHY changes occur.

## YOUR TASK

### 1. EXTRACT ALL METRICS
For each session, use the metrics that are present. Carv's current list is: Parallel Skis,
Turn Shape, Turn Closure, Early Edging, Edging Smoothness, Edging Similarity, Edge Build,
Edge Angle (degrees), Weight Release, G-Force (g), Slope Pitch (degrees), plus Ski:IQ and the
run details (technique, snow surface, pitch, tempo, corridor, radius, balance rating, Carv's
skill highlights). Older sessions may only have the legacy grouped fields. Never invent a
metric that is not in the data.

### 1b. COMPARE LIKE WITH LIKE
- Each session has a ski_iq_version. Ski:IQ 26 (Stelvio) raised off-piste / ungroomed scores by
  about 4.5 points on the same runs and now credits slope pitch. A change in Ski:IQ across the
  Wasatch → Stelvio boundary is partly a scoring change: say so explicitly, and do not report it
  as improvement on its own.
- Compare sessions with the same technique and similar snow and pitch where you can, and say
  when a difference may come from terrain rather than technique.
- Turn Closure is judged against its performance zone, not "higher is better".

### 1c. DRILLS PRACTISED
Sessions may list the drills from that session's training plan and how the skier logged them
(done, runs, how it felt, notes). Relate them to how the target metrics moved in the following
sessions: which drills seem to be working, and which aren't.

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
            "ski_iq_version": "stelvio|wasatch|null",
            "technique": "string or null",
            "key_metrics": {
                "<carv metric key, e.g. edge_angle>": number
            }
        }
    ],
    "scoring_change_note": "If sessions span the Wasatch → Stelvio change, explain how that affects the Ski:IQ trend; otherwise null",
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
            metrics = normalise_analysis(copy.deepcopy(s.metrics or {}))
            overall = metrics.get('overall_metrics') or {}
            overview = metrics.get('session_overview') or {}
            details = metrics.get('run_details') or {}
            if not overview.get('ski_iq_version') and s.session_date:
                overview['ski_iq_version'] = default_ski_iq_version(s.session_date.isoformat())
            # Extract just the numerical data and key observations
            session_entry = {
                "date": s.session_date.strftime("%Y-%m-%d") if s.session_date else "Unknown",
                "session_id": s.id,
                "location": s.location,
                "ski_iq": (overview.get('ski_iq_range') or {}).get('average') or s.ski_iq,
                "ski_iq_version": overview.get('ski_iq_version'),
                "terrain": overview.get('terrain_types_seen', []),
                "turns_analyzed": overview.get('total_turns_analyzed'),
                "carv_metrics": metrics.get('carv_metrics'),
                "run_details": {k: details.get(k) for k in (
                    'technique', 'snow_surface', 'pitch_deg', 'tempo_tpm', 'corridor_m', 'radius_m',
                    'balance_rating', 'carving_turns', 'skill_highlights'
                ) if details.get(k) not in (None, '', [], {})} or None,
                # Older sessions without carv_metrics still have the grouped fields
                "legacy_metrics": None if metrics.get('carv_metrics') else {
                    "balance": overall.get('balance', {}),
                    "edging": overall.get('edging', {}),
                    "rotary": overall.get('rotary', {}),
                    "performance": overall.get('performance', {}),
                },
                "drills_practised": drills_practised(parse_stored_plan(s.training_plan)),
                "top_priorities": [p.get('area') + f" ({p.get('current_score')})" for p in (metrics.get('top_3_priorities') or []) if p.get('area')],
                "biggest_limiter": (metrics.get('holistic_analysis') or {}).get('biggest_limiter', ''),
                "detailed_observations": (metrics.get('detailed_observations') or '')[:500]
            }
            session_data.append(session_entry)

        # Call Claude for analysis - use Haiku for speed within Render's timeout
        progression_request = lambda: client.messages.create(
            model=FAST_MODEL,  # Fast model to stay within Render timeout
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

        try:
            progression_data = request_json_reply(progression_request)
        except ModelReplyError as e:
            return jsonify({"error": "Progression analysis failed", "message": str(e)}), 502
        if not isinstance(progression_data, dict):
            return jsonify({"error": "Progression analysis failed", "message": "The AI reply wasn't in the expected format. Please try again."}), 502

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
        return jsonify({"error": "Progression analysis failed", "message": "Something went wrong analysing your progression. Please try again."}), 500


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

    # Local development only; production runs under gunicorn. FLASK_DEBUG=1 turns on the debugger.
    app.run(host='127.0.0.1', port=5001, debug=os.getenv('FLASK_DEBUG') == '1')
