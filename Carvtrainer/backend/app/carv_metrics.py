"""
The CARV metrics as the Carv app shows them today, and helpers to keep
analysis results consistent.

The metric list, units and order come from Carv "Segment Details" screens
(Ski:IQ Metrics list) seen in Feb 2026 screenshots, in both the older app
layout and the Carv app 10 layout. Each metric has a dashed "performance
zone" band on screen; the analysis records whether the value sits below,
in or above that zone.

Older saved sessions use a different structure (`overall_metrics` with
balance / edging / rotary / performance groups, including Start of Turn and
Centered Balance, which Carv no longer shows). `normalise_analysis` keeps
that structure filled from the new metrics so existing charts and saved
sessions keep working.
"""

from datetime import date

# Ski:IQ 26 "Stelvio" announcement date (getcarv.com/blog/ski-iq-26-stelvio).
# Needs Carv app version 10.
STELVIO_RELEASE_DATE = date(2026, 11, 9)

SKI_IQ_VERSIONS = ("stelvio", "wasatch")

# key, label shown in Carv, unit, legacy (category, key) in overall_metrics
CARV_METRICS = [
    {"key": "parallel_skis", "label": "Parallel Skis", "unit": "%", "legacy": ("rotary", "parallel_skis")},
    {"key": "turn_shape", "label": "Turn Shape", "unit": "%", "legacy": ("rotary", "turn_shape")},
    {"key": "turn_closure", "label": "Turn Closure", "unit": "%", "legacy": ("rotary", "turn_closure")},
    {"key": "early_edging", "label": "Early Edging", "unit": "%", "legacy": ("edging", "early_edging")},
    {"key": "edging_smoothness", "label": "Edging Smoothness", "unit": "%", "legacy": ("edging", "edging_smoothness")},
    {"key": "edging_similarity", "label": "Edging Similarity", "unit": "%", "legacy": ("edging", "edging_similarity")},
    {"key": "edge_build", "label": "Edge Build", "unit": "%", "legacy": ("edging", "progressive_edge_build")},
    {"key": "edge_angle", "label": "Edge Angle", "unit": "°", "legacy": ("edging", "edge_angle")},
    {"key": "weight_release", "label": "Weight Release", "unit": "%", "legacy": ("balance", "transition_weight_release")},
    {"key": "g_force", "label": "G-Force", "unit": "g", "legacy": ("performance", "turn_g_force")},
    {"key": "slope_pitch", "label": "Slope Pitch", "unit": "°", "legacy": None},
]

METRICS_BY_KEY = {m["key"]: m for m in CARV_METRICS}

ZONE_VALUES = ("below", "in", "above")


def _number(value):
    """Return value as a float, or None if it isn't a usable number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().rstrip('%°g').strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def default_ski_iq_version(session_datetime):
    """Best guess of the Ski:IQ scoring version from the session date."""
    if not session_datetime:
        return None
    try:
        day = date.fromisoformat(str(session_datetime)[:10])
    except ValueError:
        return None
    return "stelvio" if day >= STELVIO_RELEASE_DATE else "wasatch"


def normalise_analysis(analysis):
    """
    Clean up an analysis dict in place and return it:
    - every carv_metrics entry becomes {value, unit, zone} with the right unit
    - legacy overall_metrics fields are filled from carv_metrics
    - category averages only average % metrics (never degrees or g)
    - ski_iq_version defaults from the session date when the model left it out
    """
    if not isinstance(analysis, dict):
        return analysis

    raw_metrics = analysis.get("carv_metrics")
    carv = {}
    if isinstance(raw_metrics, dict):
        for meta in CARV_METRICS:
            entry = raw_metrics.get(meta["key"])
            if isinstance(entry, dict):
                value = _number(entry.get("value"))
                zone = entry.get("zone") if entry.get("zone") in ZONE_VALUES else None
            else:
                value = _number(entry)
                zone = None
            if value is None:
                continue
            carv[meta["key"]] = {"value": value, "unit": meta["unit"], "zone": zone}
    if carv:
        analysis["carv_metrics"] = carv
    else:
        analysis.pop("carv_metrics", None)

    overall = analysis.get("overall_metrics")
    if not isinstance(overall, dict):
        overall = {}
    for category in ("balance", "edging", "rotary", "performance"):
        if not isinstance(overall.get(category), dict):
            overall[category] = {}

    # carv_metrics is the source of truth when present
    for key, entry in carv.items():
        legacy = METRICS_BY_KEY[key]["legacy"]
        if legacy:
            category, legacy_key = legacy
            overall[category][legacy_key] = entry["value"]

    # Category averages over percentage metrics only
    percent_keys = {
        METRICS_BY_KEY[m["key"]]["legacy"][1]
        for m in CARV_METRICS
        if m["unit"] == "%" and m["legacy"]
    }
    legacy_percent_keys = {"start_of_turn", "centered_balance"}  # older sessions only
    for category, values in overall.items():
        if not isinstance(values, dict):
            continue
        numbers = [
            _number(v) for k, v in values.items()
            if k in (percent_keys | legacy_percent_keys) and _number(v) is not None
        ]
        values["category_average"] = round(sum(numbers) / len(numbers), 1) if numbers else None
    analysis["overall_metrics"] = overall

    overview = analysis.get("session_overview")
    if isinstance(overview, dict):
        version = overview.get("ski_iq_version")
        if version not in SKI_IQ_VERSIONS:
            overview["ski_iq_version"] = default_ski_iq_version(overview.get("session_datetime"))

    return analysis


def describe_metrics(analysis):
    """Plain-text lines of a session's Carv metrics and run details, for prompts."""
    if not isinstance(analysis, dict):
        return ""
    lines = []
    overview = analysis.get("session_overview") or {}
    version = overview.get("ski_iq_version")
    if version:
        lines.append(f"Ski:IQ scoring version: {'Ski:IQ 26 (Stelvio)' if version == 'stelvio' else 'pre-26 (Wasatch)'}")

    carv = analysis.get("carv_metrics") or {}
    for meta in CARV_METRICS:
        entry = carv.get(meta["key"])
        if not entry:
            continue
        zone = f", {entry['zone']} performance zone" if entry.get("zone") else ""
        lines.append(f"- {meta['label']}: {entry['value']:g}{meta['unit']}{zone}")

    details = analysis.get("run_details") or {}
    detail_fields = [
        ("technique", "Technique mode"), ("snow_surface", "Snow surface"), ("pitch_deg", "Pitch (°)"),
        ("tempo_tpm", "Tempo (turns/min)"), ("corridor_m", "Corridor (m)"), ("radius_m", "Radius (m)"),
        ("balance_rating", "Balance rating"),
    ]
    for key, label in detail_fields:
        if details.get(key) not in (None, ""):
            lines.append(f"- {label}: {details[key]}")
    carving_turns = details.get("carving_turns") or {}
    if carving_turns.get("count") is not None:
        lines.append(f"- Carving turns: {carving_turns.get('count')}/{carving_turns.get('of', '?')}")
    for skill in details.get("skill_highlights") or []:
        if isinstance(skill, dict) and skill.get("name"):
            number = f"Skill {skill['number']} " if skill.get("number") else ""
            lines.append(f"- Carv skill highlight: {number}{skill['name']} = {skill.get('status', '?')}")
    return "\n".join(lines)


