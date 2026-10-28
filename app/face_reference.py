"""Optional offline face-landmark registration, conservative face-only assistance.

No identity recognition, expression synthesis or arbitrary face generation.
"""
import hashlib
import importlib.util
from pathlib import Path

VERSION='face-mesh468-interior-luma-v1'
MODEL_HASHES={'face_detection/face_detection_short_range.tflite':'bbff11cebd1eb27a1e004cae0b0e63ec8c551cbf34a4451148b4908b8db3eca8','face_landmark/face_landmark.tflite':'1055cb9d4a9ca8b8c688902a3a5194311138ba256bcc94e336d8373a5f30c814'}
OVAL=[10,338,297,332,284,251,389,356,454,323,361,288,397,365,379,378,400,377,152,148,176,149,150,136,172,58,132,93,234,127,162,21,54,103,67,109]
STABLE=[33,133,362,263,1,4,168,6,197,195,5,98,327,205,425,50,280,61,291]


def available():return importlib.util.find_spec('mediapipe') is not None


class FaceEngine:
    def __init__(self):
        import mediapipe as mp
        from mediapipe.framework import calculator_pb2
        from mediapipe.python.solution_base import SolutionBase
        if mp.__version__!='0.10.21':raise ValueError('참조 검증에는 mediapipe==0.10.21이 필요합니다.')
        root=Path(mp.__file__).resolve().parent
        for relative,digest in MODEL_HASHES.items():
            if hashlib.sha256((root/'modules'/relative).read_bytes()).hexdigest()!=digest:raise ValueError('얼굴 랜드마크 모델 해시가 다릅니다.')
        config=calculator_pb2.CalculatorGraphConfig()
        config.ParseFromString((root/'modules/face_landmark/face_landmark_front_cpu.binarypb').read_bytes());config.num_threads=1
        self.graph=SolutionBase(graph_config=config,side_inputs=dict(num_faces=2,with_attention=False,use_prev_landmarks=False),calculator_params={'facedetectionshortrangecpu__facedetectionshortrange__facedetection__TensorsToDetectionsCalculator.min_score_thresh':.7,'facelandmarkcpu__ThresholdingCalculator.threshold':.8},outputs=['multi_face_landmarks'])
    def detect(self,rgb):
        import cv2
        import numpy as np
        h,w=rgb.shape[:2];ratio=min(1,768/max(h,w))
        small=cv2.resize(rgb,(round(w*ratio),round(h*ratio)),interpolation=cv2.INTER_AREA) if ratio<1 else rgb
        result=self.graph.process({'image':small});faces=result.multi_face_landmarks or []
        if len(faces)!=1:raise ValueError('한 얼굴이 확실히 검출된 사진만 참조 복원할 수 있습니다.')
        points=np.array([[p.x*w,p.y*h,p.z*w] for p in faces[0].landmark],dtype=np.float32)
        if len(points)!=468 or not np.isfinite(points).all():raise ValueError('얼굴 랜드마크가 불완전합니다.')
        oval=points[OVAL,:2]
        if (oval[:,0]<1).any() or (oval[:,1]<1).any() or (oval[:,0]>w-2).any() or (oval[:,1]>h-2).any():raise ValueError('얼굴 윤곽이 잘린 사진의 참조 채택을 보류합니다.')
        if min(np.ptp(oval[:,0]),np.ptp(oval[:,1]))<40:raise ValueError('원본 얼굴의 실제 픽셀 정보가 부족합니다. 얼굴 가로·세로 40px 이상이 필요합니다.')
        return points
    def close(self):self.graph.close()
    def __enter__(self):return self
    def __exit__(self,*_):self.close()


def descriptors(points):
    import numpy as np
    p=points[:,:2];left=(p[33]+p[133])/2;right=(p[263]+p[362])/2;distance=float(np.linalg.norm(right-left))
    if distance<12:raise ValueError('눈 사이의 원본 픽셀 정보가 부족합니다.')
    center=(left+right)/2;axis=(right-left)/distance;vertical=np.array([-axis[1],axis[0]])
    mouth=(p[61]+p[291])/2
    return dict(eye_distance=distance,yaw=float(np.dot(p[1]-center,axis)/distance),pitch=float(np.dot(p[1]-center,vertical)/distance),mouth_open=float(np.linalg.norm(p[13]-p[14])/distance),mouth_width=float(np.linalg.norm(p[61]-p[291])/distance),left_eye=float(np.linalg.norm(p[159]-p[145])/distance),right_eye=float(np.linalg.norm(p[386]-p[374])/distance),mouth_offset=float(np.dot(mouth-center,axis)/distance))


