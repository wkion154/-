"""Optional offline MediaPipe CPU graphs. Candidates, never person identification."""
import hashlib
import importlib.util
from pathlib import Path

EXPECTED_MODELS={'pose_detection/pose_detection.tflite': '9ba9dd3d42efaaba86b4ff0122b06f29c4122e756b329d89dca1e297fd8f866c', 'pose_landmark/pose_landmark_full.tflite': 'e9a5c5cb17f736fafd4c2ec1da3b3d331d6edbe8a0d32395855aeb2cdfd64b9f', 'face_detection/face_detection_full_range_sparse.tflite': '2c3728e6da56f21e21a320433396fb06d40d9088f2247c05e5635a688d45dfe1', 'selfie_segmentation/selfie_segmentation.tflite': '9ee168ec7c8f2a16c56fe8e1cfbc514974cbbb7e434051b455635f1bd1462f5c'}
VISION_VERSION='mediapipe-0.10.21-static-cpu1-rules-v1'
COMPOSITIONS={'unknown':'미확정','face_extreme':'얼굴 초근접 후보','face_close':'얼굴 클로즈업 후보','face_shoulders':'얼굴·어깨 후보','upper_body':'상반신 후보','waist_up':'허리 위 후보','knees_up':'무릎 위 후보','full_body':'전신 후보','profile':'측면 · 수동 확인','back':'뒷모습 · 수동 확인'}
COUNTS={'unknown':'인원 미확정','single':'1인 후보','two':'2인 후보','group':'단체 후보'}


def available():return importlib.util.find_spec('mediapipe') is not None


def classify(faces,landmarks):
    count=len(faces);count_class='unknown' if not count else 'single' if count==1 else 'two' if count==2 else 'group'
    label='unknown';confidence=0.;reasons=[]
    if count>1:return dict(composition=label,confidence=0.,count_class=count_class,reasons=['검출 얼굴 수는 실제 전체 인원 확정이 아닙니다. 복수 인물 구도는 자동 확정하지 않습니다.'])
    def visible(index,margin=.02,minimum=.8):
        if not landmarks or len(landmarks)<=index:return False
        p=landmarks[index]
        return p['visibility']>=minimum and margin<=p['x']<=1-margin and margin<=p['y']<=1-margin
    if all(visible(i) for i in (0,11,12,23,24,25,26,27,28,29,30,31,32)):
        head=landmarks[0]['y'];hip=(landmarks[23]['y']+landmarks[24]['y'])/2
        knee=(landmarks[25]['y']+landmarks[26]['y'])/2;feet=min(landmarks[31]['y'],landmarks[32]['y'])
        if head<hip<knee<feet:
            label='full_body';confidence=.8;reasons.append('머리·어깨·골반·무릎·양쪽 발끝/뒤꿈치가 화면 안에서 높은 가시성으로 검출됨')
    if label=='unknown' and faces:
        face=faces[0];height=face['height']
        if face['score']>=.7 and face['width']>.08:
            if height>=.55:label='face_extreme';confidence=.75
            elif height>=.35:label='face_close';confidence=.7
            elif height>=.20 and all(visible(i,minimum=.7) for i in (11,12)):label='face_shoulders';confidence=.65
            if label!='unknown':reasons.append('검출 얼굴의 화면 높이 비중과 어깨 검출에 따른 구도 후보')
    if label=='unknown' and all(visible(i,minimum=.8) for i in (0,11,12,23,24)):
        hips=max(landmarks[23]['y'],landmarks[24]['y'])
        if .55<=hips<.80 and faces and faces[0]['height']>=.12:
            label='upper_body';confidence=.55;reasons.append('얼굴·어깨·골반의 양성 검출과 화면 위치에 따른 상반신 후보. 하체 포함 여부는 미확정')
        if .80<=hips<=.98:
            label='waist_up';confidence=.6;reasons.append('확인된 골반이 하단 경계 가까이에 있음. 다리 미검출만으로 판정하지 않음')
        if all(visible(i,minimum=.8) for i in (25,26)) and .82<=max(landmarks[25]['y'],landmarks[26]['y'])<=.98:
            label='knees_up';confidence=.6;reasons.append('확인된 무릎이 하단 경계 가까이에 있음')
    if label=='unknown':reasons.append('구도를 판정할 충분한 검출 근거가 없음')
    return dict(composition=label,confidence=confidence,count_class=count_class,reasons=reasons)


