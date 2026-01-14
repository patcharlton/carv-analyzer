"""
CARV Analyzer MCP Server

Provides tools to analyze CARV ski screenshots and generate training plans
directly within Claude conversations.
"""

import asyncio
import base64
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, ImageContent

from anthropic import Anthropic

# Initialize server
server = Server("carv-analyzer")

# Storage for progress logs
PROGRESS_FILE = Path.home() / ".carv-mcp" / "progress.json"

# CARV Metrics Context - comprehensive carving knowledge base
CARV_METRICS_CONTEXT = """
You are an elite ski coach and CARV technology expert with deep knowledge of carving biomechanics.

## CARV METRICS INTERPRETATION

### Ski:IQ Score (Overall Performance)
- 100 = Average recreational skier
- 100-115 = Intermediate - developing skills
- 115-125 = Advanced intermediate - linking carved turns
- 125-140 = Advanced - consistent carving on varied terrain
- 140-155 = Expert - high edge angles, dynamic skiing
- 155+ = Elite - race-level technique

### BALANCE CATEGORY
- **Start of Turn**: Weight shift to ski tips at turn initiation (0-100)
- **Centered Balance**: Maintaining balance over center of ski during turn (0-100)
- **Transition Weight Release**: How cleanly you release the old outside ski (0-100)

### EDGING CATEGORY
- **Edge Angle**: Maximum angle of ski edge relative to snow (0-100 or degrees)
  * 30-40°: Recreational | 45-55°: Strong intermediate | 55-65°: Advanced | 65°+: Elite
- **Early Edging**: How quickly you establish edge grip after transition (0-100)
- **Edging Similarity**: Consistency between left and right turns (0-100)
- **Progressive Edge Build**: Whether edge angle increases throughout turn (0-100)

### ROTARY CATEGORY
- **Parallel Skis**: How parallel skis remain throughout turns (0-100)
- **Turn Shape**: Smooth C-shaped arcs vs Z-shaped jerky turns (0-100)

### PERFORMANCE CATEGORY
- **Turn G-Force**: Forces generated during turns (0-100 or G value)
  * 1.5-2.0G: Recreational | 2.0-2.5G: Strong | 2.5-3.0G: Expert | 3.0G+: Elite

## DIAGNOSTIC FRAMEWORK

### Low Start of Turn → Root causes: Fear (backseat), weak ankle flex, hip mobility, boot setup
### Low Centered Balance → Root causes: G-force pulling back, defensive uphill lean, weak core
### Low Weight Release → Root causes: Fear of fall line, Z-turn habit, static body
### Low Edge Angle → Root causes: Fear of commitment, no hip angulation, upper body rotation
### Low Early Edging → Root causes: Pivot habit, slow weight transfer, sequential movements
### Low Edging Similarity → Root causes: Dominant side, injury compensation, equipment
### Low Progressive Edge Build → Root causes: "Park and ride" habit, fear, limited dynamic range
### Low Parallel Skis → Root causes: Stemming, A-frame, inside ski not tipping
### Low Turn Shape → Root causes: Pivot-based turning, speed checking, impatience
"""

ANALYSIS_PROMPT = """
Analyze this CARV app screenshot and extract all visible metrics.

Return a JSON object with this structure:
{
  "ski_iq": <number or null>,
  "metrics": {
    "balance": {
      "start_of_turn": <0-100 or null>,
      "centered_balance": <0-100 or null>,
      "transition_weight_release": <0-100 or null>
    },
    "edging": {
      "edge_angle": <0-100 or null>,
      "early_edging": <0-100 or null>,
      "edging_similarity": <0-100 or null>,
      "progressive_edge_build": <0-100 or null>
    },
    "rotary": {
      "parallel_skis": <0-100 or null>,
      "turn_shape": <0-100 or null>
    },
    "performance": {
      "turn_g_force": <0-100 or null>
    }
  },
  "session_date": "<date shown on screenshot or null>",
  "terrain": "<terrain type if visible>",
  "turns_analyzed": <number or null>,
  "observations": "<detailed analysis of strengths and areas to improve>",
  "top_strength": "<the best metric/area>",
  "biggest_limiter": "<the #1 thing holding back their skiing>",
  "quick_win": "<one specific thing to try next session>"
}

IMPORTANT: Return ONLY valid JSON, no markdown or explanations.
"""

