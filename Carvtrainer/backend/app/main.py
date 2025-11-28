"""
CARV Skiing Analysis Backend API
Flask server that uses Claude AI to analyze CARV screenshots and generate training plans.
"""

import os
import base64
import json
import re
from io import BytesIO
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
from anthropic import Anthropic
from dotenv import load_dotenv
from PIL import Image
from PIL.ExifTags import TAGS

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app, origins=["http://localhost:3000", "http://127.0.0.1:3000"])

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

**3. Transition Weight Release** - Score 0-100
- WHAT IT MEASURES: How cleanly you release the old outside ski to start the new turn
- WHY IT MATTERS: Clean release allows quick edge change and early new edge engagement
- LOW SCORE INDICATES:
  * Hanging onto the old turn too long
  * Hesitation in transition (fear of commitment)
  * Not trusting the new outside ski
- BIOMECHANICS: Active retraction/extension, positive move down the hill
- TARGET FEELING: "Light feet between turns" / "Floating through transition"

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
ROOT CAUSES:
1. Fear of the fall line - not trusting the new turn
2. Z-turn habit - finishing turn hard, then flat, then next turn
3. Lack of extension/retraction - static body position
CASCADING EFFECTS: Choppy transitions, loss of flow, fatigue

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

**2. Javelin Turns**
- Purpose: Forces commitment to outside ski, eliminates inside ski dependency
- Execution: Lift inside ski completely off snow, hold parallel to outside ski during turn
- Feel: All weight on one ski, total commitment
- Duration: 5-8 turns each side, moderate blue terrain
- Improves: Edge Angle, Start of Turn, Centered Balance
- Common Mistake: Leaning into hill for balance instead of angulating

**3. Shuffle Turns**
- Purpose: Develops independent leg action and balance
- Execution: Slide inside foot forward, outside foot back during turns
- Feel: Scissors motion, dynamic leg independence
- Duration: Full run, green terrain
- Improves: Parallel Skis, Turn Shape, balance awareness

**4. Pivot Slips**
- Purpose: Develops rotary control and edge release ability
- Execution: From standstill, release edges and pivot 180°, then stop
- Feel: Controlled sliding, precise edge control
- Duration: 10 pivots each direction
- Improves: Transition Weight Release, edge awareness

### EDGE ANGLE DEVELOPMENT DRILLS

**5. Railroad Track Carving**
- Purpose: Develops pure carving - no skidding
- Execution: Make turns leaving only two clean pencil lines in snow
- Feel: Train on tracks, no sideways sliding
- Duration: Full runs, focus on quality not quantity
- Improves: Edge Angle, Turn Shape, Progressive Edge Build
- Common Mistake: Going too fast - start slow, prioritize clean tracks

**6. J-Turns (Edge Lock Drill)**
- Purpose: Maximizes edge angle commitment
- Execution: From traverse, commit to fall line, carve hard uphill until stop
- Feel: Maximum edge engagement, G-force building, ski bending
- Duration: 5 each direction, moderate pitch
- Improves: Edge Angle, Progressive Edge Build, commitment

**7. Angulation Exaggeration**
- Purpose: Develops hip angulation for higher edge angles
- Execution: Touch outside hand to outside boot during turns
- Feel: Body folding at waist, hips pushing into hill
- Duration: 4-6 turns each direction
- Improves: Edge Angle, Centered Balance
- Common Mistake: Bending at waist instead of creating hip angle

**8. Pole Drag Carving**
- Purpose: Forces upper body countering and angulation
- Execution: Drag inside pole tip in snow throughout turn
- Feel: Upper body stays facing downhill, separation from lower body
- Duration: Full run
- Improves: Edge Angle, Turn Shape, upper/lower body separation

### BALANCE & FORE-AFT DRILLS

**9. Shin Banger**
- Purpose: Develops forward pressure and ankle flex
- Execution: Feel constant shin pressure on boot tongue throughout turn
- Feel: Shins pressing forward, never losing contact
- Duration: Every turn, conscious focus
- Improves: Start of Turn, Centered Balance
- Cue: "Crush the tongue"

**10. Hands on Knees Turns**
- Purpose: Forces forward stance and commitment
- Execution: Ski with hands resting on kneecaps
- Feel: Stacked, forward, can't sit back
- Duration: 4-6 turns, easy terrain
- Improves: Start of Turn, Centered Balance
- Common Mistake: Bending too much at waist

**11. Tall-Small Transitions**
- Purpose: Develops extension/flexion timing
- Execution: Extend tall at turn finish, flex small at turn apex
- Feel: Up-down rhythm, dynamic range of motion
- Duration: Full run, exaggerate movement
- Improves: Transition Weight Release, Pressure Management

**12. Touch the Outside Boot**
- Purpose: Develops outside ski pressure and forward commitment
- Execution: Reach down and touch outside boot at turn apex
- Feel: Weight over outside ski, forward and low
- Duration: Alternating turns
- Improves: Start of Turn, Centered Balance, Edge Angle

### TRANSITION & FLOW DRILLS

**13. White Pass Turns**
- Purpose: Develops early weight transfer and commitment to new turn
- Execution: Transfer weight to new ski BEFORE releasing old turn
- Feel: New turn starts before old one ends, overlapping commitment
- Duration: Focus drill, 6-8 turns
- Improves: Transition Weight Release, Early Edging
- Common Mistake: Finishing old turn completely before starting new

