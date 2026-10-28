"""Compact modeless filter/review workspace; analysis stays in the local queue."""
import json
from PySide6.QtCore import Qt,Signal
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QPushButton,QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox,QCheckBox,QTabWidget,QWidget,QScrollArea,QColorDialog,QDialogButtonBox)
from .workers import Tasks
from .analysis_filters import AnalysisFilter
from .vision_analysis import COMPOSITIONS,COUNTS,available

REGION_LABELS={'whole':'전체 이미지','person':'주 인물 영역 · 추정','background':'배경 영역 · 추정','torso':'상체 영역 · 의상 확정 아님'}


class AnalysisDialog(QDialog):
    applied=Signal(object)
    changed=Signal()
    def __init__(self,online,runner,filters,parent=None):
        super().__init__(parent)
        self.online,self.service,self.runner=online,online.analysis,runner
        self.settings=online.settings;self.tasks=Tasks(self,1);self.closed=False;self.saving=False
        self.identity=None;self.generation=0;self.last=None
        self.setWindowTitle('필터 · 이미지 분석');self.resize(620,790)
        outer=QVBoxLayout(self);title=QLabel('VISUAL DISCOVERY');title.setObjectName('eyebrow');outer.addWidget(title)
        self.tabs=QTabWidget();outer.addWidget(self.tabs,1)
        body=QWidget();form=QFormLayout(body)
        self.color_enabled=QCheckBox('색상 점유율 사용');form.addRow(self.color_enabled)
        colorrow=QWidget();row=QHBoxLayout(colorrow);row.setContentsMargins(0,0,0,0)
        self.color=QLineEdit('#e6a8c4');row.addWidget(self.color)
        choose=QPushButton('색상 선택');choose.clicked.connect(self.choose_color);row.addWidget(choose);form.addRow('HEX',colorrow)
        self.delta=QDoubleSpinBox();self.delta.setRange(0,100);self.delta.setDecimals(1);self.delta.setSingleStep(1);form.addRow('색차 허용 ΔE76',self.delta)
        self.minimum=QDoubleSpinBox();self.minimum.setRange(0,100);self.minimum.setSuffix(' %');form.addRow('최소 점유율',self.minimum)
        self.maximum=QDoubleSpinBox();self.maximum.setRange(0,100);self.maximum.setSuffix(' %');form.addRow('최대 점유율',self.maximum)
        self.region=QComboBox()
        for value,label in REGION_LABELS.items():self.region.addItem(label,value)
        form.addRow('색상 영역',self.region)
        self.composition=QComboBox();self.composition.addItem('전체 구도',None)
        for value,label in COMPOSITIONS.items():self.composition.addItem(label,value)
        form.addRow('구도',self.composition)
        self.count=QComboBox();self.count.addItem('전체 인원',None)
        for value,label in COUNTS.items():self.count.addItem(label,value)
        form.addRow('검출 인원 후보',self.count)
        self.mp=QDoubleSpinBox();self.mp.setRange(0,1000);self.mp.setDecimals(1);self.mp.setSuffix(' MP');form.addRow('최소 실측 해상도',self.mp)
        self.orientation=QComboBox()
        for name,value in [('모든 비율','all'),('세로','portrait'),('가로','landscape'),('정방형에 가까움','square')]:self.orientation.addItem(name,value)
        form.addRow('화면 비율',self.orientation)
        self.unknown=QCheckBox('미분석·미확정도 함께 표시 · 색상 일치 미검증');form.addRow(self.unknown)
        hint=QLabel('색상 점유율은 축소 표본·팔레트의 추정값입니다.\n영역은 검출이 충분할 때만 제공합니다. 구도·인원은 후보이며 수동 확인이 우선합니다.');hint.setWordWrap(True);hint.setObjectName('muted');form.addRow(hint)
        buttons=QWidget();bar=QHBoxLayout(buttons);bar.setContentsMargins(0,0,0,0)
        reset=QPushButton('필터 초기화');reset.clicked.connect(lambda:self.set_filters(AnalysisFilter()));bar.addWidget(reset)
        apply=QPushButton('갤러리에 적용');apply.setObjectName('primary');apply.clicked.connect(self.apply);bar.addWidget(apply);form.addRow(buttons)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(body);self.tabs.addTab(scroll,'필터')
        review=QWidget();layout=QVBoxLayout(review)
        self.name=QLabel('사진을 선택하세요');self.name.setTextFormat(Qt.TextFormat.PlainText);self.name.setWordWrap(True);layout.addWidget(self.name)
        self.summary=QLabel('원본/검증된 캐시를 확보하고 분석 권한을 확인한 사진만 처리합니다.');self.summary.setTextFormat(Qt.TextFormat.PlainText);self.summary.setWordWrap(True);layout.addWidget(self.summary)
        self.swatches=QHBoxLayout();layout.addLayout(self.swatches)
        self.analyze=QPushButton('선택 사진 분석');self.analyze.clicked.connect(self.analyze_selected);layout.addWidget(self.analyze)
        self.rights=QPushButton('선택 로컬 사진 분석 권한');self.rights.clicked.connect(self.review_rights);layout.addWidget(self.rights)
        self.correct=QPushButton('구도 · 인원 수동 확인');self.correct.clicked.connect(self.review);layout.addWidget(self.correct)
        clear=QPushButton('수동 확인 해제');clear.clicked.connect(self.clear_override);layout.addWidget(clear)
        self.auto=QCheckBox('허용된 새 사진 자동 분석');self.auto.setChecked(self.settings.values.get('analysis_auto',False));self.auto.toggled.connect(lambda value:self.settings.values.update(analysis_auto=value));layout.addWidget(self.auto)
        self.vision=QCheckBox('설치된 얼굴·포즈 모델 사용');self.vision.setChecked(self.settings.values.get('analysis_vision',True));self.vision.toggled.connect(lambda value:self.settings.values.update(analysis_vision=value));layout.addWidget(self.vision)
        note=QLabel('선택 모델: '+('설치됨 · CPU 로컬 처리' if available() else '미설치 · 색상 분석 사용 가능')+'\n사진을 외부로 전송하지 않고, 분석이 원본 다운로드나 AI 복원 입력 허용을 대신하지 않습니다.');note.setWordWrap(True);note.setObjectName('muted');layout.addWidget(note)
        release=QPushButton('분석 모델 작업 메모리 비우기');release.clicked.connect(self.release_models);layout.addWidget(release);layout.addStretch()
        scroll2=QScrollArea();scroll2.setWidgetResizable(True);scroll2.setWidget(review);self.tabs.addTab(scroll2,'선택 사진 분석')
        self.status=QLabel('분석되지 않은 사진은 기본적으로 분석 필터에서 제외합니다.');self.status.setWordWrap(True);outer.addWidget(self.status)
        close=QPushButton('닫기');close.clicked.connect(self.hide);outer.addWidget(close)
        self.runner.changed.connect(self.refresh)
        self.color_enabled.toggled.connect(lambda value:self.minimum.setValue(20) if value and self.minimum.value()==0 else None)
        self.set_filters(filters);self.set_entry(None)
    def set_filters(self,value):
        self.color_enabled.setChecked(value.color is not None);self.color.setText(value.color or '#e6a8c4');self.delta.setValue(value.tolerance)
        self.minimum.setValue(value.min_share);self.maximum.setValue(value.max_share);self.region.setCurrentIndex(self.region.findData(value.region))
        self.composition.setCurrentIndex(max(0,self.composition.findData(value.composition)));self.count.setCurrentIndex(max(0,self.count.findData(value.count_class)))
        self.mp.setValue(value.min_mp);self.orientation.setCurrentIndex(self.orientation.findData(value.orientation));self.unknown.setChecked(value.include_unknown)
    def apply(self):
        try:value=AnalysisFilter(color=self.color.text().strip() if self.color_enabled.isChecked() else None,tolerance=self.delta.value(),min_share=self.minimum.value(),max_share=self.maximum.value(),region=self.region.currentData(),composition=self.composition.currentData(),count_class=self.count.currentData(),min_mp=self.mp.value(),orientation=self.orientation.currentData(),include_unknown=self.unknown.isChecked())
        except ValueError as error:self.status.setText(str(error));return
        self.applied.emit(value);self.status.setText('필터 적용 · 미분석/권한 미확인 결과의 취급을 확인하세요.')
    def choose_color(self):
        color=QColorDialog.getColor(parent=self)
        if color.isValid():self.color.setText(color.name());self.color_enabled.setChecked(True)
    def set_entry(self,identity):
        self.identity=identity;self.generation+=1;self.last=None
        for button in (self.analyze,self.rights,self.correct):button.setEnabled(bool(identity))
        self.rights.setEnabled(False)
        if identity:self.refresh()
        else:self.name.setText('사진을 선택하세요');self.summary.setText('갤러리에서 사진을 선택하면 분석 상태와 팔레트를 확인할 수 있습니다.')
    def refresh(self):
        if self.closed or self.saving or not self.identity or not self.isVisible():return
        generation=self.generation;identity=self.identity
        def done(value):
            if self.closed or generation!=self.generation:return
            self.last=value;entry=value['entry'];analysis=value['analysis'];override=value['override']
            self.name.setText(entry['name']);lines=['분석 권한: '+('확인됨' if value['allowed'] else '확인 필요')]
            if override:lines.append('수동 확인: '+COMPOSITIONS[override['composition']]+' · '+COUNTS[override['count_class']]+'\n'+override['note'])
            if analysis:
                payload=json.loads(analysis['payload'])
                lines.extend(['자동 구도: '+COMPOSITIONS[analysis['composition']],'검출 인원: '+COUNTS[analysis['count_class']],'구도 모델: '+{'ready':'실행됨 · 결과는 후보','not_installed':'미설치','disabled':'사용 안 함','error':'실패 · 색상만 사용'}.get(payload.get('vision_status'), '미확정')])
                lines.extend(payload.get('reasons',[]));lines.append('영역: '+('주 인물/배경 추정' if analysis['region_status']=='estimated_primary_person' else '제공할 충분한 근거 없음'))
            else:lines.append('현재 파일의 유효한 분석 결과 없음 · 권한 또는 재분석 확인')
            self.summary.setText('\n\n'.join(lines));self.rights.setEnabled(entry['kind']=='asset' and not entry.get('requires_review') and not self.saving)
            while self.swatches.count():
                item=self.swatches.takeAt(0)
                if item.widget():item.widget().deleteLater()
            for color in [c for c in value['colors'] if c['region']=='whole'][:4]:
                text=color['hex']+f"\n{100*color['weight']/color['total']:.0f}%";button=QPushButton(text)
                button.setStyleSheet('background:'+color['hex']+';color:'+('#15161b' if sum(int(color['hex'][i:i+2],16) for i in (1,3,5))>420 else '#fff')+';')
                button.clicked.connect(lambda _,hex=color['hex']:(self.color.setText(hex),self.color_enabled.setChecked(True),self.tabs.setCurrentIndex(0)))
                self.swatches.addWidget(button)
        self.tasks.submit(lambda _:self.service.details(identity),done,self.fail,key='analysis-details')
    def fail(self,message):self.status.setText('작업을 완료하지 못했습니다 · '+message)
    def mutate(self,operation):
        if self.saving:return
        self.saving=True
        def done(_):self.saving=False;self.refresh();self.changed.emit()
        def error(message):self.saving=False;self.fail(message)
        self.tasks.submit(lambda _:operation(),done,error)
    def analyze_selected(self):
        if not self.identity:return
        identity=self.identity
        def queue(_):
            self.service.require(identity)
            return self.service.enqueue(identity,force=True)
        def done(job):self.runner.request(job,'analysis');self.status.setText('선택 사진 분석을 요청했습니다. 구도는 검출 근거가 있을 때 후보로 표시합니다.')
        self.tasks.submit(queue,done,self.fail)
    def review_rights(self):
        if not self.identity or not self.last:return
        identity=self.identity;entry=self.last['entry']
        if entry['kind']!='asset' or entry.get('requires_review'):self.fail('원격 출처 권한은 온라인 → 출처 · URL에서 확인하세요.');return
        dialog=QDialog(self);dialog.setWindowTitle('선택 사진의 로컬 분석 권한');layout=QFormLayout(dialog)
        consent=QCheckBox('이 사진의 로컬 이미지 분석 권한을 확인했습니다.');consent.setChecked(self.last['allowed']);layout.addRow(consent)
        reference=QLineEdit();reference.setPlaceholderText('권리 문서/자체 촬영 기록 식별자');layout.addRow(reference)
        note=QLineEdit();note.setPlaceholderText('확인한 이용 범위와 근거');layout.addRow(note)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addRow(buttons)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        if not reference.text().strip() or not note.text().strip():self.fail('권리 근거와 검토 내용을 입력하세요.');return
        state='allowed' if consent.isChecked() else 'denied';evidence={'reference':reference.text().strip(),'note':note.text().strip()}
        def save():
            if state=='allowed':self.online.web.repo.grant_many('local',{'local_analysis':state},evidence,enabled=True)
            self.online.web.repo.grant('local','local_analysis',state,evidence,'asset:'+identity)
        self.mutate(save)
    def review(self):
        if not self.identity:return
        identity=self.identity;dialog=QDialog(self);dialog.setWindowTitle('수동 구도 확인');form=QFormLayout(dialog)
        composition=QComboBox();count=QComboBox()
        for value,label in COMPOSITIONS.items():composition.addItem(label,value)
        for value,label in COUNTS.items():count.addItem(label,value)
        note=QLineEdit();note.setPlaceholderText('직접 확인한 근거 · 인물 신원 자동 연결 없음')
        form.addRow('구도',composition);form.addRow('인원',count);form.addRow(note)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);form.addRow(buttons)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            composition_value,count_value,note_value=composition.currentData(),count.currentData(),note.text()
            self.mutate(lambda:self.service.override(identity,composition_value,count_value,note_value))
    def clear_override(self):
        if not self.identity:return
        identity=self.identity
        def clear():
            with self.online.db.connect() as c:c.execute('DELETE FROM analysis_overrides WHERE entry_id=?',(identity,))
        self.mutate(clear)
    def release_models(self):
        self.runner.analysis_tasks.submit(lambda _:self.service.close(),lambda _:self.status.setText('분석 모델의 작업 메모리를 비웠습니다.'),self.fail)
    def shutdown(self):
        if self.closed:return
        self.closed=True
        try:self.runner.changed.disconnect(self.refresh)
        except RuntimeError:pass
        self.tasks.stop();self.hide()
