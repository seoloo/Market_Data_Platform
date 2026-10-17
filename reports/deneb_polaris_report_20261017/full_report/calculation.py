from __future__ import annotations
import numpy as np
import csv, hashlib, json, math, sys, statistics, time
from pathlib import Path
from datetime import date,timedelta
from types import FrameType
from typing import Any
R=Path('C:/Users/seolh/Documents/GitHub/Auto_Trading')
W=Path(__file__).parent
sys.path[:0]=[str(R),str(R/'src')]
sys.path.append(str(R/'.venv/Lib/site-packages'))
from scripts import run_brake_184 as b
from scripts import run_brake_183 as b183
from scripts import run_namu_core_compare as c
SPEC=R/'reports/deneb_polaris_report_20261017/full_report/calculation_spec.json'
END='2026-08-31';M=200000
def sha(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def signals(days:list[str],q:dict[str,float])->tuple[dict,dict]:
    a=np.array([q[d] for d in days]);r=a[1:]/a[:-1]-1
    v=np.full(len(a),np.nan);t=np.full(len(a),np.nan)
    v[20:]=np.std(np.lib.stride_tricks.sliding_window_view(r,20),axis=1)*np.sqrt(252)
    t[120:]=(np.sign(a[120:]-a[100:-20])+np.sign(a[120:]-a[60:-60])+np.sign(a[120:]-a[:-120]))>0
    return dict(zip(days,[None if np.isnan(x) else float(x) for x in v])),dict(zip(days,[None if np.isnan(x) else bool(x) for x in t]))
def capture_pol(px:dict,days:list[str],i0:int,i1:int,pol:str)->dict:
    got={}
    def prof(f:FrameType,e:str,a:Any)->None:
        if e=='return' and f.f_code is c.run_policy.__code__:
            got['values']=list(f.f_locals['vals']);got['nav']=list(f.f_locals['navs'])
    sys.setprofile(prof)
    try:res=c.run_policy(px,days,i0,i1,pol,lambda _:float(M))
    finally:sys.setprofile(None)
    return {**res,**got,'dates':days[i0:i1+1]}
def metric(days:list[str],values:list[float],nav:list[float])->dict:
    a=np.asarray(nav);rr=a[1:]/a[:-1]-1;peak=np.maximum.accumulate(a);dd=a/peak-1
    tr=int(np.argmin(dd));pk=int(np.argmax(a[:tr+1]));rec=next((i for i in range(tr,len(a)) if a[i]>=a[pk]),None)
    yrs=(date.fromisoformat(days[-1])-date.fromisoformat(days[0])).days/365.25
    count=len({d[:7] for d in days});paid=count*M
    return {'final':values[-1],'paid':paid,'profit':values[-1]-paid,'cagr':float((a[-1]/a[0])**(1/yrs)-1),'mdd':float(dd.min()),'sharpe_zero_rf':float(rr.mean()/rr.std()*np.sqrt(252)) if rr.std()>0 else 0,'peak':days[pk],'trough':days[tr],'recovery':days[rec] if rec else None,'recovery_days':(date.fromisoformat(days[rec])-date.fromisoformat(days[pk])).days if rec else None}
def small(sim:dict)->dict:
    days=sim.get('nav_days',sim.get('dates'));vals=sim.get('vals',sim.get('values'));nav=sim.get('navs',sim.get('nav'))
    idx=[i for i,d in enumerate(days) if i==len(days)-1 or d[:7]!=days[i+1][:7]]
    return {'metrics':metric(days,vals,nav),'month_dates':[days[i] for i in idx],'values':[vals[i] for i in idx],'nav':[nav[i] for i in idx]}
def save(m:int,y:int,r:float)->float:
    z=0.
    for _ in range(y):z=z*(1+r)+12*m+6.5*m*r
    return z
def main()->None:
    spec=json.loads(SPEC.read_text(encoding='utf-8'));assert not (W/'numbers.json').exists()
    paths=[SPEC,R/'scripts/run_brake_184.py',R/'scripts/run_brake_183.py',R/'scripts/run_namu_core_compare.py',R/'src/auto_trading/ops/deneb_guard.py',R/'src/auto_trading/ops/polaris.py',Path(__file__)]
    paths+=list((R/'data/research/div_mix_180').glob('*.csv'))+[R/'data/research/lev_div_181/TQQQ.csv',R/'data/research/lev_div_181/QLD.csv']
    hashes={str(p):sha(p) for p in paths}
    b.TAX=0.;c.TAX_RATE=0.;c.FEE=.0015
    px={s:c.load(R/f'data/research/div_mix_180/{s}.csv') for s in ['QQQ','SCHD','SPY','IEF','GLD']}
    px['TQQQ']=c.load(R/'data/research/lev_div_181/TQQQ.csv');px['QLD']=c.load(R/'data/research/lev_div_181/QLD.csv')
    common=sorted(d for d in set.intersection(*(set(v) for v in px.values())) if d<=END)
    i0=next(i for i,d in enumerate(common) if d>='2012-01-01');days=common[i0:]
    vol,tr=signals(common,px['QQQ']);ov,ot=b183.signals(common,px['QQQ'])
    assert all((vol[d] is None and ov[d] is None) or math.isclose(vol[d],ov[d],abs_tol=1e-12) for d in common)
    assert tr==ot
    hist={};raw={}
    def den(sym:str,ref:str|None,vol_on:bool,cap:float)->dict:
        prev=b.LEV_MAX;b.LEV_MAX=cap
        try:return b.simulate({'days':common,'lev':px[sym],'div':px['SCHD'],'vol':vol if vol_on else {},'trend':tr,'start':'2012-01-01'},px['QQQ'],ref=ref,monthly_krw=M)
        finally:b.LEV_MAX=prev
    cases=[('Deneb','TQQQ','T',True,.6),('방어 제외','TQQQ',None,True,.6),('3배+SCHD 고정','TQQQ',None,False,.6),('1배+SCHD 고정','QQQ',None,False,.6),('2배+SCHD 고정','QLD',None,False,.6),('나스닥1배','QQQ',None,False,1.),('나스닥2배','QLD',None,False,1.),('나스닥3배','TQQQ',None,False,1.),('S&P500','SPY',None,False,1.)]
    for name,sym,ref,v,cap in cases:
        sim=den(sym,ref,v,cap);hist[name]=small(sim);raw[name]=sim
    for name,pol in [('Polaris','YEAR'),('Polaris 매수만','BUY'),('Polaris 매달조정','MONTH')]:hist[name]=small(capture_pol(px,common,i0,len(common)-1,pol))
    old=json.loads((R/'reports/deneb_polaris_report_20261017/six_layouts/calculation_result.json').read_text(encoding='utf-8'))
    for name in ['Deneb','Polaris']:assert math.isclose(hist[name]['metrics']['final'],old['historical'][name]['final'],rel_tol=1e-10)
    # Fixed-cost synthetic cash-flow identity, zero-return and linear-scaling checks.
    testdays=['2026-12-31']+[f'2027-{m:02}-01' for m in range(1,13)]
    flat=dict.fromkeys(testdays,100.)
    z=b.simulate({'days':testdays,'lev':flat,'div':flat,'vol':{},'trend':{},'start':'2027-01-01'},flat,ref=None,monthly_krw=M)
    assert math.isclose(z['value_end'],12*M*(1-b.COST),rel_tol=1e-12)
    z2=b.simulate({'days':testdays,'lev':flat,'div':flat,'vol':{},'trend':{},'start':'2027-01-01'},flat,ref=None,monthly_krw=M/4)
    assert math.isclose(z2['value_end']*4,z['value_end'],rel_tol=1e-12)
    # Synchronized asset blocks, then causal signals and full strategy state replay.
    syms=['QQQ','TQQQ','SCHD','SPY','IEF','GLD'];prices=np.array([[px[s][d] for s in syms] for d in days]);rets=prices[1:]/prices[:-1]-1
    sd=[];cur=date(2026,1,1)
    while cur<date(2047,1,1):
        if cur.weekday()<5:sd.append(cur.isoformat())
        cur+=timedelta(days=1)
    targets=[max(i for i,d in enumerate(sd) if d[:4]==str(2026+y)) for y in [5,10,15,20]]
    rng=np.random.default_rng(spec['state_replay']['seed']);ns=spec['state_replay']['paths'];dist={k:{str(y):[] for y in [5,10,15,20]} for k in ['Deneb','Polaris']}
    monthidx=[i for i,d in enumerate(sd) if d>='2027-01-01' and (i==len(sd)-1 or d[:7]!=sd[i+1][:7])]
    tracks={k:[] for k in dist};t0=time.time()
    for n in range(ns):
        starts=rng.integers(0,len(rets)-252+1,size=math.ceil((len(sd)-1)/252))
        rr=np.concatenate([rets[a:a+252] for a in starts],axis=0)[:len(sd)-1]
        pa=np.vstack([np.ones(6)*100,100*np.cumprod(1+rr,axis=0)])
        pp={s:dict(zip(sd,pa[:,j].tolist())) for j,s in enumerate(syms)};vv,tt=signals(sd,pp['QQQ'])
        sim=b.simulate({'days':sd,'lev':pp['TQQQ'],'div':pp['SCHD'],'vol':vv,'trend':tt,'start':'2027-01-01'},pp['QQQ'],ref='T',monthly_krw=M)
        pol=capture_pol(pp,sd,next(i for i,d in enumerate(sd) if d>='2027-01-01'),len(sd)-1,'YEAR')
        assert sim['contrib']==pol['paid']==240*M
        for name,dd,vals in [('Deneb',sim['nav_days'],sim['vals']),('Polaris',pol['dates'],pol['values'])]:
            di={d:i for i,d in enumerate(dd)}
            for y,j in zip([5,10,15,20],targets):dist[name][str(y)].append(vals[di[sd[j]]])
            tracks[name].append([vals[di[sd[j]]] for j in monthidx])
        if (n+1)%100==0:print(json.dumps({'paths_done':n+1,'elapsed_s':round(time.time()-t0)},ensure_ascii=False),flush=True)
    summary={};grid=[];fan={}
    for name in dist:
        summary[name]={}
        ta=np.array(tracks[name]);fan[name]={key:np.quantile(ta,q,axis=0).tolist() for key,q in [('p10',.1),('p50',.5),('p90',.9)]}
        for y,samples in dist[name].items():
            qs={key:float(np.quantile(samples,q)) for key,q in [('min',0),('p10',.1),('p50',.5),('p90',.9)]}
            assert list(qs.values())==sorted(qs.values());summary[name][y]=qs
            for m in range(50000,1000001,50000):grid.append({'model':name,'years':int(y),'monthly':m,'paid':m*12*int(y),**{k:v*m/M for k,v in qs.items()}})
    assert len(grid)==160
    sales=raw['Deneb']['sales'];eps=raw['Deneb']['episodes']
    annual={}
    for name in ['Deneb','Polaris','방어 제외']:
        h=hist[name];by={}
        for d,v in zip(h['month_dates'],h['nav']):by[d[:4]]=v
        prev=1.;annual[name]=[]
        for yy,v in by.items():annual[name].append({'year':yy,'return':v/prev-1});prev=v
    out={'spec_sha':sha(SPEC),'source_hashes':hashes,'historical':hist,'annual':annual,'rebuy':{'sales':sales,'episodes':eps,'more_expensive_share':sum(e['rebuy']>e['avg_sale'] for e in eps)/len(eps)},'resampling':{'paths':ns,'seed':spec['state_replay']['seed'],'summary':summary,'fan_months':list(range(1,241)),'fan':fan},'grid':grid,'savings_grid':[{'monthly':m,'years':y,'paid':m*12*y,'r3':save(m,y,.03),'r4':save(m,y,.04)} for y in [5,10,15,20] for m in range(50000,1000001,50000)],'checks':{'sources_unchanged':hashes=={str(p):sha(p) for p in paths},'old_endpoints_reproduced':True,'causal_signal_parity':True,'zero_return_and_scale':True,'same_paths_and_deposits':True,'grid_rows':len(grid),'paths':ns,'decision':'NO_NEW_PASS_OOS_OR_LIVE_APPROVAL'}}
    assert out['checks']['sources_unchanged']
    (W/'numbers.json').write_text(json.dumps(out,ensure_ascii=False,indent=1),encoding='utf-8')
    print(json.dumps({'checks':out['checks'],'summary':summary},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