TRAINING_PLAN_PROMPT = """
Based on this CARV skiing analysis, create a personalized training plan.

ANALYSIS DATA:
{analysis_data}

Create a structured training plan including:

1. **The Big Picture** - Summarize this skier in 2-3 sentences, their level, and biggest limiter

2. **Immediate Focus (Next 1-3 Runs)** - The single most important thing to work on with a mental cue

3. **Your 3 Key Drills** - Select from proven drills:
   - Thousand Steps (balance, weight transfer)
   - Javelin Turns (outside ski commitment)
   - Railroad Track Carving (pure carving)
   - J-Turns (edge angle commitment)
   - Shin Banger (forward pressure)
   - White Pass Turns (early weight transfer)
   - Pole Drag Carving (upper body separation)

   For each drill include: target metric, execution, terrain, success feels like

4. **Daily Session Plan** - Structure for a 10-run day

5. **Progress Checkpoints** - What to look for after 5, 10, 20 runs

Format as clean markdown with headers and bullet points.
"""


def load_progress() -> list[dict]:
    """Load progress history from file."""
    if not PROGRESS_FILE.exists():
        return []
    try:
        return json.loads(PROGRESS_FILE.read_text())
    except Exception:
        return []


def save_progress(logs: list[dict]) -> None:
    """Save progress history to file."""
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text(json.dumps(logs, indent=2))


def get_anthropic_client() -> Anthropic:
    """Get Anthropic client with API key from environment."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable not set")
    return Anthropic(api_key=api_key)


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available CARV analysis tools."""
    return [
        Tool(
            name="analyze_carv_screenshot",
            description="Analyze a CARV ski app screenshot to extract metrics like Ski:IQ, edge angle, balance scores, and get personalized insights. Provide the path to a screenshot image file.",
            inputSchema={
                "type": "object",
                "properties": {
                    "image_path": {
                        "type": "string",
                        "description": "Path to the CARV screenshot image file (PNG, JPG, or WEBP)"
                    }
                },
                "required": ["image_path"]
            }
        ),
        Tool(
            name="generate_training_plan",
            description="Generate a personalized ski training plan based on CARV analysis results. Run analyze_carv_screenshot first to get analysis data.",
            inputSchema={
                "type": "object",
                "properties": {
                    "analysis_json": {
                        "type": "string",
                        "description": "JSON string of the analysis results from analyze_carv_screenshot"
                    }
                },
                "required": ["analysis_json"]
            }
        ),
        Tool(
            name="save_carv_session",
            description="Save a CARV analysis session to your progress history for tracking improvement over time.",
            inputSchema={
                "type": "object",
                "properties": {
                    "analysis_json": {
                        "type": "string",
                        "description": "JSON string of the analysis results to save"
                    },
                    "notes": {
                        "type": "string",
                        "description": "Optional notes about the session (conditions, focus areas, etc.)"
                    }
                },
                "required": ["analysis_json"]
            }
        ),
        Tool(
            name="get_carv_progress",
            description="Retrieve your CARV progress history to see improvement over time.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of sessions to retrieve (default: 10)"
                    }
                }
            }
        ),
        Tool(
            name="compare_carv_sessions",
            description="Compare two CARV sessions to see improvement or changes in metrics.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_index_1": {
                        "type": "integer",
                        "description": "Index of first session (0 = most recent)"
                    },
                    "session_index_2": {
                        "type": "integer",
                        "description": "Index of second session to compare"
                    }
                },
                "required": ["session_index_1", "session_index_2"]
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls."""

    if name == "analyze_carv_screenshot":
        return await analyze_screenshot(arguments.get("image_path", ""))

    elif name == "generate_training_plan":
        return await generate_plan(arguments.get("analysis_json", ""))

    elif name == "save_carv_session":
        return await save_session(
            arguments.get("analysis_json", ""),
            arguments.get("notes", "")
        )

    elif name == "get_carv_progress":
        return await get_progress(arguments.get("limit", 10))

    elif name == "compare_carv_sessions":
        return await compare_sessions(
            arguments.get("session_index_1", 0),
            arguments.get("session_index_2", 1)
        )

    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def analyze_screenshot(image_path: str) -> list[TextContent]:
    """Analyze a CARV screenshot using Claude's vision."""
    try:
        path = Path(image_path).expanduser()
        if not path.exists():
            return [TextContent(type="text", text=f"Error: File not found: {image_path}")]

        # Read and encode image
        image_data = path.read_bytes()
        base64_image = base64.standard_b64encode(image_data).decode('utf-8')

        # Determine media type
        suffix = path.suffix.lower()
        media_types = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
        media_type = media_types.get(suffix, 'image/png')

        # Call Claude API
        client = get_anthropic_client()
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            system=CARV_METRICS_CONTEXT,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64_image
                        }
                    },
                    {
                        "type": "text",
                        "text": ANALYSIS_PROMPT
                    }
                ]
            }]
        )

        response_text = response.content[0].text

        # Try to parse JSON and format nicely
        try:
            # Clean potential markdown
            clean_text = response_text.strip()
            if clean_text.startswith('```'):
                clean_text = clean_text.split('\n', 1)[1]
            if clean_text.endswith('```'):
                clean_text = clean_text[:-3]
            clean_text = clean_text.strip()

            # Find JSON boundaries
            start = clean_text.find('{')
            end = clean_text.rfind('}')
            if start != -1 and end != -1:
                clean_text = clean_text[start:end+1]

            analysis = json.loads(clean_text)

            # Format a nice summary
            summary = format_analysis_summary(analysis)

            return [TextContent(
                type="text",
                text=f"{summary}\n\n---\n**Raw JSON for training plan generation:**\n```json\n{json.dumps(analysis, indent=2)}\n```"
            )]

        except json.JSONDecodeError:
            return [TextContent(type="text", text=response_text)]

    except Exception as e:
        return [TextContent(type="text", text=f"Error analyzing screenshot: {str(e)}")]