# Coaching knowledge about the current Carv metrics, included in the system prompt.
CURRENT_CARV_GUIDE = """
## CARV AS IT IS TODAY (read this before the older notes below)

### Ski:IQ 26 "Stelvio" (announced 9 Nov 2026, Carv app version 10)
- Same metrics as before; the carving model is unchanged.
- The SHORT TURN model now rewards control of the skis more than raw g-force.
- The PARALLEL model scores freeride / off-piste terrain more fairly.
- Terrain and snow detection improved (moguls, powder). Slope pitch, from crowdsourced
  slope maps, now feeds into Ski:IQ.
- On the same runs, ungroomed (off-piste) scores rise by about 4.5 points on average
  compared with the previous version (Wasatch). A rise across that boundary is a
  scoring change, not improvement.
- Carv has not published new score ranges or level thresholds.

### The Ski:IQ Metrics list (Carv's names, order and units)
Each metric is shown with a dashed PERFORMANCE ZONE: the target band for this skier's
level. Coach against the zone first; the generic number ranges further below are only a
rough guide. "Top 8 turns" means the scores come from the skier's best 8 turns in the
segment; the Entire Segment block covers all turns.

1. Parallel Skis (%) - how parallel the skis stay through the turn.
2. Turn Shape (%) - smooth, round C-shaped arcs rather than abrupt Z-shaped turns.
3. Turn Closure (%) - how far each turn is finished/closed before the next one starts.
   Carv shows it as a marker on a bar with a mid-range zone, so higher is NOT
   automatically better: judge it only against its zone. (This description is the app's
   interpretation; use Carv's definition if a Metric Details screen gives one.)
4. Early Edging (%) - how early in the turn the ski gets onto its edge.
5. Edging Smoothness (%) - how smoothly edge angle is built and released, without
   abrupt changes. (App's interpretation; prefer Carv's definition if shown.)
6. Edging Similarity (%) - how alike left and right turns are.
7. Edge Build (%) - whether edge angle keeps building through the turn rather than
   being set and held ("park and ride"). Older saved sessions call this
   Progressive Edge Build.
8. Edge Angle (degrees) - peak edge angle.
9. Weight Release (%) - Carv's definition: the percentage of weight you release during
   the transition between turns. Older saved sessions call this Transition Weight Release.
10. G-Force (g) - force generated in the turn; a RESULT of good technique, not a goal.
11. Slope Pitch (degrees) - steepness of the slope, now part of Ski:IQ.

Not shown by Carv any more as scores: Start of Turn and Centered Balance. Never report or
estimate them. Fore/aft balance is still a useful coaching idea; use Carv's Balance
rating (Ok / Good / Great) and Weight Release as the evidence for it.

### Values the skier corrected
If an analysis has a `user_corrected` list, the skier checked those fields against the Carv
app and fixed them (for example `carv_metrics.edge_angle` or `ski_iq`). Treat them as correct
and never second-guess them.

### Other things on the run screens
- Technique mode chip (e.g. CARVING). Coach the technique that was skied:
  * Carving on groomed snow: the carving guidance below applies in full.
  * Short turns: judge control, rhythm and Turn Shape; do NOT call lower G-Force a fault
    or prescribe chasing g-force.
  * Parallel / freeride, moguls, powder: balance and control first; lower edge angles are
    expected and are not a fault.
- Skill Highlights: Carv's own verdict per skill, e.g. "Skill 1 Turning without skidding:
  Nailed it" and "Skill 4 Smoothly linking turns: One to work on". Your biggest limiter
  should agree with Carv's "One to work on", or explain clearly why you differ.
- Pitch and Snow Surface (e.g. Smooth): compare runs like with like.
- Carving Progression (e.g. 8/8 carving turns).
- Tempo (turns per minute), Corridor (width of the line down the slope, metres) and
  Radius (turn radius, metres): the main evidence for turn linking and rhythm. Use them
  when Carv flags "Smoothly linking turns".
- Balance: Ok / Good / Great.
- Metric Details screens: Carv's definition of the metric, and a turn-by-turn chart with
  each turn marked L or R (useful for left/right differences).
"""
