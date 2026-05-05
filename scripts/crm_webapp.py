#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import json
import re
import os
import tempfile
import threading
from collections import Counter
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CRM = ROOT / 'data/page-profit-check-leads-from-prototypes.csv'
CRM = Path(os.environ.get('PPC_CRM_CSV', DEFAULT_CRM)).expanduser().resolve()
HOST = '127.0.0.1'
PORT = 8787
LOCK = threading.Lock()

STATUS_OPTIONS = [
    'Prototype built',
    'Ready to email',
    'Email sent',
    'Follow-up sent',
    'Reply received',
    'Won',
    'Lost',
    'Needs email',
    'Email bounced',
    'Do not contact',
]
DATE_FIELDS_BY_STATUS = {
    'Email sent': ('Date First Contacted', 'Last Contact Date'),
    'Follow-up sent': ('Last Contact Date',),
    'Reply received': ('Last Contact Date',),
    'Email bounced': ('Last Contact Date',),
}


def extract_draft_id(notes: str) -> str:
    matches = re.findall(r'\br-?\d{6,}\b', notes or '')
    return matches[-1] if matches else ''


def lead_state(row: dict[str, str]) -> str:
    status = (row.get('Status') or '').strip() or 'Unknown'
    if status == 'Ready to email':
        return 'Ready to email'
    if not (row.get('Email') or '').strip():
        return 'Needs email'
    return status


def load_crm() -> tuple[list[str], list[dict[str, str]]]:
    with CRM.open(newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    for r in rows:
        r['_state'] = lead_state(r)
        r['_draft_id'] = extract_draft_id(r.get('Notes', ''))
        r['_has_email'] = bool((r.get('Email') or '').strip())
        r['_has_draft'] = bool(r['_draft_id'])
    return fields, rows


def write_crm(fields: list[str], rows: list[dict[str, str]]) -> None:
    CRM.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', newline='', encoding='utf-8', dir=str(CRM.parent), delete=False) as f:
        tmp = Path(f.name)
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, '') for field in fields})
    tmp.replace(CRM)


def summarize(rows: list[dict[str, str]]) -> dict[str, object]:
    status_counts = Counter((r.get('Status') or 'Unknown') for r in rows)
    priority_counts = Counter((r.get('Priority') or 'Unrated') for r in rows)
    batch_counts = Counter((r.get('Batch') or 'Unknown') for r in rows)
    return {
        'total': len(rows),
        'drafts': sum(bool(r.get('_has_draft')) for r in rows),
        'ready': status_counts.get('Ready to email', 0),
        'needEmail': sum(not bool(r.get('_has_email')) for r in rows),
        'emailNoDraft': sum(bool(r.get('_has_email')) and not bool(r.get('_has_draft')) for r in rows),
        'statusCounts': dict(status_counts),
        'priorityCounts': dict(priority_counts),
        'batchCounts': dict(batch_counts),
    }


def api_data() -> dict[str, object]:
    fields, rows = load_crm()
    statuses = sorted(set(STATUS_OPTIONS + [(r.get('Status') or '').strip() for r in rows if (r.get('Status') or '').strip()]))
    return {
        'fields': fields,
        'rows': rows,
        'statusOptions': statuses,
        'summary': summarize(rows),
        'updatedAt': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'source': str(CRM),
    }


def update_status(lead_id: str, status: str, note: str = '') -> dict[str, object]:
    today = datetime.now().strftime('%Y-%m-%d')
    with LOCK:
        fields, rows = load_crm()
        target = None
        for r in rows:
            if r.get('Lead ID') == lead_id:
                target = r
                break
        if target is None:
            raise KeyError(f'Lead ID not found: {lead_id}')
        old = target.get('Status', '')
        target['Status'] = status
        for field in DATE_FIELDS_BY_STATUS.get(status, ()): 
            if field == 'Date First Contacted':
                target[field] = (target.get(field) or '').strip() or today
            else:
                target[field] = today
        notes = target.get('Notes') or ''
        auto_note = f'Status updated {today}: {status}'
        if note:
            auto_note += f' - {note}'
        if auto_note not in notes:
            target['Notes'] = (notes + '; ' + auto_note).strip('; ')
        write_crm(fields, rows)
    return {'ok': True, 'leadId': lead_id, 'oldStatus': old, 'status': status, 'row': target}


HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Page Profit Check CRM</title>
<style>
:root{--bg:#f6f7fb;--card:#fff;--ink:#162033;--muted:#667085;--line:#d9deea;--blue:#2563eb;--green:#15803d;--amber:#b45309;--red:#b91c1c;--purple:#7c3aed;--shadow:0 8px 22px rgba(16,24,40,.04)}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif}a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}.wrap{max-width:1320px;margin:0 auto;padding:28px 20px 60px}.top{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:22px}h1{font-size:28px;margin:0 0 6px}.sub,.muted{color:var(--muted)}.cards{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin:18px 0}.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px;box-shadow:var(--shadow)}.num{font-size:28px;font-weight:800}.label{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.06em}.grid{display:grid;grid-template-columns:1.2fr .8fr;gap:14px;margin:14px 0}.panel{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px}.bar{display:flex;align-items:center;gap:10px;margin:8px 0}.bar-name{width:150px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--muted)}.bar-track{flex:1;background:#eef2f7;border-radius:999px;overflow:hidden;height:10px}.bar-fill{height:100%;background:linear-gradient(90deg,#60a5fa,#2563eb)}.bar-count{width:36px;text-align:right;font-weight:700}.controls{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:16px 0}.controls input,.controls select,.status-select{border:1px solid var(--line);border-radius:10px;padding:10px 12px;background:white;color:var(--ink)}.controls input{min-width:310px;flex:1}.status-control{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.status-select{min-width:160px;padding:7px 9px}.btn{border:1px solid var(--line);background:white;border-radius:10px;padding:10px 12px;cursor:pointer}.btn:hover{border-color:#9aa4b2}.save-note{font-size:12px;color:var(--muted)}.saved{color:var(--green)}.saving{color:var(--amber)}.failed{color:var(--red)}.table-wrap{background:var(--card);border:1px solid var(--line);border-radius:16px;overflow:auto;box-shadow:var(--shadow)}table{width:100%;border-collapse:collapse;min-width:1120px}th,td{padding:11px 10px;border-bottom:1px solid #edf0f5;text-align:left;vertical-align:top}th{background:#fbfcff;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);position:sticky;top:0;z-index:1}tr:hover td{background:#fbfdff}.pill{display:inline-flex;align-items:center;border-radius:999px;padding:4px 9px;font-size:12px;font-weight:800;white-space:nowrap}.ready{background:#dcfce7;color:#166534}.sent{background:#dbeafe;color:#1d4ed8}.need{background:#fef3c7;color:#92400e}.bounced{background:#fee2e2;color:#991b1b}.proto{background:#e0e7ff;color:#3730a3}.follow{background:#ffedd5;color:#9a3412}.reply{background:#f3e8ff;color:#6b21a8}.won{background:#bbf7d0;color:#14532d}.lost{background:#f1f5f9;color:#475569}.dnc{background:#fee2e2;color:#7f1d1d}.unknown{background:#fee2e2;color:#991b1b}.note{max-width:300px;color:var(--muted);font-size:12px}.links{display:flex;gap:8px;flex-wrap:wrap}.links a{font-size:12px}.small{font-size:12px}.footer{margin-top:14px;color:var(--muted)}@media(max-width:900px){.cards{grid-template-columns:repeat(2,1fr)}.grid{grid-template-columns:1fr}.top{display:block}}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div>
      <h1>Page Profit Check CRM</h1>
      <div class="sub">CSV-backed web app. Status changes save directly to the configured private CSV file.</div>
    </div>
    <div class="sub"><button class="btn" id="refresh">Refresh</button> <span id="updated"></span></div>
  </div>
  <div class="cards" id="cards"></div>
  <div class="grid"><div class="panel"><h2>Status</h2><div id="statusBars"></div></div><div class="panel"><h2>Priority</h2><div id="priorityBars"></div></div></div>
  <div class="controls"><input id="q" placeholder="Search business, email, status, draft ID, notes..."><select id="state"><option value="">All states</option></select><select id="priority"><option value="">All priorities</option></select><select id="batch"><option value="">All batches</option></select><button class="btn" id="clear">Clear</button></div>
  <div class="table-wrap"><table><thead><tr><th>Lead</th><th>Business</th><th>Status</th><th>Priority</th><th>Email</th><th>Draft ID</th><th>Links</th><th>Issue / Angle</th><th>Notes</th></tr></thead><tbody id="tbody"></tbody></table></div>
  <div class="footer"><span id="shown"></span></div>
</div>
<script>
let leads=[], statusOptions=[], summary={};
const tbody=document.getElementById('tbody');
function esc(s){return (s||'').toString().replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function statusClass(s){return s==='Ready to email'?'ready':s==='Email sent'?'sent':s==='Needs email'?'need':s==='Email bounced'?'bounced':s==='Prototype built'?'proto':s==='Follow-up sent'?'follow':s==='Reply received'?'reply':s==='Won'?'won':s==='Lost'?'lost':s==='Do not contact'?'dnc':'unknown';}
function pill(s){return `<span class="pill ${statusClass(s)}">${esc(s||'Unknown')}</span>`;}
function link(label,url){return url?`<a target="_blank" href="${esc(url)}">${label}</a>`:'';}
function bars(id, counts){const el=document.getElementById(id); const max=Math.max(1,...Object.values(counts||{})); el.innerHTML=Object.entries(counts||{}).sort((a,b)=>b[1]-a[1]).map(([k,v])=>`<div class="bar"><div class="bar-name" title="${esc(k)}">${esc(k||'Unknown')}</div><div class="bar-track"><div class="bar-fill" style="width:${(v/max)*100}%"></div></div><div class="bar-count">${v}</div></div>`).join('');}
function fillSelect(id, values, label){const el=document.getElementById(id); const cur=el.value; el.innerHTML=`<option value="">${label}</option>`; [...new Set(values.filter(Boolean))].sort().forEach(v=>{const o=document.createElement('option');o.value=v;o.textContent=v;el.appendChild(o);}); el.value=cur;}
function renderCards(){document.getElementById('cards').innerHTML=`<div class="card"><div class="num">${summary.total||0}</div><div class="label">Total leads</div></div><div class="card"><div class="num">${summary.drafts||0}</div><div class="label">Drafts created</div></div><div class="card"><div class="num">${summary.ready||0}</div><div class="label">Ready to email</div></div><div class="card"><div class="num">${summary.needEmail||0}</div><div class="label">Need email</div></div><div class="card"><div class="num">${summary.emailNoDraft||0}</div><div class="label">Email, no draft</div></div>`;}
function statusSelect(l){const opts=statusOptions.map(s=>`<option value="${esc(s)}" ${s===(l.Status||'')?'selected':''}>${esc(s)}</option>`).join(''); return `<div class="status-control">${pill(l.Status)}<select class="status-select" data-lead-id="${esc(l['Lead ID'])}">${opts}</select><span class="save-note" id="save-${esc(l['Lead ID'])}"></span></div>`;}
function render(){const q=document.getElementById('q').value.toLowerCase(); const st=document.getElementById('state').value; const pr=document.getElementById('priority').value; const ba=document.getElementById('batch').value; const rows=leads.filter(l=>{const hay=Object.values(l).join(' ').toLowerCase(); return (!q||hay.includes(q)) && (!st||l._state===st) && (!pr||(l.Priority||'Unrated')===pr) && (!ba||(l.Batch||'Unknown')===ba);}); tbody.innerHTML=rows.map(l=>`<tr><td><b>${esc(l['Lead ID'])}</b><div class="small muted">${esc(l.Batch)}</div></td><td><b>${esc(l['Business Name'])}</b><div>${link('Website',l.Website)}</div></td><td>${statusSelect(l)}</td><td>${esc(l.Priority||'')}</td><td>${esc(l.Email||'')}</td><td><code>${esc(l._draft_id)}</code></td><td><div class="links">${link('Prototype',l['Prototype Link'])} ${link('Audit',l['Audit / Report Link'])} ${link('Offer',l['Draft Offer Link'])} ${link('Source',l['Contact / Source Link'])}</div></td><td class="note">${esc(l['Primary Issue']||l['Personalization Angle']||'')}</td><td class="note">${esc(l.Notes||'')}</td></tr>`).join(''); document.getElementById('shown').textContent=`Showing ${rows.length} of ${leads.length} leads`;}
async function load(){const res=await fetch('/api/leads'); if(!res.ok) throw new Error(await res.text()); const data=await res.json(); leads=data.rows; statusOptions=data.statusOptions; summary=data.summary; document.getElementById('updated').textContent=`Updated ${data.updatedAt}`; fillSelect('state', leads.map(l=>l._state), 'All states'); fillSelect('priority', leads.map(l=>l.Priority||'Unrated'), 'All priorities'); fillSelect('batch', leads.map(l=>l.Batch||'Unknown'), 'All batches'); renderCards(); bars('statusBars', summary.statusCounts); bars('priorityBars', summary.priorityCounts); render();}
async function saveStatus(leadId, status){const note=document.getElementById(`save-${leadId}`); if(note){note.className='save-note saving'; note.textContent='Saving...';} const res=await fetch(`/api/leads/${encodeURIComponent(leadId)}/status`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status})}); if(!res.ok){if(note){note.className='save-note failed'; note.textContent='Failed';} throw new Error(await res.text());} const out=await res.json(); const idx=leads.findIndex(l=>l['Lead ID']===leadId); if(idx>=0) leads[idx]=out.row; if(note){note.className='save-note saved'; note.textContent='Saved';} await load();}
tbody.addEventListener('change', e=>{if(!e.target.matches('.status-select')) return; saveStatus(e.target.dataset.leadId, e.target.value).catch(err=>alert(err.message));});
['q','state','priority','batch'].forEach(id=>document.getElementById(id).addEventListener('input',render));
document.getElementById('clear').addEventListener('click',()=>{['q','state','priority','batch'].forEach(id=>document.getElementById(id).value='');render();});
document.getElementById('refresh').addEventListener('click',()=>load().catch(err=>alert(err.message)));
load().catch(err=>{document.body.insertAdjacentHTML('afterbegin',`<pre style="color:red;background:#fee;padding:16px">${esc(err.message)}</pre>`);});
</script>
</body>
</html>'''


class Handler(BaseHTTPRequestHandler):
    def send_json(self, data: object, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text: str, status: int = 200, content_type: str = 'text/html; charset=utf-8') -> None:
        body = text.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path in ('/', '/crm'):
                self.send_text(HTML)
            elif path == '/api/leads':
                self.send_json(api_data())
            else:
                self.send_json({'error': 'not found'}, 404)
        except Exception as e:
            self.send_json({'error': str(e)}, 500)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        m = re.fullmatch(r'/api/leads/([^/]+)/status', path)
        if not m:
            self.send_json({'error': 'not found'}, 404)
            return
        try:
            length = int(self.headers.get('Content-Length') or '0')
            payload = json.loads(self.rfile.read(length) or b'{}')
            lead_id = html.unescape(m.group(1))
            status = (payload.get('status') or '').strip()
            note = (payload.get('note') or '').strip()
            if not status:
                self.send_json({'error': 'status is required'}, 400)
                return
            self.send_json(update_status(lead_id, status, note))
        except KeyError as e:
            self.send_json({'error': str(e)}, 404)
        except Exception as e:
            self.send_json({'error': str(e)}, 500)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f'[{datetime.now().strftime("%H:%M:%S")}] {self.address_string()} {fmt % args}')


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description='Run the Page Profit Check CSV-backed CRM web app.')
    parser.add_argument('--host', default=HOST)
    parser.add_argument('--port', type=int, default=PORT)
    parser.add_argument('--csv', dest='csv_path', help='Path to private CRM CSV. Overrides PPC_CRM_CSV.')
    args = parser.parse_args()
    global CRM
    if args.csv_path:
        CRM = Path(args.csv_path).expanduser().resolve()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f'CRM web app: http://{args.host}:{args.port}/')
    print(f'Backing CSV: {CRM}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopping CRM web app')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
