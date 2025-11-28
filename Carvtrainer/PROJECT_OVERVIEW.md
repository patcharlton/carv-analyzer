# Project Overview - CARV Analyzer

Technical documentation for the CARV Analyzer application.

---

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│                 │     │                 │     │                 │
│  React Frontend │────▶│  Flask Backend  │────▶│   Claude API    │
│  (localhost:3000)│     │ (localhost:5000)│     │   (Anthropic)   │
│                 │     │                 │     │                 │
└─────────────────┘     └─────────────────┘     └─────────────────┘
       │                        │                       │
       │  1. Upload Image       │                       │
       │─────────────────────▶ │  2. Send to Claude    │
       │                        │─────────────────────▶│
       │                        │                       │
       │                        │  3. AI Analysis       │
       │                        │◀─────────────────────│
       │  4. Display Results   │                       │
       │◀─────────────────────│                       │
```

---

## How It Works

### Screenshot Analysis Flow

1. **User uploads image** → Frontend converts to base64
2. **Frontend sends to backend** → `/api/analyze` endpoint
3. **Backend sends to Claude** → With CARV metrics context
4. **Claude analyzes image** → Extracts all visible metrics
5. **Backend parses response** → Cleans JSON, validates structure
6. **Frontend displays results** → Color-coded metrics, observations

### Training Plan Flow

1. **User clicks "Generate Plan"** → Frontend sends analysis data
2. **Backend creates prompt** → Includes all metrics and context
3. **Claude generates plan** → Structured markdown response
4. **Frontend renders plan** → Parsed markdown display

---

## File Structure

```
Carvtrainer/
├── backend/
│   ├── app/
│   │   └── main.py          # Flask API (3 endpoints)
│   ├── requirements.txt     # Python dependencies
│   ├── .env.example         # Template for API key
│   └── .env                 # Your API key (create this)
│
├── frontend/
│   ├── src/
│   │   ├── App.jsx          # Main React component
│   │   ├── App.css          # All styling
│   │   ├── main.jsx         # React entry point
│   │   └── components/      # Component directory (for future use)
│   ├── index.html           # HTML template
│   ├── package.json         # Node dependencies
│   └── vite.config.js       # Vite build config
│
├── README.md                # Main documentation
├── GETTING_STARTED.md       # Setup guide for beginners
├── QUICK_REFERENCE.md       # Command cheat sheet
├── PROJECT_OVERVIEW.md      # This file
├── START_HERE.md            # Quick start summary
├── quickstart.sh            # Setup automation
└── .gitignore               # Git ignore rules
```

---

## API Endpoints

### GET /health

Health check endpoint.

**Response:**
```json
{
  "status": "healthy",
  "api_key_configured": true,
  "timestamp": "2024-01-15T10:30:00.000Z"
}
```

### POST /analyze

Analyze a CARV screenshot.

**Request:**
- Content-Type: `multipart/form-data`
- Body: `image` file (PNG, JPG, WEBP)

**Response:**
```json
{
  "ski_iq": 118,
  "metrics": {
    "balance": {
      "start_of_turn": 72,
      "centered_balance": 68,
      "transition_weight_release": 65
    },
    "edging": {
      "edge_angle": 78,
      "early_edging": 62,
      "edging_similarity": 70,
      "progressive_edge_build": 65
    },
    "rotary": {
      "parallel_skis": 75,
      "turn_shape": 72
    },
    "performance": {
      "turn_g_force": 80
    }
  },
  "terrain": {
    "type": "groomed",
    "pitch": "moderate"
  },
  "session_info": {
    "date": "2024-01-15",
    "run_name": "Blue Valley",
    "turns": 45
  },
  "observations": "Strong edge angles with good turn shape...",
  "strengths": ["Edge angle", "Turn G-force", "Parallel skis"],
  "weaknesses": ["Early edging", "Transition weight release"],
  "analyzed_at": "2024-01-15T10:30:00.000Z",
  "filename": "carv-screenshot.png"
}
```

### POST /generate-plan

Generate a training plan from analysis data.

**Request:**
- Content-Type: `application/json`
- Body: Analysis response object

**Response:**
```json
{
  "training_plan": "# Training Plan for Ski:IQ 118\n\n## Immediate Focus...",
  "generated_at": "2024-01-15T10:31:00.000Z",
  "based_on_ski_iq": 118
}
```

---

## Technology Stack

### Backend
- **Python 3.8+**: Core language
- **Flask 3.0**: Web framework
- **Flask-CORS 4.0**: Cross-origin request handling
- **Anthropic SDK 0.39**: Claude API client
- **python-dotenv 1.0**: Environment variable management

### Frontend
- **React 18**: UI framework
- **Vite 5**: Build tool and dev server
- **Axios 1.6**: HTTP client
- **Pure CSS**: No external CSS frameworks

### AI
- **Claude Sonnet 4**: Vision analysis and text generation
- **Claude Vision**: Screenshot understanding
- **Structured prompts**: JSON and markdown output

---

## CARV Metrics Reference

### Ski:IQ Score Ranges
| Range | Level |
|-------|-------|
| Below 100 | Beginner |
| 100-125 | Intermediate |
| 125-140 | Advanced |
| 140+ | Expert |

### Metric Categories

**Balance (3 metrics)**
- Start of Turn (0-100)
- Centered Balance (0-100)
- Transition Weight Release (0-100)

**Edging (4 metrics)**
- Edge Angle (0-100)
- Early Edging (0-100)
- Edging Similarity (0-100)
- Progressive Edge Build (0-100)

**Rotary (2 metrics)**
- Parallel Skis (0-100)
- Turn Shape (0-100)

**Performance (1 metric)**
- Turn G-Force (0-100)

### Score Interpretation
| Score | Color | Meaning |
|-------|-------|---------|
| 70+ | Green | Strong area |
| 50-69 | Yellow | Needs attention |
| Below 50 | Red | Priority focus |

---

## Security & Privacy

### API Key Security
- API key stored in `.env` file (not committed to git)
- Key never sent to frontend
- Key never logged or exposed

### Data Privacy
- Screenshots processed temporarily in memory
- No images stored on disk
- No user data persisted
- All processing local to your machine

### Network Security
- Backend only accepts localhost connections
- CORS restricted to frontend origin
- No external data transmission except to Claude API

---

## Cost Structure

### Claude API Pricing (as of 2024)
- **Input tokens**: ~$3 per million tokens
- **Output tokens**: ~$15 per million tokens

### Estimated Costs Per Use
| Operation | Estimated Cost |
|-----------|---------------|
| Screenshot analysis | $0.01-0.02 |
| Training plan | $0.01-0.02 |
| Full session (both) | $0.02-0.04 |

### Monthly Estimates
| Usage | Estimated Cost |
|-------|---------------|
| Occasional (5 analyses) | $0.10-0.20 |
| Regular (20 analyses) | $0.40-0.80 |
| Heavy (50 analyses) | $1.00-2.00 |

---

## Development Notes

### Adding New Features

1. **New API endpoint**: Add route in `backend/app/main.py`
2. **New UI component**: Create in `frontend/src/components/`
3. **New styling**: Add to `frontend/src/App.css`

### Testing Changes

1. Make changes to code
2. Backend auto-reloads (debug mode)
3. Frontend auto-reloads (Vite HMR)
4. Refresh browser if needed

### Error Handling

- Backend catches and formats errors
- Frontend displays user-friendly messages
- Console logs detailed errors for debugging

---

## Future Enhancements

Potential improvements (not implemented):

1. **History Tracking**: Store analyses in local database
2. **Progress Charts**: Visualize improvement over time
3. **Multi-Image Upload**: Analyze multiple runs at once
4. **PDF Export**: Save training plans as PDF
5. **Cloud Deployment**: Access from anywhere
6. **Mobile App**: Native iOS/Android app
7. **Video Analysis**: Process CARV video recordings

---

## Troubleshooting Development

### Backend Issues

**Import errors:**
```bash
pip3 install -r requirements.txt --force-reinstall
```

**JSON parsing errors:**
Check `clean_json_response()` function handles Claude's format.

**CORS errors:**
Verify CORS origins include `http://localhost:3000`.

### Frontend Issues

**Module not found:**
```bash
rm -rf node_modules
npm install
```

**Vite errors:**
```bash
npm run dev -- --force
```

**Proxy not working:**
Check `vite.config.js` proxy settings.

---

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make changes with clear commits
4. Test thoroughly
5. Submit pull request

---

## License

MIT License - Use freely for personal projects.
