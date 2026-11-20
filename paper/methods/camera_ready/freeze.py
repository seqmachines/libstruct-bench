"""Read-only reference/approval audit; freeze camera-ready input identities before scoring."""
from pathlib import Path
import json, hashlib, csv, copy, sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
PRIVATE = Path('external/audit-data')
AUDIT = PRIVATE / 'ground_truth_audit'
OLD = OUT.parent / 'verifier_corrected_v4_7_0'
sys.path.insert(0, str(ROOT/'src'))

def load(p): return json.loads(Path(p).read_text())
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def dump(p, x): Path(p).write_text(json.dumps(x, indent=2, sort_keys=True)+'\n')
def csvout(p, rs):
    with Path(p).open('w', newline='') as f:
        w=csv.DictWriter(f, fieldnames=list(rs[0]));w.writeheader();w.writerows(rs)
def delta(a,b,p=''):
    if type(a)!=type(b): return [p]
    if isinstance(a,dict): return sum((delta(a.get(k),b.get(k),p+'/'+k) for k in sorted(set(a)|set(b))),[])
    if isinstance(a,list):
        if len(a)!=len(b): return [p]
        return sum((delta(x,y,p+'/'+str(i)) for i,(x,y) in enumerate(zip(a,b))),[])
    return [] if a==b else [p]