def register(original_points,reference_points):
    import cv2
    import numpy as np
    original=descriptors(original_points);reference=descriptors(reference_points)
    if reference['eye_distance']<original['eye_distance']*1.35:raise ValueError('참조 얼굴의 실제 해상도 증가가 부족합니다.')
    for name,tolerance in [('yaw',.12),('pitch',.14),('mouth_open',.06),('mouth_width',.13),('left_eye',.055),('right_eye',.055),('mouth_offset',.12)]:
        if abs(original[name]-reference[name])>tolerance:raise ValueError('참조 얼굴의 방향·표정·눈/입 상태가 달라 채택을 보류합니다: '+name)
    src=reference_points[STABLE,:2];dst=original_points[STABLE,:2]
    matrix,inliers=cv2.estimateAffinePartial2D(src,dst,method=cv2.RANSAC,ransacReprojThreshold=max(1.,original['eye_distance']*.055),maxIters=1000)
    if matrix is None or inliers is None or float(inliers.mean())<.85:raise ValueError('얼굴 정렬 근거가 부족합니다.')
    projected=reference_points[:,:2]@matrix[:,:2].T+matrix[:,2]
    rms=float(np.sqrt(np.mean(np.sum((projected[STABLE]-dst)**2,axis=1)))/original['eye_distance'])
    rotation=float(np.degrees(np.arctan2(matrix[1,0],matrix[0,0])))
    if rms>.065 or abs(rotation)>8:raise ValueError('얼굴 구조 또는 각도 차이가 커 채택을 보류합니다.')
    return matrix,dict(original=original,reference=reference,landmark_rms=rms,inlier_ratio=float(inliers.mean()),rotation_degrees=rotation)


def face_assist(original,neural,reference,scale,engine,strength=.15,landmarks=None):
    import cv2
    import numpy as np
    if not 0<strength<=.2:raise ValueError('참조 강도 범위가 잘못됐습니다.')
    op,rp=landmarks if landmarks is not None else (engine.detect(original),engine.detect(reference))
    matrix,evidence=register(op,rp)
    h,w=original.shape[:2];target=(w*scale,h*scale)
    target_matrix=matrix*scale
    warped=cv2.warpAffine(reference,target_matrix,target,flags=cv2.INTER_CUBIC)
    # Inset the detected face oval, excluding boundary hair, neck and scene.
    oval=op[OVAL,:2]*scale;center=oval.mean(axis=0);oval=center+(oval-center)*.82
    mask=np.zeros((target[1],target[0]),np.uint8);cv2.fillConvexPoly(mask,np.int32(cv2.convexHull(oval)),255)
    hard=mask>0
    if not hard.any():raise ValueError('유효한 얼굴 내부 영역이 없습니다.')
    alpha=cv2.GaussianBlur(mask.astype(np.float32)/255,(0,0),max(1,scale*1.5))*hard
    ref_y=cv2.cvtColor(warped,cv2.COLOR_RGB2YCrCb)[:,:,0].astype(np.float32)
    base=cv2.cvtColor(neural,cv2.COLOR_RGB2YCrCb);base_y=base[:,:,0].astype(np.float32)
    original_up=cv2.resize(original,target,interpolation=cv2.INTER_CUBIC)
    original_y=cv2.cvtColor(original_up,cv2.COLOR_RGB2GRAY).astype(np.float32)
    a=original_y[hard];b=ref_y[hard];corr=float(np.corrcoef(a,b)[0,1]);mae=float(np.mean(np.abs(a-b)))
    if not np.isfinite(corr) or corr<.9 or mae>24:raise ValueError('얼굴 내용·조명 차이가 커 참조 채택을 보류합니다.')
    base_energy=float(np.mean(np.abs(cv2.Laplacian(original_y,cv2.CV_32F)[hard])))
    ref_energy=float(np.mean(np.abs(cv2.Laplacian(ref_y,cv2.CV_32F)[hard])))
    if ref_energy/max(.001,base_energy)<1.05:raise ValueError('참조 얼굴의 추가 선명도가 부족합니다.')
    detail=ref_y-cv2.GaussianBlur(ref_y,(0,0),1.2);prior=base_y-cv2.GaussianBlur(base_y,(0,0),1.2)
    delta=np.clip((detail-prior)*strength,-6,6)*alpha
    base[:,:,0]=np.clip(base_y+delta,0,255).astype(np.uint8)
    converted=cv2.cvtColor(base,cv2.COLOR_YCrCb2RGB)
    result=neural.copy();result[hard]=converted[hard]
    # Re-detect generated result to veto gross geometry/expression changes.
    result_points=engine.detect(result)/scale;after=descriptors(result_points);before=evidence['original']
    geometry=float(np.sqrt(np.mean(np.sum((result_points[STABLE,:2]-op[STABLE,:2])**2,axis=1)))/before['eye_distance'])
    if geometry>.08 or any(abs(before[k]-after[k])>.06 for k in ('mouth_open','left_eye','right_eye')):raise ValueError('결과 얼굴의 구조·표정 변화 가능성으로 채택을 보류합니다.')
    evidence.update(scope='face-interior-only',face_mask_pixels=int(hard.sum()),face_mask_fraction=float(hard.mean()),outside_mask_unchanged=bool(np.array_equal(neural[~hard],result[~hard])),correlation=corr,gray_mae=mae,detail_energy_ratio=ref_energy/max(.001,base_energy),strength=strength,max_luma_delta=6,result_landmark_rms=geometry,identity_verified=False,identity_preservation_proven=False)
    return result,evidence
