# Nifty 500 Swing Trading Scanner

Auto-scans Nifty 500 stocks for swing trading opportunities using:
- HTF Liquidity (1D/1W)
- Trend Analysis (Structure-based)
- Support & Resistance
- Liquidity Sweep + CHoCH Detection
- Volume confirmation

## Deploy Instructions

### Deploy to Render.com (Recommended - FREE)

1. Fork/upload this repo to GitHub
2. Go to https://render.com and sign up (free, no credit card)
3. Click "New +" → "Web Service"
4. Connect your GitHub repo
5. Settings:
   - Runtime: Python
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app --timeout 300 --workers 1 --threads 4`
   - Plan: Free
6. Click "Create Web Service"
7. Wait 3-5 minutes for deployment
8. Your app will be live at `https://YOUR-APP-NAME.onrender.com`

### Local Development

```bash
pip install -r requirements.txt
python app.py
```

Then open http://localhost:5000
