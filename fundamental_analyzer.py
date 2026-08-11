"""
Fundamental Analyzer using Screener.in
Fetches key ratios and calculates a Fundamental Score (0-4)
"""
import requests
from bs4 import BeautifulSoup
import re
import time
import random

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
}


def safe_float(s):
    """Convert string like '13.6' or '1,124' to float."""
    if s is None:
        return None
    try:
        s = str(s).replace(',', '').replace('%', '').replace('₹', '').strip()
        if s in ('', '-', 'N/A'):
            return None
        return float(s)
    except (ValueError, TypeError):
        return None


def fetch_fundamentals(symbol, retries=2):
    """Fetch fundamentals from Screener.in for NSE symbol."""
    # Try consolidated first (better for holding cos), fallback to standalone
    urls = [
        f"https://www.screener.in/company/{symbol}/consolidated/",
        f"https://www.screener.in/company/{symbol}/",
    ]
    for url in urls:
        for attempt in range(retries):
            try:
                r = requests.get(url, headers=HEADERS, timeout=15)
                if r.status_code == 200 and len(r.text) > 5000:
                    return parse_screener_page(r.text, symbol)
            except Exception:
                pass
            if attempt < retries - 1:
                time.sleep(random.uniform(0.5, 1.5))
    return None


def parse_screener_page(html, symbol):
    """Parse Screener.in HTML and extract key ratios."""
    soup = BeautifulSoup(html, 'html.parser')
    data = {"symbol": symbol}

    # Top ratios section
    top_ratios = soup.find('ul', {'id': 'top-ratios'})
    if top_ratios:
        for li in top_ratios.find_all('li'):
            name_el = li.find('span', class_='name')
            value_el = li.find('span', class_='number')
            if name_el and value_el:
                name = name_el.get_text(strip=True)
                value = safe_float(value_el.get_text(strip=True))
                data[name] = value

    # Try to find Debt/Equity, Sales Growth, Profit Growth from other sections
    # These are usually in the ratios table
    tables = soup.find_all('table', class_='data-table')
    for table in tables:
        rows = table.find_all('tr')
        for row in rows:
            cells = row.find_all('td')
            if len(cells) >= 2:
                label = cells[0].get_text(strip=True)
                # Get the latest year's value (usually last column)
                last_val = cells[-1].get_text(strip=True)
                val = safe_float(last_val)
                if val is not None and label:
                    # Store common financial metrics
                    if 'Debt' in label and 'Equity' not in data.get('Debt/Equity', {}):
                        pass  # handled separately

    # Extract company info section for more ratios
    company_ratios = soup.find_all('li', class_='flex flex-space-between')
    for li in company_ratios:
        text = li.get_text(strip=True)
        if 'Debt' in text and 'Equity' in text:
            match = re.search(r'([\d.]+)', text)
            if match:
                data['Debt/Equity'] = safe_float(match.group(1))

    # Get promoter holding from shareholding section
    shareholding = soup.find('section', {'id': 'shareholding'})
    if shareholding:
        rows = shareholding.find_all('tr')
        for row in rows:
            cells = row.find_all('td')
            if len(cells) >= 2 and 'Promoter' in cells[0].get_text():
                last_val = cells[-1].get_text(strip=True)
                val = safe_float(last_val)
                if val is not None:
                    data['Promoter Holding'] = val
                    break

    # Extract profit growth and sales growth from the "Profit & Loss" ratios section
    # These appear in specific info sections
    ratios_section = soup.find_all('section')
    for section in ratios_section:
        section_id = section.get('id', '')
        if 'profit-loss' in section_id or 'ratios' in section_id:
            for row in section.find_all('tr'):
                cells = row.find_all('td')
                if len(cells) >= 2:
                    label_text = cells[0].get_text(strip=True)
                    val_text = cells[-1].get_text(strip=True)
                    val = safe_float(val_text)
                    if val is not None:
                        if 'Sales Growth' in label_text or 'Revenue Growth' in label_text:
                            if 'Sales Growth 3Y' not in data:
                                data['Sales Growth 3Y'] = val
                        elif 'Profit Growth' in label_text:
                            if 'Profit Growth 3Y' not in data:
                                data['Profit Growth 3Y'] = val

    return data


