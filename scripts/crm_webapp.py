#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import json
import os
import re
import tempfile
import threading
from collections import Counter
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

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
EXTRA_FIELDS = [
    'Next Follow-up Date',
    'Follow-up Count',
    'Reply Summary',
    'Estimated Value',
    'Owner',
    'Activity Log',
]
DATE_FIELDS_BY_STATUS = {
    'Email sent': ('Date First Contacted', 'Last Contact Date'),
    'Follow-up sent': ('Last Contact Date',),
    'Reply received': ('Last Contact Date',),
    'Email bounced': ('Last Contact Date',),
}
CLOSED_STATUSES = {'Won', 'Lost', 'Do not contact'}


def today_iso() -> str:
    return date.today().isoformat()


def parse_date(value: str) -> date | None:
    value = (value or '').strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


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


def needs_action(row: dict[str, str]) -> bool:
    if (row.get('Status') or '').strip() in CLOSED_STATUSES:
        return False
    due = parse_date(row.get('Next Follow-up Date', ''))
    return bool(due and due <= date.today())


def append_activity(row: dict[str, str], message: str) -> None:
    message = message.strip()
    if not message:
        return
    entry = f"{datetime.now().strftime('%Y-%m-%d %H:%M')} - {message}"
    old = (row.get('Activity Log') or '').strip()
    row['Activity Log'] = f"{old}\n{entry}".strip() if old else entry


def ensure_fields(fields: list[str], rows: list[dict[str, str]]) -> tuple[list[str], bool]:
    changed = False
    for field in EXTRA_FIELDS:
        if field not in fields:
            fields.append(field)
            changed = True
    for row in rows:
        for field in fields:
            if field not in row or row[field] is None:
                row[field] = ''
                changed = True
    return fields, changed


