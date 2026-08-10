"""
Nifty 500 Swing Trading Scanner - Deployment Ready
Auto-scans and caches results. Fresh scan every visit if cache expired.
"""
from flask import Flask, render_template_string, jsonify, request
import threading
import time
import os
import json
from datetime import datetime, timedelta
from nifty500_list import get_symbol_map
from scanner_engine import scan_stock
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)

CACHE_FILE = "/tmp/scan_cache.json"
CACHE_HOURS = 4  # Re-scan every 4 hours

scan_state = {
    "status": "idle",
    "progress": 0,
    "total": 0,
    "current_symbol": "",
    "results": [],
    "started_at": None,
    "completed_at": None,
    "error": None,
}
scan_lock = threading.Lock()


def load_cache():
    """Load cached scan results if fresh."""
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
        completed = datetime.fromisoformat(data["completed_at"])
        if datetime.now() - completed < timedelta(hours=CACHE_HOURS):
            return data
    except Exception:
        pass
    return None


def save_cache(data):
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(data, f)
    except Exception:
        pass


def run_scan_background():
    global scan_state
    try:
        sym_map = get_symbol_map()
        symbols = list(sym_map.items())
        with scan_lock:
            scan_state["status"] = "running"
            scan_state["progress"] = 0
            scan_state["total"] = len(symbols)
            scan_state["results"] = []
            scan_state["started_at"] = datetime.now().isoformat()
            scan_state["error"] = None

        results = []
        completed = 0

        def scan_one(item):
            sym, info = item
            try:
                return scan_stock(sym, info)
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(scan_one, item): item for item in symbols}
            for future in as_completed(futures):
                r = future.result()
                completed += 1
                if r:
                    results.append(r)
                with scan_lock:
                    scan_state["progress"] = completed
                    scan_state["current_symbol"] = futures[future][1]["symbol"]

        results.sort(key=lambda x: x["score"], reverse=True)
        cache_data = {
            "status": "completed",
            "results": results,
            "completed_at": datetime.now().isoformat(),
            "total": len(results),
        }
        with scan_lock:
            scan_state["results"] = results
            scan_state["status"] = "completed"
            scan_state["completed_at"] = cache_data["completed_at"]
        save_cache(cache_data)
    except Exception as e:
        with scan_lock:
            scan_state["status"] = "error"
            scan_state["error"] = str(e)


# Load cache at startup
cached = load_cache()
if cached:
    scan_state["results"] = cached["results"]
    scan_state["status"] = "completed"
    scan_state["completed_at"] = cached["completed_at"]
    scan_state["total"] = len(cached["results"])
    scan_state["progress"] = len(cached["results"])


