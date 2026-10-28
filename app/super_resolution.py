"""Offline FSRCNN inference and conservative, registered near-duplicate assistance.

This module runs in a child process. No arbitrary same-person face synthesis.
"""
import hashlib
import importlib.util
import json
from pathlib import Path

MODEL_HASHES={2:'366b33f0084c7b3f2bf6724f0a2c77bca94fcec9d7b6d72389d330073b380d5c',4:'5c68d18db561aed8ead4ffedf1b897ea615baaf60ebf6c35f8e641f8fa4a21bf'}
VERSION='fsrcnn-tile128-halo16-face-reference-v2'
MAX_OUTPUT_PIXELS=16_000_000
MAX_INPUT_PIXELS=16_000_000


def available():
    return importlib.util.find_spec('cv2') is not None and importlib.util.find_spec('numpy') is not None


def model_path(scale):
    if type(scale) is not int or scale not in MODEL_HASHES:raise ValueError('지원 배율은 2배와 4배입니다.')
    path=Path(__file__).parent/'models'/'fsrcnn'/f'FSRCNN_x{scale}.pb'
    if hashlib.sha256(path.read_bytes()).hexdigest()!=MODEL_HASHES[scale]:raise ValueError('업스케일 모델 해시가 다릅니다.')
    return path


def read_rgb(path,sha):
    from PIL import Image,ImageOps
    import numpy as np
    digest=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):digest.update(chunk)
    if digest.hexdigest()!=sha:raise ValueError('입력 파일이 등록한 해시와 다릅니다.')
    with Image.open(path) as image:
        if image.width*image.height>MAX_INPUT_PIXELS:raise ValueError('입력 한도 1,600만 픽셀을 초과합니다.')
        if image.mode in ('RGBA','LA','P') and (image.mode!='P' or 'transparency' in image.info):
            if image.convert('RGBA').getchannel('A').getextrema()!=(255,255):raise ValueError('투명 이미지의 복원은 아직 지원하지 않습니다.')
        return np.array(ImageOps.exif_transpose(image).convert('RGB'))


def diagnostics():
    if not available():return dict(cpu_ready=False,cuda_ready=False,reason='선택 OpenCV/NumPy 의존성 미설치')
    import cv2
    cv2.setNumThreads(1)
    if not hasattr(cv2,'dnn_superres'):return dict(cpu_ready=False,cuda_ready=False,reason='opencv-contrib-python 설치가 필요합니다.',vram_measured=False)
    try:
        count=int(cv2.cuda.getCudaEnabledDeviceCount());targets=list(cv2.dnn.getAvailableTargets(cv2.dnn.DNN_BACKEND_CUDA))
    except (AttributeError,cv2.error):count=0;targets=[]
    ready=count>0 and cv2.dnn.DNN_TARGET_CUDA in targets
    return dict(cpu_ready=hasattr(cv2,'dnn_superres'),opencv=cv2.__version__,cuda_devices=count,cuda_targets=targets,cuda_ready=ready,reason='CUDA 장치와 OpenCV DNN 실행 대상 확인됨 · 실제 모델 검증 필요' if ready else 'CUDA DNN 실행 환경 없음 · CPU 사용',vram_measured=False)