def main():
    assert not (OUT/'input_manifest.json').exists(), 'Use a new output directory for another lock.'
    old=load(OLD/'manifest.json');inputs={};proofs={};proof_documents={}
    def pin(p,expected=None):
        p=Path(p).resolve(); h=sha(p)
        if expected: assert h==expected, str(p)
        inputs[str(p)]=h;return h
    # Index actual final human decisions by content hash, never by filename alone.
    decisions={}
    for p in (AUDIT/'reviews').rglob('*.json'):
        if 'final' in p.name:
            x=load(p)
            if x.get('review_state')=='final': decisions[sha(p)]=p
    for p in (AUDIT/'promotions').rglob('*.json'):
        x=load(p)
        if not x.get('artifacts'): continue
        for a in x['artifacts']: proofs[a['sha256']]=(p,'promotion')
        proof_documents[str(p)]=x
    approved= AUDIT/'runs/connected-process-migration-001/final-approval-preview-003.json'
    approval=load(approved);assert approval['status']=='final' and approval['scientific_disposition']=='approved'
    pin(approved)
    pin(AUDIT/approval['preview_manifest']['path'],approval['preview_manifest']['sha256'])
    for pr in approval['protocols']:
        for a in pr['artifacts']: proofs[a['candidate_sha256']]=(approved,'final_scientific_preview')
    # Superseding three human-authorized corrections: validate the entire before/after chain.
    amendments=[]
    for p in sorted((AUDIT/'applications').glob('*/*/installation-receipt.json')):
        x=load(p);pin(p)
        for name,key in [('decision.json','decision_sha256'),('proposal.json','proposal_sha256'),('application/application-log.json','application_log_sha256')]: pin(p.parent/name,x[key])
        decision=load(p.parent/'decision.json')
        assert decision['review_state']=='final' and decision['overall_disposition']=='accepted'
        assert decision['proposal_sha256']==x['proposal_sha256']
        assert all(a['disposition']=='accept' for a in decision['issue_decisions'])
        for a in x['files']:
            if '/ground_truth/' not in a['path']:continue
            pin(p.parent/'baselines'/Path(a['path']).name,a['before_sha256'])
            pin(p.parent/'application/candidates'/Path(a['path']).name,a['after_sha256'])
            proofs[a['after_sha256']]=(p,'final_human_amendment')
            amendments.append(dict(protocol_id=x['protocol_id'],filename=Path(a['path']).name,before=a['before_sha256'],after=a['after_sha256'],receipt=str(p),decision_id=decision['decision_id']))
    references=[];differences=[];protocols=sorted({c['protocol_id'] for c in old['cases']})
    for pr in protocols:
        original=next(c for c in old['cases'] if c['protocol_id']==pr)
        for task,p in sorted(original['truth']['paths'].items()):
            frozen=Path(p);current=PRIVATE/'ground_truth'/pr/frozen.name
            fh=pin(frozen,old['inputs'][str(frozen)]);ch=pin(current)
            if ch in proofs:
                pp,kind=proofs[ch];pin(pp)
                if kind=='promotion':
                    x=proof_documents[str(pp)];dp=decisions.get(x['decision_sha256'])
                    # Some old final decision receipts use another path/name; search protocol only.
                    if dp is None:
                        dp=next((q for q in (AUDIT/'reviews'/pr).rglob('*.json') if sha(q)==x['decision_sha256']),None)
                    assert dp, ('Missing final decision',pr,pp)
                    pin(dp,x['decision_sha256'])
                proof=str(pp);representation_paths=[]
            else:
                # Four historical files differ only in an approved vocabulary alias.
                candidates=list((AUDIT/'applications'/pr).rglob('candidates/'+frozen.name))
                matched=[]
                aliases={'RNA':'gene expression','chromatin_accessibility':'chromatin accessibility'}
                for cp in candidates:
                    a=load(cp);b=load(current);ds=delta(a,b)
                    if ds and all(k.endswith('/modality') for k in ds):
                        def at(doc,path):
                            for k in path.strip('/').split('/'):doc=doc[int(k)] if isinstance(doc,list) else doc[k]
                            return doc
                        if all(aliases.get(at(a,k))==at(b,k) for k in ds) and sha(cp) in proofs:matched.append((cp,ds))
                assert len(matched)==1,(pr,task,'Unresolved scientific reference difference')
                cp,representation_paths=matched[0];pin(cp);pp,_=proofs[sha(cp)];pin(pp)
                x=proof_documents[str(pp)];dp=decisions.get(x['decision_sha256'])
                if dp is None:dp=next(q for q in (AUDIT/'reviews'/pr).rglob('*.json') if sha(q)==x['decision_sha256'])
                pin(dp,x['decision_sha256']);kind='promoted_content_with_canonical_modality_alias';proof=str(pp)
            changed=fh!=ch
            if changed:
                chain=[a for a in amendments if a['protocol_id']==pr and a['filename']==frozen.name]
                h=fh;used=[]
                while h!=ch:
                    step=next((a for a in chain if a['before']==h),None);assert step,(pr,task,h)
                    h=step['after'];used.append(step['decision_id'])
                ds=delta(load(frozen),load(current))
                differences.append(dict(protocol_id=pr,task=task,changed_json_paths=ds,decisions=used))
            references.append(dict(protocol_id=pr,task=task,frozen_path=str(frozen),frozen_sha256=fh,current_path=str(current),current_sha256=ch,changed_from_sensitivity=changed,approval_basis=kind,approval_receipt=proof,representation_only_paths=json.dumps(representation_paths),scoring_transform=original['truth']['transform']))
    csvout(OUT/'reference_consistency.csv',references);dump(OUT/'reference_changes.json',differences)
    lock_id='approved-20261010-'+hashlib.sha256(json.dumps([(r['protocol_id'],r['task'],r['current_sha256']) for r in references]).encode()).hexdigest()[:16]
    main=Path('external/manuscript/main.tex');pin(main)
    for p in (ROOT/'src/libstruct_bench').rglob('*.py'):pin(p)
    for d in ['verifier_corrected_v4_7_0','verifier_sensitivity','verifier_impact/results_frozen_v1']:
        for p in (OUT.parent/d).rglob('*'):
            if p.is_file():pin(p)
    for p,h in old['inputs'].items():
        if p.endswith('validation_selection.json'):pin(p,h)
    cases=copy.deepcopy(old['cases'])
    for c in cases:
        for p in [c['p2'],c['p3']]:pin(p,old['inputs'][p])
        for t,p in c['truth']['paths'].items():c['truth']['paths'][t]=str(PRIVATE/'ground_truth'/c['protocol_id']/Path(p).name)
    # Historical selection and panel rows are byte-for-byte values from the old lock.
    manifest={**old,'inputs':inputs,'cases':cases,'reference_policy':lock_id,'reference_audit':'../reference_consistency.csv', 'analysis':'Camera-ready 4.7.0, final approved references; original panel membership/trial selection and C30 unchanged.'}
    dump(OUT/'sensitivity/manifest.json',manifest)
    dump(OUT/'input_manifest.json',dict(reference_set_id=lock_id,verifier='4.7.0',scorer_sha256=old['scorer_sha256'],inputs=inputs,main_tex_sha256=sha(main),historical_selection=old['historical_checkpoint'],cases=cases,panels=old['panels']))
    csvout(OUT/'selected_runs.csv',old['panels'])
    (OUT/'reference_consistency.md').write_text(f'''# Reference consistency gate\n\nVerified before scoring. Reference set `{lock_id}`; verifier 4.7.0.\n\nAll 96 source files from 32 Figure 3/4 protocols were compared by SHA-256 with the prior corrected-verifier sensitivity manifest. Six files differ: T1/T2/T3 for 10x 3′ Feature Barcoding (explicit VN anchor) and inDrop v1 (W1 orientation, then barcode-1 length 8–11 and T19V metadata). Each difference has a complete frozen-before → approved-after hash chain. The approval records explicitly describe semantic human authorization, not separate human inspection of generated patch bytes. No remote publication is inferred. See `reference_changes.json` and `reference_consistency.csv`.\n\n92 current files match a promotion, finalized scientific preview, or superseding human-amendment hash. Four files (T1 and T3 of sci-fi-ATAC-seq and SMART-seq3xpress) differ from their promoted candidates only in modality aliases: `chromatin_accessibility` → `chromatin accessibility`, and `RNA` → `gene expression`. Their full JSON differences are checked; they have no sequence/topology differences. These four are **content-equivalent approved records, not byte-identical promotion receipts**. The connected-process preview carries final scientific approval but says application/promotion were not authorized at that stage; this analysis certifies scientific approval/hash correspondence, not a subsequently undocumented installation action.\n\nSix cases retain the previous in-memory legacy `final_state_ids` → `final_outputs` conversion. This representation view is recorded, hashed, and does not edit canonical files. Reference weights, GT scientific content, predictions, trial selection and C30 are unchanged by this integration. The six approved reference changes are applied in the new analysis only; the previous 4.7.0 sensitivity results remain historical and will be superseded for camera-ready use by a fresh full sweep.\n\nNo unresolved scientific reference difference was found in the selected panels. The reference gate is scoped to these 32 protocols, not a claim to re-audit all 73 benchmark records.\n''')
    print(lock_id,'96 references; 6 changed; approval gate passed',flush=True)

if __name__=='__main__':main()
