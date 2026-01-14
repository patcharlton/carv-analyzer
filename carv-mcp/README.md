# CARV Analyzer MCP Server

An MCP (Model Context Protocol) server that lets you analyze CARV ski screenshots directly in Claude conversations.

## Features

- **analyze_carv_screenshot** - Extract metrics from CARV app screenshots
- **generate_training_plan** - Get personalized training plans based on your metrics
- **save_carv_session** - Save sessions to track progress over time
- **get_carv_progress** - View your skiing improvement history
- **compare_carv_sessions** - Compare metrics between sessions

## Installation

### 1. Install the package

```bash
cd carv-mcp
pip install -e .
```

### 2. Set your Anthropic API key

```bash
export ANTHROPIC_API_KEY="your-api-key-here"
```

### 3. Configure Claude Code

Add to your `~/.claude/mcp-servers.json`:

```json
{
  "mcpServers": {
    "carv": {
      "command": "python3",
      "args": ["-m", "src.server"],
      "cwd": "/path/to/carv-mcp",
      "env": {
        "ANTHROPIC_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

Or for Claude Desktop, add to your `claude_desktop_config.json`.

## Usage

Once configured, you can use these tools in Claude:

### Analyze a screenshot
```
Analyze my CARV screenshot at ~/Desktop/carv-screenshot.png
```

### Generate a training plan
```
Generate a training plan based on that analysis
```

### Track progress
```
Save this session with notes "Great powder day, focused on edge angles"
```

```
Show my progress history
```

```
Compare my last two sessions
```

## Progress Storage

Session data is stored locally at `~/.carv-mcp/progress.json`

## Requirements

- Python 3.10+
- Anthropic API key
- MCP-compatible client (Claude Code or Claude Desktop)
