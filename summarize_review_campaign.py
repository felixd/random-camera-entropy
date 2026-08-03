#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, math, statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from report_ui import chart_div, esc, fmt, html_page, metric_card, metrics_grid, table_html

APP_VERSION='2026.08.03.camera-entropy-review.7.11.0'

def load(p:Path)->dict[str,Any]:
    try:
        x=json.loads(p.read_text(encoding='utf-8')); return x if isinstance(x,dict) else {}
    except Exception:return {}

def get(d:dict[str,Any], path:str, default=None):
    x:Any=d
    for k in path.split('.'):
        if not isinstance(x,dict): return default
        x=x.get(k,default)
    return x

def num(x):
    try:
        y=float(x); return y if math.isfinite(y) else None
    except Exception:return None

def rate_kbs(x):
    y=num(x); return '—' if y is None else f'{y/8000:.3f}'

def row_for(root:Path,state:dict[str,Any])->dict[str,Any]:
    run=root.parent.parent / str(state.get('run','')) if False else root / str(state.get('run','')).split('/',1)[-1]
    # state run is campaign/profiles/name; resolve robustly from campaign root
    run=root / 'profiles' / Path(str(state.get('run',''))).name
    complete=load(run/'output_complete.json'); failed=load(run/'run_failed.json'); terminal=complete or failed
    cfg=load(run/'runner_config.json'); bp=load(run/'lsb_bitplane_summary.json')
    conditioned=load(run/'analysis_conditioned'/'summary.json'); raw=load(run/'analysis_selected_raw'/'summary.json'); vn=load(run/'analysis_vn'/'summary.json')
    masked=get(terminal,'rates.masked_bps_lifetime') or get(terminal,'rates.masked_bps_10s') or get(terminal,'rates.masked_bps_current')
    rawrate=get(terminal,'rates.raw_change_bps_lifetime') or get(terminal,'rates.raw_change_bps_10s') or get(terminal,'rates.raw_change_bps_current')
    config=dict(cfg); config.update(state.get('requested_parameters') or {})
    health=terminal.get('health',{}) if isinstance(terminal.get('health'),dict) else {}
    return {
      'index':state.get('index'),'category':state.get('category'),'name':state.get('name'),'status':'failed' if failed else ('complete' if complete else state.get('status','incomplete')),
      'exit_code':state.get('exit_code'),'failure_reason':failed.get('reason',''),'run_dir':str(run.relative_to(root)),
      'sample_mode':config.get('sample_mode') or config.get('SAMPLE_MODE'),'lsb_bits':int(config.get('lsb_bits') or config.get('LSB_BITS') or 1),
      'pair_lag_frames':config.get('pair_lag_frames') or config.get('PAIR_LAG_FRAMES'),'entropy_credit':config.get('entropy_credit_bits_per_pixel') or config.get('ENTROPY_CREDIT_BITS_PER_PIXEL'),
      'conditioner_input_bits':config.get('conditioner_input_bits') or config.get('CONDITIONER_INPUT_BITS'),'spatial_mask_pattern':config.get('spatial_mask_pattern') or config.get('SPATIAL_MASK_PATTERN'),
      'spatial_step_x':config.get('spatial_step_x') or config.get('SPATIAL_STEP_X'),'spatial_step_y':config.get('spatial_step_y') or config.get('SPATIAL_STEP_Y'),
      'spatial_phase_x':config.get('spatial_phase_x') or config.get('SPATIAL_PHASE_X'),'spatial_phase_y':config.get('spatial_phase_y') or config.get('SPATIAL_PHASE_Y'),
      'temporal_offset_x':config.get('temporal_spatial_offset_x') or config.get('TEMPORAL_SPATIAL_OFFSET_X'),'temporal_offset_y':config.get('temporal_spatial_offset_y') or config.get('TEMPORAL_SPATIAL_OFFSET_Y'),
      'serialization_order':config.get('serialization_order') or config.get('SERIALIZATION_ORDER'),'tile_width':config.get('serialization_tile_width') or config.get('SERIALIZATION_TILE_WIDTH'),'tile_height':config.get('serialization_tile_height') or config.get('SERIALIZATION_TILE_HEIGHT'),
      'active_pixels':terminal.get('active_pixels'),'measured_fps':get(terminal,'mode.measured_fps'),'raw_bps':num(rawrate),'masked_bps':num(masked),'conditioned_bps':num(get(terminal,'conditioner.output_bps_until_complete') or get(terminal,'conditioner.output_bps_lifetime')),
      'time_to_target_s':num(get(terminal,'conditioner.time_to_target_seconds')),
      'symbol_hmin':num(bp.get('symbol_min_entropy_bits_per_symbol')),'hmin_per_input_bit':num(bp.get('symbol_min_entropy_bits_per_input_bit')),
      'cross_plane_phi':num(bp.get('max_abs_cross_plane_phi')),'mutual_information':num(bp.get('max_cross_plane_mutual_information_bits')),
      'raw_p1':num(raw.get('p1')),'raw_lag1_phi':num(get(raw,'lag1.phi')),'vn_p1':num(vn.get('p1')),'vn_lag1_phi':num(get(vn,'lag1.phi')),
      'conditioned_p1':num(conditioned.get('p1')),'conditioned_lag1_phi':num(get(conditioned,'lag1.phi')),'conditioned_byte_hmin':num(conditioned.get('byte_min_entropy_bits_per_byte')),'conditioned_chi_p':num(conditioned.get('byte_chi_square_p_value')),
      'rct_failures':health.get('rct_failures'),'apt_failures':health.get('apt_failures'),'health_domain':health.get('sample_domain'),'health_width_bits':health.get('sample_width_bits'),
      'parameters':config,
      'links':{'run_report':f"profiles/{run.name}/run_report.html",'bitplane_report':f"profiles/{run.name}/lsb_bitplane_report.html",'failure':f"profiles/{run.name}/run_failed.json" if failed else ''}
    }

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('campaign',type=Path); a=ap.parse_args(); root=a.campaign.resolve()
    states=[load(p) for p in sorted((root/'review_profiles').glob('*.json'))]
    rows=[row_for(root,s) for s in states]
    groups=defaultdict(list)
    for r in rows: groups[str(r['category'])].append(r)
    summary={'schema':'camera-entropy-production-review-v1','app_version':APP_VERSION,'generated_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),'campaign':root.name,'profiles':rows,'categories':{k:len(v) for k,v in groups.items()},'notes':['All rates are stored in bit/s; the HTML defaults to kB/s and supports unit switching.','Empirical Hmin is diagnostic and is not a formal SP 800-90B entropy claim.']}
    (root/'review_summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    complete=sum(r['status']=='complete' for r in rows); failed=sum(r['status']=='failed' for r in rows)
    max_sha=max((r['conditioned_bps'] for r in rows if r['conditioned_bps'] is not None),default=None)
    min_h=min((r['conditioned_byte_hmin'] for r in rows if r['conditioned_byte_hmin'] is not None),default=None)
    cards=[metric_card('Profile',f'{complete}/{len(rows)} complete',f'failed: {failed}','good' if failed==0 and complete==len(rows) else 'warn'),metric_card('Kategorie',len(groups),'LSB, lag, conditioner, spatial, serialization, offset'),metric_card('Maks. SHA3',f'{max_sha/8000:.3f} kB/s' if max_sha else '—','domyślna jednostka raportu'),metric_card('Min Hmin SHA3',fmt(min_h,7),'bit/bajt')]
    plot_specs=[]; sections=[]
    labels_map={'lsb-width':'LSB 1–4 i tryb próbkowania','pair-lag':'Lag temporalny','conditioner':'Rozmiar wejścia SHA3-512','spatial':'Maska i faza przestrzenna','serialization':'Kolejność serializacji','temporal-offset':'Offset przestrzenny XOR'}
    for gi,(cat,rs) in enumerate(groups.items()):
        labels=[r['name'] for r in rs]
        rid=f'cat-{gi}'
        plot_specs.extend([
          {'id':rid+'-rate','data':[{'type':'bar','name':'RAW','x':labels,'y':[r['raw_bps'] for r in rs],'customdata':[[r['raw_bps']] for r in rs]},{'type':'bar','name':'Po masce','x':labels,'y':[r['masked_bps'] for r in rs],'customdata':[[r['masked_bps']] for r in rs]},{'type':'bar','name':'SHA3-512','x':labels,'y':[r['conditioned_bps'] for r in rs],'customdata':[[r['conditioned_bps']] for r in rs]}],'layout':{'barmode':'group','xaxis':{'title':'Profil'},'yaxis':{'title':'kB/s'}}},
          {'id':rid+'-entropy','data':[{'type':'bar','name':'Hmin/symbol','x':labels,'y':[r['symbol_hmin'] for r in rs]},{'type':'bar','name':'Hmin/input bit','x':labels,'y':[r['hmin_per_input_bit'] for r in rs]},{'type':'scatter','mode':'lines+markers','name':'max |φ| bitplanes','x':labels,'y':[r['cross_plane_phi'] for r in rs],'yaxis':'y2'}],'layout':{'barmode':'group','xaxis':{'title':'Profil'},'yaxis':{'title':'Hmin [bit]','rangemode':'tozero'},'yaxis2':{'title':'max |φ|','overlaying':'y','side':'right','range':[0,1]}}},
          {'id':rid+'-quality','data':[{'type':'scatter','mode':'lines+markers','name':'RAW |φ lag-1|','x':labels,'y':[abs(r['raw_lag1_phi']) if r['raw_lag1_phi'] is not None else None for r in rs]},{'type':'scatter','mode':'lines+markers','name':'VN |φ lag-1|','x':labels,'y':[abs(r['vn_lag1_phi']) if r['vn_lag1_phi'] is not None else None for r in rs]},{'type':'scatter','mode':'lines+markers','name':'SHA3 |φ lag-1|','x':labels,'y':[abs(r['conditioned_lag1_phi']) if r['conditioned_lag1_phi'] is not None else None for r in rs]}],'layout':{'xaxis':{'title':'Profil'},'yaxis':{'title':'|φ| lag-1','rangemode':'tozero'}}}
        ])
        headers=['Profil','Status','Tryb','LSB','Lag','Credit','SHA3 in','Maska/faza','Serializacja','Offset','RAW [kB/s]','Masked [kB/s]','SHA3 [kB/s]','Hmin symbol','Hmin/input','max |φ|','MI','RAW lag1','SHA3 lag1','χ² p','RCT/APT']
        trows=[]
        for r in rs:
            mask=f"{r['spatial_mask_pattern']} ({r['spatial_phase_x']},{r['spatial_phase_y']})"
            ser=f"{r['serialization_order']} {r['tile_width']}×{r['tile_height']}"
            trows.append([r['name'],r['status'],r['sample_mode'],r['lsb_bits'],r['pair_lag_frames'],r['entropy_credit'],r['conditioner_input_bits'],mask,ser,f"{r['temporal_offset_x']},{r['temporal_offset_y']}",rate_kbs(r['raw_bps']),rate_kbs(r['masked_bps']),rate_kbs(r['conditioned_bps']),fmt(r['symbol_hmin'],7),fmt(r['hmin_per_input_bit'],7),fmt(r['cross_plane_phi'],7),fmt(r['mutual_information'],7),fmt(r['raw_lag1_phi'],7),fmt(r['conditioned_lag1_phi'],7),fmt(r['conditioned_chi_p'],7),f"{r['rct_failures']}/{r['apt_failures']}"])
        details=''.join(f"<details><summary>{esc(r['name'])} — pełne parametry i linki</summary><pre>{esc(json.dumps(r['parameters'],indent=2,ensure_ascii=False))}</pre><p><a href=\"{esc(r['links']['run_report'])}\">run_report</a> · <a href=\"{esc(r['links']['bitplane_report'])}\">LSB report</a></p></details>" for r in rs)
        sections.append(f'<section><h2>{esc(labels_map.get(cat,cat))}</h2><p class="muted">Parametry każdego przebiegu są pokazane w tabeli i rozwijanych blokach.</p></section><div class="chart-grid">{chart_div(rid+"-rate","Przepustowość","Jednostkę można zmienić globalnym przełącznikiem.",370)}{chart_div(rid+"-entropy","Entropia i zależności bitplane","Empiryczna Hmin oraz korelacja między płaszczyznami.",370)}</div>{chart_div(rid+"-quality","Korelacja etapów pipeline","RAW, Von Neumann i finalny SHA3-512.",350)}<section>{table_html(headers,trows,compact=True)}</section>{details}')
    embedded=esc(json.dumps(summary,ensure_ascii=False))
    unit_select='<label for="rateUnit">Jednostka przepustowości</label><select id="rateUnit"><option value="bit/s">bit/s</option><option value="kbit/s">kbit/s</option><option value="kB/s" selected>kB/s</option><option value="MiB/s">MiB/s</option><option value="MB/s">MB/s</option></select>'
    body='<div class="callout good"><strong>Plik do oceny:</strong> ten HTML zawiera pełne podsumowanie i osadzony JSON wszystkich przebiegów. Wystarczy przesłać sam <code>review_report.html</code> lub podać link.</div>'+metrics_grid(cards)+f'<section><h2>Sterowanie wykresami</h2>{unit_select}<p class="muted">Wszystkie wartości źródłowe pozostają zapisane w bit/s; zmiana jednostki nie modyfikuje danych.</p></section>'+''.join(sections)+f'<script id="review-data" type="application/json">{embedded}</script>'
    extra_js="""
const rateUnits={'bit/s':1,'kbit/s':1000,'kB/s':8000,'MiB/s':8388608,'MB/s':8000000};
const rateSelect=document.getElementById('rateUnit');
function updateRateUnits(){const unit=rateSelect.value,div=rateUnits[unit];document.querySelectorAll('[id$="-rate"]').forEach(el=>{if(!window.Plotly||!el.data)return;const ys=el.data.map(t=>(t.customdata||[]).map(v=>{const n=Array.isArray(v)?v[0]:v;return Number.isFinite(Number(n))?Number(n)/div:null;}));Plotly.restyle(el,{y:ys});Plotly.relayout(el,{'yaxis.title.text':unit});});}
rateSelect.addEventListener('change',updateRateUnits);setTimeout(updateRateUnits,250);
"""
    doc=html_page(title='Camera entropy — raport do decyzji produkcyjnej',subtitle=root.name,navigation='<a href="review_summary.json">JSON</a>',body=body,plot_specs=plot_specs,extra_js=extra_js,extra_css='select{padding:8px 10px;background:var(--panel-2);color:var(--text);border:1px solid var(--border);border-radius:7px} pre{max-height:460px;overflow:auto}')
    (root/'review_report.html').write_text(doc,encoding='utf-8')
    print(json.dumps({'report':str(root/'review_report.html'),'profiles':len(rows),'complete':complete,'failed':failed},ensure_ascii=False))
    return 0
if __name__=='__main__': raise SystemExit(main())
