"""Render approval-only Creation video with exact audio timing and short xfade transitions."""
from __future__ import annotations
import json
import subprocess
from pathlib import Path
from src.providers.video.local_ffmpeg_provider import LocalFFmpegVideoProvider
from src.pipeline.artifact_manifest import build_manifest, manifest_matches, write_manifest_atomic
from scripts.generate_creation_reference_frames import EPISODE, build_timeline

OVERLAP=0.25
FPS=30


def probe(path: Path) -> float:
    p=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',str(path)],capture_output=True,text=True,check=True)
    return float(p.stdout.strip())


def main() -> int:
    images=EPISODE/'images'; clips=EPISODE/'animation'; renders=EPISODE/'renders'; clips.mkdir(parents=True,exist_ok=True); renders.mkdir(parents=True,exist_ok=True)
    audio=EPISODE/'audio/narration_approved.mp3'; scenes=build_timeline()
    motions=['slow_push_in','pan_right','slow_pull_out','pan_left','gentle_float']
    provider=LocalFFmpegVideoProvider(output_w=1920,output_h=1080,fps=FPS,preset='veryfast',crf=20)
    final=renders/'final_approval.mp4'; manifest_path=renders/'render_manifest.json'
    input_paths=[images/f"{scene['scene_id']}.png" for scene in scenes]+[audio]
    render_config={'fps':FPS,'overlap':OVERLAP,'motions':motions,'width':1920,'height':1080,'preset':'veryfast','crf':20}
    if final.is_file() and manifest_matches(manifest_path,input_paths,scenes,render_config):
        print(json.dumps({'cache':'hit','final_video':str(final)},ensure_ascii=False),flush=True)
        return 0
    clip_paths=[]
    for i,scene in enumerate(scenes):
        sid=scene['scene_id']; out=clips/f'{sid}.mp4'; duration=float(scene['duration'])+(OVERLAP if i<len(scenes)-1 else 0.0)
        vf=provider._build_zoompan_filter(motions[i%len(motions)],duration)
        cmd=['ffmpeg','-y','-v','error','-loop','1','-i',str(images/f'{sid}.png'),'-t',f'{duration:.3f}','-vf',vf,'-an','-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p',str(out)]
        subprocess.run(cmd,check=True)
        clip_paths.append(out); print(f'{sid}: {duration:.3f}s',flush=True)
    filters=[]; cumulative=float(scenes[0]['duration']); prev='[0:v]'
    for i in range(1,len(clip_paths)):
        label=f'[v{i}]'; filters.append(f'{prev}[{i}:v]xfade=transition=fade:duration={OVERLAP}:offset={cumulative:.3f}{label}')
        prev=label; cumulative+=float(scenes[i]['duration'])
    script=renders/'xfade_filters.txt'; script.write_text(';\n'.join(filters),encoding='utf-8')
    silent=renders/'creation_silent.mp4'
    cmd=['ffmpeg','-y','-v','error']
    for p in clip_paths: cmd+=['-i',str(p)]
    cmd+=['-filter_complex',script.read_text(encoding='utf-8'),'-map',prev,'-an','-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p',str(silent)]
    proc=subprocess.run(cmd,capture_output=True,text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"xfade assembly failed ({proc.returncode}): {proc.stderr[-4000:]}")
    subprocess.run(['ffmpeg','-y','-v','error','-i',str(silent),'-i',str(audio),'-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','192k','-shortest',str(final)],check=True)
    audit={'scene_count':len(scenes),'image_count':len(list(images.glob('SC[0-9][0-9][0-9].png'))),'clip_count':len(clip_paths),'timeline_duration':round(cumulative,3),'audio_duration':round(probe(audio),3),'video_duration':round(probe(final),3),'transition_seconds':OVERLAP,'published':False,'final_video':str(final)}
    audit['sync_delta']=round(abs(audit['audio_duration']-audit['video_duration']),3); audit['approved_for_delivery']=audit['scene_count']==audit['image_count']==audit['clip_count'] and audit['sync_delta']<=0.5
    (EPISODE/'qa/render_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(audit,ensure_ascii=False),flush=True)
    write_manifest_atomic(manifest_path,build_manifest(input_paths,scenes,render_config))
    return 0 if audit['approved_for_delivery'] else 2

if __name__=='__main__': raise SystemExit(main())
