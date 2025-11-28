# Quick Reference - CARV Analyzer Commands

A cheat sheet for common commands and operations.

---

## Starting the App

### Backend (Terminal 1)
```bash
cd backend
python3 app/main.py
```

### Frontend (Terminal 2)
```bash
cd frontend
npm run dev
```

### Open in Browser
```
http://localhost:3000
```

---

## Stopping the App

Press `Ctrl + C` in each terminal window.

---

## First-Time Setup

### Backend Setup
```bash
cd backend
cp .env.example .env
# Edit .env and add your API key
pip3 install -r requirements.txt
```

### Frontend Setup
```bash
cd frontend
npm install
```

---

## Quick Checks

### Is the backend running?
```bash
curl http://localhost:5000/health
```
Should return: `{"status": "healthy", ...}`

### Is the frontend running?
Open http://localhost:3000 in browser

### Check Python version
```bash
python3 --version
```

### Check Node version
```bash
node --version
```

---

## Common Fixes

### Backend won't start

1. Check API key in `.env`:
```bash
cat backend/.env
```

2. Reinstall packages:
```bash
cd backend
pip3 install -r requirements.txt --force-reinstall
```

### Frontend won't start

1. Clear and reinstall:
```bash
cd frontend
rm -rf node_modules
npm install
```

2. Check for port conflict:
```bash
lsof -i :3000
```

### Port already in use

Kill process on port 5000:
```bash
lsof -i :5000
kill -9 <PID>
```

Kill process on port 3000:
```bash
lsof -i :3000
kill -9 <PID>
```

---

## File Locations

| What | Where |
|------|-------|
| Backend code | `backend/app/main.py` |
| API key | `backend/.env` |
| Frontend code | `frontend/src/App.jsx` |
| Styles | `frontend/src/App.css` |

---

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Check if backend is running |
| `/analyze` | POST | Analyze CARV screenshot |
| `/generate-plan` | POST | Generate training plan |

---

## Useful URLs

- **App**: http://localhost:3000
- **Backend health**: http://localhost:5000/health
- **Anthropic Console**: https://console.anthropic.com
- **API Key Management**: https://console.anthropic.com/settings/keys

---

## Environment Variables

| Variable | Purpose | Example |
|----------|---------|---------|
| `ANTHROPIC_API_KEY` | Claude API access | `sk-ant-xxx...` |

---

## Keyboard Shortcuts

| Action | Mac | Windows |
|--------|-----|---------|
| Stop server | `Ctrl + C` | `Ctrl + C` |
| Hard refresh browser | `Cmd + Shift + R` | `Ctrl + Shift + R` |
| Open Terminal | `Cmd + Space`, type "Terminal" | `Win + R`, type "cmd" |

---

## Troubleshooting Checklist

- [ ] Both terminals running?
- [ ] Backend shows "API Key: Configured"?
- [ ] Frontend shows "ready in xxx ms"?
- [ ] Browser at http://localhost:3000?
- [ ] Internet connection working?
- [ ] Image under 5MB?
- [ ] Image is PNG/JPG/WEBP?

---

## Cost Check

Monitor your API usage at:
https://console.anthropic.com/settings/usage
