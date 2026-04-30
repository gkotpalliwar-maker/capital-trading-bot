import json, logging
from pathlib import Path
from threading import Lock
logger = logging.getLogger("config")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
INSTRUMENTS_JSON = DATA_DIR / "instruments.json"
_config_lock = Lock()
BASE_INSTRUMENT_MAP = {"crude":"OIL_CRUDE","gold":"GOLD","silver":"SILVER","btcusd":"BTCUSD","ethusd":"ETHUSD","eurusd":"EURUSD","gbpusd":"GBPUSD","usdjpy":"USDJPY","audusd":"AUDUSD","nzdusd":"NZDUSD","usdcad":"USDCAD","usdchf":"USDCHF","nas100":"US100","spx500":"US500","us30":"US30"}
BASE_PIP_SIZE = {"OIL_CRUDE":0.01,"GOLD":0.01,"SILVER":0.001,"BTCUSD":1.0,"ETHUSD":0.01,"EURUSD":0.0001,"GBPUSD":0.0001,"USDJPY":0.01,"AUDUSD":0.0001,"NZDUSD":0.0001,"USDCAD":0.0001,"USDCHF":0.0001,"US100":1.0,"US500":0.1,"US30":1.0}
BASE_DEFAULT_SIZE = {"OIL_CRUDE":1,"GOLD":0.01,"SILVER":0.1,"BTCUSD":0.01,"ETHUSD":0.1,"EURUSD":1000,"GBPUSD":1000,"USDJPY":1000,"AUDUSD":1000,"NZDUSD":1000,"USDCAD":1000,"USDCHF":1000,"US100":0.1,"US500":0.1,"US30":0.1}
BASE_SCAN_INSTRUMENTS = ["gold","crude","eurusd","gbpusd","usdjpy","btcusd","ethusd","nas100","spx500"]
def _load_overrides():
    if not INSTRUMENTS_JSON.exists(): return {"added":{},"removed":[],"lot_overrides":{},"pip_overrides":{},"scan_list":None}
    try:
        with open(INSTRUMENTS_JSON) as f: data = json.load(f)
        for k in ["added","removed","lot_overrides","pip_overrides","scan_list"]: data.setdefault(k, {} if k in ("added","lot_overrides","pip_overrides") else ([] if k=="removed" else None))
        return data
    except: return {"added":{},"removed":[],"lot_overrides":{},"pip_overrides":{},"scan_list":None}
def _save_overrides(ov):
    with _config_lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(INSTRUMENTS_JSON,"w") as f: json.dump(ov,f,indent=2)
def get_merged_config():
    ov = _load_overrides()
    im,ps,ls,sl = dict(BASE_INSTRUMENT_MAP),dict(BASE_PIP_SIZE),dict(BASE_DEFAULT_SIZE),list(BASE_SCAN_INSTRUMENTS)
    for n in ov.get("removed",[]): im.pop(n.lower(),None); sl.remove(n.lower()) if n.lower() in sl else None
    for n,i in ov.get("added",{}).items(): im[n.lower()]=i["epic"]; ps[i["epic"]]=i.get("pip",0.01); ls[i["epic"]]=i.get("lot",0.1); sl.append(n.lower()) if i.get("scan",True) and n.lower() not in sl else None
    for e,s in ov.get("lot_overrides",{}).items(): ls[e]=s
    for e,s in ov.get("pip_overrides",{}).items(): ps[e]=s
    if ov.get("scan_list"): sl=ov["scan_list"]
    return {"instrument_map":im,"pip_size":ps,"default_size":ls,"scan_instruments":sl,"display_map":{v:k.upper() for k,v in im.items()}}
def add_instrument(name,epic,pip,lot,scan=True):
    ov=_load_overrides()
    if name.lower() in ov["removed"]: ov["removed"].remove(name.lower())
    ov["added"][name.lower()]={"epic":epic.upper(),"pip":pip,"lot":lot,"scan":scan}
    _save_overrides(ov)
    cfg=get_merged_config()
    return "Added: "+name.lower()+" > "+epic.upper()+"\n   Pip: "+str(pip)+" | Lot: "+str(lot)+"\n   Active: "+str(len(cfg["scan_instruments"]))
def remove_instrument(name):
    ov,cfg=_load_overrides(),get_merged_config()
    if name.lower() not in cfg["instrument_map"]: return "'"+name+"' not found."
    if name.lower() in ov["added"]: del ov["added"][name.lower()]
    elif name.lower() not in ov["removed"]: ov["removed"].append(name.lower())
    _save_overrides(ov)
    return "Removed: "+name.lower()+"\n   Active: "+str(len(get_merged_config()["scan_instruments"]))
def set_lot_size(name,size):
    cfg,ov=get_merged_config(),_load_overrides()
    if name.lower() not in cfg["instrument_map"]: return "'"+name+"' not found."
    e=cfg["instrument_map"][name.lower()]
    if name.lower() in ov["added"]: ov["added"][name.lower()]["lot"]=size
    else: ov["lot_overrides"][e]=size
    _save_overrides(ov)
    return "Lot: "+name.lower()+" ("+e+") > "+str(size)
def set_pip_size(name,size):
    cfg,ov=get_merged_config(),_load_overrides()
    if name.lower() not in cfg["instrument_map"]: return "'"+name+"' not found."
    e=cfg["instrument_map"][name.lower()]
    if name.lower() in ov["added"]: ov["added"][name.lower()]["pip"]=size
    else: ov["pip_overrides"][e]=size
    _save_overrides(ov)
    return "Pip: "+name.lower()+" ("+e+") > "+str(size)
def list_instruments():
    cfg=get_merged_config()
    lines=["Active Instruments\n",f"{'Name':<10} {'Epic':<12} {'Lot':>8} {'Pip':>10} Scan","-"*52]
    ss=set(cfg["scan_instruments"])
    for n,e in sorted(cfg["instrument_map"].items()): lines.append(f"{n:<10} {e:<12} {cfg['default_size'].get(e,'?'):>8} {cfg['pip_size'].get(e,'?'):>10} {'Y' if n in ss else 'N'}")
    lines.append(f"\nScanning: {len(ss)} instruments")
    return "<pre>"+"\n".join(lines)+"</pre>"
