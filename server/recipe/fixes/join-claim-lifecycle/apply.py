"""Validate the entire original Stage A tree, then apply the six-file combined overlay."""
from pathlib import Path
import argparse,hashlib,json
H=Path(__file__).resolve().parent
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
def apply(root):
 root=Path(root).resolve(); m=json.loads((H/'manifest.json').read_text())
 baseline,candidate=m['baseline'],m['candidate']; changes=m['changed']
 assert len(baseline)==110 and len(candidate)==111 and len(changes)==6
 for rel in candidate:
  t=root/rel
  assert t.resolve().is_relative_to(root) and not t.is_symlink(),rel
 found={rel:digest(root/rel) for rel in candidate}
 if found==candidate:
  print('Already applied: full 111-file combined source matches');return
 assert found=={rel:baseline.get(rel) for rel in candidate}, 'not exact clean Stage A source'
 for rel in changes:
  assert digest(H/rel)==candidate[rel], 'payload mismatch '+rel
 for rel in changes:
  (root/rel).write_bytes((H/rel).read_bytes())
  print('APPLIED',rel,candidate[rel])
 assert {rel:digest(root/rel) for rel in candidate}==candidate
 print('PASS exact 111-file combined source, no mixed baseline')
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--shadnet',required=True,type=Path)
 apply(p.parse_args().shadnet)