def load_crm(write_upgrade: bool = False) -> tuple[list[str], list[dict[str, str]]]:
    if not CRM.exists():
        raise FileNotFoundError(f'CRM CSV not found: {CRM}')
    with CRM.open(newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    fields, changed = ensure_fields(fields, rows)
    if changed and write_upgrade:
        write_crm(fields, rows)
    for r in rows:
        r['_state'] = lead_state(r)
        r['_draft_id'] = extract_draft_id(r.get('Notes', ''))
        r['_has_email'] = bool((r.get('Email') or '').strip())
        r['_has_draft'] = bool(r['_draft_id'])
        r['_needs_action'] = needs_action(r)
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


def find_lead(rows: list[dict[str, str]], lead_id: str) -> dict[str, str]:
    for row in rows:
        if row.get('Lead ID') == lead_id:
            return row
    raise KeyError(f'Lead ID not found: {lead_id}')


def summarize(rows: list[dict[str, str]]) -> dict[str, object]:
    status_counts = Counter((r.get('Status') or 'Unknown') for r in rows)
    priority_counts = Counter((r.get('Priority') or 'Unrated') for r in rows)
    return {
        'total': len(rows),
        'drafts': sum(bool(r.get('_has_draft')) for r in rows),
        'ready': status_counts.get('Ready to email', 0),
        'needEmail': sum(not bool(r.get('_has_email')) for r in rows),
        'needsAction': sum(bool(r.get('_needs_action')) for r in rows),
        'emailNoDraft': sum(bool(r.get('_has_email')) and not bool(r.get('_has_draft')) for r in rows),
        'statusCounts': dict(status_counts),
        'priorityCounts': dict(priority_counts),
    }


def api_data() -> dict[str, object]:
    with LOCK:
        fields, rows = load_crm(write_upgrade=True)
    statuses = sorted(set(STATUS_OPTIONS + [(r.get('Status') or '').strip() for r in rows if (r.get('Status') or '').strip()]))
    return {
        'fields': fields,
        'rows': rows,
        'statusOptions': statuses,
        'summary': summarize(rows),
        'updatedAt': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'source': str(CRM),
        'today': today_iso(),
    }


def update_status(lead_id: str, status: str, note: str = '') -> dict[str, object]:
    with LOCK:
        fields, rows = load_crm(write_upgrade=True)
        target = find_lead(rows, lead_id)
        old = target.get('Status', '')
        target['Status'] = status
        for field in DATE_FIELDS_BY_STATUS.get(status, ()): 
            if field == 'Date First Contacted':
                target[field] = (target.get(field) or '').strip() or today_iso()
            else:
                target[field] = today_iso()
        if status == 'Follow-up sent':
            try:
                target['Follow-up Count'] = str(int(target.get('Follow-up Count') or '0') + 1)
            except ValueError:
                target['Follow-up Count'] = '1'
        append_activity(target, f'Status changed from {old or "blank"} to {status}' + (f': {note}' if note else ''))
        write_crm(fields, rows)
    return {'ok': True, 'leadId': lead_id, 'oldStatus': old, 'status': status, 'row': target}


def update_lead(lead_id: str, updates: dict[str, object], note: str = '') -> dict[str, object]:
    blocked = {'Lead ID'}
    with LOCK:
        fields, rows = load_crm(write_upgrade=True)
        target = find_lead(rows, lead_id)
        changed: list[str] = []
        for field, value in updates.items():
            if field in blocked or field.startswith('_'):
                continue
            if field not in fields:
                fields.append(field)
                for row in rows:
                    row.setdefault(field, '')
            new_value = '' if value is None else str(value)
            if target.get(field, '') != new_value:
                target[field] = new_value
                changed.append(field)
        if changed:
            append_activity(target, 'Updated ' + ', '.join(changed) + (f': {note}' if note else ''))
            write_crm(fields, rows)
    return {'ok': True, 'leadId': lead_id, 'changed': changed, 'row': target}


def add_note(lead_id: str, text: str, kind: str = 'Note') -> dict[str, object]:
    text = text.strip()
    if not text:
        raise ValueError('note is required')
    with LOCK:
        fields, rows = load_crm(write_upgrade=True)
        target = find_lead(rows, lead_id)
        append_activity(target, f'{kind}: {text}')
        write_crm(fields, rows)
    return {'ok': True, 'leadId': lead_id, 'row': target}


HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Page Profit Check CRM</title>
<style>
:root{--bg:#f6f7fb;--card:#fff;--ink:#162033;--muted:#667085;--line:#d9deea;--blue:#2563eb;--green:#15803d;--amber:#b45309;--red:#b91c1c;--purple:#7c3aed;--shadow:0 8px 22px rgba(16,24,40,.04)}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif}a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}.wrap{max-width:1380px;margin:0 auto;padding:28px 20px 60px}.top{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:22px}h1{font-size:28px;margin:0 0 6px}h2{margin:0 0 10px}.sub,.muted{color:var(--muted)}.cards{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px;margin:18px 0}.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px;box-shadow:var(--shadow)}.card.action{border-color:#fed7aa;background:#fff7ed}.num{font-size:28px;font-weight:800}.label{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.06em}.grid{display:grid;grid-template-columns:1.2fr .8fr;gap:14px;margin:14px 0}.panel{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px}.bar{display:flex;align-items:center;gap:10px;margin:8px 0}.bar-name{width:150px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--muted)}.bar-track{flex:1;background:#eef2f7;border-radius:999px;overflow:hidden;height:10px}.bar-fill{height:100%;background:linear-gradient(90deg,#60a5fa,#2563eb)}.bar-count{width:36px;text-align:right;font-weight:700}.controls{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:16px 0}.controls input,.controls select,.status-select,.drawer input,.drawer select,.drawer textarea{border:1px solid var(--line);border-radius:10px;padding:10px 12px;background:white;color:var(--ink)}.controls input{min-width:310px;flex:1}.status-control{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.status-select{min-width:160px;padding:7px 9px}.btn{border:1px solid var(--line);background:white;border-radius:10px;padding:10px 12px;cursor:pointer}.btn:hover{border-color:#9aa4b2}.btn.primary{background:var(--blue);border-color:var(--blue);color:white}.btn.smallbtn{font-size:12px;padding:7px 9px}.save-note{font-size:12px;color:var(--muted)}.saved{color:var(--green)}.saving{color:var(--amber)}.failed{color:var(--red)}.table-wrap{background:var(--card);border:1px solid var(--line);border-radius:16px;overflow:auto;box-shadow:var(--shadow)}table{width:100%;border-collapse:collapse;min-width:1220px}th,td{padding:11px 10px;border-bottom:1px solid #edf0f5;text-align:left;vertical-align:top}th{background:#fbfcff;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);position:sticky;top:0;z-index:1}tr:hover td{background:#fbfdff}.pill{display:inline-flex;align-items:center;border-radius:999px;padding:4px 9px;font-size:12px;font-weight:800;white-space:nowrap}.ready{background:#dcfce7;color:#166534}.sent{background:#dbeafe;color:#1d4ed8}.need{background:#fef3c7;color:#92400e}.bounced{background:#fee2e2;color:#991b1b}.proto{background:#e0e7ff;color:#3730a3}.follow{background:#ffedd5;color:#9a3412}.reply{background:#f3e8ff;color:#6b21a8}.won{background:#bbf7d0;color:#14532d}.lost{background:#f1f5f9;color:#475569}.dnc{background:#fee2e2;color:#7f1d1d}.unknown{background:#fee2e2;color:#991b1b}.due{background:#fff7ed}.note{max-width:300px;color:var(--muted);font-size:12px}.links{display:flex;gap:8px;flex-wrap:wrap}.links a{font-size:12px}.small{font-size:12px}.footer{margin-top:14px;color:var(--muted)}.drawer-backdrop{display:none;position:fixed;inset:0;background:rgba(15,23,42,.28);z-index:10}.drawer-backdrop.open{display:block}.drawer{position:fixed;top:0;right:-560px;width:min(560px,100vw);height:100vh;background:white;z-index:11;box-shadow:-18px 0 36px rgba(15,23,42,.18);transition:right .18s ease;padding:20px;overflow:auto}.drawer.open{right:0}.drawer-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:16px}.field-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.field{display:flex;flex-direction:column;gap:5px;margin-bottom:10px}.field label{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);font-weight:700}.drawer textarea{min-height:82px;resize:vertical}.activity{white-space:pre-wrap;background:#f8fafc;border:1px solid var(--line);border-radius:12px;padding:12px;max-height:260px;overflow:auto}.drawer-actions{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0}.toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);background:#162033;color:white;padding:10px 14px;border-radius:999px;box-shadow:var(--shadow);display:none;z-index:20}.toast.show{display:block}@media(max-width:900px){.cards{grid-template-columns:repeat(2,1fr)}.grid,.field-grid{grid-template-columns:1fr}.top{display:block}}
</style>
</head>
<body>
<div class="wrap">
  <div class="top"><div><h1>Page Profit Check CRM</h1><div class="sub">CSV-backed web app. Statuses, notes, and follow-ups save directly to the configured private CSV file.</div></div><div class="sub"><button class="btn" id="refresh">Refresh</button> <span id="updated"></span></div></div>
  <div class="cards" id="cards"></div>
  <div class="grid"><div class="panel"><h2>Status</h2><div id="statusBars"></div></div><div class="panel"><h2>Priority</h2><div id="priorityBars"></div></div></div>
  <div class="controls"><input id="q" placeholder="Search business, email, status, draft ID, notes..."><select id="state"><option value="">All states</option></select><select id="priority"><option value="">All priorities</option></select><select id="batch"><option value="">All batches</option></select><select id="action"><option value="">All action states</option><option value="due">Needs action today</option><option value="nodraft">Email, no draft</option><option value="noemail">Needs email</option></select><button class="btn" id="clear">Clear</button></div>
  <div class="table-wrap"><table><thead><tr><th>Lead</th><th>Business</th><th>Status</th><th>Priority</th><th>Email</th><th>Next follow-up</th><th>Draft ID</th><th>Links</th><th>Issue / Angle</th><th>Actions</th></tr></thead><tbody id="tbody"></tbody></table></div>
  <div class="footer"><span id="shown"></span></div>
</div>
<div class="drawer-backdrop" id="backdrop"></div><aside class="drawer" id="drawer"><div class="drawer-head"><div><h2 id="drawerTitle">Lead</h2><div class="sub" id="drawerSub"></div></div><button class="btn" id="closeDrawer">Close</button></div><div id="drawerBody"></div></aside><div class="toast" id="toast"></div>
<script>
let leads=[], statusOptions=[], summary={}, selectedLeadId=null;
const tbody=document.getElementById('tbody');
function esc(s){return (s||'').toString().replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function statusClass(s){return s==='Ready to email'?'ready':s==='Email sent'?'sent':s==='Needs email'?'need':s==='Email bounced'?'bounced':s==='Prototype built'?'proto':s==='Follow-up sent'?'follow':s==='Reply received'?'reply':s==='Won'?'won':s==='Lost'?'lost':s==='Do not contact'?'dnc':'unknown';}
function pill(s){return `<span class="pill ${statusClass(s)}">${esc(s||'Unknown')}</span>`;}
function link(label,url){return url?`<a target="_blank" href="${esc(url)}">${label}</a>`:'';}
function toast(msg){const el=document.getElementById('toast'); el.textContent=msg; el.className='toast show'; setTimeout(()=>el.className='toast',1800);}
function bars(id, counts){const el=document.getElementById(id); const max=Math.max(1,...Object.values(counts||{})); el.innerHTML=Object.entries(counts||{}).sort((a,b)=>b[1]-a[1]).map(([k,v])=>`<div class="bar"><div class="bar-name" title="${esc(k)}">${esc(k||'Unknown')}</div><div class="bar-track"><div class="bar-fill" style="width:${(v/max)*100}%"></div></div><div class="bar-count">${v}</div></div>`).join('');}
function fillSelect(id, values, label){const el=document.getElementById(id); const cur=el.value; el.innerHTML=`<option value="">${label}</option>`; [...new Set(values.filter(Boolean))].sort().forEach(v=>{const o=document.createElement('option');o.value=v;o.textContent=v;el.appendChild(o);}); el.value=cur;}
function renderCards(){document.getElementById('cards').innerHTML=`<div class="card"><div class="num">${summary.total||0}</div><div class="label">Total leads</div></div><div class="card action" id="actionCard"><div class="num">${summary.needsAction||0}</div><div class="label">Needs action today</div></div><div class="card"><div class="num">${summary.drafts||0}</div><div class="label">Drafts created</div></div><div class="card"><div class="num">${summary.ready||0}</div><div class="label">Ready to email</div></div><div class="card"><div class="num">${summary.needEmail||0}</div><div class="label">Need email</div></div><div class="card"><div class="num">${summary.emailNoDraft||0}</div><div class="label">Email, no draft</div></div>`; document.getElementById('actionCard').onclick=()=>{document.getElementById('action').value='due'; render();};}
function statusSelect(l){const opts=statusOptions.map(s=>`<option value="${esc(s)}" ${s===(l.Status||'')?'selected':''}>${esc(s)}</option>`).join(''); return `<div class="status-control">${pill(l.Status)}<select class="status-select" data-lead-id="${esc(l['Lead ID'])}">${opts}</select><span class="save-note" id="save-${esc(l['Lead ID'])}"></span></div>`;}
function filteredRows(){const q=document.getElementById('q').value.toLowerCase(); const st=document.getElementById('state').value; const pr=document.getElementById('priority').value; const ba=document.getElementById('batch').value; const act=document.getElementById('action').value; return leads.filter(l=>{const hay=Object.values(l).join(' ').toLowerCase(); return (!q||hay.includes(q)) && (!st||l._state===st) && (!pr||(l.Priority||'Unrated')===pr) && (!ba||(l.Batch||'Unknown')===ba) && (!act||(act==='due'&&l._needs_action)||(act==='nodraft'&&l._has_email&&!l._has_draft)||(act==='noemail'&&!l._has_email));});}
function render(){const rows=filteredRows(); tbody.innerHTML=rows.map(l=>`<tr class="${l._needs_action?'due':''}"><td><b>${esc(l['Lead ID'])}</b><div class="small muted">${esc(l.Batch)}</div></td><td><b>${esc(l['Business Name'])}</b><div>${link('Website',l.Website)}</div></td><td>${statusSelect(l)}</td><td>${esc(l.Priority||'')}</td><td>${esc(l.Email||'')}</td><td>${esc(l['Next Follow-up Date']||'')}</td><td><code>${esc(l._draft_id)}</code></td><td><div class="links">${link('Prototype',l['Prototype Link'])} ${link('Audit',l['Audit / Report Link'])} ${link('Offer',l['Draft Offer Link'])} ${link('Source',l['Contact / Source Link'])}</div></td><td class="note">${esc(l['Primary Issue']||l['Personalization Angle']||'')}</td><td><button class="btn smallbtn detail" data-lead-id="${esc(l['Lead ID'])}">Details</button></td></tr>`).join(''); document.getElementById('shown').textContent=`Showing ${rows.length} of ${leads.length} leads`;}
async function load(){const res=await fetch('/api/leads'); if(!res.ok) throw new Error(await res.text()); const data=await res.json(); leads=data.rows; statusOptions=data.statusOptions; summary=data.summary; document.getElementById('updated').textContent=`Updated ${data.updatedAt}`; fillSelect('state', leads.map(l=>l._state), 'All states'); fillSelect('priority', leads.map(l=>l.Priority||'Unrated'), 'All priorities'); fillSelect('batch', leads.map(l=>l.Batch||'Unknown'), 'All batches'); renderCards(); bars('statusBars', summary.statusCounts); bars('priorityBars', summary.priorityCounts); render(); if(selectedLeadId) openDrawer(selectedLeadId, false);}
async function saveStatus(leadId, status){const note=document.getElementById(`save-${leadId}`); if(note){note.className='save-note saving'; note.textContent='Saving...';} const res=await fetch(`/api/leads/${encodeURIComponent(leadId)}/status`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status})}); if(!res.ok){if(note){note.className='save-note failed'; note.textContent='Failed';} throw new Error(await res.text());} if(note){note.className='save-note saved'; note.textContent='Saved';} await load(); toast('Status saved');}
function field(name,label,type='text'){const l=leads.find(x=>x['Lead ID']===selectedLeadId)||{}; const v=esc(l[name]||''); if(type==='textarea') return `<div class="field"><label>${label}</label><textarea data-field="${esc(name)}">${v}</textarea></div>`; return `<div class="field"><label>${label}</label><input type="${type}" data-field="${esc(name)}" value="${v}"></div>`;}
function openDrawer(leadId, show=true){selectedLeadId=leadId; const l=leads.find(x=>x['Lead ID']===leadId); if(!l) return; document.getElementById('drawerTitle').innerHTML=`${esc(l['Business Name'])} ${pill(l.Status)}`; document.getElementById('drawerSub').textContent=`${l['Lead ID']} · ${l.Email||'no email'} · ${l.Batch||''}`; document.getElementById('drawerBody').innerHTML=`<div class="drawer-actions">${link('Website',l.Website)} ${link('Prototype',l['Prototype Link'])} ${link('Audit',l['Audit / Report Link'])} ${link('Offer',l['Draft Offer Link'])} ${link('Source',l['Contact / Source Link'])}</div><div class="field-grid">${field('Status','Status')}${field('Priority','Priority')}${field('Next Follow-up Date','Next follow-up','date')}${field('Follow-up Count','Follow-up count','number')}${field('Estimated Value','Estimated value')}${field('Owner','Owner')}${field('Reply Summary','Reply summary')}</div>${field('Personalization Angle','Personalization angle','textarea')}${field('Primary Issue','Primary issue','textarea')}${field('Notes','Notes','textarea')}<div class="drawer-actions"><button class="btn primary" id="saveLead">Save lead</button></div><div class="field"><label>Add note / activity</label><textarea id="newNote" placeholder="Add call note, reply summary, objection, next step..."></textarea></div><div class="drawer-actions"><button class="btn primary" id="addNote">Add note</button><button class="btn" id="setToday">Follow up today</button><button class="btn" id="clearFollow">Clear follow-up</button></div><div class="field"><label>Activity log</label><div class="activity">${esc(l['Activity Log']||'No activity yet.')}</div></div>`; if(show){document.getElementById('drawer').classList.add('open'); document.getElementById('backdrop').classList.add('open');}}
async function saveLead(){const body={updates:{}}; document.querySelectorAll('#drawer [data-field]').forEach(el=>body.updates[el.dataset.field]=el.value); const res=await fetch(`/api/leads/${encodeURIComponent(selectedLeadId)}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); if(!res.ok) throw new Error(await res.text()); await load(); toast('Lead saved');}
async function addNote(){const text=document.getElementById('newNote').value; const res=await fetch(`/api/leads/${encodeURIComponent(selectedLeadId)}/notes`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})}); if(!res.ok) throw new Error(await res.text()); await load(); toast('Note added');}
async function quickUpdate(updates){const res=await fetch(`/api/leads/${encodeURIComponent(selectedLeadId)}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({updates})}); if(!res.ok) throw new Error(await res.text()); await load(); toast('Updated');}
tbody.addEventListener('change', e=>{if(!e.target.matches('.status-select')) return; saveStatus(e.target.dataset.leadId, e.target.value).catch(err=>alert(err.message));});
tbody.addEventListener('click', e=>{const b=e.target.closest('.detail'); if(b) openDrawer(b.dataset.leadId);});
document.getElementById('drawer').addEventListener('click', e=>{if(e.target.id==='saveLead') saveLead().catch(err=>alert(err.message)); if(e.target.id==='addNote') addNote().catch(err=>alert(err.message)); if(e.target.id==='setToday') quickUpdate({'Next Follow-up Date':new Date().toISOString().slice(0,10)}).catch(err=>alert(err.message)); if(e.target.id==='clearFollow') quickUpdate({'Next Follow-up Date':''}).catch(err=>alert(err.message));});
function closeDrawer(){selectedLeadId=null; document.getElementById('drawer').classList.remove('open'); document.getElementById('backdrop').classList.remove('open');}
document.getElementById('closeDrawer').onclick=closeDrawer; document.getElementById('backdrop').onclick=closeDrawer;
['q','state','priority','batch','action'].forEach(id=>document.getElementById(id).addEventListener('input',render));
document.getElementById('clear').addEventListener('click',()=>{['q','state','priority','batch','action'].forEach(id=>document.getElementById(id).value='');render();});
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

    def read_json(self) -> dict[str, object]:
        length = int(self.headers.get('Content-Length') or '0')
        return json.loads(self.rfile.read(length) or b'{}')

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
        try:
            status_match = re.fullmatch(r'/api/leads/([^/]+)/status', path)
            note_match = re.fullmatch(r'/api/leads/([^/]+)/notes', path)
            lead_match = re.fullmatch(r'/api/leads/([^/]+)', path)
            payload = self.read_json()
            if status_match:
                lead_id = unquote(status_match.group(1))
                status = (payload.get('status') or '').strip()
                note = (payload.get('note') or '').strip()
                if not status:
                    self.send_json({'error': 'status is required'}, 400)
                    return
                self.send_json(update_status(lead_id, status, note))
            elif note_match:
                lead_id = unquote(note_match.group(1))
                self.send_json(add_note(lead_id, str(payload.get('text') or ''), str(payload.get('kind') or 'Note')))
            elif lead_match:
                lead_id = unquote(lead_match.group(1))
                updates = payload.get('updates') or {}
                if not isinstance(updates, dict):
                    self.send_json({'error': 'updates must be an object'}, 400)
                    return
                self.send_json(update_lead(lead_id, updates, str(payload.get('note') or '')))
            else:
                self.send_json({'error': 'not found'}, 404)
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
