"""Independent final visual audit for all Creation episode frames."""
from __future__ import annotations
import json
import time
import urllib.error
from pathlib import Path
from scripts.generate_creation_reference_frames import EPISODE, ROOT, build_timeline, gemini_qa
from src.pipeline.reference_generation import select_reference


def main() -> int:
    images_dir=EPISODE/'images'; qa_dir=EPISODE/'qa/verified'; qa_dir.mkdir(parents=True,exist_ok=True)
    refs={
        'adam':str(ROOT/'canonical/creation/adam/adam_face_canonical_v1.png'),
        'eve':str(ROOT/'canonical/creation/eve/eve_face_canonical.png'),
        'adam_eve':str(ROOT/'canonical/creation/adam_eve_reference_16x9.png'),
    }
    scenes=build_timeline(); reports=[]
    for scene in scenes:
        sid=scene['scene_id']; image=images_dir/f'{sid}.png'
        cached_path=qa_dir/f'{sid}_visual.json'
        if cached_path.exists():
            cached=json.loads(cached_path.read_text(encoding='utf-8'))
            reports.append(cached); print(f"{sid}: cached score={cached.get('score')} approved={cached.get('approved')}",flush=True); continue
        chars=[str(x).lower() for x in scene.get('characters',[])]
        ref_value=select_reference(scene['narration'],chars,refs)
        ref=Path(ref_value) if ref_value else None
        qa=None
        for attempt in range(1,5):
            try:
                qa=gemini_qa(image,scene,ref); break
            except urllib.error.HTTPError as exc:
                if exc.code not in {429,500,502,503,504} or attempt==4: raise
                time.sleep(attempt*20)
        if qa is None: raise RuntimeError(f'No QA response for {sid}')
        qa.update({'scene_id':sid,'narration':scene['narration'],'start':scene['start'],'end':scene['end'],'duration':scene['duration'],'reference':str(ref) if ref else None,'image':str(image)})
        (qa_dir/f'{sid}_visual.json').write_text(json.dumps(qa,ensure_ascii=False,indent=2),encoding='utf-8')
        reports.append(qa); print(f"{sid}: score={qa.get('score')} approved={qa.get('approved')}",flush=True)
    approved=[x for x in reports if x.get('approved') and float(x.get('score',0))>=0.85]
    failures=[{'scene_id':x['scene_id'],'score':x.get('score'),'problems':x.get('problems',[])} for x in reports if x not in approved]
    audit={'scene_count':len(scenes),'image_count':len(list(images_dir.glob('SC[0-9][0-9][0-9].png'))),'verified_qa_count':len(reports),'approved_count':len(approved),'all_approved':len(approved)==len(scenes),'failures':failures,'timeline_end':scenes[-1]['end']}
    (qa_dir/'final_visual_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(audit,ensure_ascii=False),flush=True)
    return 0 if audit['all_approved'] else 2

if __name__=='__main__': raise SystemExit(main())