def calculate_fundamental_score(fund_data):
    """
    Calculate fundamental score out of 4.
    Returns (score, signals, reasons)
    """
    if not fund_data:
        return 0, [("Fundamental", "⚪ Data unavailable", "neutral")], []

    score = 0
    signals = []
    reasons = []

    # 1. ROE (Return on Equity) > 15%
    roe = fund_data.get('ROE')
    if roe is not None:
        if roe >= 15:
            score += 1
            signals.append(("ROE", f"🟢 {roe}% (Strong)", "pass"))
            reasons.append(f"ROE {roe}%")
        elif roe >= 10:
            signals.append(("ROE", f"🟡 {roe}% (Moderate)", "warn"))
        else:
            signals.append(("ROE", f"🔴 {roe}% (Weak)", "warn"))
    else:
        signals.append(("ROE", "⚪ N/A", "neutral"))

    # 2. ROCE (Return on Capital Employed) > 15%
    roce = fund_data.get('ROCE')
    if roce is not None:
        if roce >= 15:
            score += 1
            signals.append(("ROCE", f"🟢 {roce}% (Strong)", "pass"))
            reasons.append(f"ROCE {roce}%")
        elif roce >= 10:
            signals.append(("ROCE", f"🟡 {roce}% (Moderate)", "warn"))
        else:
            signals.append(("ROCE", f"🔴 {roce}% (Weak)", "warn"))
    else:
        signals.append(("ROCE", "⚪ N/A", "neutral"))

    # 3. P/E reasonable (< 40 for most sectors)
    pe = fund_data.get('Stock P/E')
    if pe is not None:
        if 0 < pe < 30:
            score += 1
            signals.append(("P/E", f"🟢 {pe} (Fair)", "pass"))
            reasons.append(f"P/E {pe}")
        elif pe < 50:
            signals.append(("P/E", f"🟡 {pe} (Elevated)", "warn"))
        else:
            signals.append(("P/E", f"🔴 {pe} (High)", "warn"))
    else:
        signals.append(("P/E", "⚪ N/A", "neutral"))

    # 4. Dividend Yield or Promoter Holding
    div_yield = fund_data.get('Dividend Yield')
    promoter = fund_data.get('Promoter Holding')
    if promoter is not None and promoter >= 40:
        score += 1
        signals.append(("Promoter Holding", f"🟢 {promoter}% (Strong)", "pass"))
        reasons.append(f"Promoter {promoter}%")
    elif div_yield is not None and div_yield >= 1.5:
        score += 1
        signals.append(("Dividend Yield", f"🟢 {div_yield}% (Good)", "pass"))
        reasons.append(f"Div Yield {div_yield}%")
    else:
        if promoter is not None:
            signals.append(("Promoter Holding", f"🟡 {promoter}%", "warn"))
        elif div_yield is not None:
            signals.append(("Dividend Yield", f"🟡 {div_yield}%", "warn"))
        else:
            signals.append(("Quality", "⚪ N/A", "neutral"))

    return score, signals, reasons


# Cache to avoid repeated Screener.in calls for same symbol
_cache = {}


def get_fundamental_score(symbol):
    """Get cached fundamental analysis for a symbol."""
    if symbol in _cache:
        return _cache[symbol]
    fund_data = fetch_fundamentals(symbol)
    result = {
        "data": fund_data,
        "score": 0,
        "signals": [],
        "reasons": [],
    }
    if fund_data:
        score, signals, reasons = calculate_fundamental_score(fund_data)
        result["score"] = score
        result["signals"] = signals
        result["reasons"] = reasons
    _cache[symbol] = result
    return result


if __name__ == "__main__":
    # Test
    for sym in ['HDFCBANK', 'RELIANCE', 'TCS']:
        print(f"\n{'='*50}")
        print(f"Testing {sym}...")
        r = get_fundamental_score(sym)
        print(f"Score: {r['score']}/4")
        print("Data:", r['data'])
        for s in r['signals']:
            print(f"  {s[0]}: {s[1]}")
