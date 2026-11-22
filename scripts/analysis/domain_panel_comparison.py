"""Exploratory PELT/SeedBS comparison on BDL budget and education panels.

Uses the frozen calibration in benchmark/changepoint-comparison-results/experiment-config.csv.
Raw BDL API responses and the CPI series are cached in benchmark/domain-panel-comparison/input/.
No real-panel FPR/FNR are calculated: administrative labels are incomplete.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[2]
IN=ROOT/'benchmark/domain-panel-comparison/input'
OUT=ROOT/'benchmark/domain-panel-comparison'
sys.path.insert(0,str(Path(__file__).resolve().parent))
import changepoint_comparison as cc
import panel_breaks

cfg=pd.read_csv(ROOT/'benchmark/changepoint-comparison-results/experiment-config.csv').iloc[0]
PEN=float(cfg.pelt_rbf_penalty); THR=float(cfg.seedbs_gain_threshold_noise_variance_units)
N_PERM=int(cfg.panel_permutations); ALPHA=.05; MATCH_TOL=int(cfg.match_tolerance_years)

def make_panels():
 # Cache the public BDL population denominator alongside the panel extracts so
 # this analysis runs from the public mirror without the private aggregate tree.
 popdf=pd.read_csv(IN/'population-voivodeship.csv',dtype={'unit_id':str})
 pop={(r.unit_id,int(r.year)):float(r.population) for r in popdf.itertuples()}
 budget=json.load(open(IN/'budget-five-variables.json'))
 hicp={int(y):float(v) for y,v in json.load(open(IN/'poland-hicp-2015-100.json')).items()}
 panels={}
 # Budget: five preregistered chapter series across 16 regions, 2001-25.
 byears={}; bvalues={}; bmeta={}
 for vid,obj in budget.items():
  for row in obj['results']:
   sid=row['id']; vals={int(v['year']):v['val'] for v in row['values']}; years=list(range(2001,2026))
   if not all(y in vals for y in years): continue
   z=[]
   for y in years:
    nominal=float(vals[y]); real_pc=nominal/(hicp[y]/100)/pop[(sid,y)]
    z.append(float(np.arcsinh(real_pc)))
   key=f"budget:{vid}:{sid}"; byears[key]=years; bvalues[key]=np.asarray(z); bmeta[key]={'variable_id':int(vid),'variable':obj['meta']['name'],'region':row['name'],'transform':'asinh(2015-PLN per resident)'}
 panels['budget']=(byears,bvalues,bmeta,2010,'2010 budget-classification regulation (benchmark date; legal applicability must be checked against chosen chapters)')
 # Education: primary and lower-secondary school counts and pupil counts.
 edu_ids={'838':'primary schools','862':'primary pupils','2370':'lower-secondary schools','2394':'lower-secondary pupils'}
 eyears={}; evalues={}; emeta={}
 for vid,label in edu_ids.items():
  obj=json.load(open(IN/f'edu_{vid}.json'))
  for row in obj['results']:
   sid=row['id']; vals={int(v['year']):v['val'] for v in row['values']}; years=sorted(vals)
   # Convert counts to rates per 100,000 residents for scale/composition comparability.
   ys=[y for y in years if (sid,y) in pop]
   if len(ys)<6: continue
   z=[float(vals[y])/pop[(sid,y)]*100000 for y in ys]
   key=f"education:{vid}:{sid}"; eyears[key]=ys; evalues[key]=np.asarray(z); emeta[key]={'variable_id':int(vid),'variable':label,'region':row['name'],'transform':'count per 100,000 residents','start':min(ys),'end':max(ys)}
 panels['education']=(eyears,evalues,emeta,2009,'GUS source transition to Educational Information System (SIO), school year 2008/09; BDL year 2009')
 return panels

def analyze():
 rows=[]; sysrows=[]
 for domain,(years,values,meta,benchmark_year,benchmark_description) in make_panels().items():
  if len(values)==0: raise RuntimeError(f'empty panel: {domain}')
  method_breaks={}
  for method in ['PELT','SeedBS']:
   bp={}; individual=[]
   for sid,x in values.items():
    ds=cc.pelt(x,PEN) if method=='PELT' else cc.seedbs(x,THR)
    bps=[int(years[sid][i]) for i in ds]
    bp[sid]=bps
    m=meta[sid]
    individual.append({'domain':domain,'method':method,'series_id':sid,'variable_id':m['variable_id'],'variable':m['variable'],'region':m['region'],'start_year':years[sid][0],'end_year':years[sid][-1],'break_years':';'.join(map(str,bps)),'break_count':len(bps),'benchmark_year':benchmark_year,'nearest_break_distance':min([abs(y-benchmark_year) for y in bps],default=np.nan),'benchmark_hit_within_tolerance':any(abs(y-benchmark_year)<=MATCH_TOL for y in bps)})
   rows.extend(individual); method_breaks[method]=bp
   res=panel_breaks.permutation_common_breaks(years,bp,n_perm=N_PERM,seed=20261121+(0 if domain=='budget' else 1),alpha=ALPHA)
   flagged=sorted(res.loc[res.system_wide,'year'].astype(int).tolist())
   # Score only proximity to the documented event; this is not a full error rate.
   hit=any(abs(y-benchmark_year)<=MATCH_TOL for y in flagged)
   near=res.loc[(res.year-benchmark_year).abs()<=MATCH_TOL]
   sysrows.append({'domain':domain,'method':method,'series':len(values),'single_series_breaks':sum(map(len,bp.values())),'system_years_fdr05':';'.join(map(str,flagged)),'benchmark_year':benchmark_year,'benchmark_hit_within_tolerance':hit,'benchmark_breadth_max_within_tolerance':int(near.breadth.max()) if len(near) else 0,'benchmark_description':benchmark_description,'permutations':N_PERM,'fdr_alpha':ALPHA,'match_tolerance_years':MATCH_TOL,'real_panel_fpr_fnr':'not estimable; incomplete labels'})
   res.insert(0,'domain',domain); res.insert(1,'method',method); res['benchmark_year']=benchmark_year
   res.to_csv(OUT/f'{domain}-{method.lower()}-panel-year-tests.csv',index=False)
 pd.DataFrame(rows).to_csv(OUT/'individual-series-results.csv',index=False)
 pd.DataFrame(sysrows).to_csv(OUT/'panel-summary.csv',index=False)
 print(pd.DataFrame(sysrows).to_string(index=False))
 print('\nBreak counts by domain/method/variable:')
 print(pd.DataFrame(rows).groupby(['domain','method','variable'])['break_count'].agg(['sum','mean']).to_string())
if __name__=='__main__':
 OUT.mkdir(parents=True,exist_ok=True); analyze()