class VisionEngine:
    def __init__(self):
        import mediapipe as mp
        import cv2
        from mediapipe.framework import calculator_pb2
        from mediapipe.python.solution_base import SolutionBase
        from mediapipe.modules.face_detection import face_detection_pb2
        if mp.__version__!='0.10.21':raise RuntimeError('검증된 선택 분석 버전 mediapipe==0.10.21을 설치하세요.')
        cv2.setNumThreads(1)
        root=Path(mp.__file__).resolve().parent.parent
        self.models={}
        for relative in ('pose_detection/pose_detection.tflite','pose_landmark/pose_landmark_full.tflite','face_detection/face_detection_full_range_sparse.tflite','selfie_segmentation/selfie_segmentation.tflite'):
            path=root/'mediapipe/modules'/relative
            if not path.is_file():raise RuntimeError('번들 분석 모델이 없습니다. 선택 분석 패키지를 다시 설치하세요.')
            self.models[relative]=hashlib.sha256(path.read_bytes()).hexdigest()
            if self.models[relative]!=EXPECTED_MODELS[relative]:raise RuntimeError('검증된 번들 모델 체크섬과 다릅니다. 선택 분석 패키지를 다시 확인하세요.')
        def graph(relative,**kwargs):
            config=calculator_pb2.CalculatorGraphConfig()
            config.ParseFromString((root/'mediapipe/modules'/relative).read_bytes());config.num_threads=1
            return SolutionBase(graph_config=config,**kwargs)
        self.graphs=[]
        try:
            self.face=graph('face_detection/face_detection_full_range_cpu.binarypb',graph_options=face_detection_pb2.FaceDetectionOptions(min_score_thresh=.65),outputs=['detections']);self.graphs.append(self.face)
            self.pose=graph('pose_landmark/pose_landmark_cpu.binarypb',side_inputs=dict(model_complexity=1,smooth_landmarks=False,enable_segmentation=True,smooth_segmentation=False,use_prev_landmarks=False),calculator_params={'posedetectioncpu__TensorsToDetectionsCalculator.min_score_thresh':.65,'poselandmarkbyroicpu__tensorstoposelandmarksandsegmentation__ThresholdingCalculator.threshold':.75},outputs=['pose_landmarks','segmentation_mask']);self.graphs.append(self.pose)
            self.segment=graph('selfie_segmentation/selfie_segmentation_cpu.binarypb',side_inputs=dict(model_selection=0),outputs=['segmentation_mask']);self.graphs.append(self.segment)
        except Exception:
            self.close();raise
    def analyze(self,image,cancelled):
        import numpy as np
        from PIL import Image
        from .providers.http import Cancelled
        if cancelled.is_set():raise Cancelled()
        rgb=np.asarray(image.convert('RGB'))
        detected=self.face.process({'image':rgb});faces=[]
        for d in (detected.detections or [])[:20]:
            box=d.location_data.relative_bounding_box
            faces.append(dict(x=box.xmin,y=box.ymin,width=box.width,height=box.height,score=float(d.score[0])))
        if cancelled.is_set():raise Cancelled()
        pose=self.pose.process({'image':rgb})
        landmarks=[dict(x=float(p.x),y=float(p.y),visibility=float(p.visibility)) for p in pose.pose_landmarks.landmark] if pose.pose_landmarks else []
        result=classify(faces,landmarks);masks={};status='unavailable'
        if len(faces)>1:status='multiple_people_unsupported'
        else:
            if cancelled.is_set():raise Cancelled()
            mask=pose.segmentation_mask
            if mask is None:mask=self.segment.process({'image':rgb}).segmentation_mask
            if mask is not None and np.isfinite(mask).all() and float((mask>=.75).mean())>=.02 and (faces or landmarks):
                masks['person']=Image.fromarray(np.uint8(mask>=.75)*255)
                masks['background']=Image.fromarray(np.uint8(mask<=.25)*255);status='estimated_primary_person'
                if landmarks and all(landmarks[i]['visibility']>=.8 and 0<=landmarks[i]['x']<=1 and 0<=landmarks[i]['y']<=1 for i in (11,12,23,24)):
                    xs=[landmarks[i]['x'] for i in (11,12,23,24)];ys=[landmarks[i]['y'] for i in (11,12,23,24)]
                    left,right=max(0,min(xs)-.06),min(1,max(xs)+.06);top,bottom=min(ys),max(ys)
                    roi=np.zeros_like(mask,dtype=bool);h,w=roi.shape
                    roi[int(top*h):int(bottom*h),int(left*w):int(right*w)]=True
                    masks['torso']=Image.fromarray(np.uint8(roi&(mask>=.75))*255)
        return result|dict(faces=faces,landmarks=landmarks,region_status=status,models=self.models),masks
    def close(self):
        for graph in reversed(getattr(self,'graphs',[])):graph.close()
        self.graphs=[]
