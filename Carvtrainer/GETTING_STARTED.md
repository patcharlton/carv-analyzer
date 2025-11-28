# Getting Started with CARV Analyzer

This guide walks you through everything you need to set up and run the CARV Analyzer on your computer. No coding experience required!

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Getting Your API Key](#getting-your-api-key)
3. [Setting Up the Backend](#setting-up-the-backend)
4. [Setting Up the Frontend](#setting-up-the-frontend)
5. [Running the Application](#running-the-application)
6. [Using the App](#using-the-app)
7. [Troubleshooting](#troubleshooting)

---

## Prerequisites

Before starting, make sure you have these installed on your computer:

### Check Python
Open Terminal and type:
```bash
python3 --version
```
You should see something like `Python 3.9.x` or higher. If not, download Python from [python.org](https://python.org).

### Check Node.js
```bash
node --version
```
You should see something like `v18.x.x` or higher. If not, download Node.js from [nodejs.org](https://nodejs.org).

---

## Getting Your API Key

The app uses Claude AI to analyze your screenshots. You need an API key (like a password) to use it.

### Step-by-Step:

1. **Go to the Anthropic Console**
   - Open your browser
   - Navigate to: https://console.anthropic.com/

2. **Create an Account** (if you don't have one)
   - Click "Sign Up"
   - Use your email or Google account
   - Verify your email

3. **Add Payment Method**
   - Go to Settings > Billing
   - Add a credit card
   - Don't worry - costs are very low (pennies per use)

4. **Create an API Key**
   - Click "API Keys" in the left sidebar
   - Click "Create Key"
   - Give it a name like "CARV Analyzer"
   - Click "Create"
   - **IMPORTANT**: Copy the key immediately! It starts with `sk-ant-`
   - You won't be able to see it again

5. **Save Your Key Somewhere Safe**
   - Store it in a password manager
   - Or a secure note
   - You'll need it in the next step

---

## Setting Up the Backend

The backend is the "brain" that talks to the AI.

### Step 1: Open Terminal

- On Mac: Press `Cmd + Space`, type "Terminal", press Enter
- On Windows: Press `Win + R`, type "cmd", press Enter

### Step 2: Navigate to the Project

```bash
cd /path/to/Carvtrainer
```

Replace `/path/to/` with the actual location where you downloaded this project.

### Step 3: Create Your Environment File

```bash
# Copy the example file
cp backend/.env.example backend/.env
```

### Step 4: Add Your API Key

Open the `.env` file in a text editor:
- On Mac: `open -e backend/.env`
- Or use any text editor

Find this line:
```
ANTHROPIC_API_KEY=your_api_key_here
```

Replace `your_api_key_here` with your actual API key:
```
ANTHROPIC_API_KEY=sk-ant-your-actual-key-here
```

Save and close the file.

### Step 5: Install Python Dependencies

```bash
cd backend
pip3 install -r requirements.txt
```

You should see packages being downloaded. This takes 1-2 minutes.

---

## Setting Up the Frontend

The frontend is what you see in your browser.

### Step 1: Navigate to Frontend Folder

```bash
cd ../frontend
```

Or from the main project folder:
```bash
cd frontend
```

### Step 2: Install Dependencies

```bash
npm install
```

This downloads all the necessary files. Takes 1-2 minutes.

---

## Running the Application

You need TWO terminal windows running at the same time.

### Terminal 1: Start the Backend

```bash
cd /path/to/Carvtrainer/backend
python3 app/main.py
```

You should see:
```
CARV Analyzer Backend Starting...
API Key: Configured
 * Running on http://127.0.0.1:5000
```

**Keep this terminal window open!**

### Terminal 2: Start the Frontend

Open a NEW terminal window, then:

```bash
cd /path/to/Carvtrainer/frontend
npm run dev
```

You should see:
```
VITE v5.x.x  ready in xxx ms

➜  Local:   http://localhost:3000/
```

**Keep this terminal window open too!**

### Open the App

1. Open your web browser (Chrome, Safari, Firefox)
2. Go to: http://localhost:3000
3. You should see the CARV Analyzer interface!

---

## Using the App

### Analyzing a Screenshot

1. **Take a CARV Screenshot**
   - Open the CARV app on your phone
   - Navigate to a run or session with metrics
   - Take a screenshot (varies by phone)

2. **Transfer to Your Computer**
   - Email it to yourself
   - Use AirDrop (Mac/iPhone)
   - Use cloud storage (Google Drive, Dropbox)
   - Use a USB cable

3. **Upload and Analyze**
   - Drag the screenshot onto the upload area
   - Or click the upload area and select the file
   - Click "Analyze Screenshot"
   - Wait 5-10 seconds for results

4. **Generate a Training Plan**
   - After analysis completes, click "Generate Training Plan"
   - Get personalized drills and focus areas
   - Print or screenshot for reference on the slopes

### What the Results Mean

- **Ski:IQ**: Your overall score (100=beginner, 140+=expert)
- **Green scores (70+)**: You're doing well here
- **Yellow scores (50-69)**: Room for improvement
- **Red scores (below 50)**: Focus area for training

---

## Troubleshooting

### "Backend not running" error

**Problem**: The frontend can't connect to the backend.

**Solution**:
1. Make sure Terminal 1 (backend) is still running
2. Check for error messages in that terminal
3. Try restarting the backend

### "API Key Error"

**Problem**: Your API key isn't working.

**Solution**:
1. Check that `.env` file exists in the `backend` folder
2. Make sure the key is correct (no extra spaces)
3. Verify your key at console.anthropic.com

### "Analysis failed" error

**Problem**: The AI couldn't analyze your image.

**Solution**:
1. Make sure the image is a CARV screenshot
2. Try a clearer screenshot
3. Check your internet connection
4. Check Terminal 1 for specific error messages

### Nothing happens when I click buttons

**Problem**: JavaScript isn't loading.

**Solution**:
1. Make sure Terminal 2 (frontend) is running
2. Try a hard refresh: `Cmd+Shift+R` (Mac) or `Ctrl+Shift+R` (Windows)
3. Try a different browser

### "pip not found" or "npm not found"

**Problem**: Python or Node.js isn't installed correctly.

**Solution**:
1. Reinstall Python from python.org
2. Reinstall Node.js from nodejs.org
3. Close and reopen Terminal after installing

### Port already in use

**Problem**: Something else is using port 3000 or 5000.

**Solution**:
```bash
# Find what's using port 5000
lsof -i :5000
# Kill it (replace PID with the actual number)
kill -9 PID
```

---

## Stopping the Application

To stop either server:
1. Click in the terminal window
2. Press `Ctrl + C`

---

## Next Steps

- Read `PROJECT_OVERVIEW.md` for technical details
- Check `QUICK_REFERENCE.md` for command shortcuts
- Consider cloud deployment for mobile access (future enhancement)

---

## Getting Help

If you're stuck:
1. Check the error message carefully
2. Search the error message online
3. Check if both terminals are running
4. Make sure your API key is valid

Happy skiing! The more you use this tool, the faster you'll improve.
