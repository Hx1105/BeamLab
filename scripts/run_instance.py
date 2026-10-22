"""Use the environment of a started BeamLab instance for one experiment."""
from pathlib import Path
import argparse,json,os,subprocess,sys
root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--instance',default='lab');a,extra=p.parse_known_args()
if not a.instance.replace('-','').isalnum():raise SystemExit('Invalid instance name')
config=root/'runtime'/a.instance/'instance-environment.json'
env=os.environ.copy();env.update(json.loads(config.read_text()))
raise SystemExit(subprocess.run([sys.executable,str(root/'scripts/experiment.py'),*extra],cwd=root,env=env).returncode)