**14. Crossover Focus**
- Purpose: Develops positive movement into new turn
- Execution: Feel center of mass crossing over skis into new turn
- Feel: Body moving downhill into the new arc, not pulling back
- Duration: Every transition, conscious awareness
- Improves: Transition Weight Release, Early Edging
- Cue: "Fall into the new turn"

**15. No Pole Skiing**
- Purpose: Develops balance without pole crutch
- Execution: Remove poles, hands on hips or crossed on chest
- Feel: Pure balance, can't push off anything
- Duration: Full runs
- Improves: Centered Balance, Core engagement

**16. Patience Turns**
- Purpose: Develops complete turn finish and clean transitions
- Execution: Let each turn finish completely up the hill before transitioning
- Feel: No rushing, complete the arc
- Duration: Focus on slow rhythmic skiing
- Improves: Turn Shape, Transition Weight Release

### ADVANCED PERFORMANCE DRILLS

**17. Retraction Turns**
- Purpose: Develops quick edge-to-edge transitions
- Execution: Pull feet up under body at transition, extend into new turn
- Feel: Light feet at crossover, snappy transition
- Duration: Moderate to steep terrain
- Improves: Transition Weight Release, Early Edging, G-Force

**18. Dolphin Turns**
- Purpose: Develops pressure modulation and dynamic range
- Execution: Flex deep into turn apex, extend through transition
- Feel: Wave-like body motion, pressure on-off-on
- Duration: Full runs, flowing terrain
- Improves: Progressive Edge Build, Turn G-Force, Pressure Management

**19. Speed Carving**
- Purpose: Develops trust in edge grip at speed
- Execution: Increase speed while maintaining pure carved turns
- Feel: Acceleration through the arc, G-forces building
- Duration: Open blue/black runs
- Improves: Edge Angle, Turn G-Force, confidence

**20. Variable Radius Carving**
- Purpose: Develops ability to adjust turn shape
- Execution: Alternate between long radius and short radius carved turns
- Feel: Adjustable pressure/edge, ski bending different amounts
- Duration: Full runs
- Improves: Progressive Edge Build, Turn Shape, versatility

### HIGH-PERFORMANCE DRILLS

**21. Hop Transitions**
- Purpose: Develops explosive edge change
- Execution: Hop both skis off snow at transition, land on new edges
- Feel: Explosive, athletic, immediate edge engagement
- Duration: Steep terrain, short sections
- Improves: Early Edging, Transition Weight Release, athleticism

**22. One-Ski Carving**
- Purpose: Ultimate balance and edge control test
- Execution: Remove one ski, carve turns on single ski
- Feel: Total commitment, no backup
- Duration: Easy terrain, 3-4 turns per side
- Improves: Edge Angle, Centered Balance, balance mastery

**23. Gate Training Simulation**
- Purpose: Develops race-timing and line
- Execution: Visualize gates, commit to apex, accelerate out
- Feel: Early pressure, round the gate, explode out
- Duration: Open slope, mark mental gates
- Improves: All metrics, race application

## DRILL SELECTION FRAMEWORK

Based on the skier's profile, select drills using this logic:

**For Low START OF TURN scores**: Shin Banger, Hands on Knees, Touch Outside Boot
**For Low CENTERED BALANCE scores**: Javelin Turns, Thousand Steps, No Pole Skiing
**For Low TRANSITION WEIGHT RELEASE scores**: White Pass Turns, Crossover Focus, Tall-Small
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
- **Execution**: [Step-by-step how to perform it]
- **Runs Per Session**: [X runs] (e.g., 3-4 runs)
- **Turns Per Run**: [X focused turns, then free ski]
- **Terrain**: [Green/Blue/Black, groomed, pitch]
- **Success Feels Like**: [Physical sensation when doing it right]
- **Common Mistake**: [What to watch out for]
- **Progression**: [How to make it harder as they improve]

### Drill 2: [Name] - Secondary Focus
- **Target Metric**: [The CARV metric this improves]
- **Why This Drill**: [How it addresses their specific weakness]
- **Execution**: [Step-by-step how to perform it]
- **Runs Per Session**: [X runs]
- **Turns Per Run**: [X focused turns, then free ski]
- **Terrain**: [Recommendation]
- **Success Feels Like**: [Physical sensation]
- **Common Mistake**: [What to watch out for]

### Drill 3: [Name] - Integration/Refinement
- **Target Metric**: [The CARV metric this improves]
- **Why This Drill**: [How it ties everything together]
- **Execution**: [Step-by-step how to perform it]
- **Runs Per Session**: [X runs]
- **Turns Per Run**: [X focused turns, then free ski]
- **Terrain**: [Recommendation]
- **Success Feels Like**: [Physical sensation]

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

    except Exception as e:
        return jsonify({
            "error": "Metadata extraction failed",
            "message": str(e)
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
            "message": f"Something went wrong during analysis. Error: {error_message}"
        }), 500


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

        # Call Claude API for training plan
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
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
            "message": f"Something went wrong generating your training plan. Error: {error_message}"
        }), 500


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

    app.run(host='0.0.0.0', port=5001, debug=True)