def format_analysis_summary(analysis: dict) -> str:
    """Format analysis into a readable summary."""
    lines = ["## CARV Analysis Results\n"]

    # Ski:IQ
    ski_iq = analysis.get("ski_iq")
    if ski_iq:
        level = "Elite" if ski_iq >= 155 else "Expert" if ski_iq >= 140 else "Advanced" if ski_iq >= 125 else "Intermediate" if ski_iq >= 100 else "Beginner"
        lines.append(f"### Ski:IQ: **{ski_iq}** ({level})\n")

    # Metrics
    metrics = analysis.get("metrics", {})

    if metrics.get("balance"):
        lines.append("### Balance")
        for k, v in metrics["balance"].items():
            if v is not None:
                name = k.replace("_", " ").title()
                emoji = "🟢" if v >= 70 else "🟡" if v >= 50 else "🔴"
                lines.append(f"- {name}: {emoji} **{v}**")
        lines.append("")

    if metrics.get("edging"):
        lines.append("### Edging")
        for k, v in metrics["edging"].items():
            if v is not None:
                name = k.replace("_", " ").title()
                emoji = "🟢" if v >= 70 else "🟡" if v >= 50 else "🔴"
                lines.append(f"- {name}: {emoji} **{v}**")
        lines.append("")

    if metrics.get("rotary"):
        lines.append("### Rotary")
        for k, v in metrics["rotary"].items():
            if v is not None:
                name = k.replace("_", " ").title()
                emoji = "🟢" if v >= 70 else "🟡" if v >= 50 else "🔴"
                lines.append(f"- {name}: {emoji} **{v}**")
        lines.append("")

    if metrics.get("performance"):
        lines.append("### Performance")
        for k, v in metrics["performance"].items():
            if v is not None:
                name = k.replace("_", " ").title()
                emoji = "🟢" if v >= 70 else "🟡" if v >= 50 else "🔴"
                lines.append(f"- {name}: {emoji} **{v}**")
        lines.append("")

    # Insights
    if analysis.get("top_strength"):
        lines.append(f"### Top Strength\n{analysis['top_strength']}\n")

    if analysis.get("biggest_limiter"):
        lines.append(f"### Biggest Limiter\n{analysis['biggest_limiter']}\n")

    if analysis.get("quick_win"):
        lines.append(f"### Quick Win\n{analysis['quick_win']}\n")

    if analysis.get("observations"):
        lines.append(f"### Detailed Observations\n{analysis['observations']}\n")

    return "\n".join(lines)