def upscale(rgb,scale,progress=lambda *_:None,tile=128,backend='cpu'):
    import cv2
    import numpy as np
    path=model_path(scale)
    if backend not in ('cpu','cuda'):raise ValueError('지원하지 않는 실행 장치입니다.')
    if backend=='cuda' and not diagnostics()['cuda_ready']:raise ValueError('CUDA DNN 진단을 통과하지 못했습니다.')
    h,w=rgb.shape[:2]
    if h*w*scale*scale>MAX_OUTPUT_PIXELS:raise ValueError('결과 한도 1,600만 픽셀을 초과합니다. 배율을 낮추세요.')
    cv2.setNumThreads(1);cv2.ocl.setUseOpenCL(False)
    sr=cv2.dnn_superres.DnnSuperResImpl_create();sr.readModel(str(path));sr.setModel('fsrcnn',scale)
    sr.setPreferableBackend(cv2.dnn.DNN_BACKEND_CUDA if backend=='cuda' else cv2.dnn.DNN_BACKEND_OPENCV);sr.setPreferableTarget(cv2.dnn.DNN_TARGET_CUDA if backend=='cuda' else cv2.dnn.DNN_TARGET_CPU)
    result=np.empty((h*scale,w*scale,3),dtype=np.uint8)
    total=((h+tile-1)//tile)*((w+tile-1)//tile);done=0;halo=16
    for y in range(0,h,tile):
        for x in range(0,w,tile):
            y2,x2=min(y+tile,h),min(x+tile,w)
            top,left=max(0,y-halo),max(0,x-halo)
            piece=cv2.cvtColor(rgb[top:min(y2+halo,h),left:min(x2+halo,w)],cv2.COLOR_RGB2BGR)
            # Tiny inputs are padded because FSRCNN convolutions need context.
            ph,pw=max(0,8-piece.shape[0]),max(0,8-piece.shape[1])
            if ph or pw:piece=cv2.copyMakeBorder(piece,0,ph,0,pw,cv2.BORDER_REPLICATE)
            output=cv2.cvtColor(sr.upsample(piece),cv2.COLOR_BGR2RGB)
            oy,ox=(y-top)*scale,(x-left)*scale
            result[y*scale:y2*scale,x*scale:x2*scale]=output[oy:oy+(y2-y)*scale,ox:ox+(x2-x)*scale]
            done+=1;progress(done,total)
    return result


def worker(request):
    import time
    import cv2
    import numpy as np
    from PIL import Image
    start=time.monotonic();cv2.setNumThreads(1);cv2.ocl.setUseOpenCL(False)
    original=read_rgb(request['input']['path'],request['input']['sha256'])
    face_engine=None;face_landmarks=None;reference=None
    if request['mode']=='reference':
        from .face_reference import FaceEngine,register
        face_engine=FaceEngine()
        reference=read_rgb(request['reference']['path'],request['reference']['sha256'])
        face_landmarks=(face_engine.detect(original),face_engine.detect(reference))
        register(*face_landmarks)  # Reject incompatible faces before costly neural inference.
    backend=request.get('backend','cpu');fallback_reason=None;result=None
    try:
        result=upscale(original,request['scale'],lambda done,total:print(json.dumps(dict(progress=done/total,stage='neural')),flush=True),backend=backend)
    except cv2.error as error:
        if backend!='cuda':raise
        fallback_reason=str(error)[:1500]
    if result is None:
        import gc
        gc.collect();backend='cpu'
        result=upscale(original,request['scale'],lambda done,total:print(json.dumps(dict(progress=done/total,stage='cpu-fallback')),flush=True),backend='cpu')
    evidence={}
    if request['mode']=='reference':
        try:
            from .face_reference import face_assist
            result,evidence=face_assist(original,result,reference,request['scale'],face_engine,request['strength'],face_landmarks)
        finally:face_engine.close()
    reduced=cv2.resize(result,(original.shape[1],original.shape[0]),interpolation=cv2.INTER_AREA)
    mae=float(np.mean(np.abs(reduced.astype(float)-original.astype(float))))
    if not np.isfinite(mae) or mae>18:raise ValueError('축소 결과와 원본 차이가 커 자동 채택을 보류했습니다.')
    Image.fromarray(result).save(request['output'],format='PNG')
    try:
        import resource,sys
        peak_rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024*1024 if sys.platform=='darwin' else 1024)
    except ImportError:peak_rss=None
    return dict(peak_rss_mib=peak_rss,width=result.shape[1],height=result.shape[0],roundtrip_mae=mae,reference=evidence,elapsed_seconds=round(time.monotonic()-start,3),device='CUDA' if backend=='cuda' else 'CPU',gpu_vram_mib=None,cuda_fallback_reason=fallback_reason,generated_detail=True,identity_preservation_proven=False)


if __name__=='__main__':
    import sys
    try:
        import os
        os.nice(10)
    except (AttributeError,OSError):pass
    try:
        request=json.loads(Path(sys.argv[1]).read_text('utf-8'));result=worker(request)
        print(json.dumps(dict(result=result)),flush=True)
    except Exception as error:
        print(json.dumps(dict(error=str(error))),flush=True);sys.exit(1)
