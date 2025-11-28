# CARV Analyzer

AI-powered skiing analysis tool that extracts metrics from CARV app screenshots and generates personalized training plans.

## Features

- **Screenshot Analysis**: Upload any CARV app screenshot and automatically extract all visible metrics
- **AI Insights**: Get detailed observations about your skiing technique
- **Training Plans**: Receive personalized, multi-week training plans with specific drills
- **Visual Feedback**: Color-coded metrics showing strengths and areas for improvement

## Quick Start

**New to coding?** Start with [START_HERE.md](START_HERE.md) for the simplest instructions.

### Prerequisites

- Python 3.8 or higher
- Node.js 18 or higher
- Anthropic API key ([get one here](https://console.anthropic.com))

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/carv-analyzer.git
   cd carv-analyzer
   ```

2. **Set up the backend**
   ```bash
   cd backend
   cp .env.example .env
   # Edit .env and add your ANTHROPIC_API_KEY
   pip install -r requirements.txt
   ```

3. **Set up the frontend**
   ```bash
   cd frontend
   npm install
   ```

### Running the Application

You need two terminal windows:

**Terminal 1 - Backend:**
```bash
cd backend
python app/main.py
```

**Terminal 2 - Frontend:**
```bash
cd frontend
npm run dev
```

Open http://localhost:3000 in your browser.

## Usage

1. Take a screenshot of your CARV app showing Ski:IQ and/or individual metrics
2. Transfer the screenshot to your computer
3. Drag and drop the image onto the upload area (or click to browse)
4. Click "Analyze Screenshot" and wait a few seconds
5. Review your extracted metrics and AI observations
6. Click "Generate Training Plan" for personalized coaching

## Cost

The app uses Claude AI which has usage-based pricing:

| Usage Level | Estimated Monthly Cost |
|-------------|----------------------|
| Light (5 analyses) | ~$0.10-0.20 |
| Regular (20 analyses) | ~$0.40-0.80 |
| Heavy (50 analyses) | ~$1.00-2.00 |

Monitor your usage at [console.anthropic.com](https://console.anthropic.com).

## CARV Metrics

The analyzer understands all CARV v9.0.2 metrics:

**Balance**: Start of Turn, Centered Balance, Transition Weight Release

**Edging**: Edge Angle, Early Edging, Edging Similarity, Progressive Edge Build

**Rotary**: Parallel Skis, Turn Shape

**Performance**: Turn G-Force

### Score Interpretation

- **70+** (Green): Strong area - maintain and refine
- **50-69** (Yellow): Needs attention - focus in training
- **Below 50** (Red): Priority area - dedicate practice time

## Project Structure

```
carv-analyzer/
├── backend/
│   ├── app/main.py        # Flask API
│   ├── requirements.txt   # Python dependencies
│   └── .env.example       # API key template
├── frontend/
│   ├── src/
│   │   ├── App.jsx        # Main React component
│   │   └── App.css        # Styling
│   └── package.json       # Node dependencies
└── docs/
    ├── START_HERE.md      # Quick start
    ├── GETTING_STARTED.md # Detailed setup
    ├── QUICK_REFERENCE.md # Command cheat sheet
    └── PROJECT_OVERVIEW.md# Technical details
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/analyze` | POST | Analyze CARV screenshot |
| `/generate-plan` | POST | Generate training plan |

## Troubleshooting

### Backend won't start
- Check that `.env` file exists with valid API key
- Ensure Python dependencies are installed: `pip install -r requirements.txt`

### Frontend won't connect to backend
- Verify backend is running on port 5000
- Check for CORS errors in browser console
- Ensure Vite proxy is configured correctly

### Analysis fails
- Verify image is PNG, JPG, or WEBP format
- Check image is under 5MB
- Ensure it's an actual CARV screenshot
- Check backend terminal for error details

### Common error messages

| Error | Solution |
|-------|----------|
| "API Key Error" | Check .env file has valid key |
| "Backend not running" | Start backend server first |
| "Analysis failed" | Check image format and backend logs |

## Documentation

- [START_HERE.md](START_HERE.md) - Quick start guide
- [GETTING_STARTED.md](GETTING_STARTED.md) - Detailed setup for beginners
- [QUICK_REFERENCE.md](QUICK_REFERENCE.md) - Command cheat sheet
- [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) - Technical architecture

## Technology Stack

- **Backend**: Python, Flask, Anthropic SDK
- **Frontend**: React, Vite, Axios
- **AI**: Claude Sonnet 4 with Vision

## Future Enhancements

- Progress tracking over time
- Multi-image batch analysis
- Cloud deployment for mobile access
- PDF export for training plans
- Video analysis support

## Contributing

Contributions welcome! Please read the contributing guidelines before submitting PRs.

## License

MIT License - see LICENSE file for details.

## Acknowledgments

- [CARV](https://getcarv.com) for creating amazing skiing technology
- [Anthropic](https://anthropic.com) for Claude AI
- The skiing community for feedback and testing

---

Happy skiing! ⛷️ May your Ski:IQ climb ever higher.