async def generate_plan(analysis_json: str) -> list[TextContent]:
    """Generate a training plan from analysis data."""
    try:
        analysis = json.loads(analysis_json)

        client = get_anthropic_client()
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=3000,
            system="You are an elite ski coach with deep expertise in carving biomechanics and CARV technology. Create actionable, specific training plans.",
            messages=[{
                "role": "user",
                "content": TRAINING_PLAN_PROMPT.format(analysis_data=json.dumps(analysis, indent=2))
            }]
        )

        return [TextContent(type="text", text=response.content[0].text)]

    except json.JSONDecodeError:
        return [TextContent(type="text", text="Error: Invalid JSON. Please provide the analysis JSON from analyze_carv_screenshot.")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error generating plan: {str(e)}")]


async def save_session(analysis_json: str, notes: str) -> list[TextContent]:
    """Save a session to progress history."""
    try:
        analysis = json.loads(analysis_json)

        logs = load_progress()

        session = {
            "id": f"session-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            "saved_at": datetime.now().isoformat(),
            "session_date": analysis.get("session_date"),
            "ski_iq": analysis.get("ski_iq"),
            "metrics": analysis.get("metrics", {}),
            "notes": notes,
            "observations": analysis.get("observations", "")
        }

        logs.append(session)
        save_progress(logs)

        return [TextContent(
            type="text",
            text=f"Session saved successfully!\n\n- Session ID: {session['id']}\n- Ski:IQ: {session['ski_iq']}\n- Total sessions tracked: {len(logs)}"
        )]

    except json.JSONDecodeError:
        return [TextContent(type="text", text="Error: Invalid JSON. Please provide valid analysis data.")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error saving session: {str(e)}")]


async def get_progress(limit: int) -> list[TextContent]:
    """Get progress history."""
    logs = load_progress()

    if not logs:
        return [TextContent(
            type="text",
            text="No sessions saved yet. Use `analyze_carv_screenshot` and then `save_carv_session` to start tracking your progress!"
        )]

    # Get most recent sessions
    recent = logs[-limit:][::-1]  # Reverse to show newest first

    lines = [f"## CARV Progress History ({len(logs)} total sessions)\n"]

    for i, session in enumerate(recent):
        ski_iq = session.get("ski_iq", "N/A")
        date = session.get("session_date") or session.get("saved_at", "Unknown")[:10]
        notes = session.get("notes", "")

        lines.append(f"### {i}. {date} - Ski:IQ: **{ski_iq}**")
        if notes:
            lines.append(f"   Notes: {notes}")

        # Show key metrics if available
        metrics = session.get("metrics", {})
        if metrics.get("edging", {}).get("edge_angle"):
            lines.append(f"   Edge Angle: {metrics['edging']['edge_angle']}")
        if metrics.get("balance", {}).get("centered_balance"):
            lines.append(f"   Centered Balance: {metrics['balance']['centered_balance']}")
        lines.append("")

    return [TextContent(type="text", text="\n".join(lines))]


async def compare_sessions(index1: int, index2: int) -> list[TextContent]:
    """Compare two sessions."""
    logs = load_progress()

    if not logs:
        return [TextContent(type="text", text="No sessions saved yet.")]

    # Convert to reverse index (0 = most recent)
    try:
        session1 = logs[-(index1 + 1)]
        session2 = logs[-(index2 + 1)]
    except IndexError:
        return [TextContent(type="text", text=f"Error: Invalid session index. You have {len(logs)} sessions (0 to {len(logs)-1}).")]

    lines = ["## Session Comparison\n"]

    # Ski:IQ comparison
    iq1 = session1.get("ski_iq")
    iq2 = session2.get("ski_iq")
    if iq1 and iq2:
        diff = iq1 - iq2
        emoji = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
        lines.append(f"### Ski:IQ: {iq2} → {iq1} ({emoji} {diff:+d})\n")

    # Metric comparisons
    metrics1 = session1.get("metrics", {})
    metrics2 = session2.get("metrics", {})

    for category in ["balance", "edging", "rotary", "performance"]:
        cat1 = metrics1.get(category, {})
        cat2 = metrics2.get(category, {})

        if cat1 or cat2:
            lines.append(f"### {category.title()}")
            all_keys = set(cat1.keys()) | set(cat2.keys())
            for key in all_keys:
                v1 = cat1.get(key)
                v2 = cat2.get(key)
                if v1 is not None and v2 is not None:
                    diff = v1 - v2
                    emoji = "📈" if diff > 0 else "📉" if diff < 0 else "➡️"
                    name = key.replace("_", " ").title()
                    lines.append(f"- {name}: {v2} → {v1} ({emoji} {diff:+d})")
            lines.append("")

    return [TextContent(type="text", text="\n".join(lines))]


async def run_server():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )


def main():
    """Entry point."""
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
