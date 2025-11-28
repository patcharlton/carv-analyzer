# START HERE - CARV Analyzer Quick Start

Welcome! This guide will get you up and running in about 10 minutes.

## What You're Getting

- **Screenshot Analysis**: Upload any CARV app screenshot and get detailed metric extraction
- **AI Insights**: Understand your strengths and weaknesses
- **Training Plans**: Get personalized, actionable plans to improve your skiing

## Quick Setup (3 Steps)

### Step 1: Get Your API Key (5 minutes)

1. Go to [console.anthropic.com](https://console.anthropic.com/)
2. Sign in or create a free account
3. Click "API Keys" in the sidebar
4. Click "Create Key"
5. Copy the key (starts with `sk-ant-`)

### Step 2: Configure the App (2 minutes)

```bash
# Navigate to the project
cd Carvtrainer

# Create your environment file
cp backend/.env.example backend/.env

# Open the .env file and paste your API key
# Replace 'your_api_key_here' with your actual key
```

### Step 3: Run the App (3 minutes)

**Terminal 1 - Backend:**
```bash
cd backend
pip install -r requirements.txt
python app/main.py
```

**Terminal 2 - Frontend:**
```bash
cd frontend
npm install
npm run dev
```

### Step 4: Use It!

1. Open http://localhost:3000 in your browser
2. Drag a CARV screenshot onto the upload area
3. Click "Analyze Screenshot"
4. Click "Generate Training Plan" for personalized advice

## Expected Costs

- Free tier: ~5-10 analyses
- Regular use: ~$5-10/month
- Each analysis costs about $0.01-0.02

## Need Help?

- **Detailed setup**: Read `GETTING_STARTED.md`
- **Technical info**: Read `PROJECT_OVERVIEW.md`
- **Quick commands**: Read `QUICK_REFERENCE.md`

## What's Next?

After you've tested the app locally:
- Take screenshots of your CARV data after each ski session
- Use the training plans to focus your practice
- Track your improvement over time

Happy skiing! ⛷️
