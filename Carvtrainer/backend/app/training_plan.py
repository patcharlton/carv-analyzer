"""
Training plan generation as structured data.

The model fills in a forced tool call (`build_training_plan`) instead of
writing free-form markdown. It picks drills by id from the drill library and
writes only the personal parts (why, steps, cues, targets). The server then
merges each drill's fixed details (name, purpose, terrain, video link) from
`drills.py`, and the frontend renders the daily and weekly schedule from the
three chosen drills.
"""

import json

try:
    from app.drills import DRILLS, SELECTION_GUIDE, drill_reference, library_text
    from app.carv_metrics import describe_metrics
except ImportError:  # python app/main.py
    from drills import DRILLS, SELECTION_GUIDE, drill_reference, library_text
    from carv_metrics import describe_metrics

PLAN_FORMAT = "carv-plan-v1"
TOOL_NAME = "build_training_plan"

DRILL_IDS = list(DRILLS.keys())

_text = {"type": "string"}
_string_list = {"type": "array", "items": _text}

PLAN_TOOL = {
    "name": TOOL_NAME,
    "description": "Record the skier's personalised training plan. Call this exactly once with the complete plan.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "2-3 sentences on this skier, using their actual Carv numbers."},
            "level": {"type": "string", "enum": ["Entry", "Development", "Performance", "High Performance"]},
            "biggest_limiter": {"type": "string", "description": "The ONE thing most holding them back."},
            "carv_alignment": {
                "type": "string",
                "description": "How the limiter relates to Carv's own skill highlight marked 'One to work on' (agree, or say why you differ). Empty if Carv gave none.",
            },
            "changes_since_last": {
                "type": "string",
                "description": "What changed since the previous sessions and previous drills, if history is given. Note Ski:IQ 26 rescoring where relevant. Empty if no history.",
            },
            "immediate_focus": {
                "type": "object",
                "properties": {
                    "issue": _text,
                    "drill_id": {"type": "string", "enum": DRILL_IDS},
                    "what_success_feels_like": _text,
                    "cue": {"type": "string", "description": "3-5 word mental cue."},
                },
                "required": ["issue", "drill_id", "what_success_feels_like", "cue"],
            },
            "drills": {
                "type": "array",
                "minItems": 3,
                "maxItems": 3,
                "description": "Exactly 3 drills from the library, in order: primary, secondary, integration.",
                "items": {
                    "type": "object",
                    "properties": {
                        "drill_id": {"type": "string", "enum": DRILL_IDS},
                        "role": {"type": "string", "enum": ["primary", "secondary", "integration"]},
                        "target_metric": {"type": "string", "description": "The Carv metric this drill targets, using Carv's name."},
                        "why": {"type": "string", "description": "How it addresses this skier's specific weakness."},
                        "steps": {"type": "array", "items": _text, "minItems": 4, "maxItems": 5,
                                  "description": "Step-by-step: starting position, initiation, main movement, finish, reset."},
                        "focused_turns": {"type": "integer", "description": "Focused turns per run before free skiing."},
                        "runs": {"type": "integer", "description": "Runs per ski day with this drill."},
                        "success_feels_like": {"type": "string", "description": "Physical sensations in feet, legs, hips."},
                        "mental_cue": {"type": "string", "description": "3-5 word phrase."},
                        "common_mistakes": {
                            "type": "array",
                            "items": {"type": "object", "properties": {"mistake": _text, "fix": _text}, "required": ["mistake", "fix"]},
                        },
                        "make_it_harder": _text,
                    },
                    "required": ["drill_id", "role", "target_metric", "why", "steps", "focused_turns", "runs",
                                 "success_feels_like", "mental_cue", "common_mistakes"],
                },
            },
            "priorities": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "metric": {"type": "string", "description": "Carv metric name."},
                        "current": {"type": "string", "description": "Current value with unit as Carv shows it, e.g. '56%' or '65°'."},
                        "target": {"type": "string", "description": "Realistic target with unit, or 'into the zone'."},
                        "drill_id": {"type": "string", "enum": DRILL_IDS},
                        "note": _text,
                    },
                    "required": ["metric", "current", "target", "drill_id"],
                },
            },
            "strengths": {
                "type": "array",
                "items": {"type": "object", "properties": {"strength": _text, "how_to_use": _text}, "required": ["strength", "how_to_use"]},
            },
            "progression": {
                "type": "object",
                "properties": {"weeks_1_2": _string_list, "weeks_3_4": _string_list},
                "required": ["weeks_1_2", "weeks_3_4"],
            },
            "checkpoints": {
                "type": "array",
                "items": {"type": "object", "properties": {"when": _text, "expect": _text}, "required": ["when", "expect"]},
            },
            "mental_cues": {
                "type": "object",
                "properties": {"primary": _text, "transition": _text, "confidence": _text},
                "required": ["primary", "transition", "confidence"],
            },
            "traps": {
                "type": "array",
                "items": {"type": "object", "properties": {"trap": _text, "instead": _text}, "required": ["trap", "instead"]},
            },
        },
        "required": ["summary", "level", "biggest_limiter", "immediate_focus", "drills", "priorities",
                     "strengths", "progression", "checkpoints", "mental_cues", "traps"],
    },
}