HTML = r"""<!DOCTYPE html>
<html lang="bn">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Nifty 500 Swing Scanner</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
body { background: linear-gradient(135deg, #0B1220 0%, #1a2942 100%); color: #E8EEF7; min-height: 100vh; padding: 12px; }
.container { max-width: 1400px; margin: 0 auto; }
header { background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08); border-radius: 16px; padding: 20px 22px; margin-bottom: 14px; }
h1 { font-size: 22px; background: linear-gradient(90deg, #00D4FF, #F4B942); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 6px; }
.subtitle { color: #8FA3BF; font-size: 12px; line-height: 1.5; }
.meta { color: #F4B942; font-size: 11px; margin-top: 8px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap: 8px; }
.refresh-btn { background: rgba(0,212,255,0.15); color: #00D4FF; border: 1px solid rgba(0,212,255,0.4); padding: 6px 14px; border-radius: 8px; cursor: pointer; font-size: 12px; font-weight: 600; }
.refresh-btn:hover { background: rgba(0,212,255,0.25); }
.refresh-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.loading-banner { background: rgba(244,185,66,0.1); border: 1px solid rgba(244,185,66,0.3); padding: 14px 18px; border-radius: 10px; margin-bottom: 14px; display: flex; align-items: center; gap: 12px; }
.spinner { width: 20px; height: 20px; border: 2px solid rgba(255,255,255,0.2); border-top-color: #F4B942; border-radius: 50%; animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.progress-bar { height: 5px; background: rgba(255,255,255,0.1); border-radius: 3px; overflow: hidden; margin-top: 10px; }
.progress-fill { height: 100%; background: linear-gradient(90deg, #00D4FF, #F4B942); transition: width 0.3s; border-radius: 3px; }
.stats-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin-bottom: 14px; }
.stat-card { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 14px 16px; }
.stat-card .label { color: #8FA3BF; font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; }
.stat-card .value { font-size: 24px; font-weight: 700; margin-top: 4px; }
.stat-card.aplus .value { color: #F4B942; }
.stat-card.aval .value { color: #00C48C; }
.stat-card.watch .value { color: #FF7A45; }
.stat-card.long .value { color: #00C48C; }
.stat-card.short .value { color: #FF4B5C; }
.filters { background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 12px 14px; margin-bottom: 14px; display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.filter-tab { padding: 6px 12px; border-radius: 8px; background: rgba(255,255,255,0.06); color: #B9CADD; cursor: pointer; font-size: 12px; font-weight: 500; border: 1px solid transparent; user-select: none; }
.filter-tab:hover { background: rgba(255,255,255,0.1); }
.filter-tab.active { background: rgba(0,212,255,0.15); color: #00D4FF; border-color: rgba(0,212,255,0.4); }
.filter-tab .count { display: inline-block; background: rgba(255,255,255,0.15); padding: 1px 6px; border-radius: 8px; margin-left: 4px; font-size: 10px; }
select, input { background: rgba(0,0,0,0.3); border: 1px solid rgba(255,255,255,0.15); color: white; padding: 6px 10px; border-radius: 8px; font-size: 12px; }
.results-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; }
.stock-card { background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 14px; cursor: pointer; transition: all 0.2s; }
.stock-card:hover { border-color: rgba(0,212,255,0.4); transform: translateY(-2px); }
.stock-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 10px; }
.stock-symbol { font-size: 16px; font-weight: 700; color: #fff; }
.stock-name { font-size: 11px; color: #8FA3BF; margin-top: 2px; }
.stock-sector { display: inline-block; background: rgba(123,97,255,0.15); color: #A78EFF; padding: 2px 8px; border-radius: 8px; font-size: 10px; margin-top: 4px; }
.verdict { padding: 5px 10px; border-radius: 8px; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.3px; white-space: nowrap; }
.verdict-gold { background: rgba(244,185,66,0.2); color: #F4B942; border: 1px solid rgba(244,185,66,0.4); }
.verdict-green { background: rgba(0,196,140,0.2); color: #00C48C; border: 1px solid rgba(0,196,140,0.4); }
.verdict-orange { background: rgba(255,122,69,0.2); color: #FF7A45; border: 1px solid rgba(255,122,69,0.4); }
.verdict-red { background: rgba(255,75,92,0.2); color: #FF4B5C; border: 1px solid rgba(255,75,92,0.4); }
.price-row { display: flex; justify-content: space-between; align-items: baseline; padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,0.08); }
.price { font-size: 18px; font-weight: 700; }
.change.up { color: #00C48C; }
.change.down { color: #FF4B5C; }
.score-badge { display: flex; align-items: center; gap: 8px; padding: 6px 10px; margin: 8px 0; background: rgba(0,0,0,0.3); border-radius: 8px; }
.score-value { font-size: 16px; font-weight: 700; color: #F4B942; }
.score-bar { flex: 1; height: 5px; background: rgba(255,255,255,0.1); border-radius: 3px; overflow: hidden; }
.score-fill { height: 100%; background: linear-gradient(90deg, #FF4B5C, #F4B942, #00C48C); border-radius: 3px; }
.trade-details { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; margin-top: 8px; }
.trade-item { background: rgba(0,0,0,0.25); padding: 6px 8px; border-radius: 6px; text-align: center; }
.trade-item .lbl { font-size: 9px; color: #8FA3BF; text-transform: uppercase; }
.trade-item .val { font-size: 12px; font-weight: 600; margin-top: 2px; }
.trade-item.entry .val { color: #00D4FF; }
.trade-item.sl .val { color: #FF4B5C; }
.trade-item.tp .val { color: #00C48C; }
.direction-badge { display: inline-block; padding: 2px 8px; border-radius: 6px; font-size: 10px; font-weight: 700; margin-left: 6px; }
.direction-LONG { background: rgba(0,196,140,0.2); color: #00C48C; }
.direction-SHORT { background: rgba(255,75,92,0.2); color: #FF4B5C; }
.modal-overlay { position: fixed; top:0; left:0; right:0; bottom:0; background: rgba(0,0,0,0.9); z-index: 100; display: none; justify-content: center; align-items: flex-start; padding: 20px 10px; overflow-y: auto; }
.modal-overlay.active { display: flex; }
.modal { background: #0F1B2D; border: 1px solid rgba(255,255,255,0.15); border-radius: 14px; padding: 20px; max-width: 640px; width: 100%; margin: 20px auto; }
.modal-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.close-btn { background: none; border: none; color: white; font-size: 26px; cursor: pointer; line-height: 1; }
.signal-list { list-style: none; padding: 0; margin-top: 14px; }
.signal-list li { padding: 8px 12px; margin: 5px 0; background: rgba(255,255,255,0.04); border-radius: 8px; font-size: 12px; }
.signal-list li.pass { border-left: 3px solid #00C48C; }
.signal-list li.warn { border-left: 3px solid #F4B942; }
.signal-list li.neutral { border-left: 3px solid #556680; }
.signal-list li.pass-short { border-left: 3px solid #FF4B5C; }
.empty { text-align: center; padding: 40px 20px; color: #556680; grid-column: 1/-1; }
.footer-note { text-align: center; color: #556680; font-size: 11px; margin-top: 30px; padding: 16px; line-height: 1.6; }
.tv-link { display: inline-block; margin-top: 12px; padding: 8px 14px; background: rgba(0,212,255,0.15); color: #00D4FF; border-radius: 8px; text-decoration: none; font-size: 12px; font-weight: 600; }
.hero { text-align: center; padding: 40px 20px; margin: 20px 0; background: rgba(255,255,255,0.03); border-radius: 16px; border: 2px dashed rgba(255,255,255,0.1); }
.hero h2 { color: #F4B942; margin-bottom: 12px; }
.hero p { color: #8FA3BF; margin-bottom: 20px; max-width: 500px; margin-left: auto; margin-right: auto; }
.btn-large { background: linear-gradient(135deg, #00D4FF, #7B61FF); color: white; border: none; padding: 14px 28px; border-radius: 10px; font-size: 15px; font-weight: 600; cursor: pointer; box-shadow: 0 4px 12px rgba(0,212,255,0.3); }
</style>
</head>
<body>
<div class="container">

  <header>
    <h1>⚡ Nifty 500 Swing Trading Scanner</h1>
    <div class="subtitle">HTF Liquidity + Trend + S&amp;R + CHoCH — সব automatic! PDF Playbook-এর সব rule একসাথে।</div>
    <div class="meta">
      <span id="metaText">Loading...</span>
      <button class="refresh-btn" id="refreshBtn" onclick="refreshScan()">🔄 Fresh Scan</button>
    </div>
  </header>

  <div id="loadingBanner" class="loading-banner" style="display:none;">
    <div class="spinner"></div>
    <div style="flex:1;">
      <div id="loadingText" style="font-weight:600; color:#F4B942;">Scanning Nifty 500...</div>
      <div id="loadingSub" style="font-size:11px; color:#B9CADD; margin-top:2px;"></div>
      <div class="progress-bar"><div class="progress-fill" id="progressFill" style="width:0%"></div></div>
    </div>
  </div>

  <div id="firstVisitHero" class="hero" style="display:none;">
    <h2>📊 প্রথমবার এখানে?</h2>
    <p>Nifty 500-এর সব stock scan করে PDF playbook-এর সব rule অনুযায়ী <strong>A+ swing trade setups</strong> বের করে দেবে। প্রথমবার ৩-৪ মিনিট সময় লাগবে।</p>
    <button class="btn-large" onclick="refreshScan()">🚀 Scan শুরু করুন</button>
  </div>

  <div id="resultsSection" style="display:none;">
    <div class="stats-row" id="statsRow"></div>
    <div class="filters">
      <div class="filter-tab active" data-filter="A+" onclick="setFilter('A+')">🏆 A+ Only <span class="count" id="cnt-aplus">0</span></div>
      <div class="filter-tab" data-filter="A" onclick="setFilter('A')">✅ A Setup <span class="count" id="cnt-a">0</span></div>
      <div class="filter-tab" data-filter="LONG" onclick="setFilter('LONG')">🟢 Long <span class="count" id="cnt-long">0</span></div>
      <div class="filter-tab" data-filter="SHORT" onclick="setFilter('SHORT')">🔴 Short <span class="count" id="cnt-short">0</span></div>
      <div class="filter-tab" data-filter="all" onclick="setFilter('all')">All <span class="count" id="cnt-all">0</span></div>
      <input type="text" id="searchBox" placeholder="🔍 Search..." style="margin-left:auto; width: 140px;" oninput="applyFilters()">
      <select id="sectorFilter" onchange="applyFilters()"><option value="">All Sectors</option></select>
    </div>
    <div class="results-grid" id="resultsGrid"></div>
  </div>

  <div class="footer-note">
    ⚠️ শুধুমাত্র শিক্ষামূলক উদ্দেশ্যে | Data: Yahoo Finance | Powered by Arena.ai<br>
    Trade করার আগে TradingView-এ chart নিজে verify করুন। Past performance ≠ Future results.
  </div>
</div>

<div class="modal-overlay" id="modalOverlay" onclick="closeModal(event)">
  <div class="modal" onclick="event.stopPropagation()">
    <div class="modal-header">
      <div><h2 id="modalSymbol" style="color:#fff; font-size:20px;"></h2>
        <div id="modalName" style="color:#8FA3BF; font-size:12px; margin-top:4px;"></div></div>
      <button class="close-btn" onclick="closeModal()">×</button>
    </div>
    <div id="modalBody"></div>
  </div>
</div>

<script>
let allResults = [];
let currentFilter = 'A+';
let pollInterval = null;

function pollStatus() {
  fetch('/api/scan/status').then(r=>r.json()).then(data => {
    if (data.status === 'running') {
      document.getElementById('loadingBanner').style.display = 'flex';
      document.getElementById('firstVisitHero').style.display = 'none';
      document.getElementById('loadingText').textContent = 'Scanning: ' + data.current_symbol;
      document.getElementById('loadingSub').textContent = data.progress + ' / ' + data.total + ' stocks analyzed';
      document.getElementById('progressFill').style.width = (data.progress / data.total * 100) + '%';
      if (data.results && data.results.length > 0) {
        allResults = data.results;
        displayResults();
      }
    } else if (data.status === 'completed') {
      document.getElementById('loadingBanner').style.display = 'none';
      document.getElementById('firstVisitHero').style.display = 'none';
      document.getElementById('refreshBtn').disabled = false;
      allResults = data.results;
      const dt = new Date(data.completed_at);
      document.getElementById('metaText').textContent = '📅 Last scan: ' + dt.toLocaleString('en-IN', {dateStyle:'medium', timeStyle:'short'}) + ' | ' + data.results.length + ' stocks';
      displayResults();
      if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
    } else if (data.status === 'idle') {
      document.getElementById('firstVisitHero').style.display = 'block';
      document.getElementById('metaText').textContent = 'No scan yet — click "Scan শুরু করুন"';
    } else if (data.status === 'error') {
      document.getElementById('loadingBanner').style.display = 'none';
      document.getElementById('refreshBtn').disabled = false;
      alert('Error: ' + data.error);
    }
  });
}

function refreshScan() {
  document.getElementById('refreshBtn').disabled = true;
  fetch('/api/scan/start', {method:'POST'}).then(() => {
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(pollStatus, 1500);
    pollStatus();
  });
}

function displayResults() {
  document.getElementById('resultsSection').style.display = 'block';
  updateStats();
  populateSectorFilter();
  applyFilters();
}

function updateStats() {
  const aplus = allResults.filter(r => r.verdict === 'A+ SETUP').length;
  const a = allResults.filter(r => r.verdict === 'A SETUP').length;
  const watch = allResults.filter(r => r.verdict === 'WATCH').length;
  const longs = allResults.filter(r => r.direction === 'LONG' && r.score >= 6).length;
  const shorts = allResults.filter(r => r.direction === 'SHORT' && r.score >= 6).length;
  document.getElementById('statsRow').innerHTML =
    '<div class="stat-card aplus"><div class="label">🏆 A+ Setups</div><div class="value">'+aplus+'</div></div>' +
    '<div class="stat-card aval"><div class="label">✅ A Setups</div><div class="value">'+a+'</div></div>' +
    '<div class="stat-card watch"><div class="label">👀 Watch</div><div class="value">'+watch+'</div></div>' +
    '<div class="stat-card long"><div class="label">🟢 Long Ideas</div><div class="value">'+longs+'</div></div>' +
    '<div class="stat-card short"><div class="label">🔴 Short Ideas</div><div class="value">'+shorts+'</div></div>' +
    '<div class="stat-card"><div class="label">Total</div><div class="value">'+allResults.length+'</div></div>';
  document.getElementById('cnt-all').textContent = allResults.length;
  document.getElementById('cnt-aplus').textContent = aplus;
  document.getElementById('cnt-a').textContent = a;
  document.getElementById('cnt-long').textContent = longs;
  document.getElementById('cnt-short').textContent = shorts;
}

function populateSectorFilter() {
  const sectors = [...new Set(allResults.map(r => r.sector))].sort();
  const sel = document.getElementById('sectorFilter');
  const cur = sel.value;
  sel.innerHTML = '<option value="">All Sectors</option>' + sectors.map(s => '<option value="'+s+'">'+s+'</option>').join('');
  sel.value = cur;
}

function setFilter(f) {
  currentFilter = f;
  document.querySelectorAll('.filter-tab').forEach(t => t.classList.remove('active'));
  document.querySelector('.filter-tab[data-filter="'+f+'"]').classList.add('active');
  applyFilters();
}

function getFiltered() {
  const search = document.getElementById('searchBox').value.toLowerCase();
  const sector = document.getElementById('sectorFilter').value;
  let filtered = allResults;
  if (currentFilter === 'A+') filtered = filtered.filter(r => r.verdict === 'A+ SETUP');
  else if (currentFilter === 'A') filtered = filtered.filter(r => r.verdict === 'A SETUP');
  else if (currentFilter === 'LONG') filtered = filtered.filter(r => r.direction === 'LONG' && r.score >= 6);
  else if (currentFilter === 'SHORT') filtered = filtered.filter(r => r.direction === 'SHORT' && r.score >= 6);
  if (search) filtered = filtered.filter(r => r.symbol.toLowerCase().includes(search) || r.name.toLowerCase().includes(search));
  if (sector) filtered = filtered.filter(r => r.sector === sector);
  return filtered;
}

function applyFilters() { renderCards(getFiltered()); }

function renderCards(items) {
  const grid = document.getElementById('resultsGrid');
  if (items.length === 0) {
    grid.innerHTML = '<div class="empty"><h3>No stocks found</h3><p>Try different filter</p></div>';
    return;
  }
  grid.innerHTML = items.map((r, idx) => (
    '<div class="stock-card" onclick="showDetail('+idx+')">'+
      '<div class="stock-header">'+
        '<div>'+
          '<div class="stock-symbol">'+r.symbol+' <span class="direction-badge direction-'+r.direction+'">'+r.direction+'</span></div>'+
          '<div class="stock-name">'+r.name+'</div>'+
          '<div class="stock-sector">'+r.sector+'</div>'+
        '</div>'+
        '<div class="verdict verdict-'+r.verdict_color+'">'+r.verdict+'</div>'+
      '</div>'+
      '<div class="price-row">'+
        '<div><div class="price">₹'+r.price.toFixed(2)+'</div>'+
        '<div class="change '+(r.change_pct >= 0 ? 'up' : 'down')+'" style="font-size:12px;">'+(r.change_pct >= 0 ? '▲' : '▼')+' '+Math.abs(r.change_pct).toFixed(2)+'%</div></div>'+
        '<div style="text-align:right; font-size:10px; color:#8FA3BF;">52W: ₹'+r.wk52_low+' - ₹'+r.wk52_high+'<br>From High: -'+r.dist_52wh_pct+'%</div>'+
      '</div>'+
      '<div class="score-badge">'+
        '<div class="score-value">'+r.score+'/10</div>'+
        '<div class="score-bar"><div class="score-fill" style="width:'+(r.score*10)+'%"></div></div>'+
      '</div>'+
      '<div class="trade-details">'+
        '<div class="trade-item entry"><div class="lbl">Entry</div><div class="val">₹'+r.entry+'</div></div>'+
        '<div class="trade-item sl"><div class="lbl">SL</div><div class="val">₹'+r.sl+'</div></div>'+
        '<div class="trade-item tp"><div class="lbl">TP 1:3</div><div class="val">₹'+r.tp+'</div></div>'+
      '</div>'+
      '<div style="margin-top:8px; font-size:10px; color:#8FA3BF; text-align:center;">Risk: '+r.risk_pct+'% | D: '+r.trend_daily+' | W: '+r.trend_weekly+'</div>'+
    '</div>'
  )).join('');
}

function showDetail(idx) {
  const filtered = getFiltered();
  const r = filtered[idx];
  document.getElementById('modalSymbol').innerHTML = r.symbol+' <span class="verdict verdict-'+r.verdict_color+'" style="font-size:11px; margin-left:8px;">'+r.verdict+'</span>';
  document.getElementById('modalName').textContent = r.name+' • '+r.sector;
  const signalsHtml = r.signals.map(s => '<li class="'+s[2]+'"><strong>'+s[0]+':</strong> '+s[1]+'</li>').join('');
  const reasonsHtml = r.reasons.length > 0
    ? '<div style="background: rgba(0,196,140,0.1); border:1px solid rgba(0,196,140,0.3); padding:12px; border-radius:10px; margin: 12px 0;"><strong style="color:#00C48C; font-size:13px;">🎯 কেন এই stock:</strong><ul style="margin-top:6px; padding-left:20px; color:#B9CADD; font-size:12px;">'+r.reasons.map(x => '<li>'+x+'</li>').join('')+'</ul></div>'
    : '';
  document.getElementById('modalBody').innerHTML =
    '<div style="display:grid; grid-template-columns: repeat(3,1fr); gap:8px; margin-bottom:12px;">'+
      '<div class="trade-item"><div class="lbl">Price</div><div class="val">₹'+r.price+'</div></div>'+
      '<div class="trade-item"><div class="lbl">Score</div><div class="val" style="color:#F4B942;">'+r.score+'/10</div></div>'+
      '<div class="trade-item"><div class="lbl">Direction</div><div class="val">'+r.direction+'</div></div>'+
    '</div>'+
    '<div style="display:grid; grid-template-columns: repeat(3,1fr); gap:8px;">'+
      '<div class="trade-item entry"><div class="lbl">Entry</div><div class="val">₹'+r.entry+'</div></div>'+
      '<div class="trade-item sl"><div class="lbl">Stop Loss</div><div class="val">₹'+r.sl+'</div></div>'+
      '<div class="trade-item tp"><div class="lbl">Target 1:3</div><div class="val">₹'+r.tp+'</div></div>'+
    '</div>'+
    '<div style="margin-top:10px; padding:10px; background:rgba(255,255,255,0.04); border-radius:8px; font-size:12px; color:#B9CADD;">'+
      '💰 Risk/share: ₹'+(r.entry - r.sl).toFixed(2)+' ('+r.risk_pct+'%)<br>'+
      '🎯 Reward/share: ₹'+Math.abs(r.tp - r.entry).toFixed(2)+'<br>'+
      '📊 52W: ₹'+r.wk52_low+' - ₹'+r.wk52_high+
    '</div>'+
    reasonsHtml+
    '<h3 style="margin-top:16px; color:#00D4FF; font-size:14px;">📋 Signal Breakdown</h3>'+
    '<ul class="signal-list">'+signalsHtml+'</ul>'+
    '<a class="tv-link" href="https://in.tradingview.com/chart/?symbol=NSE:'+r.symbol+'" target="_blank">📈 Open in TradingView →</a>';
  document.getElementById('modalOverlay').classList.add('active');
}

function closeModal(e) {
  if (!e || e.target.classList.contains('modal-overlay') || e.target.classList.contains('close-btn')) {
    document.getElementById('modalOverlay').classList.remove('active');
  }
}

// Initial load
pollStatus();
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/scan/start", methods=["POST"])
def scan_start():
    global scan_state
    with scan_lock:
        if scan_state["status"] == "running":
            return jsonify({"error": "Already running"}), 400
    thread = threading.Thread(target=run_scan_background, daemon=True)
    thread.start()
    return jsonify({"status": "started"})

@app.route("/api/scan/status")
def scan_status():
    with scan_lock:
        return jsonify(dict(scan_state))

@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
