"""Real-data-only historical forecasting; never invents training or prediction data."""
from datetime import date, datetime
from collections import defaultdict

MIN_RECORDS = 30
MIN_DATES = 14

def build_forecast(records, route_id=None, target_date=None, departure_time=None):
    # Only completed non-test journeys are provided by caller.
    if route_id is not None:
        records=[r for r in records if r['route_id']==route_id]
    distinct_dates={r['service_date'] for r in records}
    if len(records)<MIN_RECORDS or len(distinct_dates)<MIN_DATES:
        return {'ready':False, 'reason':f'At least {MIN_RECORDS} completed non-test trips across {MIN_DATES} distinct days are required.',
                'trips':len(records),'distinct_days':len(distinct_dates), 'required_trips':MIN_RECORDS,'required_days':MIN_DATES}
    if not target_date or not departure_time or route_id is None:
        return {'ready':True,'trips':len(records),'distinct_days':len(distinct_dates), 'reason':'Historical data is available. Choose a route, date and departure time for a forecast.'}
    try:
        when=date.fromisoformat(target_date)
        hour,minute=map(int,departure_time.split(':'))
        if hour>23 or minute>59: raise ValueError()
    except (ValueError,AttributeError):
        return {'ready':False,'reason':'Provide a valid target date and time.'}
    try:
        import numpy as np
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.metrics import mean_absolute_error
    except ImportError:
        return {'ready':False,'reason':'Machine learning dependencies are not installed.'}
    data=sorted(records,key=lambda r:(r['service_date'],r['departure_time']))
    def features(r):
        d=date.fromisoformat(r['service_date']); h,m=map(int,r['departure_time'].split(':'))
        return [d.weekday(), d.month, h+m/60, int(r['route_id'])]
    split=max(1,int(len(data)*.8))
    if len(data)-split<5:
        split=len(data)-5
    x=np.array([features(r) for r in data],dtype=float)
    y=np.array([r['passengers'] for r in data],dtype=float)
    model=RandomForestRegressor(n_estimators=100,min_samples_leaf=2,random_state=42,n_jobs=1)
    model.fit(x[:split],y[:split])
    pred=model.predict(x[split:])
    mae=mean_absolute_error(y[split:],pred)
    baseline=mean_absolute_error(y[split:],[float(np.median(y[:split]))]*(len(y)-split))
    if mae>=baseline:
        return {'ready':False,'reason':'The model did not beat a simple historical baseline on held-out trips; predictions are withheld.',
           'trips':len(data),'distinct_days':len(distinct_dates),'model_mae':round(float(mae),2),'baseline_mae':round(float(baseline),2)}
    # Retrain only after the held-out model beats baseline.
    model.fit(x,y)
    n=float(model.predict([[when.weekday(),when.month,hour+minute/60,int(route_id)]])[0])
    return {'ready':True,'predicted_passengers':max(0,round(n)),'model':'RandomForestRegressor',
        'test_mae':round(float(mae),2),'baseline_mae':round(float(baseline),2),
        'training_trips':len(data),'distinct_days':len(distinct_dates),
        'note':'Estimate from recorded completed non-test journeys, not a guaranteed future passenger count.'}