PLAN_INSTRUCTIONS = """
## YOUR TASK: TRAINING PLANS

You build personalised training plans from Carv analyses, using the drill library below.

Principles:
1. Only choose drills from the library, by their [id]. Pick drills that suit the technique
   the skier was using (each drill lists what it suits): no g-force drills for short turns.
2. Match drills to the weakest metrics, judged against Carv's performance zones, using the
   selection guide. Diagnose root causes, not symptoms.
3. If Carv marks a skill "One to work on", the plan should address it or explain why not.
4. Use the skier's actual numbers with Carv's units. Never mention Start of Turn or
   Centered Balance scores (Carv no longer shows them).
5. Steps must be specific and physical, not vague. Mental cues 3-5 words.
6. Quality over quantity: 10 focused turns beat 100 sloppy ones. One focus at a time.
7. If previous sessions are given, build on them: say what changed, keep a drill that is
   working, progress or replace one that isn't. A Ski:IQ change across the Ski:IQ 26
   (Stelvio) rescoring is not, on its own, progress.
8. Be encouraging but honest.

Return the plan by calling the build_training_plan tool once.

## DRILL SELECTION GUIDE (drill ids)
""" + SELECTION_GUIDE + """

## DRILL LIBRARY
""" + library_text()


def system_blocks(coach_context):
    """System prompt: shared Carv knowledge, then plan instructions and the drill library (cached)."""
    return [
        {"type": "text", "text": coach_context},
        # Everything up to here is identical on every request, so cache it
        {"type": "text", "text": PLAN_INSTRUCTIONS, "cache_control": {"type": "ephemeral"}},
    ]


def parse_stored_plan(text):
    """A saved training_plan column value as a structured plan dict, or None for old markdown plans."""
    if not text or not isinstance(text, str) or not text.lstrip().startswith("{"):
        return None
    try:
        plan = json.loads(text)
    except ValueError:
        return None
    return plan if isinstance(plan, dict) and plan.get("format") == PLAN_FORMAT else None


def drills_practised(plan):
    """The plan's drills with how the skier logged them (for progression analysis)."""
    if not plan:
        return None
    log = plan.get("drill_log") or {}
    out = []
    for drill in plan.get("drills", []):
        entry = log.get(drill.get("drill_id")) or {}
        out.append({
            "drill": (drill.get("reference") or {}).get("name") or drill.get("drill_id"),
            "target_metric": drill.get("target_metric"),
            "done": entry.get("done"),
            "runs_done": entry.get("runs_done"),
            "felt": {1: "hard", 2: "getting it", 3: "clicked"}.get(entry.get("felt")),
            "note": entry.get("note") or None,
        })
    return out


def history_entry(session):
    """Compact summary of a saved session for the plan prompt."""
    metrics = session.metrics or {}
    overview = metrics.get("session_overview") or {}
    entry = {
        "date": session.session_date.strftime("%Y-%m-%d") if session.session_date else None,
        "ski_iq": session.ski_iq,
        "summary": describe_metrics(metrics),
        "biggest_limiter": (metrics.get("holistic_analysis") or {}).get("biggest_limiter"),
    }
    if overview.get("ski_iq_version"):
        entry["ski_iq_version"] = overview["ski_iq_version"]
    plan = parse_stored_plan(session.training_plan)
    if plan:
        entry["previous_drills"] = [
            {k: d.get(k) for k in ("drill_id", "role", "target_metric")} for d in plan.get("drills", [])
        ]
        if plan.get("drill_log"):
            entry["drill_log"] = plan["drill_log"]
    return entry


def user_message(analysis, history, days_available, runs_per_day):
    """The per-request part of the prompt: this session's analysis, recent history, and constraints."""
    parts = [
        "Build a training plan for this skier.",
        f"They have {days_available} ski day(s) this week and about {runs_per_day} runs per day.",
        "",
        "## THIS SESSION (Carv metrics and run details)",
        describe_metrics(analysis) or "(no Carv metrics list in this analysis)",
        "",
        "## FULL ANALYSIS",
        json.dumps(analysis, indent=1, ensure_ascii=False),
    ]
    if history:
        parts += ["", "## PREVIOUS SESSIONS (newest first)", json.dumps(history, indent=1, ensure_ascii=False)]
    return "\n".join(parts)


class PlanError(Exception):
    """The model's plan could not be used."""


def assemble_plan(tool_input, *, ski_iq, days_available, runs_per_day, generated_at):
    """Validate the model's tool input and merge in the fixed drill details."""
    if not isinstance(tool_input, dict):
        raise PlanError("The plan came back empty.")
    drills = []
    for item in tool_input.get("drills") or []:
        reference = drill_reference(item.get("drill_id")) if isinstance(item, dict) else None
        if reference:
            drills.append({**item, "reference": reference})
    if len(drills) < 3:
        raise PlanError("The plan did not include three drills from the library.")

    focus = dict(tool_input.get("immediate_focus") or {})
    focus_reference = drill_reference(focus.get("drill_id"))
    if focus_reference:
        focus["reference"] = focus_reference

    priorities = []
    for p in tool_input.get("priorities") or []:
        if isinstance(p, dict):
            reference = drill_reference(p.get("drill_id"))
            priorities.append({**p, "drill_name": reference["name"] if reference else None})

    return {
        "format": PLAN_FORMAT,
        "generated_at": generated_at,
        "ski_iq": ski_iq,
        "days_available": days_available,
        "runs_per_day": runs_per_day,
        "summary": tool_input.get("summary", ""),
        "level": tool_input.get("level"),
        "biggest_limiter": tool_input.get("biggest_limiter", ""),
        "carv_alignment": tool_input.get("carv_alignment") or "",
        "changes_since_last": tool_input.get("changes_since_last") or "",
        "immediate_focus": focus,
        "drills": drills[:3],
        "priorities": priorities,
        "strengths": tool_input.get("strengths") or [],
        "progression": tool_input.get("progression") or {},
        "checkpoints": tool_input.get("checkpoints") or [],
        "mental_cues": tool_input.get("mental_cues") or {},
        "traps": tool_input.get("traps") or [],
    }
